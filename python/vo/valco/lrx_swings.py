"""
The swing adapter -- SwingPoint records in, canonical swing objects out.

WHY AN ADAPTER RATHER THAN READING SwingPoint EVERYWHERE. The user's own
architecture note, and it is right: if the swing source is ever replaced
-- a different algorithm, a tick-based engine, an indicator's own output
-- everything downstream should keep working. Detectors that reach into
SwingPoint's fields directly would all have to change together. So there
is exactly one place that knows SwingPoint's shape, and it is this file.

There is a second, sharper reason. VO must never end up with two systems
computing swings and disagreeing about them. The SwingEngine is the one
of record; nothing here re-derives a pivot, applies its own ATR filter,
or second-guesses a confirmation. This module only re-expresses and
relates what that engine already decided.

THE LOOKAHEAD RULE, WHICH IS THE WHOLE POINT OF `available_at`. A swing
high is not knowable at the moment the high printed. The engine confirms
it K bars later, and SwingPoint records both facts (`pivot_bar_id` and
`confirmed_at_bar_id`). A detector that used the pivot's own timestamp
would be reading the future -- the sweep of a level that "existed" before
anyone could have known it existed. So every canonical swing here carries
`available_at`, and `visible_at()` is the only sanctioned way to ask what
was knowable at a moment. Using `occurred_at` for availability is the bug
this design exists to make hard.

STRUCTURE IS DERIVED HERE, NOT RE-DETECTED. HH/HL/LH/LL come from
comparing consecutive confirmed swings of the same type -- a relation
between engine outputs, not a new opinion about where the pivots are.

EQUAL HIGHS AND LOWS are recorded because they are the liquidity that
matters most: a pair of highs at the same price is a pool of stops in a
way that one high is not. Tolerance is ATR-relative, since "equal" at
20,000 on a quiet morning and on a volatile afternoon are different
distances.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from itertools import pairwise

from vo.observation.swings import SwingLevel, SwingPoint, SwingStatus, SwingType


class StructureLabel(Enum):
    """How one swing relates to the previous swing of the same type.

    UNDEFINED is the honest answer for the first swing of its type, where
    there is nothing to compare against -- not a neutral guess.
    """

    HIGHER_HIGH = "HIGHER_HIGH"
    LOWER_HIGH = "LOWER_HIGH"
    HIGHER_LOW = "HIGHER_LOW"
    LOWER_LOW = "LOWER_LOW"
    EQUAL = "EQUAL"
    UNDEFINED = "UNDEFINED"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CanonicalSwing:
    """One structural pivot, in the vocabulary the detectors speak.

    Deliberately NOT a SwingPoint: this is the seam that lets the swing
    source be replaced. Anything a detector needs appears here, and
    nothing here exposes how the underlying engine found it.
    """

    swing_id: str
    swing_type: SwingType
    level: SwingLevel
    price: float
    body_price: float
    occurred_at: datetime
    available_at: datetime
    """When this swing became KNOWABLE -- the engine's confirmation
    instant, not the moment the extreme printed. Nothing may act on a
    swing before this."""
    status: SwingStatus
    structure: StructureLabel = StructureLabel.UNDEFINED
    equal_with: tuple[str, ...] = ()
    """swing_ids of other swings at effectively the same price -- the
    pool of stops that makes this level worth raiding."""

    @property
    def is_high(self) -> bool:
        return self.swing_type is SwingType.HIGH

    @property
    def external(self) -> bool:
        """SWING-tier pivots are external structure; INTERNAL-tier ones
        are the smaller moves inside it."""
        return self.level is SwingLevel.SWING

    def visible_at(self, moment: datetime) -> bool:
        """Whether this swing may be used at `moment`. The only sanctioned
        availability check -- comparing against occurred_at instead is the
        lookahead bug this exists to prevent."""
        return moment >= self.available_at

    def distance_from(self, price: float) -> float:
        return abs(self.price - price)


def _structure_label(
    swing_type: SwingType, previous_price: float, price: float, tolerance: float
) -> StructureLabel:
    if abs(price - previous_price) <= tolerance:
        return StructureLabel.EQUAL
    higher = price > previous_price
    if swing_type is SwingType.HIGH:
        return StructureLabel.HIGHER_HIGH if higher else StructureLabel.LOWER_HIGH
    return StructureLabel.HIGHER_LOW if higher else StructureLabel.LOWER_LOW


def adapt_swings(
    swings: Sequence[SwingPoint],
    *,
    bar_time_of: dict[str, datetime],
    equal_tolerance: float = 0.0,
) -> tuple[CanonicalSwing, ...]:
    """
    Turn engine output into canonical swings, in confirmation order.

    `bar_time_of` maps a Bar.bar_id to its open time -- supplied by the
    caller because this module has no bar access of its own and should
    not acquire any. A swing whose pivot or confirmation bar is missing
    from the map is dropped rather than given an invented timestamp: a
    swing with a guessed availability is worse than no swing, because it
    silently reintroduces the lookahead this adapter exists to prevent.

    `equal_tolerance` is a price distance -- callers pass an ATR-derived
    figure, since "equal" is not a fixed number of points.
    """
    adapted: list[CanonicalSwing] = []

    for swing in swings:
        occurred = bar_time_of.get(swing.pivot_bar_id)
        available = bar_time_of.get(swing.confirmed_at_bar_id)
        if occurred is None or available is None:
            continue

        adapted.append(
            CanonicalSwing(
                swing_id=swing.object_id,
                swing_type=swing.swing_type,
                level=swing.level,
                price=swing.price,
                body_price=swing.body_price,
                occurred_at=occurred,
                available_at=available,
                status=swing.status,
            )
        )

    adapted.sort(key=lambda s: (s.available_at, s.occurred_at))
    labelled = _label_structure(adapted, equal_tolerance)
    return _mark_equals(labelled, equal_tolerance)


def _label_structure(
    swings: Sequence[CanonicalSwing], tolerance: float
) -> list[CanonicalSwing]:
    """HH/HL/LH/LL, comparing each swing with the previous one of the SAME
    type. Comparing a high against a low would be meaningless."""
    out = list(swings)
    for swing_type in (SwingType.HIGH, SwingType.LOW):
        of_type = [
            (position, swing)
            for position, swing in enumerate(out)
            if swing.swing_type is swing_type
        ]
        for (_, earlier), (position, later) in pairwise(of_type):
            out[position] = CanonicalSwing(
                swing_id=later.swing_id,
                swing_type=later.swing_type,
                level=later.level,
                price=later.price,
                body_price=later.body_price,
                occurred_at=later.occurred_at,
                available_at=later.available_at,
                status=later.status,
                structure=_structure_label(
                    swing_type, earlier.price, later.price, tolerance
                ),
                equal_with=later.equal_with,
            )
    return out


def _mark_equals(
    swings: Sequence[CanonicalSwing], tolerance: float
) -> tuple[CanonicalSwing, ...]:
    """Record which swings sit at effectively the same price as which
    others. Equal highs are a stop pool in a way a single high is not."""
    if tolerance <= 0:
        return tuple(swings)

    out: list[CanonicalSwing] = []
    for swing in swings:
        peers = tuple(
            other.swing_id
            for other in swings
            if other.swing_id != swing.swing_id
            and other.swing_type is swing.swing_type
            and abs(other.price - swing.price) <= tolerance
        )
        out.append(
            CanonicalSwing(
                swing_id=swing.swing_id,
                swing_type=swing.swing_type,
                level=swing.level,
                price=swing.price,
                body_price=swing.body_price,
                occurred_at=swing.occurred_at,
                available_at=swing.available_at,
                status=swing.status,
                structure=swing.structure,
                equal_with=peers,
            )
        )
    return tuple(out)


def visible_swings(
    swings: Sequence[CanonicalSwing], *, at: datetime, active_only: bool = True
) -> tuple[CanonicalSwing, ...]:
    """Every swing knowable at `at`. `active_only` drops BROKEN swings --
    a level price has already traded through is not liquidity waiting to
    be taken."""
    return tuple(
        swing
        for swing in swings
        if swing.visible_at(at)
        and (not active_only or swing.status is SwingStatus.CONFIRMED)
    )


def most_recent_opposing(
    swings: Sequence[CanonicalSwing],
    *,
    at: datetime,
    raid_was_buy_side: bool,
) -> CanonicalSwing | None:
    """The swing a market-structure shift would have to break, after a
    raid.

    After a BUY-side raid (price spiked above a high and failed), a
    bearish shift means breaking the most recent confirmed swing LOW.
    The inverse for a sell-side raid. Naming it here rather than in the
    MSS detector keeps "which swing is relevant" a property of the
    structure map rather than a rule each detector re-invents.
    """
    wanted = SwingType.LOW if raid_was_buy_side else SwingType.HIGH
    candidates = [
        swing
        for swing in visible_swings(swings, at=at)
        if swing.swing_type is wanted
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.available_at)
