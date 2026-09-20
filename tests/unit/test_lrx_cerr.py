"""vo.valco.lrx_cerr -- the phase machine.

Case list: every valid transition, the breakout rule that a big candle
inside the range is not an expansion, retracement thresholds and the
single-candle rule, both invalidation paths, the direction invariant,
out-of-order reversals, failed-cycle preservation, immutability, and
transition auditability."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_cerr import (
    CerrConfig,
    CerrInvalidationReason,
    CerrState,
    CerrTrigger,
    begin_cycle,
    breaks_out,
    invalidate,
    observe_bar,
    observe_displacement,
    observe_reversal,
    reversal_matches,
)
from vo.valco.lrx_consolidation import (
    ConsolidationEvent,
    ConsolidationQualification,
    EfficiencyMeasure,
)
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    ExpansionDirection,
    measure_displacement,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_mss import (
    ConfirmationMethod,
    MssDirection,
    MssEvent,
    MssQualification,
    SwingSelection,
)
from vo.valco.lrx_sweep import SweepEvent

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_RANGE_HIGH = 20_010.0
_RANGE_LOW = 19_990.0


def _at(minute: int) -> datetime:
    return _START + timedelta(minutes=minute)


def _bar(minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _baseline(count: int, centre: float = 20_000.0, half: float = 5.0) -> list[Bar]:
    return [
        _bar(i, open_=centre, high=centre + half, low=centre - half, close=centre)
        for i in range(count)
    ]


def _consolidation() -> ConsolidationEvent:
    return ConsolidationEvent(
        consolidation_id="CONS:test",
        start_time=_at(0),
        end_time=_at(19),
        start_index=0,
        end_index=19,
        range_high=_RANGE_HIGH,
        range_low=_RANGE_LOW,
        range_points=_RANGE_HIGH - _RANGE_LOW,
        range_atr=1.0,
        net_move_points=0.0,
        net_move_atr=0.0,
        efficiency_ratio=0.05,
        body_efficiency_ratio=0.05,
        efficiency_measure=EfficiencyMeasure.KAUFMAN,
        bar_count=20,
        qualification=ConsolidationQualification.CONSOLIDATION,
    )


def _sweep(returned_index: int, side: LevelSide) -> SweepEvent:
    """Buy-side liquidity sits ABOVE price and is penetrated upward;
    sell-side sits BELOW and is penetrated downward. Getting that
    backwards puts the expansion origin on the wrong side of the leg,
    which invalidates every cycle built on it -- as an earlier draft of
    this fixture demonstrated."""
    buy_side = side is LevelSide.BUY_SIDE
    level = 20_050.0 if buy_side else 19_985.0
    return SweepEvent(
        sweep_id=f"SWEEP:test:{returned_index}",
        level=ReferenceLevel(
            kind=LevelKind.PREV_DAY_HIGH if buy_side else LevelKind.PREV_DAY_LOW,
            price=level,
            established_at=None,
            trading_day=_START.date(),
        ),
        side=side,
        penetration_index=returned_index - 1,
        penetration_price=level + 8.0 if buy_side else level - 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_at(returned_index),
        bars_beyond=1,
    )


def _mss(direction: MssDirection, *, break_index: int = 40) -> MssEvent:
    return MssEvent(
        mss_id=f"MSS:test:{direction}",
        sweep_id="SWEEP:test:38",
        displacement_id="DISP:reversal",
        direction=direction,
        qualification=MssQualification.PASS,
        broken_swing_id="swing-a",
        broken_price=20_000.0,
        swing_event_time=_at(30),
        swing_confirmation_time=_at(32),
        selection_method=SwingSelection.MOST_RECENT,
        alternate_swing_id=None,
        distance_from_displacement_origin=20.0,
        distance_from_sweep=20.0,
        break_index=break_index,
        break_price=20_005.0,
        break_time=_at(break_index),
        break_distance_points=5.0,
        break_distance_atr=1.0,
        confirmation_method=ConfirmationMethod.CANDLE_CLOSE,
    )


def _bullish_breakout():
    """A leg that escapes ABOVE the consolidation's high."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_005.0, high=20_030.0, low=20_004.0, close=20_028.0))
    bars.append(_bar(21, open_=20_028.0, high=20_060.0, low=20_027.0, close=20_058.0))
    displacement = measure_displacement(
        bars, 21, _sweep(20, LevelSide.SELL_SIDE), DisplacementConfig(), tick_size=_TICK
    )
    assert displacement is not None
    assert displacement.direction is ExpansionDirection.UP
    return bars, displacement


