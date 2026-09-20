"""
CERR -- Consolidation, Expansion, Retracement, Reversal.

The context and sequencing layer. CERR detects nothing: it organises
evidence the existing engines already produce into a market-cycle
narrative, so the strategy can ask the question no individual detector
can answer -- what PHASE is this event occurring inside?

    IDLE -> CONSOLIDATION -> EXPANSION -> RETRACEMENT -> REVERSAL_CONFIRMED
                                                        INVALIDATED
                                                        EXPIRED

An MSS is not "MSS detected". It is "MSS detected during the reversal
phase of a previously confirmed expansion/retracement cycle". The same
three-event sequence occurring inside a tight consolidation, in a
genuine expansion, after an exhausted move, or in random chop is four
different facts, and only some of them are tradeable. That distinction
is the entire reason this module exists.

THE REVERSAL RESUMES THE EXPANSION. The strategy is not predicting a new
trend; it is buying the end of a pullback inside one it already
identified. Hence the cycle's load-bearing invariant:

    reversal.direction == expansion.direction

A contradiction is rejected by name, never quietly reinterpreted as a
valid cycle pointing the other way.

FAILED CYCLES ARE KEPT. A store that retained only completed sequences
would be survivorship-biased toward setups that worked -- the single
easiest way to produce a backtest that looks excellent and means
nothing. Invalidated and expired cycles are first-class records.

CYCLES ARE IMMUTABLE. Every observation returns a NEW cycle carrying the
full transition history, so the state a decision was made under can
always be reconstructed. Storing only the current state would make the
research object useless.

EVENT-DRIVEN, NEVER CANDLE-COUNT-DRIVEN. No phase has a fixed length. A
cycle may take four bars or forty; phases move because observable events
occur. `max_bars_in_phase` exists only as an optional research
parameter, unset by default.

CERR DECIDES NO TRADES. It stops at REVERSAL_CONFIRMED. SETUP_ARMED,
ENTRY_PENDING, POSITION_OPEN and everything after belong to the
downstream setup and entry layer, which this module must not import.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from vo.market.bar import Bar
from vo.observation.atr import atr_ticks
from vo.valco.lrx_consolidation import ConsolidationEvent
from vo.valco.lrx_displacement import DisplacementEvent, ExpansionDirection
from vo.valco.lrx_levels import LevelSide
from vo.valco.lrx_mss import MssDirection, MssEvent
from vo.valco.lrx_sweep import SweepEvent


class CerrState(Enum):
    IDLE = "IDLE"
    CONSOLIDATION = "CONSOLIDATION"
    EXPANSION = "EXPANSION"
    RETRACEMENT = "RETRACEMENT"
    REVERSAL_CONFIRMED = "REVERSAL_CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in (
            CerrState.REVERSAL_CONFIRMED,
            CerrState.INVALIDATED,
            CerrState.EXPIRED,
        )

    def __str__(self) -> str:
        return self.value


class CerrTrigger(Enum):
    """What caused a transition. Recorded so a research run can ask which
    transition characteristics produced profitable trades, rather than
    only which cycles completed."""

    CONSOLIDATION_CONFIRMED = "CONSOLIDATION_CONFIRMED"
    BREAKOUT_DISPLACEMENT = "BREAKOUT_DISPLACEMENT"
    RETRACEMENT_THRESHOLD_REACHED = "RETRACEMENT_THRESHOLD_REACHED"
    REVERSAL_SEQUENCE_CONFIRMED = "REVERSAL_SEQUENCE_CONFIRMED"
    INVALIDATION = "INVALIDATION"
    EXPIRY = "EXPIRY"

    def __str__(self) -> str:
        return self.value


class CerrInvalidationReason(Enum):
    BREAKOUT_FAILED = "BREAKOUT_FAILED"
    """Price returned inside the consolidation after expanding out of
    it -- the breakout did not hold."""
    EXPANSION_ORIGIN_VIOLATED = "EXPANSION_ORIGIN_VIOLATED"
    """The retracement ran past where the expansion began. Past that
    point there is no expansion left to resume."""
    CONTRADICTORY_DIRECTION = "CONTRADICTORY_DIRECTION"
    """A reversal was offered pointing against the expansion it claims
    to continue."""
    PHASE_EXPIRED = "PHASE_EXPIRED"
    MANUAL = "MANUAL"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CerrConfig:
    """Retracement thresholds are the only tuning here, and both are
    optional: a run sets one, the other, or neither.

    No Fibonacci level, OTE band or candle count is baked in. Those are
    research variants to be layered on once the baseline has been
    measured -- hard-coding one now would make the baseline untestable.
    """

    minimum_retracement_points: float | None = None
    minimum_retracement_atr: float | None = None
    atr_period: int = 14
    max_bars_in_phase: int | None = None
    """Optional research parameter. Unset means phases end when events
    end them, which is the intended behaviour."""
    invalidate_on_return_inside_range: bool = True
    invalidate_on_origin_violation: bool = True

    @property
    def has_retracement_threshold(self) -> bool:
        return (
            self.minimum_retracement_points is not None
            or self.minimum_retracement_atr is not None
        )


@dataclass(frozen=True, slots=True)
class CerrTransition:
    """One state change, with its cause. The history of these is the
    research object; the current state alone is not."""

    cycle_id: str
    previous_state: CerrState
    new_state: CerrState
    transition_time: datetime
    trigger: CerrTrigger
    trigger_event_id: str | None
    trigger_event_type: str | None
    direction: ExpansionDirection | None
    detail: str | None = None
    consolidation_id: str | None = None
    displacement_id: str | None = None
    sweep_id: str | None = None
    mss_id: str | None = None
    fvg_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetracementState:
    """The counter-directional move away from the expansion's extreme."""

    retracement_id: str
    expansion_id: str
    direction: ExpansionDirection
    """The EXPANSION's direction. The retracement itself runs against
    it -- naming it after the cycle keeps the invariant readable."""
    start_time: datetime
    start_price: float
    extreme_price: float
    depth_points: float
    depth_atr: float | None


