"""
Displacement -- did the raid produce measurable opposing expansion?

    Raid -> [DISPLACEMENT] -> MSS -> Inefficiency -> Rebalance -> Objective

ONE QUESTION, AND NOT THE NEXT ONE. This detector answers "how far, how
fast, how cleanly did price move away from the swept level" and stops
there. It does NOT decide whether structure broke -- that is the MSS
detector's question, and it needs the confirmed swing map to answer it.
Keeping them apart is what makes raid, expansion and structural break
three independently testable events rather than one opaque verdict. A
displacement that moved 1.7 ATR and created a gap is a fact worth having
whether or not an MSS followed.

MEASUREMENTS ARE ALWAYS COMPUTED, INCLUDING ON FAILURE. A binary
"displaced: true/false" throws away exactly the data needed to ask
whether the threshold is in the right place. So a DisplacementEvent is
produced for every evaluated leg, carrying its full measurement set, and
`qualification` is a separate field with a stated reason. A run of FAILs
with range_atr around 1.4 against a 1.5 threshold is a finding; a run of
Falses is not.

QUALIFICATION IS A CONJUNCTION, and each clause exists for a reason:
  - range_atr: the move must be large relative to current volatility,
    not large in points. A fixed point threshold silently tightens in a
    quiet session and loosens in a violent one.
  - body_ratio: a bar that travelled far and closed mid-range is
    indecision wearing a big range. Bodies are what displacement means.
  - net_move_atr: total distance covered net of retracement, so a leg
    that went up 2 ATR and came back 1.8 does not qualify on range alone.

FVG PRESENCE, NOT THE FVG OBJECT. `fvg_created` is a boolean derived
from three-bar geometry, because "did this expansion leave an
inefficiency" is part of judging the expansion. The full inefficiency --
type, CE, mitigation, invalidation -- belongs to the Inefficiency engine,
which will consume `three_bar_gap` from here rather than re-deriving the
geometry a second time.

NO LOOKAHEAD. A leg is measured from the sweep's return bar to `index`,
reading bars[<= index] only. Evaluating at successive indices is how the
caller sees several candidate expansions develop from one raid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.valco.lrx_levels import LevelSide
from vo.valco.lrx_sweep import SweepEvent


class ExpansionDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"

    def __str__(self) -> str:
        return self.value


class Qualification(Enum):
    PASS = "PASS"
    FAIL = "FAIL"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class GapGeometry:
    """A three-bar inefficiency's bounds. Presence and shape only -- the
    Inefficiency engine owns lifecycle (mitigation, invalidation)."""

    upper: float
    lower: float
    created_index: int

    @property
    def size(self) -> float:
        return self.upper - self.lower

    @property
    def consequent_encroachment(self) -> float:
        """The midpoint -- CE in ICT's vocabulary."""
        return (self.upper + self.lower) / 2.0


def three_bar_gap(
    bars: Sequence[Bar], index: int, direction: ExpansionDirection
) -> GapGeometry | None:
    """The classic three-bar gap at `index`, where bar index-2 and bar
    index do not overlap in the direction of travel.

    Shared deliberately: the Inefficiency engine consumes this rather
    than deriving the same geometry a second time, so the two can never
    disagree about whether a gap exists.
    """
    if index < 2 or index >= len(bars):
        return None

    first, third = bars[index - 2], bars[index]

    if direction is ExpansionDirection.UP:
        if third.low > first.high:
            return GapGeometry(upper=third.low, lower=first.high, created_index=index)
        return None

    if third.high < first.low:
        return GapGeometry(upper=first.low, lower=third.high, created_index=index)
    return None


@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    """One measured expansion leg following a raid.

    Produced whether or not it qualifies -- see the module docstring on
    why a FAIL carrying its numbers is worth more than a False.
    """

    sweep_level_kind: str
    direction: ExpansionDirection
    start_index: int
    end_index: int
    start_at_utc: datetime
    end_at_utc: datetime
    start_price: float
    end_price: float
    high: float
    low: float
    range_points: float
    range_atr: float
    body_points: float
    body_ratio: float
    net_move_points: float
    net_move_atr: float
    bar_count: int
    elapsed_seconds: float
    velocity_atr_per_minute: float
    max_favorable_excursion: float
    close_location: float
    consecutive_directional_bars: int
    gap: GapGeometry | None
    expansion_origin: float
    expansion_equilibrium: float
    qualification: Qualification
    qualification_reason: str

    @property
    def qualified(self) -> bool:
        return self.qualification is Qualification.PASS

    @property
    def fvg_created(self) -> bool:
        return self.gap is not None


def _close_location(direction: ExpansionDirection, close: float, high: float, low: float) -> float:
    """Where the leg's final close sits in its range: 1.0 means it closed
    at the favourable extreme, 0.0 at the adverse one. A displacement
    that closes mid-range travelled without committing."""
    span = high - low
    if span <= 0:
        return 0.5
    if direction is ExpansionDirection.UP:
        return (close - low) / span
    return (high - close) / span


