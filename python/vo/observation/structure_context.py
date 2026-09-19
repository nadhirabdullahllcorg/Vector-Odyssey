"""
StructureContext -- the same regime, read at several timeframes at once.

THE PROBLEM THIS ADDRESSES, in the user's own words (2026-09-19): the
regime engine "is capturing the micro swings and that's useful for
scalping but it's really not mirroring market structure." That is not a
threshold being wrong. It is that ONE timeframe is the only thing the
engine has ever seen. An M1 pullback inside an H1 expansion and an M1
pullback inside an H1 reversal look identical to a single-timeframe
classifier, because from inside M1 they ARE identical -- the difference
lives entirely at a scale the engine never looks at. No tuning of K, the
ATR multiplier, or the anticipation thresholds can recover information
that was never in the input.

ICT structure is fractal (daily bias -> intermediate -> internal), so the
fix is to look at the same structure at several scales and record how
they nest. That is all this module does.

WHAT IT DOES NOT DO. It builds no new classifier and changes no existing
one. SwingEngine and RegimeEngine are used exactly as they are, one
instance per timeframe, from the same configs. Nothing here decides,
scores, or forecasts -- a StructureContext is a description of what the
engines currently say at each scale, plus the plain geometric fact of
whether their directions agree. Whether alignment is worth trading is a
question for the backtest (run LRX with and without it and compare), not
an assumption to bake in here.

NO LOOKAHEAD, AND ONE CONSEQUENCE WORTH UNDERSTANDING. Higher-timeframe
engines are fed only COMPLETED bars (vo.market.aggregate refuses to emit
a partial bucket). So at 10:23, the H1 view reflects the H1 bar that
closed at 10:00 -- it can be up to 59 minutes behind. That staleness is
not a defect to be patched with a partial-bar peek; it is the honest
answer, and the alternative would be exactly the lookahead G3 forbids. A
consumer that wants to know how fresh a reading is has `observed_at` and
`bars_completed` on every TimeframeStructure.

G8: every TimeframeStructure carries the object_id of the RegimeState it
came from, so any display or report built on this traces back to a real
canonical record rather than an anonymous label.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

from vo.market.aggregate import SUPPORTED_TARGETS, Aggregator
from vo.market.bar import Bar
from vo.market.sequence import BarSequence
from vo.market.timeframe import Timeframe
from vo.observation.regime import RegimeDirection, RegimeEngine, RegimeState, RegimeType
from vo.observation.regime_config import RegimeConfig, build_regime_engine
from vo.observation.swing_config import SwingConfig


class StructureContextError(ValueError):
    """Raised for a structurally invalid multi-timeframe setup."""


class StructuralAlignment(Enum):
    """How an execution-timeframe reading sits inside a higher one.

    A geometric fact about two directions, deliberately carrying no
    opinion about which is preferable. COUNTER is not "bad" -- a
    counter-trend reversal setup is counter by definition, and the LRX
    strategy trades against the immediate expansion on purpose.
    """

    ALIGNED = "ALIGNED"
    COUNTER = "COUNTER"
    UNDEFINED = "UNDEFINED"
    """One or both timeframes have no direction -- a consolidation has no
    direction to agree with, and neither does a timeframe that has not
    yet completed enough bars to classify."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TimeframeStructure:
    """What one timeframe's engine currently says.

    Every field is None before that timeframe has produced a state --
    which, for D1 on a short run, can be the whole run. "Not yet known"
    is a real answer and is kept distinct from any classification.
    """

    timeframe: Timeframe
    regime: RegimeType | None
    direction: RegimeDirection | None
    observed_at: datetime | None
    bars_completed: int
    state_object_id: str | None

    @property
    def known(self) -> bool:
        return self.regime is not None


@dataclass(frozen=True, slots=True)
class StructureContext:
    """One snapshot across every configured timeframe."""

    generated_at_utc: datetime
    execution: TimeframeStructure
    higher: tuple[TimeframeStructure, ...]

    def at(self, timeframe: Timeframe) -> TimeframeStructure | None:
        if self.execution.timeframe == timeframe:
            return self.execution
        for structure in self.higher:
            if structure.timeframe == timeframe:
                return structure
        return None

    def alignment_with(self, timeframe: Timeframe) -> StructuralAlignment:
        """Whether the execution timeframe's direction agrees with
        `timeframe`'s. UNDEFINED whenever either side has no direction,
        rather than guessing agreement from absence."""
        higher = self.at(timeframe)
        if higher is None:
            return StructuralAlignment.UNDEFINED
        if self.execution.direction is None or higher.direction is None:
            return StructuralAlignment.UNDEFINED
        return (
            StructuralAlignment.ALIGNED
            if self.execution.direction is higher.direction
            else StructuralAlignment.COUNTER
        )

    def describe(self) -> str:
        """A one-line human reading, e.g.
        "M1 PULLBACK_UNRESOLVED inside H1 EXPANSION UP". Built for logs
        and chart tooltips; carries no interpretation beyond naming what
        each engine said."""
        execution = self.execution
        head = (
            f"{execution.timeframe.canonical} "
            f"{execution.regime.name if execution.regime else 'UNKNOWN'}"
        )
        if execution.direction is not None:
            head += f" {execution.direction.name}"

        parts: list[str] = []
        for structure in self.higher:
            if not structure.known:
                continue
            piece = f"{structure.timeframe.canonical} {structure.regime.name}"  # type: ignore[union-attr]
            if structure.direction is not None:
                piece += f" {structure.direction.name}"
            parts.append(piece)

        if not parts:
            return head
        return f"{head} inside " + ", ".join(parts)


