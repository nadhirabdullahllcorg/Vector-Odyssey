"""Unit tests for vo.research.markov_report -- Phase 17's standalone
conditional Markov transition study. Hand-built inputs; TransitionMatrix
construction itself is already covered in test_research_transitions.py
(or wherever build_transition_matrix's own tests live), so these tests
focus on this module's own orchestration: session/value-bucket/pullback-
duration conditioning, small-cell flagging, and the Month 1 structural
constraint check."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from vo.observation.regime import (
    OBJECT_TYPE_REGIME_STATE,
    OBJECT_TYPE_REGIME_TRANSITION,
    RegimeDirection,
    RegimeState,
    RegimeTransition,
    RegimeType,
)
from vo.research.markov_report import (
    build_preceding_pullback_duration_matrices,
    build_rth_matrices,
    build_session_matrices,
    build_value_bucket_matrices,
    render_markov_report_markdown,
    small_cell_rows,
)
from vo.research.transitions import build_transition_matrix
from vo.time.sessions import SessionConfig, SessionWindow

_INSTRUMENT_ID = "US100"
_BASE = datetime(2023, 1, 1, tzinfo=UTC)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _transition(from_state: RegimeType, to_state: RegimeType, when: datetime) -> RegimeTransition:
    from vo.market.identity import InstrumentId
    from vo.market.timeframe import Timeframe

    return RegimeTransition(
        object_type=OBJECT_TYPE_REGIME_TRANSITION,
        object_id=f"tr:{from_state.name}->{to_state.name}:{when.isoformat()}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        instrument_id=InstrumentId(
            platform="MT5", broker_server="Test", broker_symbol=_INSTRUMENT_ID
        ),
        timeframe=Timeframe.M1,
        from_state=from_state,
        to_state=to_state,
        evidence="test",
    )


def _state(
    regime: RegimeType,
    when: datetime,
    *,
    supersedes: str | None = None,
    suffix: str = "",
) -> RegimeState:
    from vo.market.identity import InstrumentId
    from vo.market.timeframe import Timeframe

    return RegimeState(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id=f"st:{regime.name}:{when.isoformat()}{suffix}",
        observed_at=when,
        recorded_at=when,
        methodology_version=1,
        supersedes=supersedes,
        instrument_id=InstrumentId(
            platform="MT5", broker_server="Test", broker_symbol=_INSTRUMENT_ID
        ),
        timeframe=Timeframe.M1,
        regime=regime,
        direction=RegimeDirection.UP,
        confidence=0.6,
        evidence="test",
    )


def _session_config() -> SessionConfig:
    return SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=time(0, 0),
        sessions=(
            SessionWindow(name="MORNING", start=time(0, 0), end=time(12, 0)),
            SessionWindow(name="EVENING", start=time(18, 0), end=time(23, 59, 59)),
        ),
        rth=SessionWindow(name="RTH", start=time(0, 0), end=time(23, 59, 59)),
    )


# ── by session ─────────────────────────────────────────────────────────


def test_build_session_matrices_buckets_transitions_by_session_window() -> None:
    morning = _at(0)  # 00:00 -> MORNING
    evening = _at(60 * 19)  # 19:00 -> EVENING
    transitions = (
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, morning),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, evening),
    )
    result = build_session_matrices(transitions, _session_config())
    assert set(result) == {"MORNING", "EVENING"}
    assert result["MORNING"].counts[(RegimeType.CONSOLIDATION, RegimeType.EXPANSION)] == 1
    assert result["EVENING"].counts[(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED)] == 1


def test_build_session_matrices_off_session_bucket() -> None:
    off_hours = _at(60 * 15)  # 15:00 -> between MORNING (ends 12:00) and EVENING (starts 18:00)
    transitions = (_transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, off_hours),)
    result = build_session_matrices(transitions, _session_config())
    assert "OFF_SESSION" in result


def test_build_rth_matrices_buckets_transitions_by_rth() -> None:
    """2026-09-18, at the user's request -- same pattern as the session
    tests above, but a narrower RTH window (09:00-17:00) than
    _session_config()'s own (which spans the whole day), so RTH vs
    NON_RTH is actually exercised."""
    config = SessionConfig(
        instrument_symbol="TEST",
        timezone="UTC",
        trading_day_opens=time(0, 0),
        sessions=(SessionWindow(name="ALL_DAY", start=time(0, 0), end=time(23, 59, 59)),),
        rth=SessionWindow(name="RTH", start=time(9, 0), end=time(17, 0)),
    )
    rth_time = _at(60 * 10)  # 10:00 -> RTH
    non_rth_time = _at(60 * 20)  # 20:00 -> NON_RTH
    transitions = (
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, rth_time),
        _transition(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED, non_rth_time),
    )
    result = build_rth_matrices(transitions, config)
    assert set(result) == {"RTH", "NON_RTH"}
    assert result["RTH"].counts[(RegimeType.CONSOLIDATION, RegimeType.EXPANSION)] == 1
    assert result["NON_RTH"].counts[(RegimeType.EXPANSION, RegimeType.PULLBACK_UNRESOLVED)] == 1


# ── by value bucket (Hurst/ER, generic) ──────────────────────────────────


def test_build_value_bucket_matrices_splits_into_terciles() -> None:
    # nine evenly-spaced samples, values 0.1 .. 0.9 -> clean Low/Mid/High split
    samples = tuple((_at(i * 10), 0.1 + i * 0.1) for i in range(9))
    transitions = tuple(
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(i * 10 + 1))
        for i in range(9)
    )
    matrices, cuts = build_value_bucket_matrices(transitions, samples, max_gap_minutes=15.0)
    assert cuts is not None
    assert set(matrices) <= {"Low", "Mid", "High"}
    total = sum(sum(m.row_totals.values()) for m in matrices.values())
    assert total == 9


def test_build_value_bucket_matrices_excludes_transitions_past_max_gap() -> None:
    samples = ((_at(0), 0.5), (_at(10), 0.6), (_at(20), 0.7))
    far_transition = _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(500))
    matrices, cuts = build_value_bucket_matrices((far_transition,), samples, max_gap_minutes=15.0)
    assert cuts is not None
    assert sum(sum(m.row_totals.values()) for m in matrices.values()) == 0


def test_build_value_bucket_matrices_too_few_samples_returns_empty() -> None:
    samples = ((_at(0), 0.5), (_at(10), 0.6))
    matrices, cuts = build_value_bucket_matrices((), samples, max_gap_minutes=15.0)
    assert matrices == {}
    assert cuts is None


# ── by preceding pullback duration ───────────────────────────────────────


def test_build_preceding_pullback_duration_matrices_matches_resolution_by_observed_at() -> None:
    origin = _at(0)
    resolved_at = _at(45)
    pullback_state = _state(RegimeType.PULLBACK_UNRESOLVED, origin)
    resolution_state = _state(
        RegimeType.RETRACEMENT, resolved_at, supersedes=pullback_state.object_id
    )
    transition = _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, resolved_at)

    # pad with three more qualifying pullbacks of different durations so
    # quantiles(n=4) has enough points to compute cuts from.
    extra_states = []
    extra_transitions = []
    for i, minutes in enumerate((10, 90, 200), start=1):
        o = _at(1000 * i)
        r = o + timedelta(minutes=minutes)
        pb = _state(RegimeType.PULLBACK_UNRESOLVED, o, suffix=f":{i}")
        res = _state(RegimeType.REVERSAL, r, supersedes=pb.object_id, suffix=f":{i}")
        tr = _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.REVERSAL, r)
        extra_states.extend([pb, res])
        extra_transitions.append(tr)

    states = (pullback_state, resolution_state, *extra_states)
    transitions = (transition, *extra_transitions)

    matrices, cuts = build_preceding_pullback_duration_matrices(transitions, states)
    assert cuts is not None
    total = sum(sum(m.row_totals.values()) for m in matrices.values())
    assert total == 4


def test_build_preceding_pullback_duration_matrices_walks_supersedes_chain() -> None:
    # pullback first seen at t=0, re-leaned (superseding itself) at t=20,
    # finally resolves at t=50 -- origin should be t=0, not t=20.
    first_lean = _state(RegimeType.PULLBACK_UNRESOLVED, _at(0), suffix=":a")
    refreshed_lean = _state(
        RegimeType.PULLBACK_UNRESOLVED, _at(20), supersedes=first_lean.object_id, suffix=":b"
    )
    resolution = _state(
        RegimeType.RETRACEMENT, _at(50), supersedes=refreshed_lean.object_id, suffix=":c"
    )
    transition = _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, _at(50))

    # pad to reach the n>=4 minimum
    padding_states = []
    padding_transitions = []
    for i, minutes in enumerate((5, 15, 25), start=1):
        o = _at(2000 * i)
        r = o + timedelta(minutes=minutes)
        pb = _state(RegimeType.PULLBACK_UNRESOLVED, o, suffix=f":pad{i}")
        res = _state(RegimeType.REVERSAL, r, supersedes=pb.object_id, suffix=f":pad{i}")
        tr = _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.REVERSAL, r)
        padding_states.extend([pb, res])
        padding_transitions.append(tr)

    states = (first_lean, refreshed_lean, resolution, *padding_states)
    transitions = (transition, *padding_transitions)
    matrices, cuts = build_preceding_pullback_duration_matrices(transitions, states)
    assert cuts is not None
    # the 50-minute duration transition should land in the highest quartile
    # bucket, since padding durations are 5/15/25 minutes.
    assert "Q4 (highest)" in matrices
    q4_total = sum(matrices["Q4 (highest)"].row_totals.values())
    assert q4_total == 1


def test_build_preceding_pullback_duration_matrices_too_few_qualifying_returns_empty() -> None:
    pullback_state = _state(RegimeType.PULLBACK_UNRESOLVED, _at(0))
    resolution_state = _state(
        RegimeType.RETRACEMENT, _at(10), supersedes=pullback_state.object_id
    )
    transition = _transition(RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, _at(10))
    states = (pullback_state, resolution_state)
    matrices, cuts = build_preceding_pullback_duration_matrices((transition,), states)
    assert matrices == {}
    assert cuts is None


def test_build_preceding_pullback_duration_matrices_skips_orphan_transitions() -> None:
    # transition references a resolution that was never emitted as a state
    orphan_transition = _transition(
        RegimeType.PULLBACK_UNRESOLVED, RegimeType.RETRACEMENT, _at(999)
    )
    matrices, cuts = build_preceding_pullback_duration_matrices((orphan_transition,), ())
    assert matrices == {}
    assert cuts is None


# ── small-cell flagging ───────────────────────────────────────────────


def test_small_cell_rows_flags_rows_below_threshold() -> None:
    transitions = tuple(
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(i)) for i in range(5)
    )
    matrix = build_transition_matrix(transitions)
    warnings = small_cell_rows(matrix, min_n=30)
    assert any("CONSOLIDATION" in w for w in warnings)


def test_small_cell_rows_empty_when_all_rows_meet_threshold() -> None:
    transitions = tuple(
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(i)) for i in range(35)
    )
    matrix = build_transition_matrix(transitions)
    assert small_cell_rows(matrix, min_n=30) == ()


# ── rendering ─────────────────────────────────────────────────────────


def test_render_markov_report_markdown_flags_forbidden_direct_transition() -> None:
    transitions = (
        _transition(RegimeType.CONSOLIDATION, RegimeType.RETRACEMENT, _at(0)),
    )
    baseline = build_transition_matrix(transitions)
    markdown = render_markov_report_markdown(
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=_at(0),
        baseline=baseline,
        by_session={},
        by_hurst={},
        hurst_cuts=None,
        hurst_window=10,
        by_er={},
        er_cuts=None,
        er_window=10,
        by_pullback_duration={},
        pullback_duration_cuts=None,
    )
    assert "VIOLATED" in markdown
    assert "CONSOLIDATION" in markdown


def test_render_markov_report_markdown_holds_when_no_forbidden_transition_present() -> None:
    transitions = (
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(0)),
    )
    baseline = build_transition_matrix(transitions)
    markdown = render_markov_report_markdown(
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=_at(0),
        baseline=baseline,
        by_session={},
        by_hurst={},
        hurst_cuts=None,
        hurst_window=10,
        by_er={},
        er_cuts=None,
        er_window=10,
        by_pullback_duration={},
        pullback_duration_cuts=None,
    )
    assert "HELD" in markdown


def test_render_markov_report_markdown_renders_bucketed_sections_and_caveats() -> None:
    transitions = tuple(
        _transition(RegimeType.CONSOLIDATION, RegimeType.EXPANSION, _at(i)) for i in range(3)
    )
    baseline = build_transition_matrix(transitions)
    by_session = {"MORNING": baseline}
    markdown = render_markov_report_markdown(
        instrument_key="US100",
        timeframe_canonical="M1",
        generated_utc=_at(0),
        baseline=baseline,
        by_session=by_session,
        by_hurst={},
        hurst_cuts=None,
        hurst_window=10,
        by_er={},
        er_cuts=None,
        er_window=10,
        by_pullback_duration={},
        pullback_duration_cuts=None,
        min_cell_n=30,
    )
    assert "MORNING" in markdown
    assert "Sample-size caveats" in markdown
    assert "predictive" in markdown.lower()
