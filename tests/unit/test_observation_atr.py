"""vo.observation.atr -- true_range_ticks / atr_ticks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market import Bar, InstrumentId, Timeframe
from vo.observation.atr import atr_ticks, true_range_ticks

INSTRUMENT = InstrumentId(platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n")
T0 = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)


def _bar(minute: int, *, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=T0 + timedelta(minutes=minute),
        open=close,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
    )


def test_true_range_with_no_previous_is_just_the_bars_own_range():
    bar = _bar(0, high=100, low=99, close=99)
    assert true_range_ticks(bar, None, tick_size=1.0) == 1


def test_true_range_widens_for_a_gap_beyond_the_bars_own_range():
    previous = _bar(0, high=100, low=99, close=99)
    current = _bar(1, high=105, low=103, close=104)
    # own range = 2; gap from prev close (99) to high (105) = 6 -- the max wins.
    assert true_range_ticks(current, previous, tick_size=1.0) == 6


def test_true_range_uses_low_side_gap_when_that_is_larger():
    previous = _bar(0, high=100, low=99, close=100)
    current = _bar(1, high=91, low=90, close=90)
    # own range = 1; gap from prev close (100) to low (90) = 10.
    assert true_range_ticks(current, previous, tick_size=1.0) == 10


def test_atr_is_none_before_the_period_is_fully_observed():
    bars = [_bar(i, high=100, low=99, close=99.5) for i in range(2)]
    assert atr_ticks(bars, 0, period=3, tick_size=1.0) is None
    assert atr_ticks(bars, 1, period=3, tick_size=1.0) is None


def test_atr_is_the_simple_moving_average_of_true_range():
    bars = [
        _bar(0, high=100, low=99, close=99),  # TR=1 (no previous)
        _bar(1, high=101, low=99, close=100),  # TR=max(2, |101-99|=2, |99-99|=0)=2
        _bar(2, high=101, low=98, close=99),  # TR=max(3, |101-100|=1, |98-100|=2)=3
    ]
    # period=3 average of (1, 2, 3) = 2
    assert atr_ticks(bars, 2, period=3, tick_size=1.0) == 2


def test_atr_window_slides_and_does_not_reach_past_index():
    bars = [
        _bar(0, high=100, low=99, close=99),
        _bar(1, high=100, low=99, close=99),
        _bar(2, high=200, low=100, close=150),  # a big spike, only relevant once in-window
    ]
    # period=1: ATR at index 1 must not be affected by the index-2 spike.
    assert atr_ticks(bars, 1, period=1, tick_size=1.0) == true_range_ticks(
        bars[1], bars[0], tick_size=1.0
    )


def test_atr_rejects_bad_period():
    bars = [_bar(0, high=100, low=99, close=99)]
    with pytest.raises(ValueError):
        atr_ticks(bars, 0, period=0, tick_size=1.0)


def test_atr_rejects_out_of_range_index():
    bars = [_bar(0, high=100, low=99, close=99)]
    with pytest.raises(IndexError):
        atr_ticks(bars, 5, period=1, tick_size=1.0)
