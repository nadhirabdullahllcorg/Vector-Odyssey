"""vo.valco.lrx_sweep -- the liquidity raid, step one of the sequence.

The false-positive tests matter most. A detector that fires on every
breakout would hand the strategy a stream of setups in exactly the
conditions where the reversal model is wrong."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_sweep import detect_sweep, detect_sweeps

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01
_DAY = _START.date()

# A level at 20_050 with ~10-point bars gives an ATR around 10, so a
# penetration of 5 points is ~0.5 ATR -- comfortably over a 0.10 floor.
_LEVEL_PRICE = 20_050.0


def _bar(minute: int, *, high: float, low: float, close: float, open_: float | None = None) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_START + timedelta(minutes=minute),
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _baseline(count: int, centre: float = 20_000.0) -> list[Bar]:
    """Quiet bars that establish an ATR without going near the level."""
    return [
        _bar(i, high=centre + 5.0, low=centre - 5.0, close=centre)
        for i in range(count)
    ]


def _level(
    kind: LevelKind = LevelKind.PREV_DAY_HIGH, price: float = _LEVEL_PRICE
) -> ReferenceLevel:
    return ReferenceLevel(
        kind=kind, price=price, established_at=None, trading_day=_DAY
    )


def _detect(bars: list[Bar], index: int, level: ReferenceLevel, **overrides: object):
    params: dict[str, object] = dict(
        min_penetration_atr=0.10,
        return_max_bars=12,
        atr_period=14,
        tick_size=_TICK,
    )
    params.update(overrides)
    return detect_sweep(bars, index, level, **params)  # type: ignore[arg-type]


# ── the thing a naive detector gets wrong ─────────────────────────────────


def test_trading_through_a_level_and_continuing_is_not_a_raid() -> None:
    """The single most important negative case. In an uptrend price
    exceeds prior highs constantly; none of those are raids."""
    bars = _baseline(20)
    # Break above and keep going, closing beyond every bar.
    for i in range(20, 26):
        price = _LEVEL_PRICE + (i - 19) * 5.0
        bars.append(_bar(i, high=price + 3.0, low=price - 3.0, close=price))

    assert _detect(bars, len(bars) - 1, _level()) is None


def test_a_spike_above_that_closes_back_inside_is_a_raid() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 2.0, low=20_030.0, close=20_035.0))

    event = _detect(bars, len(bars) - 1, _level())

    assert event is not None
    assert event.side is LevelSide.BUY_SIDE
    assert event.penetration_price == _LEVEL_PRICE + 8.0
    assert event.returned_index == len(bars) - 1


def test_a_raid_of_a_low_is_detected_on_the_other_side() -> None:
    low_level = _level(LevelKind.PREV_DAY_LOW, price=19_950.0)
    bars = _baseline(20)
    bars.append(_bar(20, high=19_990.0, low=19_942.0, close=19_946.0))
    bars.append(_bar(21, high=19_975.0, low=19_952.0, close=19_970.0))

    event = _detect(bars, len(bars) - 1, low_level)

    assert event is not None
    assert event.side is LevelSide.SELL_SIDE
    assert event.expected_reversal_side is LevelSide.BUY_SIDE


def test_a_buy_side_raid_points_at_sell_side_liquidity() -> None:
    """The counter-expansion direction the whole model trades: price
    spiked above a high and failed, so the objective is below."""
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    event = _detect(bars, len(bars) - 1, _level())

    assert event is not None
    assert event.expected_reversal_side is LevelSide.SELL_SIDE


# ── the thresholds ────────────────────────────────────────────────────────


def test_a_penetration_too_shallow_for_the_atr_threshold_is_rejected() -> None:
    """Brushing the level is not taking liquidity."""
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 0.3, low=20_040.0, close=20_045.0))
    bars.append(_bar(21, high=20_048.0, low=20_030.0, close=20_035.0))

    assert _detect(bars, len(bars) - 1, _level(), min_penetration_atr=0.5) is None


def test_the_threshold_is_atr_relative_not_a_fixed_distance() -> None:
    """The same 8-point penetration passes in a quiet tape and fails in a
    volatile one -- which is the point of scaling by ATR."""
    quiet = _baseline(20, centre=20_000.0)
    quiet.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    quiet.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    volatile = [
        _bar(i, high=20_000.0 + 60.0, low=20_000.0 - 60.0, close=20_000.0)
        for i in range(20)
    ]
    volatile.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    volatile.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    assert _detect(quiet, len(quiet) - 1, _level(), min_penetration_atr=0.5) is not None
    assert _detect(volatile, len(volatile) - 1, _level(), min_penetration_atr=0.5) is None


def test_requiring_a_close_beyond_excludes_a_pure_wick_raid() -> None:
    bars = _baseline(20)
    # Wicks above but never closes above.
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE - 2.0))
    bars.append(_bar(21, high=20_048.0, low=20_030.0, close=20_035.0))

    assert _detect(bars, len(bars) - 1, _level()) is not None
    assert _detect(bars, len(bars) - 1, _level(), require_close_beyond=True) is None


def test_a_return_that_takes_too_long_is_not_a_raid() -> None:
    """Price sitting above the level for an hour before coming back is a
    failed breakout, not a raid."""
    bars = _baseline(20)
    for i in range(20, 32):
        bars.append(
            _bar(
                i,
                high=_LEVEL_PRICE + 8.0,
                low=_LEVEL_PRICE + 1.0,
                close=_LEVEL_PRICE + 4.0,
            )
        )
    bars.append(_bar(32, high=_LEVEL_PRICE + 2.0, low=20_030.0, close=20_035.0))

    assert _detect(bars, len(bars) - 1, _level(), return_max_bars=3) is None


# ── honesty about time ────────────────────────────────────────────────────


def test_a_level_cannot_be_raided_before_it_existed() -> None:
    """A developing session high only becomes a level when price makes
    it; bars before that did not raid anything."""
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    later = ReferenceLevel(
        kind=LevelKind.SESSION_HIGH,
        price=_LEVEL_PRICE,
        established_at=_START + timedelta(minutes=21),
        trading_day=_DAY,
    )

    assert _detect(bars, len(bars) - 1, later) is None


def test_detection_never_reads_past_the_current_bar() -> None:
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))
    index = len(bars) - 1

    before = _detect(bars, index, _level())
    bars.extend([_bar(22, high=21_000.0, low=19_000.0, close=19_100.0)])

    assert _detect(bars, index, _level()) == before


# ── a settlement is not liquidity ─────────────────────────────────────────


def test_a_reference_price_is_never_raided() -> None:
    """Price trades through a settlement without anyone's stops being
    taken -- that is not a raid."""
    settlement = _level(LevelKind.RTH_SETTLEMENT)
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    assert _detect(bars, len(bars) - 1, settlement) is None


def test_several_levels_taken_together_are_all_reported() -> None:
    """Which one 'counts' is the objective engine's question, not this
    one's -- reporting all keeps that decision measurable."""
    bars = _baseline(20)
    bars.append(_bar(20, high=_LEVEL_PRICE + 8.0, low=20_040.0, close=_LEVEL_PRICE + 4.0))
    bars.append(_bar(21, high=_LEVEL_PRICE + 1.0, low=20_030.0, close=20_035.0))

    events = detect_sweeps(
        bars,
        len(bars) - 1,
        [_level(LevelKind.PREV_DAY_HIGH), _level(LevelKind.SESSION_HIGH, _LEVEL_PRICE - 1.0)],
        min_penetration_atr=0.10,
        return_max_bars=12,
        atr_period=14,
        tick_size=_TICK,
    )

    assert len(events) == 2
