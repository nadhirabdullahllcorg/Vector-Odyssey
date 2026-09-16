"""
Price <-> tick-count conversion.

MT5 prices are discrete multiples of an instrument's tick_size, so exact
comparison is meaningful in tick units even though it is not reliable in
float units (architecture/vo-candle-layer.md §3, "integer-tick arithmetic").
`to_ticks` is the one place that conversion happens; `Candle` and
`BarRelation` both call it rather than each rounding independently.

tick_size is deliberately a parameter here, not something resolved from a
Bar automatically: a Bar carries only its InstrumentId (Phase 4), not the
Symbol metadata (tick_size lives on Symbol). Resolving InstrumentId ->
Symbol needs a catalog that does not exist yet, so callers that need exact
tick arithmetic (BarRelation, CandleWindow.separation) are given tick_size
explicitly by whoever already has the Symbol on hand.
"""

from __future__ import annotations


def to_ticks(price: float, tick_size: float) -> int:
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size!r}")

    return round(price / tick_size)
