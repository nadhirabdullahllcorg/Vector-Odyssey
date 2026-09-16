"""
Swing <-> previous-period reference-level distance -- [VO-D], measured,
not named.

Composes two already-canonical, already-approved measurement sources --
Phase 7's `PeriodOHLC` (`vo.market.levels` / `vo.time.levels.
ReferenceLevelEngine`) and Phase 11's `SwingPoint` (`vo.observation.
swings`) -- into a joint, purely arithmetic measurement: how far, in
ticks, is a confirmed swing's price from each of the previous day/week/
month's high/low/open/close (PDH/PDL/PDO/PDC, PWH/.../PMC -- generic
technical-analysis abbreviations, not an ICT- or VO-specific term).

Nothing here interprets that distance. In particular this is
deliberately NOT Phase 23's future "Liquidity Run Engine -- approach,
interaction, sweep, response" (vo-phase-plan.md's own words for that
phase's gate) -- no field here is named `approach`, `run`,
`interaction`, `sweep`, or `reaction`. `LevelPosition` records a purely
geometric fact (is this level on the far side of the swing's own price,
in the swing's own directional sense, or the near side, or exactly at
it) -- the same "measured, not named" discipline Phase 7 already applied
to the ORG question (`AnchorComparison`). Whatever strategy meaning this
distance/position combination deserves, if any, is Phase 21's
(Liquidity Reference Engine) or Phase 23's to decide, once either
exists -- this module only ever answers "how far, and which side,"
never "is this significant" or "is price approaching it."

Regime-conditioned significance (e.g. a different "meaningful distance"
in EXPANSION vs CONSOLIDATION) is explicitly deferred to Phase 13's
Basic Regime Engine, which does not exist yet -- see vo-phase-plan.md
Phase 13 / Section 9's open items. Nothing here invents a regime concept
early, and nothing here is a `[VO-H]` score -- every value is an exact
measurement, not a guess needing G2 promotion.

Layering: `vo.observation` (3) may import `vo.market` (1) and `vo.time`
(2); this module does, for `to_ticks`/`PeriodOHLC` and
`ReferenceLevelEngine` respectively.

NOT WIRED IN YET: nothing calls `swing_reference_distances` from
`ObservationPipeline` or anywhere else in the running `VO_EA` process --
that integration, like `SwingEngine`'s own, is left to whichever later
phase already owns connecting `vo.observation` to the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum, auto

from vo.market.tickmath import to_ticks
from vo.observation.swings import SwingPoint, SwingType
from vo.time.levels import ReferenceLevelEngine


class LevelPosition(Enum):
    """Purely geometric, relative to the swing's own directional sense
    (up for a HIGH pivot, down for a LOW pivot). Not Phase 23's future
    approach/interaction/sweep vocabulary -- see module docstring."""

    AHEAD = auto()
    """The level sits on the far side of the swing's own price, in the
    direction price moved to build the pivot -- price would have to
    continue further, in that same direction, to reach it."""
    BEHIND = auto()
    """The level sits on the near side -- price already moved past it
    while building toward the pivot."""
    AT = auto()
    """The level's price equals the swing's price exactly, in ticks."""


@dataclass(frozen=True)
class LevelDistance:
    """One previous-period OHLC field's distance from a swing's price."""

    level_name: str
    """PDH/PDL/PDO/PDC (previous day), PWH/.../PWC (previous week), or
    PMH/.../PMC (previous month) -- High/Low/Open/Close."""
    price: float
    distance_ticks: int
    """price(level) - price(swing), in ticks. Positive: level is above
    the swing's price. Negative: below. Zero: exactly at it."""
    position: LevelPosition


@dataclass(frozen=True)
class SwingReferenceContext:
    """Every previous-day/week/month OHLC distance available for one
    swing, as of its own pivot bar's trading day -- never a later one
    (see `swing_reference_distances`). Fewer than 12 entries whenever a
    period has no completed previous occurrence yet (Phase 7's own
    honest-None precedent for `PeriodOHLC`) -- never padded or guessed."""

    swing_object_id: str
    distances: tuple[LevelDistance, ...]

    def for_level(self, level_name: str) -> LevelDistance | None:
        for distance in self.distances:
            if distance.level_name == level_name:
                return distance
        return None


_FIELDS: tuple[tuple[str, str], ...] = (
    ("H", "high"),
    ("L", "low"),
    ("O", "open"),
    ("C", "close"),
)
_PERIODS: tuple[tuple[str, str], ...] = (
    ("PD", "previous_day_ohlc"),
    ("PW", "previous_week_ohlc"),
    ("PM", "previous_month_ohlc"),
)


def _position(swing_type: SwingType, distance_ticks: int) -> LevelPosition:
    if distance_ticks == 0:
        return LevelPosition.AT

    if swing_type is SwingType.HIGH:
        return LevelPosition.AHEAD if distance_ticks > 0 else LevelPosition.BEHIND

    return LevelPosition.AHEAD if distance_ticks < 0 else LevelPosition.BEHIND


def swing_reference_distances(
    swing: SwingPoint,
    level_engine: ReferenceLevelEngine,
    trading_day: date,
    *,
    tick_size: float,
) -> SwingReferenceContext:
    """
    `trading_day` is the caller's responsibility -- typically
    `VOTimeEngine.trading_day_of(swing.observed_at, swing.instrument_id)`
    -- rather than resolved here, matching `ReferenceLevelEngine`'s own
    methods, which already take a bare `trading_day`, not a raw instant.

    No lookahead: this always looks up the previous *completed* period
    relative to `trading_day` -- the trading day the swing's own pivot
    bar was observed on, never the later trading day its confirming bar
    fell on. Passing the confirming bar's trading_day instead would risk
    crossing a day/week/month boundary between pivot and confirmation
    and reading a period that did not exist yet when the pivot printed;
    callers should always pass the pivot's own trading_day.
    """
    swing_ticks = to_ticks(swing.price, tick_size)
    distances: list[LevelDistance] = []

    for prefix, method_name in _PERIODS:
        ohlc = getattr(level_engine, method_name)(trading_day)
        if ohlc is None:
            continue

        for suffix, field in _FIELDS:
            level_price = getattr(ohlc, field)
            distance_ticks = to_ticks(level_price, tick_size) - swing_ticks
            distances.append(
                LevelDistance(
                    level_name=f"{prefix}{suffix}",
                    price=level_price,
                    distance_ticks=distance_ticks,
                    position=_position(swing.swing_type, distance_ticks),
                )
            )

    return SwingReferenceContext(swing_object_id=swing.object_id, distances=tuple(distances))