def _inside_the_range():
    """A large candle that never leaves the consolidation."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_991.0, high=20_009.0, low=19_990.5, close=20_008.0))
    bars.append(_bar(21, open_=20_008.0, high=20_009.5, low=19_991.0, close=20_009.0))
    displacement = measure_displacement(
        bars, 21, _sweep(20, LevelSide.SELL_SIDE), DisplacementConfig(), tick_size=_TICK
    )
    assert displacement is not None
    return bars, displacement


def _config(**overrides) -> CerrConfig:
    base = CerrConfig(minimum_retracement_points=10.0)
    return dataclasses.replace(base, **overrides) if overrides else base


# ── the cycle starts ──────────────────────────────────────────────────────


def test_a_cycle_begins_in_consolidation_with_its_transition_recorded() -> None:
    cycle = begin_cycle(_consolidation())

    assert cycle.state is CerrState.CONSOLIDATION
    assert cycle.consolidation_id == "CONS:test"
    assert cycle.direction is None
    assert cycle.active is True
    assert len(cycle.transitions) == 1

    first = cycle.transitions[0]
    assert first.previous_state is CerrState.IDLE
    assert first.new_state is CerrState.CONSOLIDATION
    assert first.trigger is CerrTrigger.CONSOLIDATION_CONFIRMED
    assert first.trigger_event_id == "CONS:test"
    assert first.trigger_event_type == "ConsolidationEvent"


# ── consolidation -> expansion ────────────────────────────────────────────


def test_a_displacement_leaving_the_range_begins_the_expansion() -> None:
    displacement = _bullish_breakout()[1]
    cycle = observe_displacement(begin_cycle(_consolidation()), displacement)

    assert cycle.state is CerrState.EXPANSION
    assert cycle.direction is ExpansionDirection.UP
    assert cycle.expansion is not None
    assert cycle.expansion.displacement_id == displacement.displacement_id
    assert cycle.expansion.consolidation_id == "CONS:test"
    assert cycle.expansion.extreme_price == displacement.high
    assert cycle.transitions[-1].trigger is CerrTrigger.BREAKOUT_DISPLACEMENT


def test_a_big_candle_inside_the_range_is_not_an_expansion() -> None:
    """The rule that stops a violent inside-bar sequence from starting a
    cycle that never had a breakout."""
    displacement = _inside_the_range()[1]
    consolidation = _consolidation()

    assert not breaks_out(consolidation, displacement)

    cycle = observe_displacement(begin_cycle(consolidation), displacement)
    assert cycle.state is CerrState.CONSOLIDATION
    assert cycle.expansion is None


def test_breakout_requires_the_correct_side_for_the_direction() -> None:
    consolidation = _consolidation()
    up_leg = _bullish_breakout()[1]

    assert breaks_out(consolidation, up_leg)
    # The same leg against a range it does not clear.
    higher = dataclasses.replace(consolidation, range_high=25_000.0)
    assert not breaks_out(higher, up_leg)


def test_a_displacement_offered_in_the_wrong_state_changes_nothing() -> None:
    displacement = _bullish_breakout()[1]
    cycle = begin_cycle(_consolidation())
    once = observe_displacement(cycle, displacement)
    twice = observe_displacement(once, displacement)

    assert twice == once


# ── expansion -> retracement ──────────────────────────────────────────────


def _expanded():
    bars, displacement = _bullish_breakout()
    cycle = observe_displacement(begin_cycle(_consolidation()), displacement)
    return bars, cycle


def test_a_single_opposing_candle_does_not_begin_a_retracement() -> None:
    """Without the threshold rule every pullback tick would end the
    expansion phase."""
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_055.0, close=20_056.0))

    after = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    assert after.state is CerrState.EXPANSION
    assert after.retracement is None


def test_crossing_the_threshold_begins_the_retracement() -> None:
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_040.0, close=20_042.0))

    after = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    assert after.state is CerrState.RETRACEMENT
    assert after.retracement is not None
    assert after.retracement.depth_points == pytest.approx(20.0)
    assert after.retracement.expansion_id == after.expansion_id
    assert after.transitions[-1].trigger is CerrTrigger.RETRACEMENT_THRESHOLD_REACHED


def test_price_extending_further_updates_the_extreme_without_transitioning() -> None:
    bars, cycle = _expanded()
    before = cycle.expansion.extreme_price
    bars.append(_bar(22, open_=20_058.0, high=20_090.0, low=20_057.0, close=20_088.0))

    after = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    assert after.state is CerrState.EXPANSION
    assert after.expansion is not None
    assert after.expansion.extreme_price == 20_090.0
    assert after.expansion.extreme_price > before


def test_a_deeper_retracement_updates_its_depth() -> None:
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_040.0, close=20_042.0))
    cycle = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)
    bars.append(_bar(23, open_=20_042.0, high=20_043.0, low=20_030.0, close=20_032.0))

    deeper = observe_bar(cycle, bars, 23, _config(), tick_size=_TICK)

    assert deeper.state is CerrState.RETRACEMENT
    assert deeper.retracement is not None
    assert deeper.retracement.depth_points == pytest.approx(30.0)


def test_with_no_threshold_configured_no_retracement_can_open() -> None:
    """The same refusal-to-guess as the consolidation detector's
    UNCONFIGURED."""
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_020.0, close=20_022.0))

    bare = CerrConfig()
    assert bare.has_retracement_threshold is False

    after = observe_bar(cycle, bars, 22, bare, tick_size=_TICK)
    assert after.state is CerrState.EXPANSION


def test_an_atr_threshold_with_no_atr_cannot_open_a_retracement() -> None:
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_020.0, close=20_022.0))

    after = observe_bar(
        cycle,
        bars,
        22,
        CerrConfig(minimum_retracement_atr=0.5, atr_period=500),
        tick_size=_TICK,
    )

    assert after.state is CerrState.EXPANSION


# ── invalidation ──────────────────────────────────────────────────────────


def test_closing_back_inside_the_range_invalidates_the_breakout() -> None:
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_050.0, high=20_051.0, low=19_995.0, close=20_000.0))

    after = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    assert after.state is CerrState.INVALIDATED
    assert after.invalidation_reason is CerrInvalidationReason.BREAKOUT_FAILED
    assert after.active is False


def test_running_past_the_expansion_origin_invalidates_the_cycle() -> None:
    """Past the origin there is no expansion left to resume."""
    bars, cycle = _expanded()
    origin = cycle.expansion.origin
    bars.append(
        _bar(22, open_=20_050.0, high=20_051.0, low=origin - 60.0, close=origin - 50.0)
    )

    after = observe_bar(
        cycle,
        bars,
        22,
        _config(invalidate_on_return_inside_range=False),
        tick_size=_TICK,
    )

    assert after.state is CerrState.INVALIDATED
    assert (
        after.invalidation_reason
        is CerrInvalidationReason.EXPANSION_ORIGIN_VIOLATED
    )


def test_invalidation_can_be_switched_off_for_research() -> None:
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_050.0, high=20_051.0, low=19_995.0, close=20_000.0))

    after = observe_bar(
        cycle,
        bars,
        22,
        _config(
            invalidate_on_return_inside_range=False,
            invalidate_on_origin_violation=False,
        ),
        tick_size=_TICK,
    )

    assert after.state is not CerrState.INVALIDATED


def test_a_phase_can_expire_when_a_limit_is_configured() -> None:
    bars, cycle = _expanded()
    config = _config(max_bars_in_phase=2)

    for minute in (22, 23, 24):
        bars.append(
            _bar(minute, open_=20_058.0, high=20_059.0, low=20_057.0, close=20_058.0)
        )
        cycle = observe_bar(cycle, bars, minute, config, tick_size=_TICK)

    assert cycle.state is CerrState.EXPIRED
    assert cycle.invalidation_reason is CerrInvalidationReason.PHASE_EXPIRED
    assert cycle.transitions[-1].trigger is CerrTrigger.EXPIRY


def test_phases_have_no_length_limit_by_default() -> None:
    """Event-driven, not candle-count-driven."""
    assert CerrConfig().max_bars_in_phase is None

    bars, cycle = _expanded()
    for minute in range(22, 60):
        bars.append(
            _bar(minute, open_=20_058.0, high=20_059.0, low=20_057.0, close=20_058.0)
        )
        cycle = observe_bar(cycle, bars, minute, _config(), tick_size=_TICK)

    assert cycle.state is CerrState.EXPANSION


# ── retracement -> reversal ───────────────────────────────────────────────


def _retraced():
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_040.0, close=20_042.0))
    cycle = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)
    assert cycle.state is CerrState.RETRACEMENT
    return bars, cycle


def test_the_bullish_reversal_sequence_confirms() -> None:
    cycle = _retraced()[1]
    reversal_leg = _bullish_breakout()[1]

    confirmed = observe_reversal(
        cycle,
        _sweep(38, LevelSide.SELL_SIDE),
        reversal_leg,
        _mss(MssDirection.BULLISH),
    )

    assert confirmed.state is CerrState.REVERSAL_CONFIRMED
    assert confirmed.confirmed is True
    assert confirmed.reversal_sweep_id == "SWEEP:test:38"
    assert confirmed.reversal_mss_id is not None
    assert confirmed.transitions[-1].trigger is CerrTrigger.REVERSAL_SEQUENCE_CONFIRMED


def test_a_reversal_must_resume_the_expansion_direction() -> None:
    """The strategy is not predicting a new trend; it is buying the end
    of a pullback inside one it already identified."""
    cycle = _retraced()[1]
    up_leg = _bullish_breakout()[1]

    assert reversal_matches(
        cycle, _sweep(38, LevelSide.SELL_SIDE), up_leg, _mss(MssDirection.BULLISH)
    )
    # A buy-side raid belongs to a bearish reversal, not this cycle.
    assert not reversal_matches(
        cycle, _sweep(38, LevelSide.BUY_SIDE), up_leg, _mss(MssDirection.BULLISH)
    )
    assert not reversal_matches(
        cycle, _sweep(38, LevelSide.SELL_SIDE), up_leg, _mss(MssDirection.BEARISH)
    )


def test_a_contradictory_reversal_invalidates_by_name() -> None:
    cycle = _retraced()[1]
    up_leg = _bullish_breakout()[1]

    after = observe_reversal(
        cycle,
        _sweep(38, LevelSide.BUY_SIDE),
        up_leg,
        _mss(MssDirection.BEARISH),
    )

    assert after.state is CerrState.INVALIDATED
    assert (
        after.invalidation_reason is CerrInvalidationReason.CONTRADICTORY_DIRECTION
    )
    assert after.confirmed is False


def test_a_reversal_before_a_retracement_is_ignored() -> None:
    """The sequence is the claim. Accepting it out of order would make
    the phase meaningless."""
    cycle = _expanded()[1]
    up_leg = _bullish_breakout()[1]

    after = observe_reversal(
        cycle, _sweep(38, LevelSide.SELL_SIDE), up_leg, _mss(MssDirection.BULLISH)
    )

    assert after == cycle
    assert after.state is CerrState.EXPANSION


# ── failed cycles, immutability, auditability ─────────────────────────────


def test_a_failed_cycle_keeps_its_whole_history() -> None:
    """Retaining only completed sequences would make the dataset
    survivorship-biased toward setups that worked."""
    bars, cycle = _expanded()
    bars.append(_bar(22, open_=20_050.0, high=20_051.0, low=19_995.0, close=20_000.0))
    failed = observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    states = [t.new_state for t in failed.transitions]
    assert states == [
        CerrState.CONSOLIDATION,
        CerrState.EXPANSION,
        CerrState.INVALIDATED,
    ]
    assert failed.consolidation_id == "CONS:test"
    assert failed.displacement_id is not None
    assert failed.invalidation_reason is not None


def test_the_full_confirmed_path_is_reconstructable() -> None:
    cycle = _retraced()[1]
    up_leg = _bullish_breakout()[1]
    confirmed = observe_reversal(
        cycle, _sweep(38, LevelSide.SELL_SIDE), up_leg, _mss(MssDirection.BULLISH)
    )

    assert [t.new_state for t in confirmed.transitions] == [
        CerrState.CONSOLIDATION,
        CerrState.EXPANSION,
        CerrState.RETRACEMENT,
        CerrState.REVERSAL_CONFIRMED,
    ]
    assert all(t.cycle_id == confirmed.cycle_id for t in confirmed.transitions)
    assert all(t.transition_time is not None for t in confirmed.transitions)
    assert confirmed.consolidation_id is not None
    assert confirmed.expansion_id is not None
    assert confirmed.retracement_id is not None
    assert confirmed.reversal_mss_id is not None


def test_observing_never_mutates_the_cycle_it_was_given() -> None:
    bars, cycle = _expanded()
    snapshot = dataclasses.replace(cycle)
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_040.0, close=20_042.0))

    observe_bar(cycle, bars, 22, _config(), tick_size=_TICK)

    assert cycle == snapshot
    assert cycle.state is CerrState.EXPANSION


def test_a_terminal_cycle_is_never_overwritten() -> None:
    """A completed episode is a record, not something to edit."""
    bars, cycle = _retraced()
    up_leg = _bullish_breakout()[1]
    confirmed = observe_reversal(
        cycle, _sweep(38, LevelSide.SELL_SIDE), up_leg, _mss(MssDirection.BULLISH)
    )

    assert confirmed.active is False
    assert invalidate(
        confirmed, CerrInvalidationReason.MANUAL, at=_at(50)
    ) == confirmed
    bars.append(_bar(50, open_=20_000.0, high=20_001.0, low=19_900.0, close=19_905.0))
    assert observe_bar(confirmed, bars, 50, _config(), tick_size=_TICK) == confirmed


def test_every_state_is_classified_terminal_or_not() -> None:
    assert CerrState.REVERSAL_CONFIRMED.terminal
    assert CerrState.INVALIDATED.terminal
    assert CerrState.EXPIRED.terminal
    assert not CerrState.IDLE.terminal
    assert not CerrState.CONSOLIDATION.terminal
    assert not CerrState.EXPANSION.terminal
    assert not CerrState.RETRACEMENT.terminal


def test_cerr_decides_no_trades() -> None:
    """CERR stops at REVERSAL_CONFIRMED. Setup, entry, risk and
    execution belong downstream and must not be reachable from here."""
    import ast
    import inspect

    import vo.valco.lrx_cerr as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned in (
        "vo.risk",
        "vo.risk.manager",
        "vo.compliance.engine",
        "vo.compliance.news_gate",
        "vo.execution.router",
        "vo.telemetry.live_dispatch",
    ):
        assert banned not in imported, banned

    assert {s.value for s in CerrState} == {
        "IDLE",
        "CONSOLIDATION",
        "EXPANSION",
        "RETRACEMENT",
        "REVERSAL_CONFIRMED",
        "INVALIDATED",
        "EXPIRED",
    }


def test_identical_inputs_produce_identical_cycles() -> None:
    runs = []
    for _ in range(5):
        cycle = _retraced()[1]
        up_leg = _bullish_breakout()[1]
        runs.append(
            observe_reversal(
                cycle,
                _sweep(38, LevelSide.SELL_SIDE),
                up_leg,
                _mss(MssDirection.BULLISH),
            )
        )

    assert all(run == runs[0] for run in runs)
