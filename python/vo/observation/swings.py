"""
SwingEngine / SwingPoint -- Phase 11, gate G6.

G6 ("the keystone gate"): the swing and state engines pass human agreement
review before anything is built on them. Two rounds of that review shaped
this module's exact algorithm; nothing here was picked unilaterally. The
agreed design is a HYBRID detector -- two independent, separately
configurable filters, both required, neither substituting for the other:

    PRICE -> candidate pivot -> K-bar structural confirmation
          -> ATR distance threshold -> confirmed structural event

  1. K-bar structural confirmation (fractal): bar p is a candidate pivot
     high if its high is strictly greater than every bar's high within K
     positions on both sides; symmetric for a candidate pivot low. This is
     vo.month01.ontology's already-registered ICT concept
     (`internal_swing_nesting`, Month01/L08) made mechanical: "not every
     candle is an equivalent unit of structure."

  2. ATR distance threshold (magnitude, [VO-D] -- see vo.observation.atr):
     the reversal measured across that same K-bar confirming window must
     be at least `atr_multiplier` times the ATR at the pivot. This is
     independent of K and answers a different question -- K asks "is this
     a structurally confirmed local extreme", ATR asks "was the reversal
     away from it actually significant, normalized for the volatility
     regime at the time." A candidate that passes (1) but fails (2) never
     becomes a SwingPoint at that tier; it is not a lesser or provisional
     swing, it simply is not one -- there is no partial-confirmation state
     (matches the agreed v1.0 lifecycle: CONFIRMED -> BROKEN only, no
     SUPERSEDED, no half-built object anywhere in this codebase's other
     lifecycles either).

Both K and atr_multiplier are per-tier, versioned config
(vo.observation.swing_config), explicitly NOT frozen constants -- the
starting values on file are research starting points, expected to be
revised once tested against historical data, not a finished answer this
phase is asserting.

ONE OPEN QUESTION THIS PHASE DELIBERATELY DOES NOT ANSWER: what a
confirmed SwingPoint feeds into downstream ("MSS", "displacement", a setup
model, ...). vo-phase-plan.md §8 excludes "later structure-shift taxonomy"
until Month 1's definition of done passes, and no such name is registered
in vo.month01.ontology today. This module therefore emits only the
tagged, honestly-named object Month 1's own vocabulary already supports --
a SWING, per canonical.py's own worked example -- and stops there. Naming
or building anything MSS-shaped is a later phase's decision, not this
one's, exactly the discipline vo.market.relation already established for
`body_separation_ticks` vs "imbalance".

NO LOOKAHEAD (G3): SwingEngine.on_bar implements vo.core.replay.ReplayProbe
and only ever reads `window.sequence.bars[j]` for `j <= window.index` --
see the bounds check in `_check_new_pivot`/`_check_breaks` below. This is
exercised directly by tests/unit/test_observation_swings.py via
`assert_no_lookahead`, the first real engine Phase 9's harness has ever
driven.

TWO ENGINE INSTANCES, ONE PER TIER: this class detects swings for exactly
one (level, K, atr_multiplier) combination. A caller wanting both INTERNAL
and SWING output constructs two instances (see `SwingEngine.for_level`).
Composing the two into one combined stream, or wiring this into
ObservationPipeline, is deliberately left to whichever later phase already
owns that as a stated deliverable -- Phase 11's own row is the detector
itself, not its integration.

BODY_PRICE, ADDITIVE ONLY (added 2026-09-18): `SwingPoint.body_price`
records each pivot bar's body extreme (max/min of open/close on the
swing's own side) alongside the existing wick-based `price`. This does
NOT change this module's own structural swing/break detection -- `price`
(wick) remains what `_check_new_pivot`/`_check_breaks` key on, unchanged,
because standard swing-point identification isn't itself specified as
body-vs-wick anywhere in Month 1 and this is not the G6 review that
would authorize touching it. `body_price` exists because Lesson 1 states
directly that "the consolidation range... [is] defined specifically by
the bodies of the candles not the wicks" -- a fact scoped to the
CONSOLIDATION range, i.e. Phase 13's own boundary tracking, not Phase
11's detector. See vo.observation.regime's module docstring for how
RegimeEngine consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

from vo.interfaces import AppendOnlyLog, CanonicalRecord, CanonicalRecordError
from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.sequence import CandleWindow
from vo.market.tickmath import to_ticks
from vo.market.timeframe import Timeframe
from vo.observation.atr import atr_ticks
from vo.observation.swing_config import SwingConfig, SwingLevelConfig

OBJECT_TYPE_SWING = "SWING"
"""Matches CanonicalRecord's own module-docstring example -- see
vo.interfaces.canonical."""


class SwingLevel(Enum):
    """The two ICT-sourced tiers (Month01/L08, `internal_swing_nesting`)."""

    INTERNAL = auto()
    SWING = auto()


class SwingType(Enum):
    HIGH = auto()
    LOW = auto()


class SwingStatus(Enum):
    """v1.0 lifecycle, agreed scope: CONFIRMED -> BROKEN only. No
    SUPERSEDED -- "active" is a query over the log (most recent
    CONFIRMED-not-BROKEN per (level, type)), not a stored state, per
    vo.month01.ontology's `active_swing_selection_criterion` marker."""

    CONFIRMED = auto()
    BROKEN = auto()


