"""
Candle — the derived geometry view over a Bar.

C2 (architecture/vo-candle-layer.md §3): derived attributes must never live
on the record itself. `serialization.py` serializes a *Record* by walking
every dataclass field (`asdict`); putting `body_size` on `Bar` would enter
the wire format the moment anything tried to round-trip it, and a bug in
the geometry code would then look like corrupted market data. `Candle`
wraps a `Bar` and computes everything as properties — nothing here is a
dataclass field, so nothing here can be serialized by accident.

C3: a ratio is `None`, never `0.0`, whenever `total_range == 0` — see
`_ratio` below. `0.0` is a real measurement (a genuine doji, where the body
is zero but the range is not); `None` means the ratio is undefined because
there is no range to be a ratio of. Collapsing the two would make
zero-range bars (illiquid instruments, single-tick periods, halted
sessions) silently indistinguishable from real dojis in any percentile or
threshold built on this ratio later.

No `doji` field: whether a bar "is a doji" is a threshold judgement on
`body_ratio`, and thresholds are research output (config/settings/, later
phases), never a foundation-layer literal (§4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .bar import Bar
from .tickmath import to_ticks


class Direction(Enum):
    BULLISH = auto()
    BEARISH = auto()
    NEUTRAL = auto()


def _ratio(numerator: float, total_range: float) -> float | None:
    if total_range == 0:
        return None
    return numerator / total_range


@dataclass(frozen=True)
class Candle:
    """
    A read-only view over one Bar. Construct one per Bar you need geometry
    for; it holds no state of its own beyond the Bar reference.

    `tick_size` is optional. When given, `direction`/`is_bullish`/
    `is_bearish` compare `open`/`close` in integer ticks (architecture/
    vo-candle-layer.md §3's "integer-tick arithmetic" — safe against float
    rounding noise). When omitted, they fall back to a plain float
    comparison, which is exact for the decimal-quantized prices MT5 already
    sends but is not guaranteed for values that arrived via further
    arithmetic. Bar carries only an InstrumentId, not Symbol metadata, so
    tick_size has to come from whoever already has the Symbol on hand —
    see tickmath.py.
    """

    bar: Bar
    tick_size: float | None = None

    @property
    def body_high(self) -> float:
        return max(self.bar.open, self.bar.close)

    @property
    def body_low(self) -> float:
        return min(self.bar.open, self.bar.close)

    @property
    def body_size(self) -> float:
        return abs(self.bar.close - self.bar.open)

    @property
    def total_range(self) -> float:
        return self.bar.high - self.bar.low

    @property
    def upper_wick(self) -> float:
        return self.bar.high - self.body_high

    @property
    def lower_wick(self) -> float:
        return self.body_low - self.bar.low

    @property
    def direction(self) -> Direction:
        open_ticks: float
        close_ticks: float

        if self.tick_size is not None:
            open_ticks = to_ticks(self.bar.open, self.tick_size)
            close_ticks = to_ticks(self.bar.close, self.tick_size)
        else:
            open_ticks, close_ticks = self.bar.open, self.bar.close

        if close_ticks > open_ticks:
            return Direction.BULLISH
        if close_ticks < open_ticks:
            return Direction.BEARISH
        return Direction.NEUTRAL

    @property
    def is_bullish(self) -> bool:
        return self.direction is Direction.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.direction is Direction.BEARISH

    @property
    def body_ratio(self) -> float | None:
        return _ratio(self.body_size, self.total_range)

    @property
    def upper_wick_ratio(self) -> float | None:
        return _ratio(self.upper_wick, self.total_range)

    @property
    def lower_wick_ratio(self) -> float | None:
        return _ratio(self.lower_wick, self.total_range)

    @property
    def close_position(self) -> float | None:
        return _ratio(self.bar.close - self.bar.low, self.total_range)

    @property
    def open_position(self) -> float | None:
        return _ratio(self.bar.open - self.bar.low, self.total_range)
