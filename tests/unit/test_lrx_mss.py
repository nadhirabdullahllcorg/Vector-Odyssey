"""vo.valco.lrx_mss -- did the raid turn structure?

Audit case list: both directions, directional consistency between raid
and leg, sweep/displacement pairing, swing availability at the cutoff,
both selection rules, all five confirmation modes, the boundary cases
where a break is exactly at a level or a threshold, gaps, multiple and
absent candidates, rejection attribution, and determinism."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swings import SwingLevel, SwingStatus, SwingType
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    DisplacementEvent,
    ExpansionDirection,
    measure_displacement,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_mss import (
    ConfirmationMethod,
    MssConfig,
    MssDirection,
    MssQualification,
    MssRejectionReason,
    SwingSelection,
    detect_mss,
    directions_agree,
    evaluate_mss,
    expected_direction,
    select_swing,
)
from vo.valco.lrx_sweep import SweepEvent
from vo.valco.lrx_swings import CanonicalSwing

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_LEVEL = 20_050.0
_SWING_PRICE = 19_975.0


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


def _sweep(returned_index: int, side: LevelSide = LevelSide.BUY_SIDE) -> SweepEvent:
    kind = (
        LevelKind.PREV_DAY_HIGH
        if side is LevelSide.BUY_SIDE
        else LevelKind.PREV_DAY_LOW
    )
    return SweepEvent(
        sweep_id=f"SWEEP:test:{returned_index}",
        level=ReferenceLevel(
            kind=kind, price=_LEVEL, established_at=None, trading_day=_START.date()
        ),
        side=side,
        penetration_index=returned_index - 1,
        penetration_price=_LEVEL + 8.0 if side is LevelSide.BUY_SIDE else _LEVEL - 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_at(returned_index),
        bars_beyond=1,
    )


def _swing(
    name: str,
    swing_type: SwingType,
    price: float,
    *,
    confirmed_minute: int,
    pivot_minute: int | None = None,
    status: SwingStatus = SwingStatus.CONFIRMED,
) -> CanonicalSwing:
    return CanonicalSwing(
        swing_id=name,
        swing_type=swing_type,
        level=SwingLevel.SWING,
        price=price,
        body_price=price,
        occurred_at=_at(
            pivot_minute if pivot_minute is not None else max(confirmed_minute - 2, 0)
        ),
        available_at=_at(confirmed_minute),
        status=status,
    )


def _bearish() -> tuple[list[Bar], DisplacementEvent, SweepEvent]:
    """Buy-side raid, bearish expansion. The canonical bearish setup."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))
    sweep = _sweep(20)
    event = measure_displacement(bars, 21, sweep, DisplacementConfig(), tick_size=_TICK)
    assert event is not None and event.direction is ExpansionDirection.DOWN
    return bars, event, sweep


def _bullish() -> tuple[list[Bar], DisplacementEvent, SweepEvent]:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_952.0, high=19_980.0, low=19_950.0, close=19_978.0))
    bars.append(_bar(21, open_=19_978.0, high=20_012.0, low=19_977.0, close=20_010.0))
    sweep = _sweep(20, side=LevelSide.SELL_SIDE)
    event = measure_displacement(bars, 21, sweep, DisplacementConfig(), tick_size=_TICK)
    assert event is not None and event.direction is ExpansionDirection.UP
    return bars, event, sweep


def _detect(bars, event, sweep, swings, index=None, **overrides):
    config = MssConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return detect_mss(
        bars,
        len(bars) - 1 if index is None else index,
        event,
        sweep,
        swings,
        config,
        tick_size=_TICK,
    )


def _verdict(bars, event, sweep, swings, index=None, **overrides):
    config = MssConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return evaluate_mss(
        bars,
        len(bars) - 1 if index is None else index,
        event,
        sweep,
        swings,
        config,
        tick_size=_TICK,
    )


def _low(price: float = _SWING_PRICE, minute: int = 18) -> list[CanonicalSwing]:
    return [_swing("low-a", SwingType.LOW, price, confirmed_minute=minute)]


# ── direction ─────────────────────────────────────────────────────────────


def test_a_buy_side_raid_can_only_produce_a_bearish_shift() -> None:
    assert expected_direction(_sweep(20)) is MssDirection.BEARISH
    assert expected_direction(_sweep(20, LevelSide.SELL_SIDE)) is MssDirection.BULLISH