def _swing_object_id(
    instrument_id: InstrumentId,
    timeframe: Timeframe,
    level: SwingLevel,
    swing_type: SwingType,
    pivot_open_time_utc: datetime,
) -> str:
    """Deterministic, reproducible across live/replay/backtest for the same
    observation -- mirrors Bar.bar_id's own reasoning (vo.market.bar)."""
    return (
        f"{instrument_id.key}:{timeframe.canonical}:{level.name}:{swing_type.name}:"
        f"{pivot_open_time_utc.isoformat()}"
    )


@dataclass(frozen=True, kw_only=True)
class SwingPoint(CanonicalRecord):
    """
    One confirmed structural event, or its later BROKEN correction (G5:
    append-only -- a break is a new record whose `supersedes` names the
    CONFIRMED record it revises, never a mutation of it).

    All new fields are keyword-only (`kw_only=True` on this class only --
    CanonicalRecord's own fields are unaffected) so that they can follow
    CanonicalRecord's `supersedes: str | None = None` without violating
    dataclass field-ordering rules; the alternative (giving every field
    here a bogus default) would let a caller construct a nonsensical
    SwingPoint silently, which is worse.
    """

    instrument_id: InstrumentId
    timeframe: Timeframe
    level: SwingLevel
    swing_type: SwingType
    status: SwingStatus
    price: float
    """The pivot's high (SwingType.HIGH) or low (SwingType.LOW) -- the
    WICK extreme. Structural swing/break detection (this engine's own
    job) stays keyed on this field; it is intentionally unchanged."""
    body_price: float
    """The pivot bar's BODY extreme on the same side as `price`: the
    higher of open/close for a HIGH swing, the lower of open/close for a
    LOW swing. Added 2026-09-18, confirmed via G6 human review the same
    day: Lesson 1 states, directly, that "the consolidation range... [is]
    defined specifically by the bodies of the candles not the wicks."
    `[ICT]` for the bodies-not-wicks fact itself; `[VO-D]` for using the
    pivot bar's own body extreme as the boundary value a downstream
    consumer (RegimeEngine's CONSOLIDATION/EXPANSION boundary tracking)
    should read instead of `price`. Deliberately does NOT change this
    engine's own wick-based swing/break semantics -- see the module
    docstring's dated note for the scoping rationale (Phase 13's
    boundary use only, not a Phase 11 structural redefinition)."""
    pivot_bar_id: str
    """Bar.bar_id of the extremum bar -- traceable back to the source bar."""
    confirmed_at_bar_id: str
    """Bar.bar_id of the bar at which this record became knowable: the
    K-bar-later confirmation bar for a CONFIRMED record, or the bar whose
    price violated the level for a BROKEN one."""
    reversal_ticks: int | None = None
    """The measured displacement that passed the ATR filter, in ticks.
    None on a BROKEN record -- a break is a price fact, not a new
    reversal measurement."""
    atr_ticks_at_pivot: int | None = None
    """The ATR value (in ticks) the CONFIRMED decision was measured
    against, kept for traceability. None on a BROKEN record."""
    reversal_extreme_price: float | None = None
    """The price that produced `reversal_ticks` -- the lowest low
    (SwingType.HIGH) or highest high (SwingType.LOW) across the K-bar
    confirming window. None on a BROKEN record. Recorded rather than
    left for a consumer to re-derive, so a chart-drawing tool (or
    anything else wanting to show the measured reversal) has a single
    source of truth instead of risking a second implementation quietly
    disagreeing with this one -- the same divergence risk Section 7
    names for the EA's own brain."""
    reversal_extreme_bar_id: str | None = None
    """Bar.bar_id of the bar that produced `reversal_extreme_price`.
    None on a BROKEN record."""

    def __post_init__(self) -> None:
        super().__post_init__()

        if self.object_type != OBJECT_TYPE_SWING:
            raise CanonicalRecordError(
                f"SwingPoint.object_type must be {OBJECT_TYPE_SWING!r}, "
                f"got {self.object_type!r}"
            )

        if self.status is SwingStatus.CONFIRMED:
            if (
                self.reversal_ticks is None
                or self.atr_ticks_at_pivot is None
                or self.reversal_extreme_price is None
                or self.reversal_extreme_bar_id is None
            ):
                raise CanonicalRecordError(
                    "a CONFIRMED SwingPoint requires reversal_ticks, "
                    "atr_ticks_at_pivot, reversal_extreme_price and "
                    "reversal_extreme_bar_id"
                )
        elif self.supersedes is None:
            raise CanonicalRecordError("a BROKEN SwingPoint must supersede a CONFIRMED one")


