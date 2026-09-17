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

    @staticmethod
    def _validate_next(bar: Bar, last: Bar | None) -> None:
        """The ordering/identity invariants .append() and build_bar_sequence
        both enforce, factored out so the two can never drift apart, and so
        a bulk builder can apply them in one O(n) pass instead of paying an
        O(n) tuple copy per bar via a .append() loop.

        NOTE: no separate O(n) duplicate-bar_id scan here (there was one; it
        made building a long sequence O(n^2) on top of the tuple-copy cost --
        invisible at the ~500-bar live backfill, but a multi-hour hang
        building a 100k-bar deep-history backtest). It is provably redundant,
        not just slow: within one BarSequence, instrument_id/timeframe are
        pinned to the first bar (the identity check below enforces this on
        every append), so bar_id --
        f"{instrument_id.key}:{timeframe.canonical}:{open_time_utc.isoformat()}"
        (Bar.bar_id) -- collapses to a pure function of open_time_utc alone.
        Any bar_id collision with an earlier bar therefore implies an
        open_time_utc collision with an earlier bar, which (since
        open_time_utc is strictly increasing) is always <= last.open_time_utc
        -- exactly the condition the check below already raises on, one step
        earlier, every time. See test_append_rejects_duplicate_bar_id_even_
        when_not_the_immediate_predecessor in test_sequence.py, whose own
        comment already notes this raises "either way".
        """
        if last is None:
            return

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

    def append(self, bar: Bar) -> BarSequence:
        self._validate_next(bar, self.bars[-1] if self.bars else None)
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

    O(n): validates each bar against the running last-accepted bar via
    BarSequence._validate_next and freezes the final tuple once, rather
    than calling .append() in a loop -- that would copy the whole
    accepted-so-far tuple on every bar (O(n) per call, O(n^2) overall),
    fine for a live-scale backfill but the difference between seconds and
    hours once a caller asks for deep, multi-year history.
    """
    accepted: list[Bar] = []
    quarantined: list[QuarantinedBar] = []
    last: Bar | None = None

    for bar in bars:
        try:
            BarSequence._validate_next(bar, last)
        except BarSequenceViolation as exc:
            quarantined.append(QuarantinedBar(bar=bar, reason=str(exc)))
            continue
        accepted.append(bar)
        last = bar

    return BarSequenceResult(
        sequence=BarSequence(bars=tuple(accepted)),
        quarantined=tuple(quarantined),
    )


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
