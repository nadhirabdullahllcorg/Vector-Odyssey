"""vo.observation.entry_timing -- ER paired with ATR, separated out of
the regime engine's evidence bundle per vo-trade-logic-and-brain-plan.md
Section 5.7."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.entry_timing import measure_entry_timing

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_TF = Timeframe.from_mt5("PERIOD_M1")
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01


def _bars(closes: list[float]) -> list[Bar]:
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            Bar(
                instrument_id=_INSTRUMENT,
                timeframe=_TF,
                open_time_utc=_START + timedelta(minutes=i),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                tick_volume=100,
                real_volume=0,
                spread=80,
            )
        )
    return bars


def test_a_clean_trend_reads_high_efficiency() -> None:
    bars = _bars([100.0 + i for i in range(40)])

    reading = measure_entry_timing(
        bars, 39, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=10
    )

    assert reading.complete is True
    assert reading.efficiency_ratio is not None
    assert reading.efficiency_ratio > 0.9


def test_chop_reads_low_efficiency() -> None:
    bars = _bars([100.0 + (1.0 if i % 2 else 0.0) for i in range(40)])

    reading = measure_entry_timing(
        bars, 39, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=10
    )

    assert reading.efficiency_ratio is not None
    assert reading.efficiency_ratio < 0.2


def test_insufficient_history_reports_none_rather_than_a_partial_value() -> None:
    bars = _bars([100.0, 101.0, 102.0])

    reading = measure_entry_timing(
        bars, 2, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=10
    )

    assert reading.efficiency_ratio is None
    assert reading.complete is False


def test_a_reading_ranks_itself_against_its_own_recent_history() -> None:
    """A raw ER means nothing alone -- the rank is what makes a later
    threshold designable in relative terms."""
    chop = [100.0 + (1.0 if i % 2 else 0.0) for i in range(40)]
    trend = [100.0 + i for i in range(1, 21)]
    bars = _bars(chop + trend)

    reading = measure_entry_timing(
        bars, len(bars) - 1, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=30
    )

    assert reading.efficiency_ratio_percentile is not None
    assert reading.efficiency_ratio_percentile > 0.8, "a clean trend after chop should rank high"


def test_a_rank_against_nothing_is_not_a_rank() -> None:
    bars = _bars([100.0 + i for i in range(40)])

    reading = measure_entry_timing(
        bars, 39, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=0
    )

    assert reading.efficiency_ratio is not None
    assert reading.efficiency_ratio_percentile is None


def test_it_never_reads_past_the_current_bar() -> None:
    """Bars after `index` must not change the reading at `index` -- the
    G3 property, checked directly here."""
    base = [100.0 + i for i in range(40)]
    bars_short = _bars(base)
    bars_long = _bars([*base, 999.0, 1.0, 500.0])

    a = measure_entry_timing(
        bars_short, 39, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=10
    )
    b = measure_entry_timing(
        bars_long, 39, er_period=14, atr_period=14, tick_size=_TICK, percentile_lookback=10
    )

    assert a == b


def test_an_out_of_range_index_raises() -> None:
    with pytest.raises(IndexError):
        measure_entry_timing(
            _bars([100.0]), 5, er_period=14, atr_period=14, tick_size=_TICK,
            percentile_lookback=10,
        )


def test_a_negative_lookback_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        measure_entry_timing(
            _bars([100.0 + i for i in range(40)]), 39, er_period=14, atr_period=14,
            tick_size=_TICK, percentile_lookback=-1,
        )