@dataclass(frozen=True, slots=True)
class ExpansionState:
    """The leg out of consolidation, tracked as it extends.

    References the displacement by id and never duplicates its
    measurements.
    """

    expansion_id: str
    consolidation_id: str
    displacement_id: str
    direction: ExpansionDirection
    origin: float
    start_time: datetime
    start_price: float
    extreme_price: float
    extreme_time: datetime

    def extends(self, price: float) -> bool:
        if self.direction is ExpansionDirection.UP:
            return price > self.extreme_price
        return price < self.extreme_price

    def retracement_from_extreme(self, price: float) -> float:
        """Counter-directional distance from the extreme. Zero while
        price is at or beyond it."""
        if self.direction is ExpansionDirection.UP:
            return max(0.0, self.extreme_price - price)
        return max(0.0, price - self.extreme_price)

    def violates_origin(self, price: float) -> bool:
        if self.direction is ExpansionDirection.UP:
            return price < self.origin
        return price > self.origin


@dataclass(frozen=True, slots=True)
class CerrCycle:
    """One complete market episode, whether or not it ever became a
    trade. Immutable: every observation returns a new cycle."""

    cycle_id: str
    state: CerrState
    created_at: datetime
    updated_at: datetime
    transitions: tuple[CerrTransition, ...]

    consolidation: ConsolidationEvent | None = None
    expansion: ExpansionState | None = None
    retracement: RetracementState | None = None

    reversal_sweep_id: str | None = None
    reversal_displacement_id: str | None = None
    reversal_mss_id: str | None = None
    fvg_ids: tuple[str, ...] = ()

    invalidation_reason: CerrInvalidationReason | None = None
    bars_in_phase: int = 0

    @property
    def direction(self) -> ExpansionDirection | None:
        """The cycle's expected direction, established by the expansion
        and unchanged thereafter."""
        return self.expansion.direction if self.expansion is not None else None

    @property
    def consolidation_id(self) -> str | None:
        return None if self.consolidation is None else self.consolidation.consolidation_id

    @property
    def expansion_id(self) -> str | None:
        return None if self.expansion is None else self.expansion.expansion_id

    @property
    def displacement_id(self) -> str | None:
        return None if self.expansion is None else self.expansion.displacement_id

    @property
    def retracement_id(self) -> str | None:
        return None if self.retracement is None else self.retracement.retracement_id

    @property
    def active(self) -> bool:
        return not self.state.terminal

    @property
    def confirmed(self) -> bool:
        return self.state is CerrState.REVERSAL_CONFIRMED