def _confirmed_swing(
    *,
    instrument_id: InstrumentId,
    timeframe: Timeframe,
    level: SwingLevel,
    swing_type: SwingType,
    pivot: Bar,
    confirming_bar: Bar,
    reversal_ticks: int,
    atr_ticks_at_pivot: int,
    reversal_extreme_price: float,
    reversal_extreme_bar_id: str,
    methodology_version: int,
) -> SwingPoint:
    price = pivot.high if swing_type is SwingType.HIGH else pivot.low
    body_price = (
        max(pivot.open, pivot.close) if swing_type is SwingType.HIGH
        else min(pivot.open, pivot.close)
    )
    object_id = _swing_object_id(instrument_id, timeframe, level, swing_type, pivot.open_time_utc)

    return SwingPoint(
        object_type=OBJECT_TYPE_SWING,
        object_id=object_id,
        observed_at=pivot.open_time_utc,
        recorded_at=confirming_bar.open_time_utc,
        methodology_version=methodology_version,
        instrument_id=instrument_id,
        timeframe=timeframe,
        level=level,
        swing_type=swing_type,
        status=SwingStatus.CONFIRMED,
        price=price,
        body_price=body_price,
        pivot_bar_id=pivot.bar_id,
        confirmed_at_bar_id=confirming_bar.bar_id,
        reversal_ticks=reversal_ticks,
        atr_ticks_at_pivot=atr_ticks_at_pivot,
        reversal_extreme_price=reversal_extreme_price,
        reversal_extreme_bar_id=reversal_extreme_bar_id,
    )


def _broken_swing(confirmed: SwingPoint, *, breaking_bar: Bar) -> SwingPoint:
    return SwingPoint(
        object_type=OBJECT_TYPE_SWING,
        object_id=f"{confirmed.object_id}#BROKEN@{breaking_bar.open_time_utc.isoformat()}",
        observed_at=breaking_bar.open_time_utc,
        recorded_at=breaking_bar.open_time_utc,
        methodology_version=confirmed.methodology_version,
        supersedes=confirmed.object_id,
        instrument_id=confirmed.instrument_id,
        timeframe=confirmed.timeframe,
        level=confirmed.level,
        swing_type=confirmed.swing_type,
        status=SwingStatus.BROKEN,
        price=confirmed.price,
        body_price=confirmed.body_price,
        pivot_bar_id=confirmed.pivot_bar_id,
        confirmed_at_bar_id=breaking_bar.bar_id,
    )


