"""
Rolling Average True Range series -- vo.research (layer 5), Phase 32 aside.

WHY THIS EXISTS. vo.observation.atr's `atr_ticks` is a per-bar, per-call
primitive (Phase 11's swing-filter caller pattern) -- it has no rolling
"sample every stride-th bar across history" builder, unlike Hurst/ER,
which hurst_report.py/efficiency_ratio_report.py already provide via
build_rolling_hurst/build_rolling_er. Phase 32's confirmed ATR role
(architecture/vo-trade-logic-and-brain-plan.md Sec 5.7-revision,
2026-09-18 -- stop/target sizing, Markov/HMM input normalization,
inverse-volatility position sizing) needs the same rolling-series shape,
so it is added here rather than duplicated inline wherever it is first
consumed (vo.research.hmm_report is the first caller -- see that module).

Returned in PRICE units (ticks * tick_size), not raw ticks -- Bar's own
OHLC fields and Hurst/ER's sample values are already plain floats in
price units, and this series is meant to sit directly alongside them
(e.g. `log_return / atr` in hmm_report.py), so ticks would need
converting at every call site instead of once here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks

DEFAULT_STRIDE = 30


def build_rolling_atr(
    bars: Sequence[Bar],
    *,
    tick_size: float,
    window_lengths: Sequence[int],
    stride: int = DEFAULT_STRIDE,
) -> dict[int, list[tuple[datetime, float]]]:
    """Sample atr_ticks at every `stride`-th bar index, for each period in
    `window_lengths`, converted to price units. Returns, per period, a
    chronological list of (bar open_time_utc, ATR-in-price value) -- None
    estimates (warmup window) are dropped, never coerced to a fake
    number -- same convention as build_rolling_hurst/build_rolling_er."""
    if stride < 1:
        raise ValueError(f"stride must be >= 1, got {stride}")
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got {tick_size}")

    out: dict[int, list[tuple[datetime, float]]] = {period: [] for period in window_lengths}
    for index in range(0, len(bars), stride):
        for period in window_lengths:
            ticks = atr_ticks(bars, index, period=period, tick_size=tick_size)
            if ticks is not None:
                out[period].append((bars[index].open_time_utc, ticks * tick_size))
    return out
