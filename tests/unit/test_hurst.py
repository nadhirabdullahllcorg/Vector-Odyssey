"""Structure-function Hurst estimate (vo.observation.hurst)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.hurst import hurst_exponent

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)


def _bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            instrument_id=_INSTRUMENT,
            timeframe=Timeframe.M1,
            open_time_utc=_T0 + timedelta(minutes=i),
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            tick_volume=10,
            real_volume=0,
        )
        for i, c in enumerate(closes)
    ]


def test_straight_ramp_is_persistent_near_one():
    bars = _bars([100 + i for i in range(31)])
    h = hurst_exponent(bars, 30, period=20)
    assert h is not None and h > 0.8


def test_choppy_is_far_below_the_ramp():
    ramp = _bars([100 + i for i in range(31)])
    chop = _bars([100 + (i % 2) for i in range(31)])
    h_ramp = hurst_exponent(ramp, 30, period=20)
    h_chop = hurst_exponent(chop, 30, period=20)
    assert h_ramp is not None and h_chop is not None
    assert h_chop < h_ramp
    assert h_chop < 0.5


def test_none_before_enough_history():
    bars = _bars([100 + i for i in range(10)])
    assert hurst_exponent(bars, 9, period=20) is None


def test_flat_window_is_none_not_a_guess():
    bars = _bars([100.0] * 31)
    assert hurst_exponent(bars, 30, period=20) is None


def test_period_floor_is_enforced():
    with pytest.raises(ValueError, match="period must be >= 4"):
        hurst_exponent(_bars([100.0] * 10), 5, period=3)