def test_bearish_shift_breaks_a_low_after_a_buy_side_raid() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low())

    assert mss is not None
    assert mss.direction is MssDirection.BEARISH
    assert mss.qualification is MssQualification.PASS
    assert mss.broken_swing_id == "low-a"
    assert mss.broken_price == _SWING_PRICE
    assert mss.break_price == 19_965.0
    assert mss.break_distance_points == pytest.approx(10.0)
    assert mss.sweep_id == sweep.sweep_id
    assert mss.displacement_id == event.displacement_id
    assert mss.confirmation_method is ConfirmationMethod.CANDLE_CLOSE


def test_bullish_shift_breaks_a_high_after_a_sell_side_raid() -> None:
    bars, event, sweep = _bullish()
    bars.append(_bar(22, open_=20_010.0, high=20_040.0, low=20_009.0, close=20_038.0))
    swings = [_swing("high-a", SwingType.HIGH, 20_025.0, confirmed_minute=18)]

    mss = _detect(bars, event, sweep, swings)

    assert mss is not None
    assert mss.direction is MssDirection.BULLISH
    assert mss.broken_swing_id == "high-a"


# ── directional consistency: the raid and the leg must disagree ──────────


def test_a_raid_and_a_leg_pointing_the_same_way_are_not_a_shift() -> None:
    """A sell-side raid followed by a BEARISH leg is price continuing
    down through liquidity it just took. Reading direction off the
    displacement alone would manufacture a bullish shift out of it."""
    bars, event, _ = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    contradicting = _sweep(20, side=LevelSide.SELL_SIDE)

    assert not directions_agree(contradicting, event)

    verdict = _verdict(bars, event, contradicting, _low())
    assert verdict.qualification is MssQualification.REJECTED
    assert verdict.rejection_reason is MssRejectionReason.CONTRADICTORY_DIRECTION
    assert verdict.direction is None
    assert verdict.event is None


def test_a_buy_side_raid_with_a_bullish_leg_is_rejected() -> None:
    bars, event, _ = _bullish()
    bars.append(_bar(22, open_=20_010.0, high=20_040.0, low=20_009.0, close=20_038.0))
    contradicting = _sweep(20, side=LevelSide.BUY_SIDE)

    verdict = _verdict(bars, event, contradicting, _low())
    assert verdict.rejection_reason is MssRejectionReason.CONTRADICTORY_DIRECTION


def test_agreement_holds_for_both_valid_pairings() -> None:
    _, bearish_leg, buy_side = _bearish()
    _, bullish_leg, sell_side = _bullish()

    assert directions_agree(buy_side, bearish_leg)
    assert directions_agree(sell_side, bullish_leg)


# ── causal chain ──────────────────────────────────────────────────────────


def test_a_sweep_from_a_different_raid_is_refused() -> None:
    bars, event, _ = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    with pytest.raises(ValueError, match="not the raid behind"):
        evaluate_mss(
            bars, 22, event, _sweep(99), _low(), MssConfig(), tick_size=_TICK
        )


def test_the_matching_sweep_is_accepted_and_carried_through() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low())

    assert mss is not None
    assert mss.sweep_id == event.sweep_id == sweep.sweep_id
    assert mss.distance_from_sweep == pytest.approx(abs(_SWING_PRICE - _LEVEL))


# ── lookahead ─────────────────────────────────────────────────────────────


def test_a_swing_confirmed_before_the_leg_began_may_be_used() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    assert event.start_at_utc == _at(20)
    mss = _detect(bars, event, sweep, _low(minute=20))

    assert mss is not None
    assert mss.swing_confirmation_time <= event.start_at_utc


def test_a_swing_confirmed_after_the_leg_began_may_not_be_used() -> None:
    """The load-bearing rule. A backtest that let this through would be
    reading structure the strategy could not have seen."""
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    verdict = _verdict(bars, event, sweep, _low(minute=21))
    assert verdict.rejection_reason is MssRejectionReason.NO_ELIGIBLE_SWING


