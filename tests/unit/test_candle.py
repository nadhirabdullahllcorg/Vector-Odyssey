"""
Candle geometry — the worked test matrix from
architecture/vo-candle-layer.md §12, verified against this implementation.
"""

from datetime import UTC, datetime

from vo.market import Bar, Candle, Direction, InstrumentId, Timeframe

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
OPEN_TIME = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)


def _bar(open_, high, low, close, **overrides):
    kwargs = {
        "instrument_id": INSTRUMENT,
        "timeframe": Timeframe.M1,
        "open_time_utc": OPEN_TIME,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": 100,
        "real_volume": 0,
    }
    kwargs.update(overrides)
    return Bar(**kwargs)


def test_bullish_reference():
    candle = Candle(_bar(100, 110, 95, 108))

    assert candle.direction is Direction.BULLISH
    assert candle.body_size == 8
    assert candle.body_high == 108
    assert candle.body_low == 100
    assert candle.upper_wick == 2
    assert candle.lower_wick == 5
    assert candle.total_range == 15

    # master invariant: catches essentially every sign/ordering error
    assert candle.body_size + candle.upper_wick + candle.lower_wick == candle.total_range
    assert candle.body_ratio + candle.upper_wick_ratio + candle.lower_wick_ratio == 1.0

    assert candle.close_position == (108 - 95) / 15
    assert candle.open_position == (100 - 95) / 15


def test_bearish_mirror_is_property_not_just_a_case():
    """Swapping open/close leaves geometry unchanged and flips only direction."""
    bullish = Candle(_bar(100, 110, 95, 108))
    bearish = Candle(_bar(108, 110, 95, 100))

    assert bearish.body_size == bullish.body_size
    assert bearish.body_high == bullish.body_high
    assert bearish.body_low == bullish.body_low
    assert bearish.upper_wick == bullish.upper_wick
    assert bearish.lower_wick == bullish.lower_wick
    assert bearish.total_range == bullish.total_range
    assert bullish.direction is Direction.BULLISH
    assert bearish.direction is Direction.BEARISH


def test_neutral_open_equals_close_has_a_defined_zero_ratio():
    candle = Candle(_bar(100, 110, 95, 100))

    assert candle.direction is Direction.NEUTRAL
    assert candle.body_size == 0
    assert candle.total_range == 15
    assert candle.body_ratio == 0.0  # defined: a real doji, not "undefined"


def test_zero_range_ratios_are_none_not_zero():
    """C3: open=high=low=close must not collide with the neutral-but-ranged case."""
    candle = Candle(_bar(100, 100, 100, 100))

    assert candle.total_range == 0
    assert candle.body_size == 0
    assert candle.body_ratio is None
    assert candle.upper_wick_ratio is None
    assert candle.lower_wick_ratio is None
    assert candle.close_position is None
    assert candle.open_position is None


def test_neutral_and_zero_range_do_not_compare_equal():
    neutral = Candle(_bar(100, 110, 95, 100))
    zero_range = Candle(_bar(100, 100, 100, 100))

    assert neutral.body_ratio != zero_range.body_ratio  # 0.0 != None


def test_direction_with_tick_size_uses_integer_ticks():
    """Guards against float noise: a close that is "the same as open" up to
    float error must not read as BULLISH/BEARISH when tick_size says it's
    the same tick."""
    almost_equal = _bar(100.00, 100.01, 99.99, 100.00 + 1e-10)
    candle = Candle(almost_equal, tick_size=0.01)

    assert candle.direction is Direction.NEUTRAL


def test_is_bullish_is_bearish():
    assert Candle(_bar(100, 110, 95, 108)).is_bullish is True
    assert Candle(_bar(100, 110, 95, 108)).is_bearish is False
    assert Candle(_bar(108, 110, 95, 100)).is_bearish is True
    assert Candle(_bar(108, 110, 95, 100)).is_bullish is False
