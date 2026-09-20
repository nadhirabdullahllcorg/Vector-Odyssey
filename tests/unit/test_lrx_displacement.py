"""vo.valco.lrx_displacement -- did the raid produce real opposing
expansion?

Covers the case list specified for this detector: expansion both ways,
weak reactions, single- and multi-bar legs, gaps present and absent,
stalls, reversals, multiple candidates from one raid, unusable ATR,
session boundaries, and no-lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_displacement import (
    ExpansionDirection,
    Qualification,
    measure_displacement,
    three_bar_gap,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_sweep import SweepEvent

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_LEVEL = 20_050.0


def _bar(minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_START + timedelta(minutes=minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _baseline(count: int, centre: float = 20_000.0, half_range: float = 5.0) -> list[Bar]:
    """Quiet bars establishing an ATR of roughly 2 * half_range."""
    return [
        _bar(i, open_=centre, high=centre + half_range, low=centre - half_range, close=centre)
        for i in range(count)
    ]


def _sweep(returned_index: int, side: LevelSide = LevelSide.BUY_SIDE) -> SweepEvent:
    kind = LevelKind.PREV_DAY_HIGH if side is LevelSide.BUY_SIDE else LevelKind.PREV_DAY_LOW
    return SweepEvent(
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
        returned_at_utc=_START + timedelta(minutes=returned_index),
        bars_beyond=1,
    )


def _measure(bars: list[Bar], index: int, sweep: SweepEvent, **overrides: object):
    params: dict[str, object] = dict(
        min_atr_multiple=1.5,
        min_body_ratio=0.5,
        min_net_move_atr=1.0,
        max_bars=5,
        atr_period=14,
        tick_size=_TICK,
    )
    params.update(overrides)
    return measure_displacement(bars, index, sweep, **params)  # type: ignore[arg-type]


# ── expansion in both directions ──────────────────────────────────────────


def test_bearish_expansion_after_a_buy_side_raid_qualifies() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(21, open_=20_022.0, high=20_023.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.direction is ExpansionDirection.DOWN
    assert event.qualification is Qualification.PASS
    assert event.net_move_atr > 1.0


def test_bullish_expansion_after_a_sell_side_raid_qualifies() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=19_952.0, high=19_980.0, low=19_950.0, close=19_978.0))
    bars.append(_bar(21, open_=19_978.0, high=20_012.0, low=19_977.0, close=20_010.0))

    event = _measure(bars, 21, _sweep(20, side=LevelSide.SELL_SIDE))

    assert event is not None
    assert event.direction is ExpansionDirection.UP
    assert event.qualification is Qualification.PASS


# ── failure carries its numbers ───────────────────────────────────────────


def test_a_weak_reaction_fails_but_is_still_measured() -> None:
    """The whole point: a FAIL with its numbers is a finding; a False is
    not."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_044.0, close=20_046.0))
    bars.append(_bar(21, open_=20_046.0, high=20_047.0, low=20_042.0, close=20_045.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert event.range_atr > 0.0
    assert event.net_move_atr > 0.0
    assert "below" in event.qualification_reason


def test_a_big_range_that_closes_mid_range_fails_on_body_ratio() -> None:
    """Travelled far, committed to nothing -- indecision wearing a big
    range."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_045.0, high=20_050.0, low=19_990.0, close=20_044.0))
    bars.append(_bar(21, open_=20_044.0, high=20_048.0, low=19_995.0, close=20_020.0))

    event = _measure(bars, 21, _sweep(20), min_atr_multiple=0.5, min_net_move_atr=0.1)

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert "body ratio" in event.qualification_reason


def test_a_move_that_retraces_most_of_itself_fails_on_net_move() -> None:
    """Range alone would pass; net distance covered is what matters."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=19_990.0, close=19_992.0))
    bars.append(_bar(21, open_=19_992.0, high=20_046.0, low=19_991.0, close=20_044.0))

    event = _measure(bars, 21, _sweep(20), min_body_ratio=0.1)

    assert event is not None
    assert event.qualification is Qualification.FAIL
    assert "net move" in event.qualification_reason


# ── leg shape ─────────────────────────────────────────────────────────────


def test_a_single_bar_displacement_is_measurable() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 20, _sweep(19))

    assert event is not None
    assert event.bar_count == 2


def test_a_multi_bar_expansion_counts_its_directional_run() -> None:
    bars = _baseline(20)
    for i, close in enumerate((20_030.0, 20_010.0, 19_990.0), start=20):
        bars.append(_bar(i, open_=close + 18.0, high=close + 19.0, low=close - 1.0, close=close))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.consecutive_directional_bars >= 2


def test_a_leg_longer_than_the_budget_is_not_measured() -> None:
    """An expansion that took twenty bars is not the impulsive move this
    model is looking for."""
    bars = _baseline(20)
    for i in range(20, 32):
        bars.append(_bar(i, open_=20_040.0, high=20_042.0, low=20_030.0, close=20_032.0))

    assert _measure(bars, 31, _sweep(20), max_bars=5) is None


def test_a_stall_produces_a_measured_failure_not_an_absence() -> None:
    bars = _baseline(20)
    for i in range(20, 24):
        bars.append(_bar(i, open_=20_045.0, high=20_047.0, low=20_043.0, close=20_045.0))

    event = _measure(bars, 23, _sweep(20))

    assert event is not None
    assert event.qualification is Qualification.FAIL


# ── gaps ──────────────────────────────────────────────────────────────────


def test_an_expansion_that_leaves_a_gap_records_it() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=20_000.0, close=20_002.0))
    # Third bar's high stays below the first bar's low -> bearish gap.
    bars.append(_bar(22, open_=20_002.0, high=20_020.0, low=19_980.0, close=19_985.0))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.fvg_created is True
    assert event.gap is not None
    assert event.gap.lower < event.gap.upper
    assert event.gap.lower < event.gap.consequent_encroachment < event.gap.upper


