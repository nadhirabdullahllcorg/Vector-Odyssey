"""Unit tests for vo.telemetry.regime_report -- the deep-history backtest
summary. Segment durations/shares/transitions, ER/Hurst aggregation, and
the anticipation-lean accuracy, all over hand-built inputs."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.regime import (
    OBJECT_TYPE_REGIME_STATE,
    OBJECT_TYPE_REGIME_TRANSITION,
    AnticipatedResolution,
    RegimeDirection,
    RegimeFeature,
    RegimeState,
    RegimeTransition,
    RegimeType,
)
from vo.telemetry.regime_feed import RegimeSegment
from vo.telemetry.regime_report import (
    build_regime_report,
    build_session_breakdown,
    durations_by_regime,
)
from vo.time.sessions import SessionConfig, SessionWindow

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")


_BASE = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _seg(regime: RegimeType, start_min: int, end_min: int | None) -> RegimeSegment:
    return RegimeSegment(
        regime=regime,
        direction=None,
        start_utc=_at(start_min),
        end_utc=_at(end_min) if end_min is not None else None,
        high=10.0,
        low=9.0,
        confidence=0.6,
        anticipated=None,
        efficiency_ratio=None,
        hurst_exponent=None,
        object_id=f"seg:{regime.name}:{start_min}",
        methodology_version=1,
        instrument_key="MT5:Test:US100",
        timeframe_canonical="M1",
    )


def _state(
    regime: RegimeType,
    minute: int,
    *,
    supersedes: str | None = None,
    anticipated: AnticipatedResolution | None = None,
    features: tuple[RegimeFeature, ...] = (),
    suffix: str = "",
) -> RegimeState:
    return RegimeState(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id=f"st:{regime.name}:{minute}{suffix}",
        observed_at=_at(minute),
        recorded_at=_at(minute),
        methodology_version=1,
        supersedes=supersedes,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        regime=regime,
        direction=RegimeDirection.UP if regime is RegimeType.RETRACEMENT else None,
        confidence=1.0 if regime in (RegimeType.RETRACEMENT, RegimeType.REVERSAL) else 0.5,
        evidence="test",
        supporting_features=features,
        anticipated_resolution=anticipated,
    )


def _transition(from_state: RegimeType, to_state: RegimeType, minute: int) -> RegimeTransition:
    return RegimeTransition(
        object_type=OBJECT_TYPE_REGIME_TRANSITION,
        object_id=f"tr:{from_state.name}->{to_state.name}:{minute}",
        observed_at=_at(minute),
        recorded_at=_at(minute),
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        from_state=from_state,
        to_state=to_state,
        evidence="test",
    )


def _bar(minute: int) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=10.0,
        high=10.5,
        low=9.5,
        close=10.0,
        tick_volume=1,
        real_volume=0,
        spread=1,
    )


def _session_config() -> SessionConfig:
    """A trivial UTC two-window config -- MORNING 00:00-00:30, AFTERNOON
    00:30-01:00 -- so test wall-clock == UTC and the arithmetic stays
    readable. Not real US100 config; that lives in sessions.yaml."""
    return SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=time(0, 0),
        sessions=(
            SessionWindow(name="MORNING", start=time(0, 0), end=time(0, 30)),
            SessionWindow(name="AFTERNOON", start=time(0, 30), end=time(1, 0)),
        ),
        rth=SessionWindow(name="RTH", start=time(0, 0), end=time(1, 0)),
    )


def test_shares_and_durations_from_segments() -> None:
    segments = (
        _seg(RegimeType.CONSOLIDATION, 0, 10),   # 10 min
        _seg(RegimeType.EXPANSION, 10, 40),      # 30 min
        _seg(RegimeType.CONSOLIDATION, 40, None),  # closed by history_end at 60 -> 20 min
    )
    report = build_regime_report(
        segments, (), transitions_log=(), history_end_utc=_at(60), bar_count=60
    )

    by_regime = {s.regime: s for s in report.per_regime}
    cons = by_regime[RegimeType.CONSOLIDATION]
    exp = by_regime[RegimeType.EXPANSION]

    assert cons.segment_count == 2
    assert cons.total_minutes == 30.0  # 10 + 20
    assert exp.total_minutes == 30.0
    # Two regimes split 30/60 each.
    assert abs(cons.share - 0.5) < 1e-9
    assert abs(exp.share - 0.5) < 1e-9
    assert exp.mean_minutes == 30.0
    assert cons.is_momentary is False
    assert exp.is_momentary is False


def test_transitions_come_from_the_real_log_not_segments() -> None:
    """The Transitions table is built from the engine's own
    RegimeTransition log (transitions_log), not reconstructed from
    consecutive segments -- see build_regime_report's own docstring for
    why a segment reconstruction can be wrong."""
    transitions_log = (
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, 0),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, 10),
        _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.EXPANSION, 20),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, 30),
    )
    report = build_regime_report((), (), transitions_log=transitions_log, history_end_utc=None)
    counts = {(f.name, t.name): c for f, t, c in report.transitions}
    assert counts[("CONSOLIDATION", "EXPANSION")] == 1
    assert counts[("EXPANSION", "PULLBACK_UNRESOLVED")] == 2
    assert counts[("PULLBACK_UNRESOLVED", "EXPANSION")] == 1


def test_transitions_survive_a_dropped_zero_width_resolution() -> None:
    """The exact bug this design fixes: PULLBACK_UNRESOLVED resolves to
    RETRACEMENT and immediately re-enters EXPANSION in the same bar (a
    zero-width run build_regime_segments drops), and the market pulls
    back again right away. A segment-pairwise reconstruction would see
    only [PULLBACK_UNRESOLVED, PULLBACK_UNRESOLVED] (RETRACEMENT and the
    brief EXPANSION both dropped) and report a phantom self-transition
    that never happened. The real log has no such gap."""
    transitions_log = (
        _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, 10),
        _transition(RegimeType.RETRACEMENT, RegimeType.EXPANSION, 10),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, 10),
    )
    report = build_regime_report((), (), transitions_log=transitions_log, history_end_utc=None)
    counts = {(f.name, t.name): c for f, t, c in report.transitions}
    assert ("PULLBACK_UNRESOLVED", "PULLBACK_UNRESOLVED") not in counts
    assert counts[("PULLBACK_UNRESOLVED", "RETRACEMENT")] == 1
    assert counts[("RETRACEMENT", "EXPANSION")] == 1
    assert counts[("EXPANSION", "PULLBACK_UNRESOLVED")] == 1


def test_momentary_regimes_appear_with_occurrence_counts_not_durations() -> None:
    """RETRACEMENT/REVERSAL have no segments (build_regime_segments drops
    their zero-width runs) but they still happened -- the distribution
    table must show them, flagged momentary, with a true occurrence
    count, not silently vanish."""
    segments = (_seg(RegimeType.EXPANSION, 0, None),)
    states = (
        _state(RegimeType.RETRACEMENT, 5),
        _state(RegimeType.RETRACEMENT, 15, suffix="b"),
        _state(RegimeType.REVERSAL, 25),
    )
    report = build_regime_report(
        segments, states, transitions_log=(), history_end_utc=_at(30)
    )
    by_regime = {s.regime: s for s in report.per_regime}

    retr = by_regime[RegimeType.RETRACEMENT]
    rev = by_regime[RegimeType.REVERSAL]
    assert retr.is_momentary is True
    assert retr.segment_count == 2
    assert retr.total_minutes == 0.0
    assert retr.share == 0.0
    assert rev.is_momentary is True
    assert rev.segment_count == 1

    # EXPANSION still carries the real duration; momentary rows never
    # dilute another regime's share (durations only ever sum segments).
    exp = by_regime[RegimeType.EXPANSION]
    assert exp.is_momentary is False
    assert exp.share == 1.0


def test_er_and_hurst_means_per_regime() -> None:
    def feat(er: float, hurst: float) -> tuple[RegimeFeature, ...]:
        return (
            RegimeFeature(name="efficiency_ratio", value=er, methodology="er"),
            RegimeFeature(name="hurst_exponent", value=hurst, methodology="h"),
        )

    segments = (_seg(RegimeType.EXPANSION, 0, None),)
    states = (
        _state(RegimeType.EXPANSION, 1, features=feat(0.6, 0.7)),
        _state(RegimeType.EXPANSION, 2, features=feat(0.8, 0.5), suffix="b"),
    )
    report = build_regime_report(segments, states, transitions_log=(), history_end_utc=_at(3))
    exp = next(s for s in report.per_regime if s.regime is RegimeType.EXPANSION)
    assert exp.mean_efficiency_ratio == 0.7  # (0.6 + 0.8) / 2
    assert exp.mean_hurst == 0.6  # (0.7 + 0.5) / 2


def test_anticipation_accuracy_matches_lean_against_outcome() -> None:
    # Pullback A leaned RETRACEMENT, resolved RETRACEMENT -> match.
    # Pullback B leaned REVERSAL, resolved RETRACEMENT -> miss.
    states = (
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            1,
            anticipated=AnticipatedResolution.RETRACEMENT,
            suffix="A",
        ),
        _state(RegimeType.RETRACEMENT, 2, supersedes="st:PULLBACK_UNRESOLVED:1A"),
        _state(
            RegimeType.PULLBACK_UNRESOLVED,
            3,
            anticipated=AnticipatedResolution.REVERSAL,
            suffix="B",
        ),
        _state(RegimeType.RETRACEMENT, 4, supersedes="st:PULLBACK_UNRESOLVED:3B", suffix="b"),
    )
    report = build_regime_report((), states, transitions_log=(), history_end_utc=None)
    a = report.anticipation
    assert a.resolutions == 2
    assert a.to_retracement == 2
    assert a.leaned == 2
    assert a.matched == 1
    assert a.rate == 0.5


def test_session_breakdown_attributes_bar_time_by_wall_clock_window() -> None:
    """60 one-minute bars, CONSOLIDATION for the first 20, EXPANSION (open
    -ended) after that. MORNING covers wall-clock minutes 0-29, AFTERNOON
    30-59, so MORNING should see a CONSOLIDATION/EXPANSION split and
    AFTERNOON should be pure EXPANSION."""
    segments = (
        _seg(RegimeType.CONSOLIDATION, 0, 20),
        _seg(RegimeType.EXPANSION, 20, None),
    )
    bars = tuple(_bar(m) for m in range(60))
    stats = build_session_breakdown(segments, bars, _session_config())
    by_cell = {(s.session, s.regime): s for s in stats}

    assert by_cell[("MORNING", RegimeType.CONSOLIDATION)].bar_count == 20
    assert by_cell[("MORNING", RegimeType.EXPANSION)].bar_count == 10
    assert by_cell[("AFTERNOON", RegimeType.EXPANSION)].bar_count == 30
    assert ("AFTERNOON", RegimeType.CONSOLIDATION) not in by_cell

    # Shares are of that session's own total, not the whole backtest.
    morning_cons = by_cell[("MORNING", RegimeType.CONSOLIDATION)]
    morning_exp = by_cell[("MORNING", RegimeType.EXPANSION)]
    assert abs(morning_cons.share_of_session - 20 / 30) < 1e-9
    assert abs(morning_exp.share_of_session - 10 / 30) < 1e-9
    afternoon_exp = by_cell[("AFTERNOON", RegimeType.EXPANSION)]
    assert afternoon_exp.share_of_session == 1.0

    # Each regime run counted where it STARTED, not where it later ran.
    assert morning_cons.segments_started == 1  # CONSOLIDATION starts at minute 0
    assert morning_exp.segments_started == 1  # EXPANSION starts at minute 20, still MORNING
    assert afternoon_exp.segments_started == 0  # EXPANSION did not start in AFTERNOON


def test_session_breakdown_marks_bars_before_first_segment_unclassified() -> None:
    """Bars before the engine ever emitted a state (warmup) get regime
    None, not silently folded into whichever regime came first."""
    segments = (_seg(RegimeType.EXPANSION, 10, None),)
    bars = tuple(_bar(m) for m in range(15))
    stats = build_session_breakdown(segments, bars, _session_config())
    by_cell = {(s.session, s.regime): s for s in stats}

    assert by_cell[("MORNING", None)].bar_count == 10  # minutes 0-9
    assert by_cell[("MORNING", RegimeType.EXPANSION)].bar_count == 5  # minutes 10-14


def test_build_regime_report_wires_session_breakdown_when_given_bars_and_config() -> None:
    """The report only computes a session breakdown when both `bars` and
    `session_config` are supplied -- omit either and `per_session` stays
    empty (same report as before this feature existed)."""
    segments = (_seg(RegimeType.EXPANSION, 0, None),)
    bars = tuple(_bar(m) for m in range(5))

    without = build_regime_report(
        segments, (), transitions_log=(), history_end_utc=_at(5), bar_count=5
    )
    assert without.per_session == ()

    with_config = build_regime_report(
        segments,
        (),
        transitions_log=(),
        history_end_utc=_at(5),
        bar_count=5,
        bars=bars,
        session_config=_session_config(),
    )
    assert with_config.per_session != ()
    assert sum(s.bar_count for s in with_config.per_session) == 5


def test_duration_uses_bar_count_not_wall_clock_when_bars_given() -> None:
    """The exact bug this fix targets: a segment spanning a gap with no
    bars in it (a stand-in for a weekend or the daily off-session close)
    must not have that gap counted as duration once real bars are
    supplied -- only the bars that actually exist should count."""
    # EXPANSION starts at minute 0. Bars exist for minutes 0-9 (10 bars),
    # then a big gap (no bars for "minutes" 10-999 -- a stand-in weekend),
    # then bars resume at minute 1000-1009 (10 more bars) before the
    # segment ends at minute 1010.
    segments = (_seg(RegimeType.EXPANSION, 0, 1010),)
    bars = tuple(_bar(m) for m in list(range(10)) + list(range(1000, 1010)))

    without_bars = build_regime_report(
        segments, (), transitions_log=(), history_end_utc=_at(1010)
    )
    exp_wallclock = without_bars.per_regime[0]
    assert exp_wallclock.total_minutes == 1010.0  # the old, wrong behavior

    with_bars = build_regime_report(
        segments, (), transitions_log=(), history_end_utc=_at(1010), bars=bars
    )
    exp_real = with_bars.per_regime[0]
    assert exp_real.total_minutes == 20.0  # only the 20 bars that actually exist
    assert exp_real.share == 1.0  # still the only regime, still 100% of real time


def test_durations_by_regime_matches_what_build_regime_report_uses_internally() -> None:
    """durations_by_regime was extracted out of build_regime_report's own
    body (a behavior-preserving refactor, v32) so other telemetry
    (vo.telemetry.regime_validation) can reuse the exact same bar-counted
    duration accounting rather than re-deriving it. This pins that the
    extracted function alone reproduces the per-regime totals the full
    report already reports."""
    segments = (
        _seg(RegimeType.CONSOLIDATION, 0, 10),
        _seg(RegimeType.EXPANSION, 10, 40),
    )
    bars = tuple(_bar(m) for m in range(0, 40))
    durations = durations_by_regime(segments, bars=bars)
    assert sum(durations[RegimeType.CONSOLIDATION]) == pytest.approx(10.0)
    assert sum(durations[RegimeType.EXPANSION]) == pytest.approx(30.0)

    report = build_regime_report(
        segments, (), transitions_log=(), history_end_utc=_at(40), bar_count=40, bars=bars
    )
    by_regime = {s.regime: s for s in report.per_regime}
    assert by_regime[RegimeType.CONSOLIDATION].total_minutes == pytest.approx(
        sum(durations[RegimeType.CONSOLIDATION])
    )
    assert by_regime[RegimeType.EXPANSION].total_minutes == pytest.approx(
        sum(durations[RegimeType.EXPANSION])
    )
