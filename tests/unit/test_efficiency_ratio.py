"""Kaufman Efficiency Ratio (vo.observation.efficiency_ratio)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.efficiency_ratio import efficiency_ratio

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)


def _bars(closes: list[float]) -> list[Bar]:
    out = []
    for i, c in enumerate(closes):
        out.append(
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
        )
    return out


def test_perfectly_directional_run_is_one():
    bars = _bars([100 + i for i in range(11)])  # straight ramp
    assert efficiency_ratio(bars, 10, period=5) == 1.0


def test_pure_chop_is_near_zero():
    bars = _bars([100 + (i % 2) for i in range(11)])  # 100,101,100,101,...
    er = efficiency_ratio(bars, 10, period=10)
    assert er is not None and er < 0.2


def test_none_before_enough_history():
    bars = _bars([100 + i for i in range(6)])
    assert efficiency_ratio(bars, 3, period=5) is None


def test_flat_window_is_zero_not_undefined():
    bars = _bars([100.0] * 11)
    assert efficiency_ratio(bars, 10, period=5) == 0.0


def test_bounded_zero_to_one():
    bars = _bars([100, 103, 101, 106, 104, 109, 108, 112, 110, 115, 118])
    er = efficiency_ratio(bars, 10, period=8)
    assert er is not None and 0.0 <= er <= 1.0


def test_period_must_be_positive():
    with pytest.raises(ValueError, match="period must be >= 1"):
        efficiency_ratio(_bars([100, 101]), 1, period=0)