def test_the_cutoff_is_the_leg_start_not_the_evaluated_bar() -> None:
    """A swing confirmed at minute 21 is knowable by the time bar 22 is
    evaluated -- and is still refused, because the setup began at 20."""
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mid_leg = _low(minute=21)
    assert mid_leg[0].available_at < bars[22].open_time_utc
    assert _detect(bars, event, sweep, mid_leg) is None


def test_an_already_broken_swing_is_not_eligible() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    broken = [
        _swing(
            "low-gone",
            SwingType.LOW,
            _SWING_PRICE,
            confirmed_minute=18,
            status=SwingStatus.BROKEN,
        )
    ]

    verdict = _verdict(bars, event, sweep, broken)
    assert verdict.rejection_reason is MssRejectionReason.NO_ELIGIBLE_SWING


# ── swing selection ───────────────────────────────────────────────────────


def _two_candidates() -> list[CanonicalSwing]:
    """An older low CLOSER to price than the newer one, so the two rules
    must disagree."""
    return [
        _swing("low-near", SwingType.LOW, 19_985.0, confirmed_minute=10),
        _swing("low-recent", SwingType.LOW, 19_960.0, confirmed_minute=18),
    ]


def test_the_two_selection_rules_pick_different_swings() -> None:
    _, event, sweep = _bearish()
    swings = _two_candidates()

    recent, alt_recent = select_swing(
        swings, sweep=sweep, displacement=event, selection=SwingSelection.MOST_RECENT
    )
    nearest, alt_nearest = select_swing(
        swings, sweep=sweep, displacement=event, selection=SwingSelection.NEAREST_PRICE
    )

    assert recent is not None and recent.swing_id == "low-recent"
    assert nearest is not None and nearest.swing_id == "low-near"
    assert alt_recent is not None and alt_recent.swing_id == "low-near"
    assert alt_nearest is not None and alt_nearest.swing_id == "low-recent"


def test_both_selection_modes_record_what_they_rejected() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_940.0, close=19_945.0))

    recent = _detect(bars, event, sweep, _two_candidates())
    nearest = _detect(
        bars, event, sweep, _two_candidates(), selection=SwingSelection.NEAREST_PRICE
    )

    assert recent is not None and nearest is not None
    assert recent.broken_swing_id == "low-recent"
    assert recent.alternate_swing_id == "low-near"
    assert recent.selection_method is SwingSelection.MOST_RECENT
    assert nearest.broken_swing_id == "low-near"
    assert nearest.alternate_swing_id == "low-recent"
    assert nearest.selection_method is SwingSelection.NEAREST_PRICE


def test_no_alternate_is_recorded_when_both_rules_agree() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low())

    assert mss is not None
    assert mss.alternate_swing_id is None


def test_selection_follows_the_sweep_not_the_leg() -> None:
    """Wrong-type swings are not a fallback: a bearish shift breaks a
    low, never a high, and the type comes from the raid's side."""
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    highs = [_swing("high-a", SwingType.HIGH, _SWING_PRICE, confirmed_minute=18)]

    assert _verdict(
        bars, event, sweep, highs
    ).rejection_reason is MssRejectionReason.NO_ELIGIBLE_SWING


def test_nearest_price_ignores_swings_behind_the_origin() -> None:
    _, event, sweep = _bearish()
    above = [
        _swing(
            "low-above", SwingType.LOW, event.expansion_origin + 50.0, confirmed_minute=18
        )
    ]

    chosen, _alt = select_swing(
        above, sweep=sweep, displacement=event, selection=SwingSelection.NEAREST_PRICE
    )
    assert chosen is None


def test_no_opposing_swing_at_all() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    verdict = _verdict(bars, event, sweep, [])
    assert verdict.rejection_reason is MssRejectionReason.NO_ELIGIBLE_SWING
    assert verdict.candidate_swing_id is None


# ── the five confirmation modes ───────────────────────────────────────────


def test_wick_break_confirms_where_candle_close_does_not() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_970.0, close=19_985.0))

    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.CANDLE_CLOSE) is None

    wick = _detect(bars, event, sweep, _low(), method=ConfirmationMethod.WICK_BREAK)
    assert wick is not None
    assert wick.break_price == 19_970.0


def test_candle_close_uses_the_close_as_the_break_price() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_950.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low(), method=ConfirmationMethod.CANDLE_CLOSE)
    assert mss is not None
    assert mss.break_price == 19_965.0


