"""
The levels LRX will watch for a raid, classified by how much weight they
carry.

BUILT ON WHAT ALREADY EXISTS. vo.time.levels.ReferenceLevelEngine already
computes previous-day/week/month OHLC, session opens, settlement and the
opening-range gap. None of that is recomputed here. This module's job is
narrower and different: take those prices, give each one an identity and
a CLASS, and hand back a flat list of things price could raid.

WHY CLASSES AT ALL. The spec (section 3) is explicit that levels are not
interchangeable -- a sweep of yesterday's high is not the same event as a
sweep of a 20-minute internal swing, and a strategy that treats them
identically is throwing away the only thing that distinguishes a
meaningful raid from noise. Class A is prior-session structure that the
whole market can see; B is today's own developing structure; C is
internal. `minimum_class` in lrx.yaml is what refuses the small stuff.

EXTENSIBLE ON PURPOSE. The user's own note: once FVGs, breakers and order
blocks are built, "they will be reference levels as well". So this is a
registry over a LevelKind enum rather than a fixed set of fields -- a new
kind is a new enum member and a new contributor function, not a
refactor of everything that consumes levels. Getting that shape right
now is much cheaper than retrofitting it later.

NO LOOKAHEAD, AND ONE SUBTLETY THAT MATTERS. Previous-day and
previous-session levels are fixed once their period closed, so they are
safe at any time. TODAY's session high and low are NOT: the session high
is only "the session high" in retrospect, and a backtest that uses the
eventual value is reading the future. So session extremes are computed
strictly from bars at or before the current one, which is what makes
them honest and also what makes them move during the day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from vo.market.bar import Bar
from vo.time.levels import ReferenceLevelEngine


class LevelKind(Enum):
    """What a level IS. New kinds (FVG boundaries, breakers, order blocks)
    join here as they are built -- see the module docstring."""

    PREV_DAY_HIGH = "PREV_DAY_HIGH"
    PREV_DAY_LOW = "PREV_DAY_LOW"
    PREV_DAY_CLOSE = "PREV_DAY_CLOSE"
    PREV_WEEK_HIGH = "PREV_WEEK_HIGH"
    PREV_WEEK_LOW = "PREV_WEEK_LOW"
    RTH_SETTLEMENT = "RTH_SETTLEMENT"
    MIDNIGHT_OPEN = "MIDNIGHT_OPEN"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    ORG_HIGH = "ORG_HIGH"
    ORG_LOW = "ORG_LOW"
    INTERNAL_SWING_HIGH = "INTERNAL_SWING_HIGH"
    INTERNAL_SWING_LOW = "INTERNAL_SWING_LOW"

    def __str__(self) -> str:
        return self.value


class LevelClass(Enum):
    """How much weight a level carries. A is the heaviest."""

    A = "A"
    B = "B"
    C = "C"

    def __str__(self) -> str:
        return self.value

    def at_least(self, minimum: LevelClass) -> bool:
        order = {LevelClass.A: 3, LevelClass.B: 2, LevelClass.C: 1}
        return order[self] >= order[minimum]


class LevelSide(Enum):
    """Which side of price the level sits on as liquidity. A HIGH is
    buy-side liquidity (stops of shorts rest above it); a LOW is
    sell-side."""

    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"

    def __str__(self) -> str:
        return self.value


_CLASS_OF: dict[LevelKind, LevelClass] = {
    LevelKind.PREV_DAY_HIGH: LevelClass.A,
    LevelKind.PREV_DAY_LOW: LevelClass.A,
    LevelKind.PREV_WEEK_HIGH: LevelClass.A,
    LevelKind.PREV_WEEK_LOW: LevelClass.A,
    LevelKind.RTH_SETTLEMENT: LevelClass.A,
    LevelKind.PREV_DAY_CLOSE: LevelClass.B,
    LevelKind.MIDNIGHT_OPEN: LevelClass.B,
    LevelKind.SESSION_HIGH: LevelClass.B,
    LevelKind.SESSION_LOW: LevelClass.B,
    LevelKind.ORG_HIGH: LevelClass.B,
    LevelKind.ORG_LOW: LevelClass.B,
    LevelKind.INTERNAL_SWING_HIGH: LevelClass.C,
    LevelKind.INTERNAL_SWING_LOW: LevelClass.C,
}

_SIDE_OF: dict[LevelKind, LevelSide] = {
    LevelKind.PREV_DAY_HIGH: LevelSide.BUY_SIDE,
    LevelKind.PREV_WEEK_HIGH: LevelSide.BUY_SIDE,
    LevelKind.SESSION_HIGH: LevelSide.BUY_SIDE,
    LevelKind.ORG_HIGH: LevelSide.BUY_SIDE,
    LevelKind.INTERNAL_SWING_HIGH: LevelSide.BUY_SIDE,
    LevelKind.PREV_DAY_LOW: LevelSide.SELL_SIDE,
    LevelKind.PREV_WEEK_LOW: LevelSide.SELL_SIDE,
    LevelKind.SESSION_LOW: LevelSide.SELL_SIDE,
    LevelKind.ORG_LOW: LevelSide.SELL_SIDE,
    LevelKind.INTERNAL_SWING_LOW: LevelSide.SELL_SIDE,
}


@dataclass(frozen=True, slots=True)
class ReferenceLevel:
    """One price LRX will watch. `established_at` is when this level
    became knowable -- for a previous-day high, the close of that day;
    for a developing session high, the bar that made it. Recorded so a
    sweep can never be attributed to a level that did not exist yet."""

    kind: LevelKind
    price: float
    established_at: datetime | None
    trading_day: date

    @property
    def level_class(self) -> LevelClass:
        return _CLASS_OF[self.kind]

    @property
    def side(self) -> LevelSide:
        """A settlement or midnight open is a reference, not liquidity on
        one side, so it has no natural raid side and is excluded from
        sweep detection by returning None-equivalent handling upstream.
        Kinds without an entry here raise, so adding a LevelKind without
        deciding its side fails loudly rather than silently."""
        side = _SIDE_OF.get(self.kind)
        if side is None:
            raise KeyError(
                f"{self.kind} has no liquidity side -- it is a reference price, "
                f"not a pool of resting orders. Use is_liquidity() first."
            )
        return side

    def is_liquidity(self) -> bool:
        """Whether this level represents a pool of resting orders that can
        be raided. Settlement and the midnight open are references price
        is measured against, not stops waiting to be taken."""
        return self.kind in _SIDE_OF


def session_extremes_so_far(
    bars: Sequence[Bar], *, up_to_index: int, session_start: datetime
) -> tuple[float | None, float | None]:
    """Today's high and low AS KNOWN AT `up_to_index` -- never the
    eventual values.

    This is the lookahead trap the spec calls out by name (section 24):
    "a current session high cannot be treated as known before price
    creates it". Reading the finished session's high in a backtest would
    make every sweep of it look perfectly timed.
    """
    if up_to_index < 0 or up_to_index >= len(bars):
        raise IndexError(f"up_to_index {up_to_index} out of range for {len(bars)} bars")

    high: float | None = None
    low: float | None = None
    for bar in bars[: up_to_index + 1]:
        if bar.open_time_utc < session_start:
            continue
        high = bar.high if high is None else max(high, bar.high)
        low = bar.low if low is None else min(low, bar.low)
    return high, low


def collect_levels(
    engine: ReferenceLevelEngine,
    *,
    trading_day: date,
    bars: Sequence[Bar],
    up_to_index: int,
    session_start: datetime | None = None,
) -> tuple[ReferenceLevel, ...]:
    """Every level LRX can see right now, from the engine that already
    computes them plus the developing session extremes.

    A level the engine cannot produce yet (no previous day on the first
    day of a run) is simply absent -- never a placeholder, never a zero.
    """
    levels: list[ReferenceLevel] = []

    previous_day = engine.previous_day_ohlc(trading_day)
    if previous_day is not None:
        for kind, price in (
            (LevelKind.PREV_DAY_HIGH, previous_day.high),
            (LevelKind.PREV_DAY_LOW, previous_day.low),
            (LevelKind.PREV_DAY_CLOSE, previous_day.close),
        ):
            levels.append(
                ReferenceLevel(
                    kind=kind,
                    price=price,
                    established_at=None,
                    trading_day=trading_day,
                )
            )

    previous_week = engine.previous_week_ohlc(trading_day)
    if previous_week is not None:
        for kind, price in (
            (LevelKind.PREV_WEEK_HIGH, previous_week.high),
            (LevelKind.PREV_WEEK_LOW, previous_week.low),
        ):
            levels.append(
                ReferenceLevel(
                    kind=kind,
                    price=price,
                    established_at=None,
                    trading_day=trading_day,
                )
            )

    settlement = engine.settlement(trading_day)
    if settlement is not None:
        levels.append(
            ReferenceLevel(
                kind=LevelKind.RTH_SETTLEMENT,
                price=settlement.price,
                established_at=None,
                trading_day=trading_day,
            )
        )

    gap = engine.opening_range_gap(trading_day)
    if gap is not None:
        for kind, price in (
            (LevelKind.ORG_HIGH, gap.high),
            (LevelKind.ORG_LOW, gap.low),
        ):
            levels.append(
                ReferenceLevel(
                    kind=kind,
                    price=price,
                    established_at=None,
                    trading_day=trading_day,
                )
            )

    if session_start is not None and bars:
        high, low = session_extremes_so_far(
            bars, up_to_index=up_to_index, session_start=session_start
        )
        established = bars[up_to_index].open_time_utc
        if high is not None:
            levels.append(
                ReferenceLevel(
                    kind=LevelKind.SESSION_HIGH,
                    price=high,
                    established_at=established,
                    trading_day=trading_day,
                )
            )
        if low is not None:
            levels.append(
                ReferenceLevel(
                    kind=LevelKind.SESSION_LOW,
                    price=low,
                    established_at=established,
                    trading_day=trading_day,
                )
            )

    return tuple(levels)


def raidable(
    levels: Sequence[ReferenceLevel], *, minimum_class: LevelClass
) -> tuple[ReferenceLevel, ...]:
    """The levels worth watching: liquidity pools at or above the
    configured minimum weight. Settlement and the midnight open drop out
    here -- price trades through a reference without anyone's stops being
    taken, which is not a raid."""
    return tuple(
        level
        for level in levels
        if level.is_liquidity() and level.level_class.at_least(minimum_class)
    )