class SwingEngine:
    """One tier's detector. See module docstring for the full algorithm
    and why one instance covers exactly one (level, K, atr_multiplier)."""

    def __init__(
        self,
        *,
        level: SwingLevel,
        k: int,
        atr_period: int,
        atr_multiplier: float,
        tick_size: float,
        methodology_version: int = 1,
    ) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        if atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {atr_period}")
        if atr_multiplier <= 0:
            raise ValueError(f"atr_multiplier must be positive, got {atr_multiplier}")
        if tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {tick_size}")

        self._level = level
        self._k = k
        self._atr_period = atr_period
        self._atr_multiplier = atr_multiplier
        self._tick_size = tick_size
        self._methodology_version = methodology_version

        self.log: AppendOnlyLog[SwingPoint] = AppendOnlyLog()
        self._active_high: SwingPoint | None = None
        self._active_low: SwingPoint | None = None

    @classmethod
    def for_level(
        cls,
        config: SwingConfig,
        level: SwingLevel,
        *,
        tick_size: float,
        methodology_version: int = 1,
    ) -> SwingEngine:
        level_config: SwingLevelConfig = (
            config.internal if level is SwingLevel.INTERNAL else config.swing
        )

        return cls(
            level=level,
            k=level_config.k,
            atr_period=config.atr_period,
            atr_multiplier=level_config.atr_multiplier,
            tick_size=tick_size,
            methodology_version=methodology_version,
        )

    def active(self, swing_type: SwingType) -> SwingPoint | None:
        """The most recent CONFIRMED-not-BROKEN swing of this type, or
        None. Answers vo.month01.ontology's `active_swing_selection_
        criterion` for this engine's tier: a query over the log, not a
        stored field."""
        return self._active_high if swing_type is SwingType.HIGH else self._active_low

    def on_bar(self, window: CandleWindow) -> tuple[SwingPoint, ...]:
        """ReplayProbe.on_bar -- see vo.core.replay. Bounded strictly to
        `window.index`; never reads a bar after it."""
        new_events: list[SwingPoint] = []

        new_events.extend(self._check_breaks(window))
        new_events.extend(self._check_new_pivot(window))

        for event in new_events:
            self.log.append(event)
            if event.status is SwingStatus.BROKEN:
                if event.swing_type is SwingType.HIGH:
                    self._active_high = None
                else:
                    self._active_low = None
            elif event.swing_type is SwingType.HIGH:
                self._active_high = event
            else:
                self._active_low = event

        return tuple(new_events)

    def _check_breaks(self, window: CandleWindow) -> list[SwingPoint]:
        current = window.current
        broken: list[SwingPoint] = []

        if self._active_high is not None and current.high > self._active_high.price:
            broken.append(_broken_swing(self._active_high, breaking_bar=current))

        if self._active_low is not None and current.low < self._active_low.price:
            broken.append(_broken_swing(self._active_low, breaking_bar=current))

        return broken

    def _check_new_pivot(self, window: CandleWindow) -> list[SwingPoint]:
        i = window.index
        k = self._k
        pivot_index = i - k

        # Need k bars on each side of the pivot, all at or before `i`:
        # [pivot_index - k, pivot_index + k] == [i - 2k, i]. Nothing here
        # ever indexes past `i` (== window.index).
        if pivot_index < k:
            return []

        bars = window.sequence.bars
        pivot = bars[pivot_index]
        window_bars = bars[pivot_index - k : pivot_index + k + 1]
        confirming_bar = bars[i]

        events: list[SwingPoint] = []

        if all(pivot.high > other.high for other in window_bars if other is not pivot):
            events.extend(
                self._try_confirm(
                    swing_type=SwingType.HIGH,
                    pivot=pivot,
                    pivot_index=pivot_index,
                    bars=bars,
                    confirming_bar=confirming_bar,
                )
            )

        if all(pivot.low < other.low for other in window_bars if other is not pivot):
            events.extend(
                self._try_confirm(
                    swing_type=SwingType.LOW,
                    pivot=pivot,
                    pivot_index=pivot_index,
                    bars=bars,
                    confirming_bar=confirming_bar,
                )
            )

        return events

    def _try_confirm(
        self,
        *,
        swing_type: SwingType,
        pivot: Bar,
        pivot_index: int,
        bars: tuple[Bar, ...],
        confirming_bar: Bar,
    ) -> list[SwingPoint]:
        atr = atr_ticks(bars, pivot_index, period=self._atr_period, tick_size=self._tick_size)
        if atr is None:
            return []

        after_pivot = bars[pivot_index + 1 : pivot_index + self._k + 1]

        if swing_type is SwingType.HIGH:
            extreme_bar = min(after_pivot, key=lambda bar: bar.low)
            reversal_ticks = to_ticks(pivot.high, self._tick_size) - to_ticks(
                extreme_bar.low, self._tick_size
            )
            extreme_price = extreme_bar.low
        else:
            extreme_bar = max(after_pivot, key=lambda bar: bar.high)
            reversal_ticks = to_ticks(extreme_bar.high, self._tick_size) - to_ticks(
                pivot.low, self._tick_size
            )
            extreme_price = extreme_bar.high

        if reversal_ticks < self._atr_multiplier * atr:
            return []

        return [
            _confirmed_swing(
                instrument_id=pivot.instrument_id,
                timeframe=pivot.timeframe,
                level=self._level,
                swing_type=swing_type,
                pivot=pivot,
                confirming_bar=confirming_bar,
                reversal_ticks=reversal_ticks,
                atr_ticks_at_pivot=atr,
                reversal_extreme_price=extreme_price,
                reversal_extreme_bar_id=extreme_bar.bar_id,
                methodology_version=self._methodology_version,
            )
        ]
