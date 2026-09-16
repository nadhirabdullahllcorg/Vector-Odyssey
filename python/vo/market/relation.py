"""
BarRelation / Separation — pairwise, objective, unnamed measurements between
two bars (architecture/vo-candle-layer.md §6).

Naming discipline (the interpretation firewall, §1/§13): no field here is
called `gap`, `imbalance`, `inefficiency`, `fvg`, or `vi`. `body_separation
_ticks = +3` is an observation. Whether +3 ticks of body separation on a
given instrument, session and volatility regime constitutes a Volume
Imbalance is a VO definition that belongs to a much later phase — once a
field is named after that definition, every downstream consumer inherits
it permanently, including the research meant to test it.

All comparisons are in integer ticks (`tickmath.to_ticks`), never float
subtraction directly on price — see architecture/vo-candle-layer.md §3 and
`candle.py`'s docstring for why exact-equality-sensitive comparisons need
tick units rather than float ones. Unlike `Candle`, `tick_size` here is not
optional: gap/overlap/separation measurements are exactly the case the spec
calls out as unsafe in float arithmetic, so there is no silent fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .bar import Bar
from .candle import Candle, Direction
from .tickmath import to_ticks


@dataclass(frozen=True)
class BarRelation:
    """a is earlier, b is later — not necessarily adjacent."""

    a: Bar
    b: Bar
    tick_size: float

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {self.tick_size!r}")

    def _ticks(self, price: float) -> int:
        return to_ticks(price, self.tick_size)

    # ── separation / gap ─────────────────────────────────────────────

    @property
    def open_gap_ticks(self) -> int:
        return self._ticks(self.b.open) - self._ticks(self.a.close)

    @property
    def body_separation_ticks(self) -> int:
        """Signed: >0 bodies gap up, <0 gap down, 0 touching or overlapping."""
        a_body_high = self._ticks(max(self.a.open, self.a.close))
        a_body_low = self._ticks(min(self.a.open, self.a.close))
        b_body_high = self._ticks(max(self.b.open, self.b.close))
        b_body_low = self._ticks(min(self.b.open, self.b.close))

        if b_body_low > a_body_high:
            return b_body_low - a_body_high
        if b_body_high < a_body_low:
            return -(a_body_low - b_body_high)
        return 0

    @property
    def body_overlap_ticks(self) -> int:
        a_body_high = self._ticks(max(self.a.open, self.a.close))
        a_body_low = self._ticks(min(self.a.open, self.a.close))
        b_body_high = self._ticks(max(self.b.open, self.b.close))
        b_body_low = self._ticks(min(self.b.open, self.b.close))

        return max(0, min(a_body_high, b_body_high) - max(a_body_low, b_body_low))

    @property
    def range_overlap_ticks(self) -> int:
        a_high, a_low = self._ticks(self.a.high), self._ticks(self.a.low)
        b_high, b_low = self._ticks(self.b.high), self._ticks(self.b.low)

        return max(0, min(a_high, b_high) - max(a_low, b_low))

    @property
    def range_gap_ticks(self) -> int:
        """Unsigned magnitude: >0 only when the full ranges do not touch."""
        a_high, a_low = self._ticks(self.a.high), self._ticks(self.a.low)
        b_high, b_low = self._ticks(self.b.high), self._ticks(self.b.low)

        if b_low > a_high:
            return b_low - a_high
        if b_high < a_low:
            return a_low - b_high
        return 0

    # ── comparison ───────────────────────────────────────────────────

    @property
    def high_diff(self) -> int:
        return self._ticks(self.b.high) - self._ticks(self.a.high)

    @property
    def low_diff(self) -> int:
        return self._ticks(self.b.low) - self._ticks(self.a.low)

    @property
    def close_diff(self) -> int:
        return self._ticks(self.b.close) - self._ticks(self.a.close)

    @property
    def open_diff(self) -> int:
        return self._ticks(self.b.open) - self._ticks(self.a.open)

    @property
    def close_to_open_diff(self) -> int:
        return self._ticks(self.b.open) - self._ticks(self.a.close)

    @property
    def open_to_close_diff(self) -> int:
        return self._ticks(self.b.close) - self._ticks(self.a.open)

    @property
    def range_ratio(self) -> float | None:
        a_range = Candle(self.a).total_range
        if a_range == 0:
            return None
        return Candle(self.b).total_range / a_range

    @property
    def body_ratio_change(self) -> float | None:
        a_ratio = Candle(self.a).body_ratio
        b_ratio = Candle(self.b).body_ratio
        if a_ratio is None or b_ratio is None:
            return None
        return b_ratio - a_ratio

    @property
    def range_expansion(self) -> bool:
        return Candle(self.b).total_range > Candle(self.a).total_range

    @property
    def range_contraction(self) -> bool:
        return Candle(self.b).total_range < Candle(self.a).total_range

    @property
    def continuation(self) -> bool:
        """Same direction. Two NEUTRAL bars trivially count as continuation."""
        return Candle(self.a, self.tick_size).direction == Candle(self.b, self.tick_size).direction

    @property
    def reversal(self) -> bool:
        """Opposite of BULLISH/BEARISH specifically — a NEUTRAL bar on
        either side is neither a continuation nor a reversal."""
        a_dir = Candle(self.a, self.tick_size).direction
        b_dir = Candle(self.b, self.tick_size).direction
        return {a_dir, b_dir} == {Direction.BULLISH, Direction.BEARISH}

    @property
    def contains(self) -> bool:
        """b's range fully contains a's range (b is an outside/engulfing bar)."""
        return self.b.high >= self.a.high and self.b.low <= self.a.low

    @property
    def contained_by(self) -> bool:
        """b's range is fully inside a's range (b is an inside bar)."""
        return self.b.high <= self.a.high and self.b.low >= self.a.low


@dataclass(frozen=True)
class Separation:
    """
    The non-adjacent, 3-bar-formation measurement (architecture/
    vo-candle-layer.md §5's "3-bar case, unnamed"): the gap between an
    earlier bar's high and a later bar's low (or vice versa), reported as a
    fact — never named after what it might mean once a VO definition exists.
    """

    high_low_ticks: int
    """ticks(b.low) - ticks(a.high). >0 => b.low sits above a.high."""
    low_high_ticks: int
    """ticks(a.low) - ticks(b.high). >0 => b.high sits below a.low."""
    overlap_ticks: int
    """>= 0."""
    bars_between: int
    time_between: timedelta


def separation_of(a: Bar, b: Bar, *, bars_between: int, tick_size: float) -> Separation:
    a_high, a_low = to_ticks(a.high, tick_size), to_ticks(a.low, tick_size)
    b_high, b_low = to_ticks(b.high, tick_size), to_ticks(b.low, tick_size)

    return Separation(
        high_low_ticks=b_low - a_high,
        low_high_ticks=a_low - b_high,
        overlap_ticks=max(0, min(a_high, b_high) - max(a_low, b_low)),
        bars_between=bars_between,
        time_between=b.open_time_utc - a.open_time_utc,
    )
