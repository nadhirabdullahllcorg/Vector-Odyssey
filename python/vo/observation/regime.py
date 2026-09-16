"""
Basic Regime Engine -- Phase 13, gate G6 (the keystone gate).

Answers one question and only one: *what market condition is VO observing
right now?* It never decides a trade (G2: nothing here is on a decision
path -- there is no decision path yet), and it does not use Markov (Phase
17, historical stats) as an input. Its design was locked with the user
across a G6 review before any code; the agreed shape:

  CLASSIFIER = market structure (confirmed swings, Phase 11). The tier is
  configurable (default SWING). A confirmed break of the prior swing
  extreme in one direction is EXPANSION; confirmed swings contained
  between a high and a low with neither broken is CONSOLIDATION. Per Month
  1 (Lesson 1/2, vo-curriculum.md), the market only leaves CONSOLIDATION
  THROUGH EXPANSION -- RETRACEMENT/REVERSAL are never reached directly
  from CONSOLIDATION. That transition structure is enforced as a state
  machine here, not left to chance.

  RETRACEMENT vs REVERSAL is structural and honest: after an expansion, a
  pullback is emitted live as PULLBACK_UNRESOLVED (the plan's SS3 rule --
  the two are NOT distinguishable in real time). It RESOLVES to
  RETRACEMENT when the expansion resumes (a new extreme in the trend
  direction, the defining swing still intact) or to REVERSAL when the
  defining swing is broken (a structure shift). The resolution is an
  APPEND -- a new RegimeState whose `supersedes` names the unresolved one
  -- never an overwrite (G5), the same CONFIRMED->BROKEN discipline
  SwingPoint uses.

  ANTICIPATION (the user's ask, reconciled with "observe, don't forecast"):
  while unresolved, the record carries an evidence-backed [VO-H] LEAN --
  anticipated RETRACEMENT / REVERSAL / UNCLEAR -- from the Efficiency
  Ratio and how deep the pullback is toward the defining swing. The
  canonical STATE stays PULLBACK_UNRESOLVED until structure confirms; the
  lean is recorded, never acted on. It is a marked hypothesis in the
  RAW FACT -> ... -> PREDICTION pipeline, not a classification.

  EVIDENCE = Efficiency Ratio (vo.observation.efficiency_ratio, pulled
  forward from Phase 15 as instrumentation, never the classifier):
  quantifies how cleanly directional the delivery is, feeding each state's
  confidence and the anticipation lean. Recorded as a RegimeFeature with
  its own methodology string, per the plan's reproducibility rule.

NO LOOKAHEAD (G3): RegimeEngine.on_bar implements vo.core.replay.
ReplayProbe and reads only window.current and the swings its internal
SwingEngine has already confirmed from bars <= window.index. Emitted
records' object_ids and timestamps derive from those bars (never a wall
clock), so a replay is bit-identical -- exercised by
tests/unit/test_observation_regime.py via assert_no_lookahead.

The v1 thresholds (ER period, trend/chop ER cutoffs, pullback-depth
ratios) are [VO-D] starting points in config/settings/regime.yaml, exactly
like the swing K/ATR values -- research inputs, not settled answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.interfaces import AppendOnlyLog, CanonicalRecord, CanonicalRecordError
from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.sequence import CandleWindow
from vo.market.timeframe import Timeframe
from vo.observation.efficiency_ratio import efficiency_ratio
from vo.observation.hurst import hurst_exponent
from vo.observation.swings import SwingEngine, SwingPoint, SwingStatus, SwingType

OBJECT_TYPE_REGIME_STATE = "REGIME_STATE"
OBJECT_TYPE_REGIME_TRANSITION = "REGIME_TRANSITION"


class RegimeType(Enum):
    """Month 1's four market conditions (Lesson 1) plus the honest interim
    label PULLBACK_UNRESOLVED (SS3): RETRACEMENT and REVERSAL are not
    distinguishable in real time, so a live pullback is UNRESOLVED until
    structure resolves it."""

    CONSOLIDATION = "CONSOLIDATION"
    EXPANSION = "EXPANSION"
    PULLBACK_UNRESOLVED = "PULLBACK_UNRESOLVED"
    RETRACEMENT = "RETRACEMENT"
    REVERSAL = "REVERSAL"

    def __str__(self) -> str:
        return self.value


class RegimeDirection(Enum):
    """The direction of the active expansion (and of the pullback's parent
    structure). Not an exposure or a trade opinion -- a structural fact."""

    UP = "UP"
    DOWN = "DOWN"

    def __str__(self) -> str:
        return self.value


class AnticipatedResolution(Enum):
    """[VO-H] lean carried on a PULLBACK_UNRESOLVED record: which way the
    evidence leans, before structure confirms. Never a classification,
    never acted on."""

    RETRACEMENT = "RETRACEMENT"
    REVERSAL = "REVERSAL"
    UNCLEAR = "UNCLEAR"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class RegimeFeature:
    """One quantitative feature cited as evidence, with its own methodology
    string so a value is reproducible without guessing how it was made
    (the plan's rule for Hurst/ER-style instrumentation)."""

    name: str
    value: float
    methodology: str


@dataclass(frozen=True, kw_only=True)
class RegimeState(CanonicalRecord):
    """
    A dated, inspectable market-condition classification. Never a bare
    label: state + direction + confidence + evidence + supporting_features
    all travel together, and `observed_at` (== detected_at) / timeframe /
    methodology_version come from CanonicalRecord. A resolution
    (RETRACEMENT/REVERSAL) is a new record whose `supersedes` names the
    PULLBACK_UNRESOLVED it revises (G5).
    """

    instrument_id: InstrumentId
    timeframe: Timeframe
    regime: RegimeType
    direction: RegimeDirection | None
    confidence: float
    evidence: str
    supporting_features: tuple[RegimeFeature, ...] = ()
    anticipated_resolution: AnticipatedResolution | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.object_type != OBJECT_TYPE_REGIME_STATE:
            raise CanonicalRecordError(
                f"RegimeState.object_type must be {OBJECT_TYPE_REGIME_STATE!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise CanonicalRecordError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        # The anticipation lean is meaningful only while unresolved.
        if (
            self.anticipated_resolution is not None
            and self.regime is not RegimeType.PULLBACK_UNRESOLVED
        ):
            raise CanonicalRecordError(
                "anticipated_resolution is only valid on a PULLBACK_UNRESOLVED state"
            )


@dataclass(frozen=True, kw_only=True)
class RegimeTransition(CanonicalRecord):
    """An explicit regime-change event -- a different fact from the current
    regime, preserved on its own. Never inferred to be a trade signal
    (that is Phase 32's reading, if ever)."""

    instrument_id: InstrumentId
    timeframe: Timeframe
    from_state: RegimeType
    to_state: RegimeType
    evidence: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.object_type != OBJECT_TYPE_REGIME_TRANSITION:
            raise CanonicalRecordError(
                f"RegimeTransition.object_type must be {OBJECT_TYPE_REGIME_TRANSITION!r}"
            )


@dataclass(frozen=True)
class AnticipationConfig:
    """[VO-H] thresholds for the pullback lean. Fibonacci-flavoured depths
    are a nod to common practice, explicitly NOT asserted as correct --
    revisable starting points, like the swing K/ATR values."""

    er_trend_threshold: float = 0.5
    er_chop_threshold: float = 0.3
    shallow_pullback_ratio: float = 0.382
    deep_pullback_ratio: float = 0.786


def _regime_object_id(
    instrument_id: InstrumentId,
    timeframe: Timeframe,
    regime: RegimeType,
    at: datetime,
) -> str:
    return (
        f"{instrument_id.key}:{timeframe.canonical}:REGIME:{regime.name}:{at.isoformat()}"
    )


class RegimeEngine:
    """
    One instrument/timeframe's regime classifier. Drives a SwingEngine of
    the configured tier internally and runs the state machine over its
    confirmed swings plus the current bar. Implements
    vo.core.replay.ReplayProbe.
    """

    def __init__(
        self,
        *,
        swing_engine: SwingEngine,
        efficiency_ratio_period: int,
        hurst_period: int,
        anticipation: AnticipationConfig | None = None,
        methodology_version: int = 1,
    ) -> None:
        if efficiency_ratio_period < 1:
            raise ValueError(
                f"efficiency_ratio_period must be >= 1, got {efficiency_ratio_period}"
            )
        if hurst_period < 4:
            raise ValueError(f"hurst_period must be >= 4, got {hurst_period}")
        self._swing = swing_engine
        self._er_period = efficiency_ratio_period
        self._hurst_period = hurst_period
        self._current_hurst: float | None = None
        self._anticipation = anticipation or AnticipationConfig()
        self._methodology_version = methodology_version

        self.states: AppendOnlyLog[RegimeState] = AppendOnlyLog()
        self.transitions: AppendOnlyLog[RegimeTransition] = AppendOnlyLog()

        # Structural anchors, updated as swings confirm.
        self._prev_high: SwingPoint | None = None
        self._last_high: SwingPoint | None = None
        self._prev_low: SwingPoint | None = None
        self._last_low: SwingPoint | None = None

        # Current regime context.
        self._regime: RegimeType = RegimeType.CONSOLIDATION
        self._direction: RegimeDirection | None = None
        self._defining_price: float | None = None  # break of this vs direction => REVERSAL
        self._extreme_price: float | None = None  # expansion's furthest point
        self._unresolved: RegimeState | None = None

    def current_regime(self) -> RegimeType:
        return self._regime

    # ── ReplayProbe ─────────────────────────────────────────────────────

    def on_bar(self, window: CandleWindow) -> tuple[RegimeState, ...]:
        emitted: list[RegimeState] = []
        bars = window.sequence.bars
        index = window.index
        current = window.current

        new_swings = self._swing.on_bar(window)
        er = efficiency_ratio(bars, index, period=self._er_period)
        # Hurst is recorded evidence alongside ER (Phase 15 instrumentation,
        # pulled forward). It never classifies -- stashed for _emit_state.
        self._current_hurst = hurst_exponent(bars, index, period=self._hurst_period)

        # 1. Bar-level: a break of the defining swing resolves a pullback to
        #    REVERSAL immediately (a structure break is a price event, worth
        #    catching on the bar it happens, not waiting for a swing).
        if self._regime is RegimeType.PULLBACK_UNRESOLVED:
            broke = self._defining_broken(current)
            if broke:
                emitted.extend(self._resolve_reversal(current, er))

        # 2. Process each newly CONFIRMED swing through the state machine.
        for swing in new_swings:
            if swing.status is not SwingStatus.CONFIRMED:
                continue
            self._record_anchor(swing)
            emitted.extend(self._on_confirmed_swing(swing, current, er))

        # 3. Refresh the anticipation lean on the still-unresolved record as
        #    evidence (ER, pullback depth) moves, without changing the state.
        if self._regime is RegimeType.PULLBACK_UNRESOLVED and not emitted:
            refreshed = self._maybe_refresh_lean(current, er)
            if refreshed is not None:
                emitted.append(refreshed)

        return tuple(emitted)

    # ── structural bookkeeping ──────────────────────────────────────────

    def _record_anchor(self, swing: SwingPoint) -> None:
        if swing.swing_type is SwingType.HIGH:
            self._prev_high, self._last_high = self._last_high, swing
        else:
            self._prev_low, self._last_low = self._last_low, swing

    def _is_higher_high(self, swing: SwingPoint) -> bool:
        return (
            swing.swing_type is SwingType.HIGH
            and self._prev_high is not None
            and swing.price > self._prev_high.price
        )

    def _is_lower_low(self, swing: SwingPoint) -> bool:
        return (
            swing.swing_type is SwingType.LOW
            and self._prev_low is not None
            and swing.price < self._prev_low.price
        )

    def _defining_broken(self, current: Bar) -> bool:
        if self._defining_price is None:
            return False
        if self._direction is RegimeDirection.UP:
            return current.low < self._defining_price
        return current.high > self._defining_price

    # ── state machine ───────────────────────────────────────────────────

    def _on_confirmed_swing(
        self, swing: SwingPoint, current: Bar, er: float | None
    ) -> list[RegimeState]:
        if self._regime is RegimeType.CONSOLIDATION:
            if self._is_higher_high(swing):
                return self._enter_expansion(RegimeDirection.UP, swing, current, er)
            if self._is_lower_low(swing):
                return self._enter_expansion(RegimeDirection.DOWN, swing, current, er)
            return []

        if self._regime is RegimeType.EXPANSION:
            return self._expansion_swing(swing, current, er)

        if self._regime is RegimeType.PULLBACK_UNRESOLVED:
            return self._unresolved_swing(swing, current, er)

        return []

    def _expansion_swing(
        self, swing: SwingPoint, current: Bar, er: float | None
    ) -> list[RegimeState]:
        assert self._direction is not None
        if self._direction is RegimeDirection.UP:
            if self._is_higher_high(swing):  # expansion extends
                self._extreme_price = swing.price
                if self._last_low is not None:
                    self._defining_price = self._last_low.price
                return []
            if swing.swing_type is SwingType.LOW:  # a pullback low formed
                return self._enter_pullback(current, er)
        else:
            if self._is_lower_low(swing):
                self._extreme_price = swing.price
                if self._last_high is not None:
                    self._defining_price = self._last_high.price
                return []
            if swing.swing_type is SwingType.HIGH:
                return self._enter_pullback(current, er)
        return []

    def _unresolved_swing(
        self, swing: SwingPoint, current: Bar, er: float | None
    ) -> list[RegimeState]:
        # A new extreme in the expansion direction, defining swing intact,
        # resolves the pullback to RETRACEMENT -> back to EXPANSION.
        assert self._direction is not None and self._extreme_price is not None
        if self._direction is RegimeDirection.UP:
            resumed = swing.swing_type is SwingType.HIGH and swing.price > self._extreme_price
        else:
            resumed = swing.swing_type is SwingType.LOW and swing.price < self._extreme_price
        if resumed:
            return self._resolve_retracement(swing, current, er)
        return []

    # ── transitions ─────────────────────────────────────────────────────

    def _enter_expansion(
        self,
        direction: RegimeDirection,
        swing: SwingPoint,
        current: Bar,
        er: float | None,
    ) -> list[RegimeState]:
        previous = self._regime
        self._regime = RegimeType.EXPANSION
        self._direction = direction
        self._extreme_price = swing.price
        self._defining_price = (
            self._last_low.price if direction is RegimeDirection.UP and self._last_low
            else self._last_high.price if direction is RegimeDirection.DOWN and self._last_high
            else None
        )
        confidence = er if er is not None else 0.5
        state = self._emit_state(
            current,
            RegimeType.EXPANSION,
            direction=direction,
            confidence=confidence,
            evidence=(
                f"confirmed {'higher high' if direction is RegimeDirection.UP else 'lower low'}"
                f" at {swing.price} breaks prior swing extreme"
            ),
            er=er,
        )
        self._emit_transition(
            current, previous, RegimeType.EXPANSION, "structural break out of prior state"
        )
        return [state]

    def _enter_pullback(self, current: Bar, er: float | None) -> list[RegimeState]:
        previous = self._regime
        self._regime = RegimeType.PULLBACK_UNRESOLVED
        lean = self._lean(current, er)
        state = self._emit_state(
            current,
            RegimeType.PULLBACK_UNRESOLVED,
            direction=self._direction,
            confidence=0.5,
            evidence="counter-swing after expansion; retracement vs reversal not yet decidable",
            er=er,
            anticipated=lean,
        )
        self._unresolved = state
        self._emit_transition(
            current, previous, RegimeType.PULLBACK_UNRESOLVED, "pullback against the expansion"
        )
        return [state]

    def _resolve_retracement(
        self, swing: SwingPoint, current: Bar, er: float | None
    ) -> list[RegimeState]:
        assert self._direction is not None
        retr = self._emit_state(
            current,
            RegimeType.RETRACEMENT,
            direction=self._direction,
            confidence=1.0,
            evidence=f"expansion resumed: new extreme at {swing.price}, defining swing intact",
            er=er,
            supersedes=self._unresolved.object_id if self._unresolved else None,
        )
        self._emit_transition(
            current,
            RegimeType.PULLBACK_UNRESOLVED,
            RegimeType.RETRACEMENT,
            "pullback held; trend resumed",
        )
        self._unresolved = None
        # ... -> RETRACEMENT -> EXPANSION (Month 1 structure).
        self._regime = RegimeType.RETRACEMENT
        expansion = self._enter_expansion(self._direction, swing, current, er)
        return [retr, *expansion]

    def _resolve_reversal(self, current: Bar, er: float | None) -> list[RegimeState]:
        assert self._direction is not None
        broken_up = self._direction is RegimeDirection.UP
        rev = self._emit_state(
            current,
            RegimeType.REVERSAL,
            direction=self._direction,
            confidence=1.0,
            evidence=f"defining swing broken at {self._defining_price}: structure shift",
            er=er,
            supersedes=self._unresolved.object_id if self._unresolved else None,
        )
        self._emit_transition(
            current, RegimeType.PULLBACK_UNRESOLVED, RegimeType.REVERSAL, "defining swing broken"
        )
        self._unresolved = None
        # ... -> REVERSAL -> EXPANSION in the NEW direction (Month 1 structure).
        new_direction = RegimeDirection.DOWN if broken_up else RegimeDirection.UP
        self._regime = RegimeType.REVERSAL
        previous = RegimeType.REVERSAL
        self._direction = new_direction
        self._extreme_price = current.low if new_direction is RegimeDirection.DOWN else current.high
        self._defining_price = (
            self._last_high.price if new_direction is RegimeDirection.DOWN and self._last_high
            else self._last_low.price if new_direction is RegimeDirection.UP and self._last_low
            else None
        )
        self._regime = RegimeType.EXPANSION
        confidence = er if er is not None else 0.5
        exp = self._emit_state(
            current,
            RegimeType.EXPANSION,
            direction=new_direction,
            confidence=confidence,
            evidence="expansion in the new direction after reversal",
            er=er,
        )
        self._emit_transition(current, previous, RegimeType.EXPANSION, "expansion after reversal")
        return [rev, exp]

    def _maybe_refresh_lean(self, current: Bar, er: float | None) -> RegimeState | None:
        if self._unresolved is None:
            return None
        new_lean = self._lean(current, er)
        if new_lean == self._unresolved.anticipated_resolution:
            return None  # unchanged -- don't spam records
        refreshed = self._emit_state(
            current,
            RegimeType.PULLBACK_UNRESOLVED,
            direction=self._direction,
            confidence=0.5,
            evidence="pullback lean updated as evidence moved; state still unresolved",
            er=er,
            anticipated=new_lean,
            supersedes=self._unresolved.object_id,
        )
        self._unresolved = refreshed
        return refreshed

    # ── lean + emit helpers ─────────────────────────────────────────────

    def _lean(self, current: Bar, er: float | None) -> AnticipatedResolution:
        depth = self._pullback_depth(current)
        cfg = self._anticipation
        if (
            er is not None
            and er >= cfg.er_trend_threshold
            and depth is not None
            and depth <= cfg.shallow_pullback_ratio
        ):
            return AnticipatedResolution.RETRACEMENT
        if (er is not None and er <= cfg.er_chop_threshold) or (
            depth is not None and depth >= cfg.deep_pullback_ratio
        ):
            return AnticipatedResolution.REVERSAL
        return AnticipatedResolution.UNCLEAR

    def _pullback_depth(self, current: Bar) -> float | None:
        """0.0 = at the expansion extreme, 1.0 = at the defining swing (a
        break). None when the geometry is undefined."""
        if self._extreme_price is None or self._defining_price is None:
            return None
        span = abs(self._extreme_price - self._defining_price)
        if span == 0.0:
            return None
        if self._direction is RegimeDirection.UP:
            travelled = self._extreme_price - current.low
        else:
            travelled = current.high - self._extreme_price
        return max(0.0, travelled / span)

    def _emit_state(
        self,
        current: Bar,
        regime: RegimeType,
        *,
        direction: RegimeDirection | None,
        confidence: float,
        evidence: str,
        er: float | None,
        anticipated: AnticipatedResolution | None = None,
        supersedes: str | None = None,
    ) -> RegimeState:
        feature_list: list[RegimeFeature] = []
        if er is not None:
            feature_list.append(
                RegimeFeature(
                    name="efficiency_ratio",
                    value=er,
                    methodology=f"kaufman_er/close/period={self._er_period}",
                )
            )
        if self._current_hurst is not None:
            feature_list.append(
                RegimeFeature(
                    name="hurst_exponent",
                    value=self._current_hurst,
                    methodology=f"structure_function/close/period={self._hurst_period}",
                )
            )
        features = tuple(feature_list)
        state = RegimeState(
            object_type=OBJECT_TYPE_REGIME_STATE,
            object_id=_regime_object_id(
                current.instrument_id, current.timeframe, regime, current.open_time_utc
            ),
            observed_at=current.open_time_utc,
            recorded_at=current.open_time_utc,
            methodology_version=self._methodology_version,
            supersedes=supersedes,
            instrument_id=current.instrument_id,
            timeframe=current.timeframe,
            regime=regime,
            direction=direction,
            confidence=confidence,
            evidence=evidence,
            supporting_features=features,
            anticipated_resolution=anticipated,
        )
        self.states.append(state)
        return state

    def _emit_transition(
        self, current: Bar, from_state: RegimeType, to_state: RegimeType, evidence: str
    ) -> None:
        transition = RegimeTransition(
            object_type=OBJECT_TYPE_REGIME_TRANSITION,
            object_id=(
                f"{current.instrument_id.key}:{current.timeframe.canonical}:TRANSITION:"
                f"{from_state.name}->{to_state.name}:{current.open_time_utc.isoformat()}"
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