def test_body_close_rejects_a_bar_that_opened_above_the_swing() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.CANDLE_CLOSE) is not None
    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.BODY_CLOSE) is None


def test_body_close_accepts_a_bar_wholly_beyond_the_swing() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_970.0, high=19_972.0, low=19_940.0, close=19_945.0))

    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.BODY_CLOSE) is not None


def test_close_plus_min_distance_needs_the_extra_points() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_968.0, close=19_970.0))
    method = ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE

    assert _detect(bars, event, sweep, _low(), method=method, min_break_points=3.0) is not None
    assert _detect(bars, event, sweep, _low(), method=method, min_break_points=8.0) is None


def test_close_plus_atr_threshold_scales_with_volatility() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    method = ConfirmationMethod.CLOSE_PLUS_ATR_THRESHOLD

    assert _detect(bars, event, sweep, _low(), method=method, min_break_atr=0.1) is not None
    assert _detect(bars, event, sweep, _low(), method=method, min_break_atr=5.0) is None


def test_an_atr_threshold_with_no_atr_declines_rather_than_guessing() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    verdict = _verdict(
        bars,
        event,
        sweep,
        _low(),
        method=ConfirmationMethod.CLOSE_PLUS_ATR_THRESHOLD,
        min_break_atr=0.5,
        atr_period=500,
    )
    assert verdict.rejection_reason is MssRejectionReason.ATR_UNAVAILABLE
    assert verdict.candidate_swing_id == "low-a"
    assert _detect(bars, event, sweep, _low()) is not None


def test_every_confirmation_mode_is_reachable_and_recorded() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_960.0, high=19_962.0, low=19_900.0, close=19_905.0))

    for method in ConfirmationMethod:
        mss = _detect(
            bars,
            event,
            sweep,
            _low(),
            method=method,
            min_break_points=1.0,
            min_break_atr=0.1,
        )
        assert mss is not None, method
        assert mss.confirmation_method is method


# ── boundaries: touch, one tick, exact threshold, gap ─────────────────────


def test_an_exact_touch_of_the_swing_is_not_a_break() -> None:
    """Exact equality is common at prior extremes and round numbers.
    Letting it confirm would add trades precisely where the level is
    most contested."""
    bars, event, sweep = _bearish()
    bars.append(
        _bar(22, open_=19_992.0, high=19_994.0, low=_SWING_PRICE, close=19_990.0)
    )

    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.WICK_BREAK) is None


def test_one_tick_beyond_the_swing_is_a_break() -> None:
    bars, event, sweep = _bearish()
    bars.append(
        _bar(22, open_=19_992.0, high=19_994.0, low=_SWING_PRICE - _TICK, close=19_990.0)
    )

    mss = _detect(bars, event, sweep, _low(), method=ConfirmationMethod.WICK_BREAK)
    assert mss is not None
    assert mss.break_distance_points == pytest.approx(_TICK)


def test_a_close_exactly_at_the_swing_is_not_a_break() -> None:
    bars, event, sweep = _bearish()
    bars.append(
        _bar(22, open_=19_992.0, high=19_994.0, low=19_970.0, close=_SWING_PRICE)
    )

    assert _detect(bars, event, sweep, _low(), method=ConfirmationMethod.CANDLE_CLOSE) is None


def test_a_close_exactly_at_the_threshold_is_not_a_break() -> None:
    bars, event, sweep = _bearish()
    bars.append(
        _bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=_SWING_PRICE - 5.0)
    )
    method = ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE

    assert _detect(bars, event, sweep, _low(), method=method, min_break_points=5.0) is None


def test_one_tick_past_the_threshold_is_a_break() -> None:
    bars, event, sweep = _bearish()
    bars.append(
        _bar(
            22,
            open_=19_992.0,
            high=19_994.0,
            low=19_960.0,
            close=_SWING_PRICE - 5.0 - _TICK,
        )
    )
    method = ConfirmationMethod.CLOSE_PLUS_MIN_DISTANCE

    assert _detect(bars, event, sweep, _low(), method=method, min_break_points=5.0) is not None