def _consecutive_directional(bars: Sequence[Bar], direction: ExpansionDirection) -> int:
    """Longest run of bars closing in the direction of travel."""
    best = 0
    current = 0
    for bar in bars:
        went_our_way = (
            bar.close > bar.open
            if direction is ExpansionDirection.UP
            else bar.close < bar.open
        )
        current = current + 1 if went_our_way else 0
        best = max(best, current)
    return best


def measure_displacement(
    bars: Sequence[Bar],
    index: int,
    sweep: SweepEvent,
    *,
    min_atr_multiple: float,
    min_body_ratio: float,
    min_net_move_atr: float,
    max_bars: int,
    atr_period: int,
    tick_size: float,
) -> DisplacementEvent | None:
    """
    Measure the expansion leg running from the raid's return bar to
    `index`, and say whether it qualifies.

    Returns None only when the leg cannot be measured at all -- `index`
    is at or before the raid, the leg is longer than `max_bars`, or ATR
    is unavailable. A measurable-but-weak leg comes back as a FAIL with
    its numbers, never as None: that distinction is the whole point.
    """
    start = sweep.returned_index
    if index <= start or index >= len(bars):
        return None
    if index - start > max_bars:
        return None

    atr = atr_ticks(bars, index, period=atr_period, tick_size=tick_size)
    if atr is None:
        return None
    atr_price = atr * tick_size
    if atr_price <= 0:
        return None

    # A buy-side raid (price spiked above a high and failed) sets up a
    # move DOWN -- the counter-expansion this model trades.
    direction = (
        ExpansionDirection.DOWN
        if sweep.side is LevelSide.BUY_SIDE
        else ExpansionDirection.UP
    )

    leg = bars[start : index + 1]
    first, last = leg[0], leg[-1]

    high = max(bar.high for bar in leg)
    low = min(bar.low for bar in leg)
    range_points = high - low

    start_price = first.open
    end_price = last.close
    net_move_points = (
        end_price - start_price
        if direction is ExpansionDirection.UP
        else start_price - end_price
    )

    body_points = sum(abs(bar.close - bar.open) for bar in leg)
    total_range = sum(bar.high - bar.low for bar in leg)
    body_ratio = body_points / total_range if total_range > 0 else 0.0

    elapsed_seconds = (last.open_time_utc - first.open_time_utc).total_seconds()
    elapsed_minutes = elapsed_seconds / 60.0
    net_move_atr = net_move_points / atr_price
    velocity = net_move_atr / elapsed_minutes if elapsed_minutes > 0 else 0.0

    mfe = (
        high - start_price
        if direction is ExpansionDirection.UP
        else start_price - low
    )

    gap: GapGeometry | None = None
    for probe in range(start + 2, index + 1):
        found = three_bar_gap(bars, probe, direction)
        if found is not None:
            gap = found
            break

    range_atr = range_points / atr_price

    reasons: list[str] = []
    if range_atr < min_atr_multiple:
        reasons.append(f"range {range_atr:.2f} ATR below {min_atr_multiple:.2f}")
    if body_ratio < min_body_ratio:
        reasons.append(f"body ratio {body_ratio:.2f} below {min_body_ratio:.2f}")
    if net_move_atr < min_net_move_atr:
        reasons.append(f"net move {net_move_atr:.2f} ATR below {min_net_move_atr:.2f}")

    qualification = Qualification.FAIL if reasons else Qualification.PASS
    reason = "; ".join(reasons) if reasons else "range, body and net move all met"

    # Origin is where the leg began -- the raid's own extreme, which is
    # the price the reversal is measured away from. Equilibrium is its
    # midpoint against the leg's far extreme.
    origin = sweep.penetration_price
    far = low if direction is ExpansionDirection.DOWN else high
    equilibrium = (origin + far) / 2.0

    return DisplacementEvent(
        sweep_level_kind=str(sweep.level.kind),
        direction=direction,
        start_index=start,
        end_index=index,
        start_at_utc=first.open_time_utc,
        end_at_utc=last.open_time_utc,
        start_price=start_price,
        end_price=end_price,
        high=high,
        low=low,
        range_points=range_points,
        range_atr=range_atr,
        body_points=body_points,
        body_ratio=body_ratio,
        net_move_points=net_move_points,
        net_move_atr=net_move_atr,
        bar_count=len(leg),
        elapsed_seconds=elapsed_seconds,
        velocity_atr_per_minute=velocity,
        max_favorable_excursion=mfe,
        close_location=_close_location(direction, end_price, high, low),
        consecutive_directional_bars=_consecutive_directional(leg, direction),
        gap=gap,
        expansion_origin=origin,
        expansion_equilibrium=equilibrium,
        qualification=qualification,
        qualification_reason=reason,
    )
