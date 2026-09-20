"""
Historical event generation for LRX -- turning a bar series into the
upstream evidence CERR consumes.

    bars -> levels + swings -> sweeps -> displacements -> MSS
                                              |
                                              v
                                    lrx_calibration (cycles)

THIS MODULE OWNS NO DETECTOR. Every event here comes from the existing
engines: ReferenceLevelEngine, SwingEngine, the Swing Adapter,
detect_sweeps, measure_displacement, detect_mss. Nothing is
reimplemented and no algorithm is altered. A replay that computed its
own version of any of these would make historical results describe
something other than what trades.

BAR-BY-BAR, FORWARD ONLY. The series is walked once, index ascending,
and every call at index i is given bars[: i + 1]. Detectors that would
happily accept the whole array are handed a prefix instead, so
lookahead is prevented structurally rather than promised. A test
asserts that truncating history at N reproduces the identical events
through N.

EVENTS CARRY THEIR OWN PROVENANCE. Sweeps know their level and
penetration bar, displacements know their sweep and span, MSS knows its
displacement, its sweep and the swing it broke. The chain is followed
by id rather than reconstructed by proximity, so any cycle can be taken
back to the bars that caused it.

ORDER WITHIN A BAR is fixed and meaningful: swings first (structure is
what everything else is measured against), then levels, then sweeps,
then displacement from those sweeps, then MSS from that displacement.
Each stage consumes only what earlier stages have already produced at
this bar or before.

NO THRESHOLDS ARE CHOSEN HERE. Every parameter comes from LrxConfig or
an explicit argument. This module makes the funnel observable; it does
not decide what qualifies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time

from vo.market.bar import Bar
from vo.market.sequence import build_bar_sequence
from vo.observation.swings import SwingEngine, SwingLevel, SwingPoint
from vo.time.engine import VOTimeEngine
from vo.time.levels import ReferenceLevelEngine
from vo.valco.lrx_config import LrxConfig
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    DisplacementEvent,
    measure_displacement,
)
from vo.valco.lrx_levels import collect_levels
from vo.valco.lrx_mss import MssConfig, MssEvent, detect_mss
from vo.valco.lrx_sweep import SweepEvent, detect_sweeps
from vo.valco.lrx_swings import CanonicalSwing, adapt_swings


class ReplayDependencyError(RuntimeError):
    """A replay cannot run because something it needs is absent.

    Raised rather than worked around: a missing session config or an
    unusable bar series must be reported as the exact dependency it is,
    never papered over with an invented default. A fabricated session
    boundary would silently relocate every level in the dataset.
    """


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Detector parameters for one replay. Mirrors the LRX config's own
    fields; nothing here invents a value."""

    min_penetration_atr: float
    return_max_bars: int
    atr_period: int
    displacement: DisplacementConfig
    mss: MssConfig
    swing_equal_tolerance: float = 0.0
    displacement_search_bars: int = 0
    """How many bars AFTER a raid returns inside may still begin its
    expansion.

    The detectors do not define this: measure_displacement is asked
    about one bar, and something has to decide which bars to ask about.
    A leg cannot be measured on the raid's own return bar -- the
    expansion follows the raid -- so a search horizon is unavoidable.
    Defaulted from the sweep's own return_max_bars rather than invented,
    on the reasoning that a raid which has not produced expansion within
    the window it was allowed to return in has not produced one at all.
    Zero means "use return_max_bars"."""


@dataclass(frozen=True, slots=True)
class ReplayEvents:
    """Everything the replay produced, in the order it became knowable."""

    swings: tuple[CanonicalSwing, ...] = ()
    sweeps: tuple[SweepEvent, ...] = ()
    displacements: tuple[DisplacementEvent, ...] = ()
    mss_events: tuple[MssEvent, ...] = ()
    levels_seen: int = 0
    bars_replayed: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {
            "bars": self.bars_replayed,
            "swings": len(self.swings),
            "levels_seen": self.levels_seen,
            "sweeps": len(self.sweeps),
            "displacements": len(self.displacements),
            "mss": len(self.mss_events),
        }


@dataclass(slots=True)
class _Accumulator:
    swing_points: list[SwingPoint] = field(default_factory=list)
    bar_time_of: dict[str, datetime] = field(default_factory=dict)
    sweeps: list[SweepEvent] = field(default_factory=list)
    pending: list[SweepEvent] = field(default_factory=list)
    """Raids still waiting for an expansion. A displacement cannot be
    measured on the bar a sweep returned -- measure_displacement refuses
    index <= returned_index, because the leg comes AFTER the raid -- so
    sweeps are carried forward and asked about on subsequent bars."""
    awaiting_shift: list[tuple[SweepEvent, DisplacementEvent]] = field(
        default_factory=list
    )
    """Legs still waiting for a structural break. The same reasoning as
    `pending`, one stage later: a shift is a break of a prior swing and
    happens on some bar AFTER the leg, not on the leg's own confirming
    bar, so displacements are carried forward too."""
    displacements: list[DisplacementEvent] = field(default_factory=list)
    mss_events: list[MssEvent] = field(default_factory=list)
    levels_seen: int = 0