def test_an_overlapping_expansion_leaves_no_gap() -> None:
    bars = _baseline(20)
    for i, close in enumerate((20_040.0, 20_030.0, 20_020.0), start=20):
        bars.append(_bar(i, open_=close + 10.0, high=close + 14.0, low=close - 12.0, close=close))

    event = _measure(bars, 22, _sweep(20))

    assert event is not None
    assert event.fvg_created is False


def test_gap_geometry_is_direction_aware() -> None:
    bars = [
        _bar(0, open_=20_000.0, high=20_010.0, low=19_990.0, close=20_005.0),
        _bar(1, open_=20_005.0, high=20_040.0, low=20_004.0, close=20_035.0),
        _bar(2, open_=20_035.0, high=20_050.0, low=20_020.0, close=20_045.0),
    ]

    assert three_bar_gap(bars, 2, ExpansionDirection.UP) is not None
    assert three_bar_gap(bars, 2, ExpansionDirection.DOWN) is None


# ── boundaries and data quality ───────────────────────────────────────────


def test_an_unusable_atr_yields_no_measurement_rather_than_a_guess() -> None:
    """Too little history for ATR means the thresholds have no scale to
    be measured against."""
    bars = [
        _bar(0, open_=20_048.0, high=20_050.0, low=20_040.0, close=20_042.0),
        _bar(1, open_=20_042.0, high=20_043.0, low=20_000.0, close=20_002.0),
    ]

    assert _measure(bars, 1, _sweep(0)) is None


def test_a_flat_market_with_zero_atr_is_refused() -> None:
    flat = [
        _bar(i, open_=20_000.0, high=20_000.0, low=20_000.0, close=20_000.0)
        for i in range(20)
    ]
    flat.append(_bar(20, open_=20_000.0, high=20_000.0, low=20_000.0, close=20_000.0))

    assert _measure(flat, 20, _sweep(19)) is None


def test_an_index_at_or_before_the_raid_is_not_a_leg() -> None:
    bars = _baseline(22)

    assert _measure(bars, 20, _sweep(20)) is None
    assert _measure(bars, 19, _sweep(20)) is None


def test_a_leg_spanning_a_session_gap_still_measures_elapsed_time() -> None:
    """Bars either side of a closure are hours apart; velocity must
    reflect that rather than assuming one minute per bar."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    late = _bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0)
    bars.append(
        Bar(
            instrument_id=late.instrument_id,
            timeframe=late.timeframe,
            open_time_utc=late.open_time_utc + timedelta(hours=17),
            open=late.open,
            high=late.high,
            low=late.low,
            close=late.close,
            tick_volume=late.tick_volume,
            real_volume=late.real_volume,
            spread=late.spread,
        )
    )

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.elapsed_seconds > 3600
    assert event.velocity_atr_per_minute < 0.1


# ── multiple candidates and lookahead ─────────────────────────────────────


def test_one_raid_can_produce_several_candidate_legs() -> None:
    """Evaluating at successive indices is how a caller watches an
    expansion develop."""
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_040.0, close=20_042.0))
    bars.append(_bar(21, open_=20_042.0, high=20_043.0, low=20_020.0, close=20_022.0))
    bars.append(_bar(22, open_=20_022.0, high=20_023.0, low=19_985.0, close=19_988.0))

    sweep = _sweep(20)
    legs = [_measure(bars, i, sweep) for i in (21, 22)]

    assert all(leg is not None for leg in legs)
    assert legs[0].net_move_atr < legs[1].net_move_atr  # type: ignore[union-attr]


def test_measurement_never_reads_past_the_evaluated_bar() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    before = _measure(bars, 21, _sweep(20))
    bars.append(_bar(22, open_=19_992.0, high=21_500.0, low=19_000.0, close=21_400.0))

    assert _measure(bars, 21, _sweep(20)) == before


def test_the_origin_and_equilibrium_bracket_the_leg() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, open_=20_048.0, high=20_050.0, low=20_030.0, close=20_032.0))
    bars.append(_bar(21, open_=20_032.0, high=20_033.0, low=19_990.0, close=19_992.0))

    event = _measure(bars, 21, _sweep(20))

    assert event is not None
    assert event.low < event.expansion_equilibrium < event.expansion_origin
