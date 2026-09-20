"""
Inefficiency -- the Fair Value Gap left behind by a displacement.

    Raid -> Displacement -> MSS -> BOS -> [Inefficiency] -> Rebalance

A THREE-CANDLE GAP AND NOTHING ELSE. No order blocks, breakers, inverted
FVGs, liquidity voids, volume imbalance, BISI/SIBI as separate logic, OTE
or Silver Bullet. Those are research variants and each one added here
before the baseline is measured would make it impossible to say what the
baseline was.

WHAT THIS MODULE DOES NOT DO: decide anything. It does not choose an
entry, a stop or a target, does not judge whether an FVG is good, and
does not know the daily trade cap exists. It reports that an
inefficiency is present and what shape it has. The Rebalance and Setup
layers decide; conflating detection with decision is what makes a
backtest unable to answer which of the two was wrong.

CE IS A MEASUREMENT, NOT AN INSTRUCTION. `ce_price` is the gap's
midpoint. It is emphatically not "enter here" -- that reading belongs to
the Rebalance layer, which may or may not choose CE as its entry model.

LINEAGE IS THE POINT. The question this module exists to make answerable
is "which inefficiency did THIS displacement create?", answered by
following an id rather than searching backwards for whichever nearby gap
makes a setup work. A gap is attributed to a displacement only when its
three candles lie INSIDE that displacement's leg -- the leg physically
contains the sequence that produced it. Anything else is a raw candidate
with `creator_displacement_id = None`. A loose "nearest displacement"
heuristic would populate the field while destroying the only thing it
was for.

GEOMETRY IS IMMUTABLE ONCE CREATED. Bounds, size, CE, direction,
creation time and lineage are fixed at creation. Later mitigation and
invalidation are a separate lifecycle concern and must never rewrite
what was measured -- an FVG whose recorded size changes when price
touches it cannot support any statistic computed over it.

TIMING. The gap is knowable only once the third candle is complete: its
low (bullish) or high (bearish) is not final until then. `creation_time`
is that third bar's label, following the convention displacement and MSS
already use -- a bar is named by its open time and its facts are
knowable at its close. Nothing may consume an FVG before that bar
completes, and the first two candles cannot produce one on their own.

GEOMETRY IS SHARED, NOT RE-DERIVED. `three_bar_gap` in lrx_displacement
is the one definition of the bounds; this module consumes it, so the
displacement's own `gap` field and an emitted FVG can never disagree
about whether a gap exists or where it sits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.valco.lrx_displacement import (
    DisplacementEvent,
    ExpansionDirection,
    GapGeometry,
    three_bar_gap,
)
from vo.valco.lrx_mss import MssEvent


class InefficiencyDirection(Enum):
    """Which way the gap points, taken from the three-candle structure
    itself and never inferred from context.

    A BULLISH gap is an unfilled area ABOVE candle 1's high -- it says
    price left a void moving up. It does NOT say "go long": an
    inefficiency is an object, not a signal, and the same gap is a
    retracement target for one strategy and a continuation area for
    another.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    def __str__(self) -> str:
        return self.value


