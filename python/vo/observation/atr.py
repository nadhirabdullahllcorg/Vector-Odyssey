"""
Average True Range (ATR) -- [VO-D].

Month 1 never mentions ATR; it belongs to VO's own swing-engineering
layer, not the ICT source material (see vo.month01.ontology's module
docstring on where Month-1-sourced concepts live -- this is not one of
them). It exists here because Phase 11's swing detector needs a
volatility-normalized minimum-reversal-distance filter, independent of
the K-bar structural confirmation rule: a 2-bar reversal is tiny in a
quiet market and enormous in a violent one, and ATR is the normalization
that makes "how big was this reversal" comparable across regimes.

DELIBERATE CHOICE: a plain simple moving average of True Range, not
Wilder's original recursive smoothing. An SMA is fully determined by its
own window -- no seed value, no infinite-lookback tail, no "which bar did
the recursion start from" ambiguity -- so it is exactly reproducible
bar-by-bar and auditable by hand against a chart. This is a real
simplification relative to the indicator most platforms ship, made
explicit rather than silently assumed, and revisable later without
renaming anything (the config's own `atr_period` is exactly what a future
`atr_method` field would sit next to -- see swing_config.py).

Everything here is integer-tick arithmetic (architecture/vo-candle-layer.
md §3, `tickmath.to_ticks`), never float subtraction directly on price --
the same discipline `vo.market.relation` already established for
gap/separation measurement.

No lookahead: `atr_ticks(bars, index, ...)` only ever reads
`bars[<= index]`. Phase 11's engine (`vo.observation.swings`) is the
thing that actually enforces this is called from a bounded position; this
module itself has no CandleWindow dependency because it works over a
plain, already-bounded slice -- the caller decides how much of the past
it is allowed to show it.
"""

from __future__ import annotations

from collections.abc import Sequence

from vo.market.bar import Bar
from vo.market.tickmath import to_ticks


def true_range_ticks(current: Bar, previous: Bar | None, *, tick_size: float) -> int:
    """
    True range for `current`, in integer ticks. `previous=None` (no prior
    close to gap from -- the first bar VO has ever observed for this
    instrument/timeframe) is answered honestly as just the bar's own
    high-low range, not a guess at what came before.
    """
    high = to_ticks(current.high, tick_size)
    low = to_ticks(current.low, tick_size)

    if previous is None:
        return high - low

    prev_close = to_ticks(previous.close, tick_size)
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_ticks(
    bars: Sequence[Bar],
    index: int,
    *,
    period: int,
    tick_size: float,
) -> int | None:
    """
    Simple moving average of true range over `period` bars ending at and
    including `index`, in integer ticks.

    None -- never a partial-window average, never a guessed 0 -- when
    fewer than `period` bars exist up to `index`. An ATR computed from a
    short window is not the same measurement as one from a full window;
    conflating them is exactly the "plausible-looking estimate" this
    project's None conventions exist to refuse (TickCoverage.UNKNOWN,
    Candle's None ratios, ...). Callers that receive None cannot yet
    evaluate the ATR filter at this bar -- that is a fact about
    insufficient history, not an error.

    Recomputed fresh on every call, O(period): no caching, matching
    ReferenceLevelEngine's own precedent (vo.time.levels) that this is
    fine at today's data volumes and worth revisiting only if profiling
    ever says otherwise.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    if index < 0 or index >= len(bars):
        raise IndexError(f"index {index} out of range for {len(bars)} bars")

    if index < period - 1:
        return None

    total = 0
    for i in range(index - period + 1, index + 1):
        previous = bars[i - 1] if i > 0 else None
        total += true_range_ticks(bars[i], previous, tick_size=tick_size)

    return round(total / period)
