"""
Efficiency Ratio paired with ATR -- the entry-timing measurement, pulled
OUT of the regime engine's evidence bundle and given its own home.

WHY THIS IS SEPARATE FROM THE REGIME ENGINE. ER and Hurst live on
RegimeState.supporting_features as recorded evidence for a
classification, and that is the right place for them in that role. But
architecture/vo-trade-logic-and-brain-plan.md Section 5.7 gave ER a
SECOND and different job, at the user's own direction: entry timing,
considered relative to ATR, once a regime change is already in effect --
never bias, never setup selection. Two jobs sharing one home means every
change made for one perturbs the other, which is exactly the entanglement
that made VO_Regime and VO_ReferenceLevels worth splitting apart. So the
entry-timing reading gets its own module, its own record type and (in
due course) its own indicator.

WHAT THIS DELIBERATELY DOES NOT DO: decide anything. Section 5.7 names
the ingredients -- ER relative to ATR -- and stops there; no threshold,
ratio, or trigger rule has been designed, and none is invented here. An
EntryTimingReading records what both instruments said at one bar, plus
where each sat in its own recent distribution, and that is all. The rule
that eventually reads these is a separate, reviewable decision (G6), and
building the measurement first is what lets that rule be designed against
real distributions instead of guesses.

Both underlying functions are reused unchanged --
vo.observation.efficiency_ratio.efficiency_ratio and
vo.observation.atr.atr_ticks. Nothing is re-implemented here, so there
is no second copy to drift.

No lookahead: every value reads bars[<= index] only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.observation.efficiency_ratio import efficiency_ratio


@dataclass(frozen=True, slots=True)
class EntryTimingReading:
    """ER and ATR at one bar, plus each one's rank within its own recent
    history.

    The percentile fields exist because a raw ER of 0.31 means nothing on
    its own -- it is only interpretable against what ER has been doing
    lately on this instrument, and the same is true of ATR. Recording the
    rank alongside the level is what makes a later threshold rule
    designable in relative terms rather than as a magic number that
    silently rots when volatility regime changes.

    Any field is None when its own window had insufficient history --
    never a partial-window guess, matching efficiency_ratio/atr_ticks'
    own honesty.
    """

    index: int
    efficiency_ratio: float | None
    atr_ticks: int | None
    efficiency_ratio_percentile: float | None
    atr_percentile: float | None

    @property
    def complete(self) -> bool:
        """Whether both instruments produced a value at this bar. A caller
        deciding anything should require this rather than treating a
        missing reading as a low one."""
        return self.efficiency_ratio is not None and self.atr_ticks is not None


def _percentile_rank(values: Sequence[float], current: float) -> float | None:
    """Fraction of `values` at or below `current`, in [0.0, 1.0]. None for
    an empty window -- a rank against nothing is not a rank."""
    if not values:
        return None
    at_or_below = sum(1 for value in values if value <= current)
    return at_or_below / len(values)


def measure_entry_timing(
    bars: Sequence[Bar],
    index: int,
    *,
    er_period: int,
    atr_period: int,
    tick_size: float,
    percentile_lookback: int,
) -> EntryTimingReading:
    """
    One paired ER/ATR reading at `index`, with each value ranked against
    its own previous `percentile_lookback` readings.

    The lookback walks backwards recomputing each instrument at earlier
    indices rather than caching, which is O(lookback) per call --
    acceptable for a per-bar measurement and deliberately simple, since
    a cache here would be one more piece of state to get wrong. If this
    ever sits on a hot path, cache it there, not in the measurement.
    """
    if index < 0 or index >= len(bars):
        raise IndexError(f"index {index} out of range for {len(bars)} bars")
    if percentile_lookback < 0:
        raise ValueError(f"percentile_lookback cannot be negative, got {percentile_lookback}")

    current_er = efficiency_ratio(bars, index, period=er_period)
    current_atr = atr_ticks(bars, index, period=atr_period, tick_size=tick_size)

    past_er: list[float] = []
    past_atr: list[float] = []
    first = max(0, index - percentile_lookback)
    for earlier in range(first, index):
        value_er = efficiency_ratio(bars, earlier, period=er_period)
        if value_er is not None:
            past_er.append(value_er)
        value_atr = atr_ticks(bars, earlier, period=atr_period, tick_size=tick_size)
        if value_atr is not None:
            past_atr.append(float(value_atr))

    return EntryTimingReading(
        index=index,
        efficiency_ratio=current_er,
        atr_ticks=current_atr,
        efficiency_ratio_percentile=(
            None if current_er is None else _percentile_rank(past_er, current_er)
        ),
        atr_percentile=(
            None if current_atr is None else _percentile_rank(past_atr, float(current_atr))
        ),
    )