def _advance(
    cycle: CerrCycle,
    new_state: CerrState,
    *,
    at: datetime,
    trigger: CerrTrigger,
    trigger_event_id: str | None = None,
    trigger_event_type: str | None = None,
    detail: str | None = None,
    **fields: object,
) -> CerrCycle:
    transition = CerrTransition(
        cycle_id=cycle.cycle_id,
        previous_state=cycle.state,
        new_state=new_state,
        transition_time=at,
        trigger=trigger,
        trigger_event_id=trigger_event_id,
        trigger_event_type=trigger_event_type,
        direction=cycle.direction,
        detail=detail,
        consolidation_id=cycle.consolidation_id,
        displacement_id=cycle.displacement_id,
    )
    return replace(
        cycle,
        state=new_state,
        updated_at=at,
        transitions=(*cycle.transitions, transition),
        bars_in_phase=0,
        **fields,  # type: ignore[arg-type]
    )


def begin_cycle(consolidation: ConsolidationEvent) -> CerrCycle:
    """IDLE -> CONSOLIDATION. The only way a cycle starts."""
    cycle_id = f"CERR:{consolidation.consolidation_id}"
    seed = CerrCycle(
        cycle_id=cycle_id,
        state=CerrState.IDLE,
        created_at=consolidation.end_time,
        updated_at=consolidation.end_time,
        transitions=(),
    )
    return _advance(
        seed,
        CerrState.CONSOLIDATION,
        at=consolidation.end_time,
        trigger=CerrTrigger.CONSOLIDATION_CONFIRMED,
        trigger_event_id=consolidation.consolidation_id,
        trigger_event_type="ConsolidationEvent",
        consolidation=consolidation,
    )


def breaks_out(
    consolidation: ConsolidationEvent, displacement: DisplacementEvent
) -> bool:
    """Whether the displacement actually LEFT the consolidation.

    A large candle inside the range is not an expansion out of it, and
    treating it as one is how a violent inside-bar sequence starts a
    cycle that never had a breakout.
    """
    if displacement.direction is ExpansionDirection.UP:
        return displacement.high > consolidation.range_high
    return displacement.low < consolidation.range_low


def observe_displacement(
    cycle: CerrCycle, displacement: DisplacementEvent
) -> CerrCycle:
    """CONSOLIDATION -> EXPANSION, when the leg escapes the range.

    Returns the cycle unchanged when it is not in CONSOLIDATION or the
    displacement stayed inside -- a non-event is not an error.
    """
    if cycle.state is not CerrState.CONSOLIDATION or cycle.consolidation is None:
        return cycle
    if not breaks_out(cycle.consolidation, displacement):
        return cycle

    extreme = (
        displacement.high
        if displacement.direction is ExpansionDirection.UP
        else displacement.low
    )
    expansion = ExpansionState(
        expansion_id=f"EXP:{displacement.displacement_id}",
        consolidation_id=cycle.consolidation.consolidation_id,
        displacement_id=displacement.displacement_id,
        direction=displacement.direction,
        origin=displacement.expansion_origin,
        start_time=displacement.start_at_utc,
        start_price=displacement.start_price,
        extreme_price=extreme,
        extreme_time=displacement.end_at_utc,
    )
    advanced = _advance(
        cycle,
        CerrState.EXPANSION,
        at=displacement.confirmation_at_utc,
        trigger=CerrTrigger.BREAKOUT_DISPLACEMENT,
        trigger_event_id=displacement.displacement_id,
        trigger_event_type="DisplacementEvent",
        expansion=expansion,
    )
    return advanced


