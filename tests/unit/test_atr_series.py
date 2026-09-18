"""Unit tests for vo.research.atr_series -- the rolling-ATR builder Phase
32's ATR role needs alongside build_rolling_hurst/build_rolling_er."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.atr import atr_ticks
from vo.research.atr_series import build_rolling_atr

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="Test", broker_symbol="US100")
_BASE = datetime(2023, 1, 1, tzinfo=UTC)
_TICK_SIZE = 0.25


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _bar(minute: int, close: float, *, high: float | None = None, low: float | None = None) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=close,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        tick_volume=1,
        real_volume=0,
    )


def _wiggly_bars(n: int) -> tuple[Bar, ...]:
    return tuple(_bar(i, 100.0 + (i % 5) * 2.0 - i * 0.01) for i in range(n))


def test_build_rolling_atr_matches_atr_ticks_in_price_units() -> None:
    bars = _wiggly_bars(60)
    rolling = build_rolling_atr(bars, tick_size=_TICK_SIZE, window_lengths=(14,), stride=5)

    assert 14 in rolling
    samples = rolling[14]
    assert samples, "expected at least one sample past the warmup window"

    # Spot-check the first sample against atr_ticks directly, converted to
    # price units -- the whole point of this builder is not reinventing
    # the ATR math, just sampling it on a stride.
    first_index = 15  # the first stride=5 index >= period=14 with a prior bar
    expected_ticks = atr_ticks(bars, first_index, period=14, tick_size=_TICK_SIZE)
    assert expected_ticks is not None
    when, value = samples[0]
    assert when == bars[first_index].open_time_utc
    assert value == expected_ticks * _TICK_SIZE


def test_build_rolling_atr_drops_warmup_none_samples() -> None:
    bars = _wiggly_bars(20)
    rolling = build_rolling_atr(bars, tick_size=_TICK_SIZE, window_lengths=(14,), stride=1)
    # index < period - 1 (i.e. 0..12 for period=14) has too few bars --
    # atr_ticks returns None there; index 13 is the first valid one.
    times = {when for when, _ in rolling[14]}
    for index in range(13):
        assert bars[index].open_time_utc not in times
    assert bars[13].open_time_utc in times


def test_build_rolling_atr_rejects_bad_stride_and_tick_size() -> None:
    bars = _wiggly_bars(5)
    with pytest.raises(ValueError, match="stride"):
        build_rolling_atr(bars, tick_size=_TICK_SIZE, window_lengths=(2,), stride=0)
    with pytest.raises(ValueError, match="tick_size"):
        build_rolling_atr(bars, tick_size=0.0, window_lengths=(2,), stride=1)