class MultiTimeframeStructure:
    """
    One M1 stream in, a StructureContext out.

    Owns one Aggregator and one RegimeEngine per higher timeframe, plus
    an engine on the execution timeframe itself. push() one M1 bar at a
    time, in order; snapshot() whenever a reading is wanted.

    Engines are built from the SAME regime/swing configs the
    single-timeframe path uses. Per-timeframe tuning is a later question
    and deliberately not invented here: one config across scales is the
    honest starting point, and any per-scale divergence should be
    something the data asked for.
    """

    def __init__(
        self,
        *,
        regime_config: RegimeConfig,
        swing_config: SwingConfig,
        tick_size: float,
        higher_timeframes: Sequence[Timeframe],
        execution_timeframe: Timeframe = Timeframe.M1,
        zone: ZoneInfo | None = None,
        day_opens: time | None = None,
    ) -> None:
        unsupported = [tf for tf in higher_timeframes if tf not in SUPPORTED_TARGETS]
        if unsupported:
            raise StructureContextError(
                f"unsupported higher timeframes: "
                f"{sorted(tf.canonical for tf in unsupported)}"
            )
        if execution_timeframe in higher_timeframes:
            raise StructureContextError(
                f"{execution_timeframe.canonical} is both the execution timeframe and a "
                f"higher one -- it would be classified twice from different bar streams"
            )

        self._execution_timeframe = execution_timeframe
        self._tick_size = tick_size

        def _engine() -> RegimeEngine:
            return build_regime_engine(regime_config, swing_config, tick_size=tick_size)

        self._execution_engine = _engine()
        self._execution_sequence = BarSequence()
        self._execution_latest: RegimeState | None = None

        self._aggregators: dict[Timeframe, Aggregator] = {
            tf: Aggregator(tf, zone=zone, day_opens=day_opens) for tf in higher_timeframes
        }
        self._engines: dict[Timeframe, RegimeEngine] = {
            tf: _engine() for tf in higher_timeframes
        }
        self._sequences: dict[Timeframe, BarSequence] = {
            tf: BarSequence() for tf in higher_timeframes
        }
        self._latest: dict[Timeframe, RegimeState | None] = {
            tf: None for tf in higher_timeframes
        }

    @property
    def higher_timeframes(self) -> tuple[Timeframe, ...]:
        return tuple(self._aggregators)

    def push(self, bar: Bar) -> None:
        """Feed one execution-timeframe bar. Higher-timeframe engines see
        it only once it completes one of their buckets."""
        self._execution_sequence = self._execution_sequence.append(bar)
        states = self._execution_engine.on_bar(
            self._execution_sequence.window_at(len(self._execution_sequence) - 1)
        )
        if states:
            self._execution_latest = states[-1]

        for timeframe, aggregator in self._aggregators.items():
            completed = aggregator.push(bar)
            if completed is None:
                continue
            sequence = self._sequences[timeframe].append(completed)
            self._sequences[timeframe] = sequence
            higher_states = self._engines[timeframe].on_bar(
                sequence.window_at(len(sequence) - 1)
            )
            if higher_states:
                self._latest[timeframe] = higher_states[-1]

    def snapshot(self, *, generated_at_utc: datetime) -> StructureContext:
        """`generated_at_utc` is given, not fetched -- the same
        no-internal-clock-read discipline the rest of the observation
        layer follows, and what makes a replay reproducible."""
        return StructureContext(
            generated_at_utc=generated_at_utc,
            execution=_structure_of(
                self._execution_timeframe,
                self._execution_latest,
                len(self._execution_sequence),
            ),
            higher=tuple(
                _structure_of(tf, self._latest[tf], len(self._sequences[tf]))
                for tf in self._aggregators
            ),
        )


def _structure_of(
    timeframe: Timeframe, state: RegimeState | None, bars_completed: int
) -> TimeframeStructure:
    if state is None:
        return TimeframeStructure(
            timeframe=timeframe,
            regime=None,
            direction=None,
            observed_at=None,
            bars_completed=bars_completed,
            state_object_id=None,
        )
    return TimeframeStructure(
        timeframe=timeframe,
        regime=state.regime,
        direction=state.direction,
        observed_at=state.observed_at,
        bars_completed=bars_completed,
        state_object_id=state.object_id,
    )