def observe_bar(
    cycle: CerrCycle,
    bars: Sequence[Bar],
    index: int,
    config: CerrConfig,
    *,
    tick_size: float,
) -> CerrCycle:
    """
    Feed one completed bar. Extends the expansion, opens a retracement
    once the configured threshold is met, deepens an existing one, and
    applies invalidation.

    A single opposing candle does NOT begin a retracement; only crossing
    the threshold does. Without that rule every pullback tick would end
    the expansion phase.
    """
    if not cycle.active or index < 0 or index >= len(bars):
        return cycle

    bar = bars[index]
    at = bar.open_time_utc
    cycle = replace(cycle, bars_in_phase=cycle.bars_in_phase + 1, updated_at=at)

    if (
        config.max_bars_in_phase is not None
        and cycle.bars_in_phase > config.max_bars_in_phase
    ):
        return _advance(
            cycle,
            CerrState.EXPIRED,
            at=at,
            trigger=CerrTrigger.EXPIRY,
            detail=f"{cycle.bars_in_phase} bars in {cycle.state}",
            invalidation_reason=CerrInvalidationReason.PHASE_EXPIRED,
        )

    expansion = cycle.expansion
    if expansion is None:
        return cycle

    if (
        cycle.state is CerrState.EXPANSION
        and config.invalidate_on_return_inside_range
        and cycle.consolidation is not None
        and cycle.consolidation.contains(bar.close)
    ):
        return _advance(
            cycle,
            CerrState.INVALIDATED,
            at=at,
            trigger=CerrTrigger.INVALIDATION,
            detail="price closed back inside the consolidation range",
            invalidation_reason=CerrInvalidationReason.BREAKOUT_FAILED,
        )

    if config.invalidate_on_origin_violation and expansion.violates_origin(bar.close):
        return _advance(
            cycle,
            CerrState.INVALIDATED,
            at=at,
            trigger=CerrTrigger.INVALIDATION,
            detail="retracement ran past the expansion origin",
            invalidation_reason=CerrInvalidationReason.EXPANSION_ORIGIN_VIOLATED,
        )

    extended = expansion.extends(bar.high) or expansion.extends(bar.low)
    if cycle.state is CerrState.EXPANSION and extended:
        new_extreme = (
            max(expansion.extreme_price, bar.high)
            if expansion.direction is ExpansionDirection.UP
            else min(expansion.extreme_price, bar.low)
        )
        return replace(
            cycle,
            expansion=replace(
                expansion, extreme_price=new_extreme, extreme_time=at
            ),
            updated_at=at,
        )

    adverse = (
        bar.low if expansion.direction is ExpansionDirection.UP else bar.high
    )
    depth = expansion.retracement_from_extreme(adverse)
    atr = atr_ticks(bars, index, period=config.atr_period, tick_size=tick_size)
    atr_price = atr * tick_size if atr is not None else None
    depth_atr = (
        depth / atr_price if atr_price is not None and atr_price > 0 else None
    )

    if cycle.state is CerrState.EXPANSION:
        if not _threshold_met(depth, depth_atr, config):
            return cycle
        retracement = RetracementState(
            retracement_id=f"RET:{expansion.expansion_id}:{at.isoformat()}",
            expansion_id=expansion.expansion_id,
            direction=expansion.direction,
            start_time=at,
            start_price=expansion.extreme_price,
            extreme_price=adverse,
            depth_points=depth,
            depth_atr=depth_atr,
        )
        return _advance(
            cycle,
            CerrState.RETRACEMENT,
            at=at,
            trigger=CerrTrigger.RETRACEMENT_THRESHOLD_REACHED,
            trigger_event_id=retracement.retracement_id,
            trigger_event_type="RetracementState",
            detail=f"depth {depth:.2f} points",
            retracement=retracement,
        )

    if (
        cycle.state is CerrState.RETRACEMENT
        and cycle.retracement is not None
        and depth > cycle.retracement.depth_points
    ):
        return replace(
            cycle,
            retracement=replace(
                cycle.retracement,
                extreme_price=adverse,
                depth_points=depth,
                depth_atr=depth_atr,
            ),
            updated_at=at,
        )
    return cycle


