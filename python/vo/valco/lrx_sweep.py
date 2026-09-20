"""
Liquidity raid detection -- the first step of the sequence.

    Liquidity Raid -> Displacement -> MSS -> FVG Retracement -> Draw on Liquidity

WHAT A RAID IS NOT. The spec is emphatic about this and it is the single
most important rule in the module: a sweep is NOT

    high > previous_high

Price trades above prior highs constantly in an uptrend, and every one
of those would be a "sweep" under that definition. What distinguishes a
raid is that price reaches beyond the level, FAILS to hold there, and
comes back. Trading through and continuing is not a raid -- it is a
breakout, which is close to the opposite thing.

So a raid here requires three facts, and records the evidence for each:

  1. PENETRATION. Price traded beyond the level by at least
     `min_penetration_atr` x ATR. ATR-relative rather than a fixed point
     count, so the threshold means the same thing in a quiet open and a
     volatile afternoon -- a fixed number silently tightens and loosens
     as volatility moves.
  2. REJECTION. Price returned back inside the level within
     `return_max_bars`. This is the failure that makes it a raid.
  3. (Not here.) Opposing displacement -- that is the NEXT stage's
     question, and keeping it there is what lets a raid be recorded and
     studied even when no displacement followed. A raid that led
     nowhere is data; folding the requirement in here would delete it.

CANDIDATE, NOT VERDICT. This module reports a SweepEvent when 1 and 2
hold. Whether that raid becomes a trade depends on everything
downstream. The separation matters for research: the spec wants to
measure which components actually predict outcome (section 27), which is
impossible if a component silently filters before it is measured.

NO LOOKAHEAD. Detection at bar i reads bars[<= i] only. A raid is
confirmed on the bar where price returns inside -- never backdated to
the penetration bar, which would be knowing the outcome before it
happened.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.valco.lrx_levels import LevelSide, ReferenceLevel


@dataclass(frozen=True, slots=True)
class SweepEvent:
    """One completed liquidity raid, with the evidence that made it one.

    Every field the spec's section 4 asks to be recorded, so a later
    study can ask which properties of a raid mattered rather than
    re-deriving them from bars.
    """

    sweep_id: str
    """Stable identity for this raid. Every downstream event carries it,
    so a displacement, an MSS, an FVG and a trade can all be attributed
    back to the raid that started them (spec sections 3 and 39). Without
    it the research log records what happened but not what caused what."""
    level: ReferenceLevel
    side: LevelSide
    penetration_index: int
    penetration_price: float
    penetration_distance: float
    penetration_atr_multiple: float
    closed_beyond: bool
    returned_index: int
    returned_at_utc: datetime
    bars_beyond: int

    @property
    def expected_reversal_side(self) -> LevelSide:
        """Which way a reversal off this raid would go. A buy-side raid
        (price spiked above a high and failed) sets up a move DOWN, so
        the objective is sell-side liquidity. This is the counter-
        expansion direction the whole model trades."""
        return (
            LevelSide.SELL_SIDE
            if self.side is LevelSide.BUY_SIDE
            else LevelSide.BUY_SIDE
        )


def _beyond(level_price: float, side: LevelSide, bar: Bar) -> float:
    """How far this bar reached past the level, in price. Zero when it
    did not reach past at all."""
    if side is LevelSide.BUY_SIDE:
        return max(0.0, bar.high - level_price)
    return max(0.0, level_price - bar.low)


def _closed_beyond(level_price: float, side: LevelSide, bar: Bar) -> bool:
    if side is LevelSide.BUY_SIDE:
        return bar.close > level_price
    return bar.close < level_price


def _back_inside(level_price: float, side: LevelSide, bar: Bar) -> bool:
    """Price has returned inside the level -- measured on the CLOSE, not
    a wick. A wick back inside while the body stays beyond has not
    rejected anything yet."""
    if side is LevelSide.BUY_SIDE:
        return bar.close < level_price
    return bar.close > level_price


def detect_sweep(
    bars: Sequence[Bar],
    index: int,
    level: ReferenceLevel,
    *,
    min_penetration_atr: float,
    return_max_bars: int,
    atr_period: int,
    tick_size: float,
    require_close_beyond: bool = False,
) -> SweepEvent | None:
    """
    Whether a raid of `level` COMPLETED at `index` -- that is, whether
    this is the bar on which price came back inside after having reached
    far enough beyond.

    Returns None when no raid completed here, which is the overwhelmingly
    common answer. Called per level per bar.
    """
    if index < 1 or index >= len(bars):
        return None
    if not level.is_liquidity():
        return None

    side = level.side
    current = bars[index]

    # This bar must be the RETURN. If price is still beyond the level,
    # the raid has not completed -- it may still become a breakout.
    if not _back_inside(level.price, side, current):
        return None

    atr = atr_ticks(bars, index, period=atr_period, tick_size=tick_size)
    if atr is None:
        return None
    atr_price = atr * tick_size
    if atr_price <= 0:
        return None

    # Walk back over the run of bars that were beyond the level, bounded
    # by return_max_bars. The raid is whatever happened in that run.
    best_distance = 0.0
    best_index = index
    best_price = current.high if side is LevelSide.BUY_SIDE else current.low
    closed_beyond = False
    bars_beyond = 0

    # Walk back over the run of bars that were beyond the level. The
    # budget is return_max_bars + 1: if the run is still going after
    # return_max_bars bars, price stayed outside too long and this is a
    # failed breakout, not a raid. Truncating the walk at the budget
    # instead would silently reclassify every long excursion as a raid --
    # exactly backwards.
    excursion_too_long = False

    for offset in range(1, return_max_bars + 2):
        probe = index - offset
        if probe < 0:
            break
        bar = bars[probe]

        # A level established after this bar cannot have been raided by
        # it -- the level did not exist yet.
        if level.established_at is not None and bar.open_time_utc < level.established_at:
            break

        distance = _beyond(level.price, side, bar)
        if distance <= 0.0:
            break

        if offset > return_max_bars:
            excursion_too_long = True
            break

        bars_beyond += 1
        if distance > best_distance:
            best_distance = distance
            best_index = probe
            best_price = bar.high if side is LevelSide.BUY_SIDE else bar.low
        if _closed_beyond(level.price, side, bar):
            closed_beyond = True

    if bars_beyond == 0 or excursion_too_long:
        return None

    if require_close_beyond and not closed_beyond:
        return None

    multiple = best_distance / atr_price
    if multiple < min_penetration_atr:
        return None

    return SweepEvent(
        sweep_id=(
            f"SWEEP:{level.kind}:{level.price:.2f}:"
            f"{current.open_time_utc.isoformat()}"
        ),
        level=level,
        side=side,
        penetration_index=best_index,
        penetration_price=best_price,
        penetration_distance=best_distance,
        penetration_atr_multiple=multiple,
        closed_beyond=closed_beyond,
        returned_index=index,
        returned_at_utc=current.open_time_utc,
        bars_beyond=bars_beyond,
    )


def detect_sweeps(
    bars: Sequence[Bar],
    index: int,
    levels: Sequence[ReferenceLevel],
    *,
    min_penetration_atr: float,
    return_max_bars: int,
    atr_period: int,
    tick_size: float,
    require_close_beyond: bool = False,
) -> tuple[SweepEvent, ...]:
    """Every raid completing at `index`, across all watched levels.

    More than one can complete on the same bar -- clustered levels get
    taken together, and which one "counts" is the objective engine's
    question, not this one's. Reporting all of them keeps that decision
    downstream where it can be measured.
    """
    found = [
        detect_sweep(
            bars,
            index,
            level,
            min_penetration_atr=min_penetration_atr,
            return_max_bars=return_max_bars,
            atr_period=atr_period,
            tick_size=tick_size,
            require_close_beyond=require_close_beyond,
        )
        for level in levels
    ]
    return tuple(event for event in found if event is not None)
