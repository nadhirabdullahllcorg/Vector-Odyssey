"""vo.valco.lrx_mss -- did the displacement break structure?

Covers the case list for this detector: both directions, all five
confirmation methods, both swing-selection rules, the no-lookahead rule
that a swing confirmed mid-leg is not eligible, unusable ATR, chain
integrity, and the absence of any pivot detection of its own."""

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
    SwingSelection,
    detect_mss,
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


def _baseline(count: int, centre: float = 20_000.0, half_range: float = 5.0) -> list[Bar]:
    return [
        _bar(i, open_=centre, high=centre + half_range, low=centre - half_range, close=centre)
        for i in range(count)
    ]


def _sweep(returned_index: int, side: LevelSide = LevelSide.BUY_SIDE) -> SweepEvent:
    kind = LevelKind.PREV_DAY_HIGH if side is LevelSide.BUY_SIDE else LevelKind.PREV_DAY_LOW
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
        occurred_at=_at(pivot_minute if pivot_minute is not None else confirmed_minute - 2),
        available_at=_at(confirmed_minute),
        status=status,
    )


# ── a bearish setup: buy-side raid, down leg, break of a prior low ────────


def _bearish_displacement() -> tuple[list[Bar], DisplacementEvent, SweepEvent]:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))
    sweep = _sweep(20)
    event = measure_displacement(bars, 21, sweep, DisplacementConfig(), tick_size=_TICK)
    assert event is not None and event.direction is ExpansionDirection.DOWN
    return bars, event, sweep


def _detect(bars, event, sweep, swings, **overrides):
    config = MssConfig()
    if overrides:
        config = dataclasses.replace(config, **overrides)
    return detect_mss(bars, len(bars) - 1, event, sweep, swings, config, tick_size=_TICK)


def test_a_close_below_a_prior_confirmed_low_is_a_bearish_shift() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    mss = _detect(bars, event, sweep, swings)

    assert mss is not None
    assert mss.direction is MssDirection.BEARISH
    assert mss.swing_id == "low-a"
    assert mss.swing_price == 19_975.0
    assert mss.break_price == 19_965.0
    assert mss.break_distance == pytest.approx(10.0)
    assert mss.sweep_id == sweep.sweep_id
    assert mss.displacement_id == event.displacement_id
    assert mss.confirmation_method is ConfirmationMethod.CLOSE


def test_a_bullish_shift_mirrors_the_bearish_one() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_952.0, high=19_980.0, low=19_950.0, close=19_978.0))
    bars.append(_bar(21, open_=19_978.0, high=20_012.0, low=19_977.0, close=20_010.0))
    sweep = _sweep(20, side=LevelSide.SELL_SIDE)
    event = measure_displacement(bars, 21, sweep, DisplacementConfig(), tick_size=_TICK)
    assert event is not None and event.direction is ExpansionDirection.UP
    bars.append(_bar(22, open_=20_010.0, high=20_040.0, low=20_009.0, close=20_038.0))
    swings = [_swing("high-a", SwingType.HIGH, 20_025.0, confirmed_minute=18)]

    mss = detect_mss(bars, 22, event, sweep, swings, MssConfig(), tick_size=_TICK)

    assert mss is not None
    assert mss.direction is MssDirection.BULLISH
    assert mss.swing_id == "high-a"


def test_price_holding_above_the_swing_is_not_a_shift() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_996.0, low=19_980.0, close=19_985.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    assert _detect(bars, event, sweep, swings) is None


def test_no_eligible_swing_produces_nothing() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))

    assert _detect(bars, event, sweep, []) is None
    # A swing of the WRONG TYPE is not a fallback -- a bearish shift
    # breaks a low, never a high.
    highs = [_swing("high-a", SwingType.HIGH, 19_975.0, confirmed_minute=18)]
    assert _detect(bars, event, sweep, highs) is None


# ── no lookahead ──────────────────────────────────────────────────────────


def test_a_swing_confirmed_after_the_leg_began_is_not_eligible() -> None:
    """The load-bearing rule. A swing that became CONFIRMED while the
    displacement was already running was not knowable when the setup
    started, so breaking it is not the shift the model describes."""
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    late = [_swing("low-late", SwingType.LOW, 19_975.0, confirmed_minute=21)]

    assert event.event_at_utc == _at(20)
    assert _detect(bars, event, sweep, late) is None

    early = [_swing("low-early", SwingType.LOW, 19_975.0, confirmed_minute=20)]
    assert _detect(bars, event, sweep, early) is not None


def test_a_broken_swing_is_not_eligible() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [
        _swing(
            "low-gone",
            SwingType.LOW,
            19_975.0,
            confirmed_minute=18,
            status=SwingStatus.BROKEN,
        )
    ]

    assert _detect(bars, event, sweep, swings) is None


# ── the five confirmation methods ─────────────────────────────────────────


def test_wick_confirms_where_close_does_not() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_970.0, close=19_985.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    assert _detect(bars, event, sweep, swings, method=ConfirmationMethod.CLOSE) is None

    wick = _detect(bars, event, sweep, swings, method=ConfirmationMethod.WICK)
    assert wick is not None
    assert wick.break_price == 19_970.0


def test_body_close_rejects_a_bar_that_opened_above_the_swing() -> None:
    """CLOSE accepts it; BODY_CLOSE wants the whole body through. The two
    disagreeing on the same bar is the point of testing them separately."""
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    assert _detect(bars, event, sweep, swings, method=ConfirmationMethod.CLOSE) is not None
    assert _detect(bars, event, sweep, swings, method=ConfirmationMethod.BODY_CLOSE) is None


