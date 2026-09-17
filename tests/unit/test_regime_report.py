"""Unit tests for vo.telemetry.regime_report -- the deep-history backtest
summary. Segment durations/shares/transitions, ER/Hurst aggregation, and
the anticipation-lean accuracy, all over hand-built inputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from vo.telemetry.regime_report import build_regime_report

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