def _threshold_met(
    depth: float, depth_atr: float | None, config: CerrConfig
) -> bool:
    """Unset thresholds are not applied. With neither set, no retracement
    can open -- the same refusal-to-guess as the consolidation
    detector's UNCONFIGURED."""
    if not config.has_retracement_threshold:
        return False
    if (
        config.minimum_retracement_points is not None
        and depth < config.minimum_retracement_points
    ):
        return False
    return not (
        config.minimum_retracement_atr is not None
        and (depth_atr is None or depth_atr < config.minimum_retracement_atr)
    )


def reversal_matches(
    cycle: CerrCycle,
    sweep: SweepEvent,
    displacement: DisplacementEvent,
    mss: MssEvent,
) -> bool:
    """Whether the three events form a reversal resuming this cycle's
    expansion.

    A bullish cycle needs a SELL-side raid, a bullish leg and a bullish
    shift. Anything else is a different market event that happens to
    involve the same detectors.
    """
    if cycle.expansion is None:
        return False
    wanted = cycle.expansion.direction
    wanted_side = (
        LevelSide.SELL_SIDE
        if wanted is ExpansionDirection.UP
        else LevelSide.BUY_SIDE
    )
    wanted_mss = (
        MssDirection.BULLISH
        if wanted is ExpansionDirection.UP
        else MssDirection.BEARISH
    )
    return (
        sweep.side is wanted_side
        and displacement.direction is wanted
        and mss.direction is wanted_mss
    )


def observe_reversal(
    cycle: CerrCycle,
    sweep: SweepEvent,
    displacement: DisplacementEvent,
    mss: MssEvent,
) -> CerrCycle:
    """
    RETRACEMENT -> REVERSAL_CONFIRMED, when sweep + displacement + MSS
    agree with the cycle's own direction.

    A reversal offered before a retracement exists leaves the cycle
    untouched: the sequence is the claim, and accepting it out of order
    would make the phase meaningless. A reversal pointing AGAINST the
    expansion invalidates by name rather than being reinterpreted.
    """
    if cycle.state is not CerrState.RETRACEMENT or cycle.expansion is None:
        return cycle

    if not reversal_matches(cycle, sweep, displacement, mss):
        return _advance(
            cycle,
            CerrState.INVALIDATED,
            at=mss.break_time,
            trigger=CerrTrigger.INVALIDATION,
            trigger_event_id=mss.mss_id,
            trigger_event_type="MssEvent",
            detail=(
                f"expansion {cycle.expansion.direction} vs sweep {sweep.side}, "
                f"displacement {displacement.direction}, mss {mss.direction}"
            ),
            invalidation_reason=CerrInvalidationReason.CONTRADICTORY_DIRECTION,
        )

    return _advance(
        cycle,
        CerrState.REVERSAL_CONFIRMED,
        at=mss.break_time,
        trigger=CerrTrigger.REVERSAL_SEQUENCE_CONFIRMED,
        trigger_event_id=mss.mss_id,
        trigger_event_type="MssEvent",
        reversal_sweep_id=sweep.sweep_id,
        reversal_displacement_id=displacement.displacement_id,
        reversal_mss_id=mss.mss_id,
    )


def attach_inefficiency(cycle: CerrCycle, fvg_id: str) -> CerrCycle:
    """Record an inefficiency as relevant to this cycle.

    CERR does not detect FVGs and does not REQUIRE one to classify a
    retracement: RETRACEMENT is a phase, an FVG is an object that may
    be relevant inside it. Conflating them would make the phase
    undetectable whenever the gap failed to form.
    """
    if fvg_id in cycle.fvg_ids:
        return cycle
    return replace(cycle, fvg_ids=(*cycle.fvg_ids, fvg_id))


def invalidate(
    cycle: CerrCycle,
    reason: CerrInvalidationReason,
    *,
    at: datetime,
    detail: str | None = None,
) -> CerrCycle:
    """End a cycle explicitly. Terminal cycles are returned untouched --
    a completed episode is a record, not something to overwrite."""
    if not cycle.active:
        return cycle
    return _advance(
        cycle,
        CerrState.INVALIDATED,
        at=at,
        trigger=CerrTrigger.INVALIDATION,
        detail=detail,
        invalidation_reason=reason,
    )