def test_body_close_accepts_a_bar_wholly_beyond_the_swing() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_970.0, high=19_972.0, low=19_940.0, close=19_945.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    assert _detect(bars, event, sweep, swings, method=ConfirmationMethod.BODY_CLOSE) is not None


def test_close_plus_points_needs_the_extra_distance() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_968.0, close=19_970.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    method = ConfirmationMethod.CLOSE_PLUS_POINTS
    assert _detect(bars, event, sweep, swings, method=method, min_break_points=3.0) is not None
    assert _detect(bars, event, sweep, swings, method=method, min_break_points=8.0) is None


def test_close_plus_atr_scales_with_volatility() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    method = ConfirmationMethod.CLOSE_PLUS_ATR
    assert _detect(bars, event, sweep, swings, method=method, min_break_atr=0.1) is not None
    assert _detect(bars, event, sweep, swings, method=method, min_break_atr=5.0) is None


def test_an_atr_threshold_with_no_atr_available_declines_rather_than_guesses() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    # A window longer than the history available. The break itself is
    # unambiguous -- only the threshold's scale is missing, and an
    # unscalable threshold is declined rather than treated as zero.
    config = MssConfig(
        method=ConfirmationMethod.CLOSE_PLUS_ATR, min_break_atr=0.5, atr_period=500
    )
    assert detect_mss(bars, 22, event, sweep, swings, config, tick_size=_TICK) is None
    assert _detect(bars, event, sweep, swings) is not None


# ── swing selection ───────────────────────────────────────────────────────


def _two_candidate_swings() -> list[CanonicalSwing]:
    """An older low sitting CLOSER to price than the newer one, so the
    two selection rules must disagree."""
    return [
        _swing("low-near", SwingType.LOW, 19_985.0, confirmed_minute=10),
        _swing("low-recent", SwingType.LOW, 19_960.0, confirmed_minute=18),
    ]


def test_the_two_selection_rules_pick_different_swings() -> None:
    _, event, _unused = _bearish_displacement()
    swings = _two_candidate_swings()

    recent, alt_recent = select_swing(
        swings, displacement=event, selection=SwingSelection.MOST_RECENT
    )
    nearest, alt_nearest = select_swing(
        swings, displacement=event, selection=SwingSelection.NEAREST_PRICE
    )

    assert recent is not None and recent.swing_id == "low-recent"
    assert nearest is not None and nearest.swing_id == "low-near"
    assert alt_recent is not None and alt_recent.swing_id == "low-near"
    assert alt_nearest is not None and alt_nearest.swing_id == "low-recent"


def test_the_rejected_candidate_is_recorded_on_the_event() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_940.0, close=19_945.0))

    mss = _detect(bars, event, sweep, _two_candidate_swings())

    assert mss is not None
    assert mss.swing_id == "low-recent"
    assert mss.alternate_swing_id == "low-near"
    assert mss.swing_selection is SwingSelection.MOST_RECENT


def test_no_alternate_is_recorded_when_both_rules_agree() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    mss = _detect(bars, event, sweep, swings)

    assert mss is not None
    assert mss.alternate_swing_id is None


def test_nearest_price_ignores_swings_on_the_wrong_side_of_the_origin() -> None:
    """A low ABOVE the leg's origin was never in front of price."""
    _, event, _unused = _bearish_displacement()
    above = [_swing("low-above", SwingType.LOW, event.expansion_origin + 50.0,
                    confirmed_minute=18)]

    chosen, _ = select_swing(
        above, displacement=event, selection=SwingSelection.NEAREST_PRICE
    )
    assert chosen is None


# ── chain integrity and bounds ────────────────────────────────────────────


def test_a_sweep_from_a_different_raid_is_refused() -> None:
    bars, event, _unused = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    with pytest.raises(ValueError, match="not the raid behind"):
        detect_mss(bars, 22, event, _sweep(99), swings, MssConfig(), tick_size=_TICK)


def test_a_break_cannot_predate_the_displacement() -> None:
    bars, event, sweep = _bearish_displacement()
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=10)]

    assert detect_mss(
        bars, event.start_index, event, sweep, swings, MssConfig(), tick_size=_TICK
    ) is None


def test_the_event_is_knowable_on_the_bar_that_produced_it() -> None:
    bars, event, sweep = _bearish_displacement()
    bars.append(_bar(22, open_=19_992.0, high=19_994.0, low=19_960.0, close=19_965.0))
    swings = [_swing("low-a", SwingType.LOW, 19_975.0, confirmed_minute=18)]

    mss = _detect(bars, event, sweep, swings)

    assert mss is not None
    assert mss.confirmation_at_utc == mss.event_at_utc == _at(22)


def test_mss_imports_no_swing_producer() -> None:
    """Spec section 13: MSS consumes Swing Adapter data. Two systems
    computing structure would disagree invisibly, so the detector may
    not reach the engine that produces swings -- only the adapter that
    normalises them.

    An AST scan rather than a text search: the module docstring names
    SwingEngine on purpose, to say what it defers to."""
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

    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    assert not any(
        "pivot" in name or "swing" in name
        for name in defined
        if name != "select_swing"
    ), f"MSS defines its own structure logic: {sorted(defined)}"
