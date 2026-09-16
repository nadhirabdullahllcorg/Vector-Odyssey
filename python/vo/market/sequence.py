"""
BarSequence / CandleWindow — ordering, and the lookahead firewall (C1).

architecture/vo-candle-layer.md §5: `CandleWindow` is "bounded at current,
with no forward accessor — not a discouraged one, none." That is a
structural guarantee here, not a convention: every query on `CandleWindow`
either reads `self.sequence.bars[<= self.index]` or raises. There is no
`next`, `forward`, or `peek`, and `separation()`/`relation()` reject any
index past `current`. A strategy — or a helper three call-frames deep in a
future engine — cannot reach a future bar through this object, because the
object has no path to one.

`BarSequence.append` mirrors the two-tier pattern `vo.market.mapping`
already established for E.7 (single strict primitive + a batch quarantiner
built on top of it): `append` raises `BarSequenceViolation` on a duplicate
or out-of-order bar (a fact about the feed, not something to silently
drop — architecture/vo-candle-layer.md §5), and `build_bar_sequence`
catches that per bar to partition a stream into an accepted sequence plus
quarantined (bar, reason) pairs, instead of one bad bar aborting the whole
build.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto

from .bar import Bar
from .relation import BarRelation, Separation, separation_of


class BarSequenceViolation(ValueError):
    """Raised by BarSequence.append for a duplicate or out-of-order bar."""


@dataclass(frozen=True)
class BarSequence:
    """Append-only, ordered, immutable snapshot of bars for one
    (instrument_id, timeframe)."""

    bars: tuple[Bar, ...] = ()

    def append(self, bar: Bar) -> BarSequence:
        if self.bars:
            last = self.bars[-1]

            if bar.instrument_id != last.instrument_id or bar.timeframe != last.timeframe:
                raise BarSequenceViolation(
                    f"Bar {bar.bar_id!r} does not match this sequence's "
                    f"(instrument_id, timeframe): expected "
                    f"({last.instrument_id.key}, {last.timeframe.canonical})"
                )

            if bar.open_time_utc <= last.open_time_utc:
                raise BarSequenceViolation(
                    f"Bar {bar.bar_id!r} at {bar.open_time_utc.isoformat()} is not "
                    f"strictly after the last bar at {last.open_time_utc.isoformat()}"
                )

        if any(existing.bar_id == bar.bar_id for existing in self.bars):
            raise BarSequenceViolation(f"Duplicate bar_id: {bar.bar_id!r}")

        return BarSequence(bars=(*self.bars, bar))

    def window_at(self, index: int) -> CandleWindow:
        return CandleWindow(sequence=self, index=index)

    def __len__(self) -> int:
        return len(self.bars)


@dataclass(frozen=True)
class QuarantinedBar:
    """One bar build_bar_sequence could not append, and why."""

    bar: Bar
    reason: str


@dataclass(frozen=True)
class BarSequenceResult:
    sequence: BarSequence
    quarantined: tuple[QuarantinedBar, ...]

    @property
    def all_accepted(self) -> bool:
        return not self.quarantined


def build_bar_sequence(bars: Iterable[Bar]) -> BarSequenceResult:
    """
    Build a BarSequence from a stream of bars, quarantining (not dropping,
    not aborting on) any bar that is a duplicate or out of order — the
    sequence-level counterpart to vo.market.mapping.map_records' record-
    level quarantine.
    """
    sequence = BarSequence()
    quarantined: list[QuarantinedBar] = []

    for bar in bars:
        try:
            sequence = sequence.append(bar)
        except BarSequenceViolation as exc:
            quarantined.append(QuarantinedBar(bar=bar, reason=str(exc)))

    return BarSequenceResult(sequence=sequence, quarantined=tuple(quarantined))


@dataclass(frozen=True)
class CandleWindow:
    """
    A view onto a BarSequence bounded at `index` ("current"). See the
    module docstring: nothing on this object can resolve to a bar after
    `current` — that is what makes the lookahead firewall (C1) structural
    rather than a matter of discipline.
    """

    sequence: BarSequence
    index: int

    def __post_init__(self) -> None:
        if not (0 <= self.index < len(self.sequence.bars)):
            raise IndexError(
                f"index {self.index} out of range for a sequence of "
                f"{len(self.sequence.bars)} bars"
            )

    @property
    def current(self) -> Bar:
        return self.sequence.bars[self.index]

    def prev(self, k: int = 1) -> Bar | None:
        """The bar k positions before current, or None before the start."""
        if k < 1:
            raise ValueError("k must be >= 1")
        j = self.index - k
        return self.sequence.bars[j] if j >= 0 else None

    def last(self, n: int) -> tuple[Bar, ...]:
        """The n most recent bars, current last. Fewer than n if history is short."""
        if n < 1:
            raise ValueError("n must be >= 1")
        start = max(0, self.index - n + 1)
        return self.sequence.bars[start : self.index + 1]

    def relation(self, k: int = 1, *, tick_size: float) -> BarRelation | None:
        """current vs prev(k). None when there is no prev(k) yet."""
        earlier = self.prev(k)
        if earlier is None:
            return None
        return BarRelation(a=earlier, b=self.current, tick_size=tick_size)

    def highest(self, n: int) -> float:
        return max(bar.high for bar in self.last(n))

    def lowest(self, n: int) -> float:
        return min(bar.low for bar in self.last(n))

    def is_new_high(self, n: int) -> bool:
        """current.high strictly exceeds every one of the n-1 bars before it."""
        window = self.last(n)
        if len(window) < 2:
            return False
        return window[-1].high > max(bar.high for bar in window[:-1])

    def is_new_low(self, n: int) -> bool:
        window = self.last(n)
        if len(window) < 2:
            return False
        return window[-1].low < min(bar.low for bar in window[:-1])

    def is_higher_high(self, k: int = 1) -> bool:
        earlier = self.prev(k)
        return earlier is not None and self.current.high > earlier.high

    def is_higher_low(self, k: int = 1) -> bool:
        earlier = self.prev(k)
        return earlier is not None and self.current.low > earlier.low

    def is_lower_high(self, k: int = 1) -> bool:
        earlier = self.prev(k)
        return earlier is not None and self.current.high < earlier.high

    def is_lower_low(self, k: int = 1) -> bool:
        earlier = self.prev(k)
        return earlier is not None and self.current.low < earlier.low

    def is_inside(self, k: int = 1) -> bool:
        """current's range is fully within prev(k)'s range."""
        earlier = self.prev(k)
        return (
            earlier is not None
            and self.current.high <= earlier.high
            and self.current.low >= earlier.low
        )

    def is_outside(self, k: int = 1) -> bool:
        """current's range fully contains prev(k)'s range."""
        earlier = self.prev(k)
        return (
            earlier is not None
            and self.current.high >= earlier.high
            and self.current.low <= earlier.low
        )

    def separation(self, i: int, j: int, *, tick_size: float) -> Separation:
        """
        Non-adjacent 3-bar measurement between absolute sequence indices i
        and j (i < j <= current). Bounded exactly like everything else on
        this object: j may never exceed `index`.
        """
        if not (0 <= i < j <= self.index):
            raise IndexError(
                f"separation({i}, {j}) is out of bounds for index={self.index} "
                f"(both must satisfy 0 <= i < j <= index)"
            )

        a, b = self.sequence.bars[i], self.sequence.bars[j]
        return separation_of(a, b, bars_between=j - i - 1, tick_size=tick_size)


class CoverageStatus(Enum):
    UNKNOWN = auto()
    """No tick feed was ever attempted for this bar (captured_tick_count is None)."""
    NONE = auto()
    """A feed was present but captured zero ticks — a data-quality event,
    not the same fact as UNKNOWN (C7)."""
    PARTIAL = auto()
    COMPLETE = auto()


@dataclass(frozen=True)
class TickCoverage:
    """
    Whether a bar's tick_volume (broker-reported price-change count) is
    backed by actual captured ticks. Tick-interaction research must gate on
    `status == COMPLETE` — measuring anything tick-level against PARTIAL
    coverage produces a confidently wrong number (architecture/
    vo-candle-layer.md §9).
    """

    expected: int
    captured: int | None

    @property
    def ratio(self) -> float | None:
        if self.captured is None:
            return None
        if self.expected == 0:
            return None
        return self.captured / self.expected

    @property
    def status(self) -> CoverageStatus:
        if self.captured is None:
            return CoverageStatus.UNKNOWN
        if self.captured == 0:
            return CoverageStatus.NONE
        if self.captured >= self.expected:
            return CoverageStatus.COMPLETE
        return CoverageStatus.PARTIAL


def tick_coverage_of(bar: Bar) -> TickCoverage:
    return TickCoverage(expected=bar.tick_volume, captured=bar.captured_tick_count)