class InefficiencyQualification(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNMEASURABLE = "UNMEASURABLE"
    """A configured threshold needed a value this gap could not produce
    -- an ATR-relative minimum with no ATR available. Distinct from FAIL:
    the threshold was never tested, which is not the same as the gap
    falling short of it."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class InefficiencyConfig:
    """Baseline ships with both thresholds off, per the spec: a gap is a
    gap. Thresholds exist so they can be turned on and MEASURED, not so
    the baseline can be quietly filtered."""

    min_size_points: float = 0.0
    min_size_atr: float = 0.0
    atr_period: int = 14

    @property
    def filters_anything(self) -> bool:
        return self.min_size_points > 0.0 or self.min_size_atr > 0.0


@dataclass(frozen=True, slots=True)
class InefficiencyEvent:
    """One three-candle gap, as measured at the moment it became
    knowable.

    Every field here is fixed for the life of the object. Touch,
    mitigation, fill and invalidation are a later lifecycle layer and get
    their own record; they do not edit this one.
    """

    inefficiency_id: str
    direction: InefficiencyDirection
    qualification: InefficiencyQualification
    rejection_reason: str | None

    creation_time: datetime
    """The third bar's label. A bar is named by its open time and its
    facts are knowable at its close, so nothing may consume this event
    before that bar completes -- the same convention displacement and
    MSS use."""
    first_bar_time: datetime
    first_index: int
    third_index: int

    lower_price: float
    upper_price: float
    ce_price: float
    """The gap's midpoint. A MEASUREMENT. Not an entry instruction --
    that reading belongs to the Rebalance layer."""
    size_points: float
    size_atr: float | None
    """None when ATR is unavailable at the creation bar -- NOT 0.0,
    which would be indistinguishable from a gap of no size, and an
    emitted gap always has size."""

    creator_displacement_id: str | None
    """The displacement whose leg CONTAINS these three candles, or None
    for a raw candidate. Never a nearest-neighbour guess."""
    creator_mss_id: str | None
    creator_sweep_id: str | None

    @property
    def attributed(self) -> bool:
        """Whether this gap has defensible lineage. A research run that
        mixes attributed and raw gaps without separating them is not
        testing the model the chain describes."""
        return self.creator_displacement_id is not None

    @property
    def qualified(self) -> bool:
        return self.qualification is InefficiencyQualification.PASS

    @property
    def confirmation_at_utc(self) -> datetime:
        return self.creation_time

    @property
    def event_at_utc(self) -> datetime:
        return self.creation_time

    def contains(self, price: float) -> bool:
        """Whether a price sits inside the gap. Read-only geometry; it
        records nothing and mutates nothing."""
        return self.lower_price <= price <= self.upper_price


def _direction_of(expansion: ExpansionDirection) -> InefficiencyDirection:
    return (
        InefficiencyDirection.BULLISH
        if expansion is ExpansionDirection.UP
        else InefficiencyDirection.BEARISH
    )


def displacement_contains_gap(
    displacement: DisplacementEvent, gap: GapGeometry
) -> bool:
    """Whether the leg physically contains the three candles that made
    this gap.

    The gap's candles are `created_index - 2` through `created_index`.
    All three must lie within the leg's own span for the displacement to
    be the thing that created it. This is the whole attribution rule --
    deliberately structural, deliberately not "closest in time".
    """
    return (
        displacement.start_index <= gap.created_index - 2
        and gap.created_index <= displacement.end_index
    )


def _qualify(
    config: InefficiencyConfig, *, size_points: float, size_atr: float | None
) -> tuple[InefficiencyQualification, str | None]:
    if config.min_size_atr > 0.0 and size_atr is None:
        return (
            InefficiencyQualification.UNMEASURABLE,
            "ATR-relative minimum configured but ATR unavailable at creation",
        )
    failures: list[str] = []
    if config.min_size_points > 0.0 and size_points < config.min_size_points:
        failures.append(
            f"size {size_points:.2f} below minimum {config.min_size_points:.2f}"
        )
    if (
        config.min_size_atr > 0.0
        and size_atr is not None
        and size_atr < config.min_size_atr
    ):
        failures.append(
            f"size {size_atr:.2f} ATR below minimum {config.min_size_atr:.2f}"
        )
    if failures:
        return InefficiencyQualification.FAIL, "; ".join(failures)
    return InefficiencyQualification.PASS, None


def detect_inefficiency(
    bars: Sequence[Bar],
    index: int,
    direction: ExpansionDirection,
    config: InefficiencyConfig,
    *,
    tick_size: float,
    displacement: DisplacementEvent | None = None,
    mss: MssEvent | None = None,
) -> InefficiencyEvent | None:
    """
    The three-candle gap completed at `index`, or None if there is none.

    `index` is the THIRD candle: nothing here can see past it, and bars
    beyond it are never read. Passing a displacement offers lineage --
    granted only if that displacement's leg contains all three candles,
    refused silently otherwise, leaving a raw candidate. An `mss` is
    recorded only alongside a granted displacement, and only when the two
    already agree about which displacement they belong to.

    Rejected candidates are still returned, carrying every measurement
    that was calculable: a gap that failed a size threshold is a finding,
    and erasing its numbers would hide every near miss.
    """
    gap = three_bar_gap(bars, index, direction)
    if gap is None:
        return None

    # three_bar_gap uses strict inequalities, so an exact touch is not a
    # gap. Asserted rather than assumed: this is the definition's edge
    # and it must not soften if that helper changes.
    size_points = gap.upper - gap.lower
    if size_points <= 0.0:
        return None

    atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    atr_price = atr * tick_size if atr is not None else None
    size_atr = (
        size_points / atr_price if atr_price is not None and atr_price > 0 else None
    )

    creator_displacement_id: str | None = None
    creator_sweep_id: str | None = None
    creator_mss_id: str | None = None
    if displacement is not None and displacement_contains_gap(displacement, gap):
        creator_displacement_id = displacement.displacement_id
        creator_sweep_id = displacement.sweep_id
        if mss is not None and mss.displacement_id == displacement.displacement_id:
            creator_mss_id = mss.mss_id

    qualification, reason = _qualify(
        config, size_points=size_points, size_atr=size_atr
    )
    third = bars[index]

    return InefficiencyEvent(
        inefficiency_id=f"FVG:{third.open_time_utc.isoformat()}:{index}",
        direction=_direction_of(direction),
        qualification=qualification,
        rejection_reason=reason,
        creation_time=third.open_time_utc,
        first_bar_time=bars[index - 2].open_time_utc,
        first_index=index - 2,
        third_index=index,
        lower_price=gap.lower,
        upper_price=gap.upper,
        ce_price=gap.consequent_encroachment,
        size_points=size_points,
        size_atr=size_atr,
        creator_displacement_id=creator_displacement_id,
        creator_mss_id=creator_mss_id,
        creator_sweep_id=creator_sweep_id,
    )


def detect_inefficiencies_in_leg(
    bars: Sequence[Bar],
    displacement: DisplacementEvent,
    config: InefficiencyConfig,
    *,
    tick_size: float,
    mss: MssEvent | None = None,
) -> tuple[InefficiencyEvent, ...]:
    """Every gap the displacement's own leg created, oldest first.

    A leg can leave more than one. Returning all of them keeps the choice
    of which matters with the Setup layer instead of hiding it in a
    detector that silently picked the first or the biggest.
    """
    events: list[InefficiencyEvent] = []
    for index in range(displacement.start_index + 2, displacement.end_index + 1):
        event = detect_inefficiency(
            bars,
            index,
            displacement.direction,
            config,
            tick_size=tick_size,
            displacement=displacement,
            mss=mss,
        )
        if event is not None:
            events.append(event)
    return tuple(events)
