"""
Kaufman's Efficiency Ratio (ER) -- [VO-D], pulled forward from Phase 15.

ER answers "how cleanly directional was the move over this window": the
net distance travelled divided by the total path length walked to get
there.

    ER = |close[i] - close[i - period]| / sum(|close[j] - close[j-1]|)

0.0 means pure chop -- price wandered a long path and ended where it
started (consolidation-like). 1.0 means a perfectly straight run -- every
step went the same way (clean expansion-like delivery). It is bounded to
[0, 1] by construction (the numerator is one leg of the path the
denominator sums).

WHY IT LIVES HERE, AND WHAT IT IS NOT. ER is Phase 15 material in the
plan (Hurst + Efficiency Ratio), where its formal home is vo.research.
The regime engine (Phase 13) was asked to use it as its confidence /
evidence measure, so a minimal ER computation is pulled forward here --
placed in vo.observation alongside atr.py (same layer, same "VO's own
quantitative instrumentation, not ICT source material" status), not in
vo.research, because vo.observation (layer 3) may not import vo.research
(layer 5). Crucially, per the plan's own non-negotiable split, ER is
INSTRUMENTATION, never a classifier: it may quantify how confident a
structurally-decided regime is, and feed the anticipation lean, but it
never itself decides the regime state and never reaches a trade decision
(G2). Phase 15, when built, may formalize/extend this (Hurst alongside,
windowing choices, its own methodology_version); this is the minimal
version Phase 13 needs.

No lookahead: efficiency_ratio(bars, index, ...) only ever reads
bars[<= index], the same bounded-slice discipline as atr_ticks -- the
caller decides how much history it is shown.

Computed on float close prices (unlike ATR's integer ticks): ER is a
dimensionless ratio, so the tick-integer discipline that matters for
distance/gap measurement buys nothing here, and the ratio is
scale-invariant anyway.
"""

from __future__ import annotations

from collections.abc import Sequence

from vo.market.bar import Bar


def efficiency_ratio(
    bars: Sequence[Bar],
    index: int,
    *,
    period: int,
) -> float | None:
    """
    Kaufman Efficiency Ratio over `period` bars ending at and including
    `index`, on close prices, in [0.0, 1.0].

    None -- never a partial-window value -- when fewer than `period + 1`
    closes exist up to `index` (ER needs the close `period` bars back to
    measure net direction against). Same insufficient-history honesty as
    atr_ticks returning None.

    A perfectly flat window (no movement at all) has zero path length; ER
    is 0.0 there by definition -- no movement is maximally non-directional,
    the consolidation extreme -- rather than an undefined 0/0.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    if index < 0 or index >= len(bars):
        raise IndexError(f"index {index} out of range for {len(bars)} bars")

    if index < period:
        return None

    direction = abs(bars[index].close - bars[index - period].close)
    volatility = 0.0
    for i in range(index - period + 1, index + 1):
        volatility += abs(bars[i].close - bars[i - 1].close)

    if volatility == 0.0:
        return 0.0

    return direction / volatility
