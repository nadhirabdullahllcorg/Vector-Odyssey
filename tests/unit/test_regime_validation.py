"""Unit tests for vo.telemetry.regime_validation -- the Regime Engine
Validation Report v1 (temporal stability, transition matrix, duration
distributions, ER/Hurst comparison, class-imbalance-aware anticipation
accuracy). Hand-built inputs and, where a formula's correctness matters
(Mann-Whitney U, the binomial test, the Wilson interval), hand-verifiable
numeric examples."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from vo.telemetry.regime_feed import build_regime_segments
from vo.telemetry.regime_report import durations_by_regime
from vo.telemetry.regime_validation import (
    build_accuracy_validation,
    build_duration_distributions,
    build_evidence_comparison,
    build_period_breakdown,
    build_transition_matrix,
    render_validation_report_markdown,
)
from vo.time.sessions import SessionConfig, SessionWindow

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")
_BASE = datetime(2023, 1, 1, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _bar(when: datetime) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=when,
        open=10.0,
        high=10.5,
        low=9.5,
        close=10.0,
        tick_volume=1,
        real_volume=0,
    )


def _state(
    regime: RegimeType,
    when: datetime,
    *,
    direction: RegimeDirection | None = None,
    confidence: float = 0.6,
    supersedes: str | None = None,
    anticipated: AnticipatedResolution | None = None,
    features: tuple[RegimeFeature, ...] = (),
    suffix: str = "",
) -> RegimeState:
    return RegimeState(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id=f"st:{regime.name}:{when.isoformat()}{suffix}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        supersedes=supersedes,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        regime=regime,
        direction=direction,
        confidence=confidence,
        evidence="test",
        supporting_features=features,
        anticipated_resolution=anticipated,
    )


def _transition(from_state: RegimeType, to_state: RegimeType, when: datetime) -> RegimeTransition:
    return RegimeTransition(
        object_type=OBJECT_TYPE_REGIME_TRANSITION,
        object_id=f"tr:{from_state.name}->{to_state.name}:{when.isoformat()}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        from_state=from_state,
        to_state=to_state,
        evidence="test",
    )


def _session_config() -> SessionConfig:
    return SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=__import__("datetime").time(0, 0),
        sessions=(
            SessionWindow(
                name="MORNING",
                start=__import__("datetime").time(0, 0),
                end=__import__("datetime").time(12, 0),
            ),
            SessionWindow(
                name="AFTERNOON",
                start=__import__("datetime").time(12, 0),
                end=__import__("datetime").time(23, 59, 59),
            ),
        ),
        rth=SessionWindow(
            name="RTH",
            start=__import__("datetime").time(0, 0),
            end=__import__("datetime").time(23, 59, 59),
        ),
    )


# ── period breakdown ──────────────────────────────────────────────────────


def test_build_period_breakdown_returns_empty_for_no_bars() -> None:
    assert build_period_breakdown((), (), (), ()) == ()


def test_build_period_breakdown_splits_by_calendar_year_and_labels_ytd() -> None:
    dec31 = datetime(2023, 12, 31, 23, 0, tzinfo=UTC)
    jan5 = datetime(2024, 1, 5, 0, 0, tzinfo=UTC)
    states = (
        _state(RegimeType.EXPANSION, dec31, direction=RegimeDirection.UP),
        _state(RegimeType.EXPANSION, jan5, direction=RegimeDirection.UP),
    )
    bars = (_bar(dec31), _bar(dec31 + timedelta(minutes=1)), _bar(jan5))
    segments = build_regime_segments(states, bars)
    periods = build_period_breakdown(segments, states, (), bars)
    labels = [p.label for p in periods]
    assert labels == ["2023", "2024 (YTD)"]
    assert periods[0].report.bar_count == 2
    assert periods[1].report.bar_count == 1


def test_build_period_breakdown_labels_a_complete_final_year_without_ytd() -> None:
    dec28 = datetime(2023, 12, 28, 0, 0, tzinfo=UTC)
    states = (_state(RegimeType.EXPANSION, dec28, direction=RegimeDirection.UP),)
    bars = (_bar(dec28),)
    segments = build_regime_segments(states, bars)
    periods = build_period_breakdown(segments, states, (), bars)
    assert periods[0].label == "2023"


# ── transition matrix ─────────────────────────────────────────────────────


def test_transition_matrix_counts_and_conditional_probability() -> None:
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, _at(0)),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, _at(1)),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, _at(2)),
        _transition(RegimeType.EXPANSION, RegimeType.CONSOLIDATION, _at(3)),
        _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, _at(4)),
    )
    matrix = build_transition_matrix(transitions)
    assert matrix.counts[(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED)] == 3
    assert matrix.counts[(RegimeType.EXPANSION, RegimeType.CONSOLIDATION)] == 1
    assert matrix.row_totals[RegimeType.EXPANSION] == 4
    exp_to_pullback = matrix.probability(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED)
    assert exp_to_pullback == pytest.approx(0.75)
    assert matrix.probability(RegimeType.EXPANSION, RegimeType.REVERSAL) == pytest.approx(0.0)


def test_transition_matrix_probability_is_none_for_a_from_state_never_seen() -> None:
    matrix = build_transition_matrix(())
    assert matrix.probability(RegimeType.CONSOLIDATION, RegimeType.EXPANSION) is None


# ── duration distributions ────────────────────────────────────────────────
#
# percentile()'s own formula verification lives in
# test_research_statistics.py now that it moved to vo.research.statistics.


def test_build_duration_distributions_only_includes_regimes_with_real_duration() -> None:
    durations = {
        RegimeType.EXPANSION: [10.0, 20.0, 30.0, 40.0],
        RegimeType.RETRACEMENT: [],  # momentary -- never has real duration
    }
    dists = build_duration_distributions(durations)
    assert len(dists) == 1
    assert dists[0].regime is RegimeType.EXPANSION
    assert dists[0].n == 4
    assert dists[0].minimum == 10.0
    assert dists[0].maximum == 40.0


# ── evidence comparison ───────────────────────────────────────────────────
#
# Mann-Whitney U / binomial test / Wilson interval formula verification
# lives in test_research_statistics.py now that they moved to
# vo.research.statistics.


def test_build_evidence_comparison_groups_by_regime_and_feature() -> None:
    states = (
        _state(
            RegimeType.RETRACEMENT,
            _at(0),
            features=(RegimeFeature("efficiency_ratio", 0.2, "x"),),
        ),
        _state(
            RegimeType.RETRACEMENT,
            _at(1),
            features=(RegimeFeature("efficiency_ratio", 0.3, "x"),),
        ),
        _state(
            RegimeType.REVERSAL,
            _at(2),
            features=(RegimeFeature("efficiency_ratio", 0.8, "x"),),
        ),
        _state(
            RegimeType.REVERSAL,
            _at(3),
            features=(RegimeFeature("efficiency_ratio", 0.9, "x"),),
        ),
    )
    comparison = build_evidence_comparison(states)
    er_groups = {g.regime: g for g in comparison.er_by_regime}
    assert er_groups[RegimeType.RETRACEMENT].mean == pytest.approx(0.25)
    assert er_groups[RegimeType.REVERSAL].mean == pytest.approx(0.85)
    # Fully separated ER between the two regimes -> a below-0.5 p-value
    # (n=2 each is too small for the normal approximation to reach a very
    # small p on full separation alone -- see the dedicated Mann-Whitney
    # tests above for exact-value verification at a size where that holds).
    assert comparison.er_retracement_vs_reversal.p_value is not None
    assert comparison.er_retracement_vs_reversal.p_value < 0.5


def test_build_evidence_comparison_skips_regimes_with_no_recorded_feature() -> None:
    states = (_state(RegimeType.EXPANSION, _at(0)),)  # no supporting_features
    comparison = build_evidence_comparison(states)
    assert comparison.er_by_regime == ()
    assert comparison.hurst_by_regime == ()


# ── accuracy validation: class imbalance, sessions, quartiles ───────────


def test_build_accuracy_validation_class_imbalance_baseline() -> None:
    # 3 RETRACEMENT resolutions, 1 REVERSAL -- majority class is RETRACEMENT
    # at 3/4 = 0.75. All four leaned, all matched -> observed rate 1.0,
    # so the class-imbalance baseline (0.75) is beaten here.
    pullback_r1 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(0),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.9,
    )
    pullback_r2 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(10),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.9,
        suffix="b",
    )
    pullback_r3 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(20),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.9,
        suffix="c",
    )
    pullback_v = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(30),
        anticipated=AnticipatedResolution.REVERSAL,
        confidence=0.9,
        suffix="d",
    )
    states = (
        pullback_r1,
        _state(RegimeType.RETRACEMENT, _at(1), supersedes=pullback_r1.object_id),
        pullback_r2,
        _state(RegimeType.RETRACEMENT, _at(11), supersedes=pullback_r2.object_id),
        pullback_r3,
        _state(RegimeType.RETRACEMENT, _at(21), supersedes=pullback_r3.object_id),
        pullback_v,
        _state(RegimeType.REVERSAL, _at(31), supersedes=pullback_v.object_id),
    )
    accuracy = build_accuracy_validation(states)
    assert accuracy.resolutions == 4
    assert accuracy.to_retracement == 3
    assert accuracy.to_reversal == 1
    assert accuracy.leaned == 4
    assert accuracy.matched == 4
    assert accuracy.rate == pytest.approx(1.0)
    assert accuracy.majority_class is RegimeType.RETRACEMENT
    assert accuracy.majority_baseline_rate == pytest.approx(0.75)
    assert accuracy.wilson_ci is not None


def test_build_accuracy_validation_flags_lean_below_majority_baseline() -> None:
    # 3 REVERSAL, 1 RETRACEMENT (majority = REVERSAL, baseline 0.75). Every
    # lean is wrong (always leans the minority outcome) -> rate 0.0, well
    # below the 0.75 baseline -- exactly the "looks better than a coin flip,
    # worse than the trivial baseline" case this module exists to catch.
    pb1 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(0),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.5,
    )
    pb2 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(10),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.5,
        suffix="b",
    )
    pb3 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(20),
        anticipated=AnticipatedResolution.RETRACEMENT,
        confidence=0.5,
        suffix="c",
    )
    pb4 = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(30),
        anticipated=AnticipatedResolution.REVERSAL,
        confidence=0.5,
        suffix="d",
    )
    states = (
        pb1,
        _state(RegimeType.REVERSAL, _at(1), supersedes=pb1.object_id),
        pb2,
        _state(RegimeType.REVERSAL, _at(11), supersedes=pb2.object_id),
        pb3,
        _state(RegimeType.REVERSAL, _at(21), supersedes=pb3.object_id),
        pb4,
        _state(RegimeType.RETRACEMENT, _at(31), supersedes=pb4.object_id),
    )
    accuracy = build_accuracy_validation(states)
    assert accuracy.majority_class is RegimeType.REVERSAL
    assert accuracy.majority_baseline_rate == pytest.approx(0.75)
    assert accuracy.rate == pytest.approx(0.0)
    assert accuracy.matched == 0


def test_build_accuracy_validation_walks_a_multi_hop_lean_refresh_chain() -> None:
    """A pullback whose lean flips before resolving supersedes itself
    (regime.py's _maybe_refresh_lean) -- the accuracy check must use the
    FINAL lean (one hop back from the resolution) but the ORIGINAL entry
    time for pullback-duration bucketing (see _pullback_origin)."""
    origin = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(0),
        anticipated=AnticipatedResolution.REVERSAL,
        confidence=0.4,
    )
    refreshed = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(5),
        anticipated=AnticipatedResolution.RETRACEMENT,  # lean flipped
        confidence=0.7,
        supersedes=origin.object_id,
        suffix="b",
    )
    resolution = _state(RegimeType.RETRACEMENT, _at(6), supersedes=refreshed.object_id)
    accuracy = build_accuracy_validation((origin, refreshed, resolution))
    assert accuracy.leaned == 1
    assert accuracy.matched == 1  # final lean (RETRACEMENT) matches the outcome
    assert len(accuracy.by_pullback_duration_quartile) == 0  # <4 leaned records, no quartiles


def test_build_accuracy_validation_by_session(monkeypatch: pytest.MonkeyPatch) -> None:
    pb_morning = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        datetime(2023, 1, 1, 1, 0, tzinfo=UTC),
        anticipated=AnticipatedResolution.RETRACEMENT,
    )
    pb_afternoon = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        datetime(2023, 1, 1, 13, 0, tzinfo=UTC),
        anticipated=AnticipatedResolution.REVERSAL,
        suffix="b",
    )
    states = (
        pb_morning,
        _state(
            RegimeType.RETRACEMENT,
            datetime(2023, 1, 1, 1, 1, tzinfo=UTC),
            supersedes=pb_morning.object_id,
        ),
        pb_afternoon,
        _state(
            RegimeType.REVERSAL,
            datetime(2023, 1, 1, 13, 1, tzinfo=UTC),
            supersedes=pb_afternoon.object_id,
        ),
    )
    accuracy = build_accuracy_validation(states, session_config=_session_config())
    by_session = {s.label: s for s in accuracy.by_session}
    assert by_session["MORNING"].n == 1
    assert by_session["MORNING"].matched == 1
    assert by_session["AFTERNOON"].n == 1
    assert by_session["AFTERNOON"].matched == 1


# ── markdown rendering (smoke) ────────────────────────────────────────────


def test_render_validation_report_markdown_smoke() -> None:
    pb = _state(
        RegimeType.PULLBACK_UNRESOLVED,
        _at(0),
        anticipated=AnticipatedResolution.RETRACEMENT,
        features=(RegimeFeature("efficiency_ratio", 0.4, "x"),),
    )
    states = (
        _state(
            RegimeType.EXPANSION,
            _at(-10),
            direction=RegimeDirection.UP,
            features=(RegimeFeature("efficiency_ratio", 0.6, "x"),),
        ),
        pb,
        _state(
            RegimeType.RETRACEMENT,
            _at(1),
            supersedes=pb.object_id,
            features=(RegimeFeature("efficiency_ratio", 0.3, "x"),),
        ),
    )
    bars = tuple(_bar(_at(i)) for i in range(-10, 2))
    segments = build_regime_segments(states, bars)
    transitions = (
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, _at(0)),
        _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, _at(1)),
    )
    periods = build_period_breakdown(segments, states, transitions, bars)
    matrix = build_transition_matrix(transitions)
    durations = build_duration_distributions(durations_by_regime(segments, bars=bars))
    evidence = build_evidence_comparison(states)
    accuracy = build_accuracy_validation(states)

    markdown = render_validation_report_markdown(
        instrument_key="MT5:Test:US100",
        timeframe_canonical="M1",
        generated_utc=_at(100),
        periods=periods,
        transition_matrix=matrix,
        durations=durations,
        evidence=evidence,
        accuracy=accuracy,
    )
    assert markdown.startswith("# Regime Engine Validation Report v1")
    assert "## 1. Temporal stability" in markdown
    assert "## 2. Transition matrix" in markdown
    assert "## 3. Duration distributions" in markdown
    assert "## 4. ER / Hurst relationship" in markdown
    assert "## 5. Anticipation lean accuracy" in markdown
    assert "not a trading edge" in markdown
