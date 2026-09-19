"""
StructureRangeEngine -- Phase 13b, deliverable (C): the provisional
front-running layer, gate G6.

WHERE THIS SITS, AND WHERE IT DOES NOT: Phase 13's RegimeEngine
(vo.observation.regime) is untouched. This is a SEPARATE, [VO-H]
classifier scored against it, never a replacement -- confirmed with the
user via two rounds of AskUserQuestion review (2026-09-19) before any
code: (1) this stays a new layer alongside the canonical classifier, not
a redefinition of it; (2) REVERSAL's "inefficiency" confirmation uses the
candle layer's existing unnamed measurement (vo.market.relation.Separation
/ body_separation_ticks) directly, tagged [VO-D] -- it is NOT called an
FVG here, and Phase 27 (Delivery Inefficiency Engine) still owns that
name and its full lifecycle when it is eventually built. Naming
discipline matches vo-candle-layer.md's own rule exactly: using a
measurement is not the same as naming what it means.

THE RULE, IN THE USER'S OWN WORDS (2026-09-19), restated in VO's
vocabulary: "the consolidation range I'm trying to capture is when the
market is trading between a swing low and internal high, or between a
swing high and an internal low" -- an ASYMMETRIC range, one boundary from
each of Phase 11's two tiers (SwingLevel.SWING and SwingLevel.INTERNAL),
not the symmetric same-tier range Phase 13 uses. "Expansion is when
price expands to either side of that range." "Retracement is any price
within the range where bodies are respecting the high or low" -- read,
and confirmed by the user, as: after an expansion, a pullback whose
bodies keep closing on the expansion side of the boundary just broken
(the break is being retested, not given back). "Reversal [is] when the
market goes beyond the high or low with some kind of inefficiency" --
confirmed by the user as: the break-back-through bar itself must show
the separation reading, not that a gap must sit at the original level.

RANGE DEFINITION: the most recently confirmed SWING-tier pivot anchors
one side; the most recent CONFIRMED INTERNAL-tier pivot of the OPPOSITE
type, formed at or after that SWING pivot, anchors the other. Swing
LOW -> internal HIGH is the upper/lower pairing; swing HIGH -> internal
LOW is its mirror. Both anchors use `SwingPoint.body_price` (Lesson 1:
"defined specifically by the bodies of the candles not the wicks"),
matching Phase 13's own v2 boundary methodology exactly -- this rule
disagrees with Phase 13 about WHICH TWO POINTS bound the range, not
about wicks-vs-bodies.

FOUR STATES, NO PULLBACK_UNRESOLVED: unlike Phase 13, this rule's own
wording gives RETRACEMENT and REVERSAL each a direct, checkable trigger
(boundary respected vs. boundary reclaimed-with-inefficiency), so there
is no structurally-honest "unresolved" state to carry between them the
way Phase 13's SS3 rule requires. A bar that reclaims the boundary
without an inefficiency reading is classified RETRACEMENT (the pullback
persists, not yet promoted) -- this is the one real interpretive gap in
an otherwise fully-specified rule; a later bar that reclaims WITH
inefficiency still fires REVERSAL from there.

WHAT THIS PHASE INFERRED TO COMPLETE THE MACHINE (not literally stated by
the user -- flagged here rather than silently folded in as certain, the
same discipline vo.observation.regime's own module docstring uses for its
[VO-D] reconciliations):

  1. A RETRACEMENT that resumes making new extremes in the expansion
     direction (without ever reclaiming the boundary) returns to
     EXPANSION -- a plain continuation, not itself asked about.
  2. After REVERSAL, the engine resets to a fresh, anchor-less
     CONSOLIDATION rather than reusing the just-reclaimed level as a new
     anchor of the opposite configuration. The simpler reset was chosen
     to keep a first, scoreable version easy to audit; carrying the
     reclaimed level forward is a reasonable v2 refinement if scoring
     shows the reset loses useful continuity.
  3. No magnitude threshold on the inefficiency reading -- any nonzero
     separation in the reclaim direction counts. A minimum-ticks
     threshold is a natural, versioned v2 knob if raw "any gap" proves
     noisy; not invented here.

CONFIDENCE: none is recorded. The rule as given is fully mechanical
(boundary comparisons and a signed separation reading), so a confidence
figure here would be decoration, not evidence -- unlike Phase 13's
Efficiency-Ratio-backed anticipation lean, there is no continuous
evidence variable this rule is built from.

NO LOOKAHEAD (G3): StructureRangeEngine.on_bar implements
vo.core.replay.ReplayProbe and reads only window.current, the two
SwingEngines' own bounded on_bar output, and window.separation(i, j, ...)
with j == window.index -- exercised by
tests/unit/test_observation_structure_range.py via assert_no_lookahead.

G2 / [VO-H]: this entire classifier is a forecast of Phase 13's canonical
label, stated ahead of Phase 13's own (slower) confirmation -- exactly
the kind of untested idea G2 exists to fence off. Registered as
`provisional_structure_range_forecast` in vo.month01.ontology, tagged
VO_H. Nothing on any decision path may import this module until it is
promoted with evidence (Phase 30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.interfaces import AppendOnlyLog, CanonicalRecord, CanonicalRecordError
from vo.market.bar import Bar
from vo.market.candle import Candle
from vo.market.identity import InstrumentId
from vo.market.sequence import CandleWindow
from vo.market.timeframe import Timeframe
from vo.observation.swings import SwingEngine, SwingPoint, SwingStatus, SwingType

OBJECT_TYPE_RANGE_STRUCTURE_STATE = "RANGE_STRUCTURE_STATE"
OBJECT_TYPE_RANGE_STRUCTURE_TRANSITION = "RANGE_STRUCTURE_TRANSITION"


class RangeStructureType(Enum):
    """The user's own four states (2026-09-19) -- see module docstring for
    why there is no fifth PULLBACK_UNRESOLVED-style interim label here."""

    CONSOLIDATION = "CONSOLIDATION"
    EXPANSION = "EXPANSION"
    RETRACEMENT = "RETRACEMENT"
    REVERSAL = "REVERSAL"

    def __str__(self) -> str:
        return self.value


class RangeDirection(Enum):
    """Which side of the range broke. Deliberately a separate enum from
    vo.observation.regime.RegimeDirection -- this classifier is scored
    against Phase 13, never coupled to its implementation."""

    UP = "UP"
    DOWN = "DOWN"

    def __str__(self) -> str:
        return self.value


def _range_object_id(
    instrument_id: InstrumentId,
    timeframe: Timeframe,
    state: RangeStructureType,
    at: datetime,
) -> str:
    return f"{instrument_id.key}:{timeframe.canonical}:RANGE:{state.name}:{at.isoformat()}"


@dataclass(frozen=True, kw_only=True)
class RangeStructureState(CanonicalRecord):
    """One classification from the provisional rule. `lower_boundary`/
    `upper_boundary`/`swing_anchor_id`/`internal_anchor_id` are None only
    when no range has been established yet (should not occur on an
    emitted record in practice, since nothing is emitted before both
    anchors exist -- kept Optional rather than asserted, matching
    CanonicalRecord's own "expose what is not known" discipline)."""

    instrument_id: InstrumentId
    timeframe: Timeframe
    state: RangeStructureType
    direction: RangeDirection | None
    lower_boundary: float | None
    upper_boundary: float | None
    swing_anchor_id: str | None
    internal_anchor_id: str | None
    evidence: str
    inefficiency_ticks: int | None = None
    """The signed Separation reading (vo.market.relation) that confirmed a
    REVERSAL. None on every other state -- see __post_init__."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.object_type != OBJECT_TYPE_RANGE_STRUCTURE_STATE:
            raise CanonicalRecordError(
                f"RangeStructureState.object_type must be {OBJECT_TYPE_RANGE_STRUCTURE_STATE!r}"
            )
        if self.inefficiency_ticks is not None and self.state is not RangeStructureType.REVERSAL:
            raise CanonicalRecordError("inefficiency_ticks is only valid on a REVERSAL state")
        if self.state is RangeStructureType.CONSOLIDATION and self.direction is not None:
            raise CanonicalRecordError(
                "direction is only meaningful once a boundary has broken"
            )


@dataclass(frozen=True, kw_only=True)
class RangeStructureTransition(CanonicalRecord):
    """Mirrors vo.observation.regime.RegimeTransition exactly, for its own
    independent state machine."""

    instrument_id: InstrumentId
    timeframe: Timeframe
    from_state: RangeStructureType
    to_state: RangeStructureType
    evidence: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.object_type != OBJECT_TYPE_RANGE_STRUCTURE_TRANSITION:
            raise CanonicalRecordError(
                f"RangeStructureTransition.object_type must be "
                f"{OBJECT_TYPE_RANGE_STRUCTURE_TRANSITION!r}"
            )


class StructureRangeEngine:
    """
    Drives two SwingEngines (one SWING-tier, one INTERNAL-tier) internally
    and runs the asymmetric-range rule over their confirmed pivots plus
    the current bar. Implements vo.core.replay.ReplayProbe.

    The caller wires which SwingEngine is which tier (mirroring
    RegimeEngine's own injected-swing_engine pattern) -- typically via
    `SwingEngine.for_level(config, SwingLevel.SWING, ...)` and
    `SwingEngine.for_level(config, SwingLevel.INTERNAL, ...)` against the
    same swings.yaml already driving Phase 11/13.
    """

    def __init__(
        self,
        *,
        swing_tier_engine: SwingEngine,
        internal_tier_engine: SwingEngine,
        tick_size: float,
        methodology_version: int = 1,
    ) -> None:
        if tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {tick_size}")

        self._swing_tier = swing_tier_engine
        self._internal_tier = internal_tier_engine
        self._tick_size = tick_size
        self._methodology_version = methodology_version

        self.states: AppendOnlyLog[RangeStructureState] = AppendOnlyLog()
        self.transitions: AppendOnlyLog[RangeStructureTransition] = AppendOnlyLog()

        self._regime: RangeStructureType = RangeStructureType.CONSOLIDATION
        self._swing_anchor: SwingPoint | None = None
        self._internal_anchor: SwingPoint | None = None
        self._consolidation_announced = False
        self._direction: RangeDirection | None = None
        self._broken_boundary: float | None = None
        self._extreme_price: float | None = None

    def current_state(self) -> RangeStructureType:
        return self._regime

    def current_boundaries(self) -> tuple[float, float] | None:
        """(lower, upper), or None while no range is established."""
        if self._swing_anchor is None or self._internal_anchor is None:
            return None
        return self._boundaries()

    # ── ReplayProbe ─────────────────────────────────────────────────────

    def on_bar(self, window: CandleWindow) -> tuple[RangeStructureState, ...]:
        emitted: list[RangeStructureState] = []
        current = window.current
        index = window.index

        swing_events = self._swing_tier.on_bar(window)
        internal_events = self._internal_tier.on_bar(window)

        if self._regime is RangeStructureType.CONSOLIDATION:
            self._update_anchors(swing_events, internal_events)

            if self._swing_anchor is None or self._internal_anchor is None:
                return ()

            if not self._consolidation_announced:
                emitted.append(self._announce_consolidation(current))

            broke = self._check_expansion(current)
            if broke is not None:
                direction, boundary = broke
                emitted.append(self._enter_expansion(current, direction, boundary))
            return tuple(emitted)

        if self._regime in (RangeStructureType.EXPANSION, RangeStructureType.RETRACEMENT):
            result = self._advance_leg(window, current, index)
            if result is not None:
                emitted.append(result)
            return tuple(emitted)

        return ()

    # ── anchor bookkeeping (CONSOLIDATION only -- see module docstring) ──

    def _update_anchors(
        self, swing_events: tuple[SwingPoint, ...], internal_events: tuple[SwingPoint, ...]
    ) -> None:
        for sw in swing_events:
            if sw.status is not SwingStatus.CONFIRMED:
                continue
            if self._swing_anchor is None or sw.swing_type is not self._swing_anchor.swing_type:
                # A fresh anchor, or the configuration flipped (LOW<->HIGH):
                # the internal side has to be re-established against the
                # new anchor, not carried over from the old one.
                self._swing_anchor = sw
                self._internal_anchor = None
            else:
                # Another swing pivot of the same type: the boundary moves
                # to it (a lower low, or a higher high, on the swing side).
                self._swing_anchor = sw

        if self._swing_anchor is None:
            return

        wanted = (
            SwingType.HIGH if self._swing_anchor.swing_type is SwingType.LOW else SwingType.LOW
        )
        for iv in internal_events:
            if iv.status is not SwingStatus.CONFIRMED or iv.swing_type is not wanted:
                continue
            if iv.observed_at < self._swing_anchor.observed_at:
                continue  # only an internal pivot formed at/after the swing anchor counts
            self._internal_anchor = iv

    def _boundaries(self) -> tuple[float, float]:
        assert self._swing_anchor is not None and self._internal_anchor is not None
        if self._swing_anchor.swing_type is SwingType.LOW:
            return self._swing_anchor.body_price, self._internal_anchor.body_price
        return self._internal_anchor.body_price, self._swing_anchor.body_price

    # ── state machine ───────────────────────────────────────────────────

    def _check_expansion(self, current: Bar) -> tuple[RangeDirection, float] | None:
        lower, upper = self._boundaries()
        candle = Candle(current)
        if candle.body_high > upper:
            return RangeDirection.UP, upper
        if candle.body_low < lower:
            return RangeDirection.DOWN, lower
        return None

    def _advance_leg(
        self, window: CandleWindow, current: Bar, index: int
    ) -> RangeStructureState | None:
        assert (
            self._direction is not None
            and self._broken_boundary is not None
            and self._extreme_price is not None
        )
        candle = Candle(current)

        reclaimed = (
            candle.body_low < self._broken_boundary
            if self._direction is RangeDirection.UP
            else candle.body_high > self._broken_boundary
        )
        if reclaimed:
            inefficiency_ticks = self._inefficiency_confirms(window, index)
            if inefficiency_ticks is not None:
                return self._enter_reversal(current, inefficiency_ticks)
            # Reclaimed without a confirming separation reading: the rule
            # as given does not promote this to REVERSAL yet (see module
            # docstring's "interpretive gap") -- stays/enters RETRACEMENT.
            if self._regime is not RangeStructureType.RETRACEMENT:
                return self._enter_retracement(current)
            return None

        extreme = candle.body_high if self._direction is RangeDirection.UP else candle.body_low
        new_extreme = (
            extreme > self._extreme_price
            if self._direction is RangeDirection.UP
            else extreme < self._extreme_price
        )
        if new_extreme:
            self._extreme_price = extreme
            if self._regime is not RangeStructureType.EXPANSION:
                return self._enter_expansion_continuation(current)
            return None

        if self._regime is not RangeStructureType.RETRACEMENT:
            return self._enter_retracement(current)
        return None

    def _inefficiency_confirms(self, window: CandleWindow, index: int) -> int | None:
        """[VO-D]: the existing candle-layer Separation measurement
        (vo.market.relation), read directly, never named an FVG here --
        see module docstring. None when index < 2 (not enough history for
        the 3-bar measurement) or when no gap exists in the reclaim
        direction."""
        if index < 2:
            return None
        sep = window.separation(index - 2, index, tick_size=self._tick_size)
        if self._direction is RangeDirection.UP:
            # Giving back an upside expansion: a bearish (downward) gap
            # behind the reclaim bar confirms it.
            return sep.low_high_ticks if sep.low_high_ticks > 0 else None
        return sep.high_low_ticks if sep.high_low_ticks > 0 else None

    # ── transitions ─────────────────────────────────────────────────────

    def _announce_consolidation(self, current: Bar) -> RangeStructureState:
        self._consolidation_announced = True
        return self._emit_state(
            current,
            RangeStructureType.CONSOLIDATION,
            direction=None,
            evidence="range established between the swing and internal anchors",
        )

    def _enter_expansion(
        self, current: Bar, direction: RangeDirection, boundary: float
    ) -> RangeStructureState:
        previous = self._regime
        self._regime = RangeStructureType.EXPANSION
        self._direction = direction
        self._broken_boundary = boundary
        candle = Candle(current)
        self._extreme_price = (
            candle.body_high if direction is RangeDirection.UP else candle.body_low
        )
        state = self._emit_state(
            current,
            RangeStructureType.EXPANSION,
            direction=direction,
            evidence=(
                f"body closed beyond the "
                f"{'upper' if direction is RangeDirection.UP else 'lower'} "
                f"boundary at {boundary}"
            ),
        )
        self._emit_transition(current, previous, RangeStructureType.EXPANSION, "boundary broken")
        return state

    def _enter_expansion_continuation(self, current: Bar) -> RangeStructureState:
        previous = self._regime
        self._regime = RangeStructureType.EXPANSION
        state = self._emit_state(
            current,
            RangeStructureType.EXPANSION,
            direction=self._direction,
            evidence="a new extreme resumed the expansion after a retracement",
        )
        self._emit_transition(
            current, previous, RangeStructureType.EXPANSION, "expansion resumed"
        )
        return state

    def _enter_retracement(self, current: Bar) -> RangeStructureState:
        previous = self._regime
        self._regime = RangeStructureType.RETRACEMENT
        state = self._emit_state(
            current,
            RangeStructureType.RETRACEMENT,
            direction=self._direction,
            evidence="pullback continues to respect the broken boundary",
        )
        self._emit_transition(
            current,
            previous,
            RangeStructureType.RETRACEMENT,
            "pullback; boundary still respected",
        )
        return state

    def _enter_reversal(self, current: Bar, inefficiency_ticks: int) -> RangeStructureState:
        previous = self._regime
        self._regime = RangeStructureType.REVERSAL
        state = self._emit_state(
            current,
            RangeStructureType.REVERSAL,
            direction=self._direction,
            evidence=(
                f"body closed back through the boundary at {self._broken_boundary}, "
                f"confirmed by a {inefficiency_ticks}-tick separation"
            ),
            inefficiency_ticks=inefficiency_ticks,
        )
        self._emit_transition(
            current, previous, RangeStructureType.REVERSAL, "boundary reclaimed with inefficiency"
        )
        # Reset to a fresh range -- see module docstring's inferred-completion #2.
        self._regime = RangeStructureType.CONSOLIDATION
        self._direction = None
        self._broken_boundary = None
        self._extreme_price = None
        self._swing_anchor = None
        self._internal_anchor = None
        self._consolidation_announced = False
        return state

    # ── emit helpers ─────────────────────────────────────────────────────

    def _emit_state(
        self,
        current: Bar,
        state: RangeStructureType,
        *,
        direction: RangeDirection | None,
        evidence: str,
        inefficiency_ticks: int | None = None,
    ) -> RangeStructureState:
        lower = upper = None
        swing_id = internal_id = None
        if self._swing_anchor is not None and self._internal_anchor is not None:
            lower, upper = self._boundaries()
            swing_id = self._swing_anchor.object_id
            internal_id = self._internal_anchor.object_id

        record = RangeStructureState(
            object_type=OBJECT_TYPE_RANGE_STRUCTURE_STATE,
            object_id=_range_object_id(
                current.instrument_id, current.timeframe, state, current.open_time_utc
            ),
            observed_at=current.open_time_utc,
            recorded_at=current.open_time_utc,
            methodology_version=self._methodology_version,
            instrument_id=current.instrument_id,
            timeframe=current.timeframe,
            state=state,
            direction=direction,
            lower_boundary=lower,
            upper_boundary=upper,
            swing_anchor_id=swing_id,
            internal_anchor_id=internal_id,
            evidence=evidence,
            inefficiency_ticks=inefficiency_ticks,
        )
        self.states.append(record)
        return record

    def _emit_transition(
        self,
        current: Bar,
        from_state: RangeStructureType,
        to_state: RangeStructureType,
        evidence: str,
    ) -> None:
        transition = RangeStructureTransition(
            object_type=OBJECT_TYPE_RANGE_STRUCTURE_TRANSITION,
            object_id=(
                f"{current.instrument_id.key}:{current.timeframe.canonical}:"
                f"RANGE_TRANSITION:{from_state.name}->{to_state.name}:"
                f"{current.open_time_utc.isoformat()}"
            ),
            observed_at=current.open_time_utc,
            recorded_at=current.open_time_utc,
            methodology_version=self._methodology_version,
            instrument_id=current.instrument_id,
            timeframe=current.timeframe,
            from_state=from_state,
            to_state=to_state,
            evidence=evidence,
        )
        self.transitions.append(transition)