def test_a_bar_gapping_wholly_through_the_swing_is_a_break() -> None:
    """The whole bar prints below the level -- no wick touches it. Every
    close-based mode must still see this, or a gap-through would be the
    one way structure breaks unnoticed."""
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_940.0, high=19_945.0, low=19_920.0, close=19_925.0))

    for method in (
        ConfirmationMethod.WICK_BREAK,
        ConfirmationMethod.CANDLE_CLOSE,
        ConfirmationMethod.BODY_CLOSE,
    ):
        assert _detect(bars, event, sweep, _low(), method=method) is not None, method


# ── bounds and non-events ─────────────────────────────────────────────────


def test_price_holding_above_the_swing_is_not_a_shift() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_996.0, low=19_980.0, close=19_985.0))

    verdict = _verdict(bars, event, sweep, _low())
    assert verdict.rejection_reason is MssRejectionReason.NOT_BROKEN
    assert verdict.candidate_swing_id == "low-a"
    assert verdict.event is None


def test_a_break_cannot_predate_the_displacement() -> None:
    bars, event, sweep = _bearish()

    verdict = _verdict(bars, event, sweep, _low(minute=10), index=event.start_index)
    assert verdict.rejection_reason is MssRejectionReason.BEFORE_DISPLACEMENT


def test_an_index_past_the_series_is_refused() -> None:
    bars, event, sweep = _bearish()

    verdict = _verdict(bars, event, sweep, _low(), index=len(bars) + 5)
    assert verdict.rejection_reason is MssRejectionReason.BEFORE_DISPLACEMENT


def test_the_event_is_knowable_on_the_bar_that_produced_it() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low())

    assert mss is not None
    assert mss.confirmation_at_utc == mss.event_at_utc == mss.break_time == _at(22)


# ── determinism ───────────────────────────────────────────────────────────


def test_identical_inputs_produce_identical_output() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_940.0, close=19_945.0))

    runs = [_detect(bars, event, sweep, _two_candidates()) for _ in range(5)]

    assert all(run is not None for run in runs)
    assert all(run == runs[0] for run in runs)
    assert len({run.mss_id for run in runs if run is not None}) == 1


def test_rejections_are_deterministic_too() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_996.0, low=19_980.0, close=19_985.0))

    runs = [_verdict(bars, event, sweep, _low()) for _ in range(5)]
    assert all(run == runs[0] for run in runs)


# ── scope: MSS stays a structural detector ────────────────────────────────


def test_mss_imports_no_swing_producer_and_no_confluence() -> None:
    """Section 10 of the audit. MSS consumes Swing Adapter data and
    nothing else; confluence belongs where it can be switched off and
    measured. An AST scan rather than a text search -- the docstring
    names SwingEngine on purpose, to say what it defers to."""
    import ast
    import inspect

    import vo.valco.lrx_mss as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "vo.observation.swing_engine" not in imported
    assert "vo.valco.lrx_swings" in imported
    for banned in (
        "vo.observation.hurst",
        "vo.observation.markov",
        "vo.observation.regime",
        "vo.observation.entry_timing",
        "vo.compliance.news_gate",
        "vo.research.markov_validation",
    ):
        assert banned not in imported, banned

    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert not any(
        "pivot" in name or ("swing" in name and name != "select_swing")
        for name in defined
    ), f"MSS defines its own structure logic: {sorted(defined)}"


# ── data integrity regression ─────────────────────────────────────────────


def test_an_unmeasurable_atr_distance_is_reported_as_none_not_zero() -> None:
    """Regression. A reported 0.0 is indistinguishable from a break that
    genuinely covered no ATR, and drags any average computed over the
    column toward zero while still looking like data. The break itself is
    unaffected -- only its ATR normalisation is unavailable."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))
    sweep = _sweep(20)
    event = measure_displacement(
        bars, 21, sweep, DisplacementConfig(atr_period=14), tick_size=_TICK
    )
    assert event is not None
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low(), atr_period=500)

    assert mss is not None
    assert mss.break_distance_atr is None
    assert mss.break_distance_points == pytest.approx(10.0)


def test_a_measurable_atr_distance_is_a_real_number() -> None:
    bars, event, sweep = _bearish()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    mss = _detect(bars, event, sweep, _low())

    assert mss is not None
    assert mss.break_distance_atr is not None
    assert mss.break_distance_atr > 0.0
