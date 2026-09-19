"""
StructureRangeEngine (vo.observation.structure_range), Phase 13b deliverable
(C) / gate G6.

Style mirrors tests/unit/test_observation_regime.py: value-type invariants
first, then direct-state transition checks (engine private fields set by
hand, exactly as test_observation_regime.py's own
test_unresolved_pullback_resolves_to_consolidation_on_failed_resumption
does -- the real SwingEngine wiring is only exercised once, in the
integration + no-lookahead tests at the bottom).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.core.replay import ReplayHarness, assert_no_lookahead
from vo.interfaces import CanonicalRecordError
from vo.market import Bar, BarSequence, InstrumentId, Timeframe
from vo.observation.structure_range import (
    OBJECT_TYPE_RANGE_STRUCTURE_STATE,
    RangeDirection,
    RangeStructureState,
    RangeStructureType,
    StructureRangeEngine,
)
from vo.observation.swings import (
    OBJECT_TYPE_SWING,
    SwingEngine,
    SwingLevel,
    SwingPoint,
    SwingStatus,
    SwingType,
)

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 19, 7, 0, tzinfo=UTC)


def _bar(minute: int, *, open: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0 + timedelta(minutes=minute),
        open=open,
        high=high,
        low=low,
        close=close,
        tick_volume=10,
        real_volume=0,
    )


def _swing_point(
    *, level: SwingLevel, swing_type: SwingType, body_price: float, minute: int
) -> SwingPoint:
    at = _T0 + timedelta(minutes=minute)
    return SwingPoint(
        object_type=OBJECT_TYPE_SWING,
        object_id=f"test:{level.name}:{swing_type.name}:{minute}",
        observed_at=at,
        recorded_at=at,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        level=level,
        swing_type=swing_type,
        status=SwingStatus.CONFIRMED,
        price=body_price,
        body_price=body_price,
        pivot_bar_id=f"bar-{minute}",
        confirmed_at_bar_id=f"bar-{minute}",
        reversal_ticks=10,
        atr_ticks_at_pivot=1,
        reversal_extreme_price=body_price,
        reversal_extreme_bar_id=f"bar-{minute}",
    )


def _make_engine() -> StructureRangeEngine:
    swing = SwingEngine(
        level=SwingLevel.SWING, k=1, atr_period=1, atr_multiplier=0.1, tick_size=1.0
    )
    internal = SwingEngine(
        level=SwingLevel.INTERNAL, k=1, atr_period=1, atr_multiplier=0.1, tick_size=1.0
    )
    return StructureRangeEngine(
        swing_tier_engine=swing, internal_tier_engine=internal, tick_size=1.0
    )


def _state(**over) -> RangeStructureState:
    base = dict(
        object_type=OBJECT_TYPE_RANGE_STRUCTURE_STATE,
        object_id="id-1",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        state=RangeStructureType.EXPANSION,
        direction=RangeDirection.UP,
        lower_boundary=100.0,
        upper_boundary=110.0,
        swing_anchor_id="swing-1",
        internal_anchor_id="internal-1",
        evidence="x",
    )
    base.update(over)
    return RangeStructureState(**base)


# ── value type invariants ────────────────────────────────────────────


def test_object_type_is_checked():
    with pytest.raises(CanonicalRecordError):
        _state(object_type="WRONG")


def test_inefficiency_ticks_only_valid_on_reversal():
    with pytest.raises(CanonicalRecordError):
        _state(state=RangeStructureType.RETRACEMENT, inefficiency_ticks=3)
    # Valid on REVERSAL.
    _state(state=RangeStructureType.REVERSAL, inefficiency_ticks=3)


def test_direction_only_meaningful_once_a_boundary_has_broken():
    with pytest.raises(CanonicalRecordError):
        _state(state=RangeStructureType.CONSOLIDATION, direction=RangeDirection.UP)
    # None is fine on CONSOLIDATION.
    _state(state=RangeStructureType.CONSOLIDATION, direction=None)


def test_engine_starts_in_consolidation_with_no_range():
    engine = _make_engine()
    assert engine.current_state() is RangeStructureType.CONSOLIDATION
    assert engine.current_boundaries() is None


# ── _update_anchors ──────────────────────────────────────────────────


def test_anchors_established_from_swing_low_and_internal_high():
    engine = _make_engine()
    low = _swing_point(level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=100.0, minute=0)
    high = _swing_point(
        level=SwingLevel.INTERNAL, swing_type=SwingType.HIGH, body_price=110.0, minute=1
    )
    engine._update_anchors((low,), (high,))
    assert engine.current_boundaries() == (100.0, 110.0)


def test_same_type_swing_moves_the_boundary_without_clearing_internal():
    engine = _make_engine()
    low = _swing_point(level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=100.0, minute=0)
    high = _swing_point(
        level=SwingLevel.INTERNAL, swing_type=SwingType.HIGH, body_price=110.0, minute=1
    )
    engine._update_anchors((low,), (high,))

    lower_low = _swing_point(
        level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=95.0, minute=2
    )
    engine._update_anchors((lower_low,), ())
    assert engine.current_boundaries() == (95.0, 110.0)


def test_opposite_type_swing_flips_configuration_and_clears_internal():
    engine = _make_engine()
    low = _swing_point(level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=100.0, minute=0)
    high = _swing_point(
        level=SwingLevel.INTERNAL, swing_type=SwingType.HIGH, body_price=110.0, minute=1
    )
    engine._update_anchors((low,), (high,))
    assert engine.current_boundaries() is not None

    flip = _swing_point(
        level=SwingLevel.SWING, swing_type=SwingType.HIGH, body_price=112.0, minute=3
    )
    engine._update_anchors((flip,), ())
    # Internal side cleared -- no range until a new opposite (LOW) internal
    # pivot confirms after the flip.
    assert engine.current_boundaries() is None

    new_internal_low = _swing_point(
        level=SwingLevel.INTERNAL, swing_type=SwingType.LOW, body_price=105.0, minute=4
    )
    engine._update_anchors((), (new_internal_low,))
    assert engine.current_boundaries() == (105.0, 112.0)


def test_internal_pivot_before_the_swing_anchor_is_ignored():
    engine = _make_engine()
    low = _swing_point(level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=100.0, minute=5)
    stale_high = _swing_point(
        level=SwingLevel.INTERNAL, swing_type=SwingType.HIGH, body_price=110.0, minute=2
    )
    engine._update_anchors((low,), (stale_high,))
    assert engine.current_boundaries() is None


# ── _check_expansion ─────────────────────────────────────────────────


def _with_range(engine: StructureRangeEngine, *, lower_type: SwingType) -> None:
    """Wires a 100/110 range directly, in either swing/internal
    configuration, bypassing _update_anchors for a focused check."""
    if lower_type is SwingType.LOW:
        engine._swing_anchor = _swing_point(
            level=SwingLevel.SWING, swing_type=SwingType.LOW, body_price=100.0, minute=0
        )
        engine._internal_anchor = _swing_point(
            level=SwingLevel.INTERNAL, swing_type=SwingType.HIGH, body_price=110.0, minute=1
        )
    else:
        engine._internal_anchor = _swing_point(
            level=SwingLevel.INTERNAL, swing_type=SwingType.LOW, body_price=100.0, minute=0
        )
        engine._swing_anchor = _swing_point(
            level=SwingLevel.SWING, swing_type=SwingType.HIGH, body_price=110.0, minute=1
        )


def test_expansion_up_on_body_close_above_upper_boundary():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    inside = _bar(10, open=107.0, high=109.0, low=106.0, close=108.0)
    assert engine._check_expansion(inside) is None

    breakout = _bar(11, open=111.0, high=113.0, low=110.5, close=112.0)
    assert engine._check_expansion(breakout) == (RangeDirection.UP, 110.0)


def test_expansion_down_on_body_close_below_lower_boundary_swing_high_config():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.HIGH)  # swing HIGH=110 upper, internal LOW=100 lower
    breakout = _bar(11, open=99.0, high=99.5, low=95.0, close=96.0)
    assert engine._check_expansion(breakout) == (RangeDirection.DOWN, 100.0)


def test_enter_expansion_emits_state_and_transition():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    breakout = _bar(11, open=111.0, high=113.0, low=110.5, close=112.0)
    state = engine._enter_expansion(breakout, RangeDirection.UP, 110.0)

    assert state.state is RangeStructureType.EXPANSION
    assert state.direction is RangeDirection.UP
    assert (state.lower_boundary, state.upper_boundary) == (100.0, 110.0)
    assert state.swing_anchor_id is not None and state.internal_anchor_id is not None
    assert engine.current_state() is RangeStructureType.EXPANSION
    assert engine._extreme_price == 112.0

    transitions = list(engine.transitions.all())
    assert transitions[-1].from_state is RangeStructureType.CONSOLIDATION
    assert transitions[-1].to_state is RangeStructureType.EXPANSION


# ── _advance_leg: extend / retrace / continue / reverse ─────────────


def _sequence_for_reversal_check(*, confirming_high_at_2: float) -> BarSequence:
    """5 bars; index 4 is the candidate reclaim bar, index 2 is its
    3-bar-separation comparator (see StructureRangeEngine._inefficiency_
    confirms). Only bars 2 and 4 matter for the assertions below."""
    seq = BarSequence()
    seq = seq.append(_bar(0, open=105.0, high=106.0, low=104.0, close=105.0))
    seq = seq.append(_bar(1, open=105.0, high=106.0, low=104.0, close=105.0))
    seq = seq.append(_bar(2, open=113.5, high=114.0, low=113.0, close=113.5))
    seq = seq.append(_bar(3, open=113.0, high=114.0, low=112.0, close=113.0))
    seq = seq.append(
        _bar(4, open=112.0, high=confirming_high_at_2, low=100.0, close=101.0)
    )
    return seq


def test_advance_leg_extends_expansion_on_a_new_extreme():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.EXPANSION
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 112.0

    seq = BarSequence().append(_bar(0, open=112.0, high=115.0, low=111.0, close=114.0))
    window = seq.window_at(0)

    result = engine._advance_leg(window, window.current, 0)
    assert result is None  # extending silently -- no new record
    assert engine._extreme_price == 114.0  # body_high = max(open, close), not the wick
    assert engine.current_state() is RangeStructureType.EXPANSION


def test_advance_leg_enters_retracement_when_boundary_still_respected():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.EXPANSION
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 115.0

    seq = BarSequence().append(_bar(0, open=114.0, high=115.0, low=112.0, close=113.0))
    window = seq.window_at(0)

    result = engine._advance_leg(window, window.current, 0)
    assert result is not None
    assert result.state is RangeStructureType.RETRACEMENT
    assert engine.current_state() is RangeStructureType.RETRACEMENT
    assert engine._extreme_price == 115.0  # untouched


def test_advance_leg_resumes_expansion_from_retracement_on_new_extreme():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.RETRACEMENT
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 115.0

    seq = BarSequence().append(_bar(0, open=115.0, high=118.0, low=114.0, close=117.0))
    window = seq.window_at(0)

    result = engine._advance_leg(window, window.current, 0)
    assert result is not None
    assert result.state is RangeStructureType.EXPANSION
    assert engine._extreme_price == 117.0


def test_advance_leg_confirms_reversal_with_a_body_separation_gap():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.RETRACEMENT
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 115.0

    seq = _sequence_for_reversal_check(confirming_high_at_2=112.0)
    window = seq.window_at(4)

    result = engine._advance_leg(window, window.current, 4)
    assert result is not None
    assert result.state is RangeStructureType.REVERSAL
    assert result.inefficiency_ticks == 1  # bar2.low(113) - bar4.high(112)
    # Reset for a fresh range (module docstring's inferred completion #2).
    assert engine.current_state() is RangeStructureType.CONSOLIDATION
    assert engine.current_boundaries() is None
    assert engine._direction is None


def test_advance_leg_reclaim_without_gap_stays_retracement():
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.RETRACEMENT
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 115.0

    seq = _sequence_for_reversal_check(confirming_high_at_2=114.0)  # no gap: 113 - 114 < 0
    window = seq.window_at(4)

    result = engine._advance_leg(window, window.current, 4)
    assert result is None  # no state change -- the interpretive gap case
    assert engine.current_state() is RangeStructureType.RETRACEMENT
    assert engine.current_boundaries() == (100.0, 110.0)  # untouched


def test_advance_leg_reclaim_without_gap_from_expansion_enters_retracement():
    """The reclaim-without-inefficiency branch still records the pullback
    (RETRACEMENT) the first time it happens, even though it does not
    promote to REVERSAL -- only a second, already-RETRACEMENT bar in the
    same state produces no further record."""
    engine = _make_engine()
    _with_range(engine, lower_type=SwingType.LOW)
    engine._regime = RangeStructureType.EXPANSION
    engine._direction = RangeDirection.UP
    engine._broken_boundary = 110.0
    engine._extreme_price = 115.0

    seq = _sequence_for_reversal_check(confirming_high_at_2=114.0)
    window = seq.window_at(4)

    result = engine._advance_leg(window, window.current, 4)
    assert result is not None
    assert result.state is RangeStructureType.RETRACEMENT


# ── integration: real SwingEngines end to end ───────────────────────


def _oscillating_tape(bars: int = 40) -> BarSequence:
    """A trending, oscillating tape with real (non-doji) bodies so both
    the swing-detection and the body-boundary logic actually get
    exercised -- unlike a symmetric center+-2 doji tape."""
    seq = BarSequence()
    for i in range(bars):
        drift = i * 1.5 if i < bars // 2 else (bars // 2) * 1.5 - (i - bars // 2) * 2.0
        osc = 6.0 if i % 2 == 0 else -6.0
        center = 100.0 + drift
        open_ = center + osc
        close = center - osc * 0.6
        high = max(open_, close) + 3.0
        low = min(open_, close) - 3.0
        seq = seq.append(_bar(i, open=open_, high=high, low=low, close=close))
    return seq


def _integration_engine() -> StructureRangeEngine:
    swing = SwingEngine(
        level=SwingLevel.SWING, k=3, atr_period=3, atr_multiplier=0.5, tick_size=0.01
    )
    internal = SwingEngine(
        level=SwingLevel.INTERNAL, k=1, atr_period=1, atr_multiplier=0.3, tick_size=0.01
    )
    return StructureRangeEngine(
        swing_tier_engine=swing, internal_tier_engine=internal, tick_size=0.01
    )


def test_engine_runs_end_to_end_against_real_swing_engines():
    seq = _oscillating_tape()
    engine = _integration_engine()
    ReplayHarness(seq).run(engine)

    states = list(engine.states.all())
    assert states, "engine emitted no range states against a trending, oscillating tape"
    assert all(s.evidence for s in states)

    # Month-1-independent structural sanity: CONSOLIDATION is only ever
    # left by breaking a boundary into EXPANSION (this rule's own
    # equivalent of the Month 1 "only through EXPANSION" invariant).
    for tr in engine.transitions.all():
        if tr.from_state is RangeStructureType.CONSOLIDATION:
            assert tr.to_state is RangeStructureType.EXPANSION


def test_no_lookahead():
    seq = _oscillating_tape()
    assert_no_lookahead(seq, _integration_engine, cutoff=25)