def replay_events(
    bars: Sequence[Bar],
    *,
    time_engine: VOTimeEngine,
    swing_engine_factory: Callable[[SwingLevel], SwingEngine],
    config: ReplayConfig,
    tick_size: float,
    trading_day_opens: time | None = None,
) -> ReplayEvents:
    """
    Walk `bars` once, forward, producing the upstream event stream.

    `swing_engine_factory` returns a fresh SwingEngine for a SwingLevel;
    the caller supplies it so the SAME swing configuration already
    loaded elsewhere is used, never a second set of tunables invented
    here.

    `trading_day_opens` is accepted for callers that key trading days
    themselves; the trading day otherwise comes from the Time Engine's
    own context, which is the one place that convention is defined.

    Raises ReplayDependencyError when the series cannot be sequenced or
    the instrument has no session config -- both are missing
    infrastructure, not conditions to guess past.
    """
    _ = trading_day_opens
    if not bars:
        return ReplayEvents()

    sequenced = build_bar_sequence(bars)
    if not sequenced.all_accepted:
        raise ReplayDependencyError(
            f"{len(sequenced.quarantined)} of {len(bars)} bars were quarantined "
            "by BarSequence; replaying a series the pipeline itself would "
            "reject would describe history the engines never see"
        )
    sequence = sequenced.sequence
    instrument = bars[0].instrument_id

    try:
        time_engine.config_for(instrument)
    except Exception as error:
        raise ReplayDependencyError(
            f"no session config for {instrument.broker_symbol!r}: {error}"
        ) from error

    swing_tier = swing_engine_factory(SwingLevel.SWING)
    internal_tier = swing_engine_factory(SwingLevel.INTERNAL)
    level_engine = ReferenceLevelEngine(time_engine, sequence)
    acc = _Accumulator()

    for index, bar in enumerate(bars):
        visible = bars[: index + 1]
        acc.bar_time_of[bar.bar_id] = bar.open_time_utc
        window = sequence.window_at(index)

        # 1. Structure first: everything below is measured against it.
        for engine in (swing_tier, internal_tier):
            acc.swing_points.extend(engine.on_bar(window))

        # 2. Levels knowable at this bar.
        context = time_engine.context_for(bar.open_time_utc, instrument)
        levels = collect_levels(
            level_engine,
            trading_day=context.trading_day,
            bars=visible,
            up_to_index=index,
            session_start=None,
        )
        acc.levels_seen += len(levels)

        # 3. Raids on those levels.
        sweeps = (
            detect_sweeps(
                visible,
                index,
                levels,
                min_penetration_atr=config.min_penetration_atr,
                return_max_bars=config.return_max_bars,
                atr_period=config.atr_period,
                tick_size=tick_size,
            )
            if levels
            else ()
        )
        acc.sweeps.extend(sweeps)
        acc.pending.extend(sweeps)

        # 4. Expansion from raids that have ALREADY returned, and 5. the
        #    shift that follows. Both are given the PREFIX, so neither
        #    can see past this bar even though both would accept the
        #    whole series.
        swings_so_far = adapt_swings(
            acc.swing_points,
            bar_time_of=acc.bar_time_of,
            equal_tolerance=config.swing_equal_tolerance,
        )
        horizon = config.displacement_search_bars or config.return_max_bars
        still_pending: list[SweepEvent] = []
        for sweep in acc.pending:
            if index <= sweep.returned_index:
                still_pending.append(sweep)
                continue
            if index - sweep.returned_index > horizon:
                continue  # the raid's window closed without expansion

            displacement = measure_displacement(
                visible, index, sweep, config.displacement, tick_size=tick_size
            )
            if displacement is None:
                still_pending.append(sweep)
                continue
            acc.displacements.append(displacement)
            acc.awaiting_shift.append((sweep, displacement))
        acc.pending = still_pending

        # 5. The shift, searched over the bars AFTER each leg for the
        #    same reason expansion is searched after each raid.
        still_awaiting: list[tuple[SweepEvent, DisplacementEvent]] = []
        for sweep, displacement in acc.awaiting_shift:
            if index <= displacement.end_index:
                still_awaiting.append((sweep, displacement))
                continue
            if index - displacement.end_index > horizon:
                continue  # the leg's window closed without a shift
            mss = detect_mss(
                visible,
                index,
                displacement,
                sweep,
                swings_so_far,
                config.mss,
                tick_size=tick_size,
            )
            if mss is None:
                still_awaiting.append((sweep, displacement))
                continue
            acc.mss_events.append(mss)
        acc.awaiting_shift = still_awaiting

    return ReplayEvents(
        swings=adapt_swings(
            acc.swing_points,
            bar_time_of=acc.bar_time_of,
            equal_tolerance=config.swing_equal_tolerance,
        ),
        sweeps=tuple(acc.sweeps),
        displacements=tuple(acc.displacements),
        mss_events=tuple(acc.mss_events),
        levels_seen=acc.levels_seen,
        bars_replayed=len(bars),
    )


def replay_config_from_lrx(
    lrx: LrxConfig,
    *,
    displacement: DisplacementConfig | None = None,
    mss: MssConfig | None = None,
) -> ReplayConfig:
    """Build a ReplayConfig from the loaded LRX config.

    Detector parameters come from the shipped lrx.yaml so a replay and a
    live run are driven by the same numbers. Displacement and MSS
    definitions are passed explicitly because each is a research CHOICE
    -- which qualification mode, which confirmation method -- and
    defaulting them silently would hide which definition produced a
    dataset.
    """
    return ReplayConfig(
        min_penetration_atr=lrx.sweep.min_penetration_atr,
        return_max_bars=lrx.sweep.return_max_bars,
        atr_period=lrx.expansion.atr_period,
        displacement=displacement if displacement is not None else DisplacementConfig(),
        mss=mss if mss is not None else MssConfig(),
    )
