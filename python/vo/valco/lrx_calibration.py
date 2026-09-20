"""
CERR calibration -- what the cycle actually looks like in history,
before anyone chooses its thresholds.

    bars -> consolidation measurements -> CERR machine -> observations

DESCRIPTIVE, NOT OPTIMISING. This module answers "what is the
distribution" and "what fraction of windows proceeded to each phase".
It does not maximise anything, rank parameter sets by profitability,
search a grid for the best result, or claim CERR works. A runner that
picked whichever threshold produced the prettiest history would be an
overfitting machine wearing a research coat.

NO PRODUCTION SEMANTICS ARE RELAXED FOR RESEARCH. The obvious temptation
-- a "research mode" that lets the state machine advance without
configured thresholds -- is refused, because the moment production and
research disagree about what a cycle is, every number here stops
describing the thing that will trade. Instead:

  Consolidation measurement needs NO thresholds at all. The distribution
  pass is therefore threshold-free by construction: measure every
  window, classify nothing.

  Cycle observation runs the UNMODIFIED production machine once per
  CANDIDATE CONFIG. Sweeping candidates is what produces the funnel --
  how many windows qualify, how many expand, retrace, reverse -- at each
  threshold under consideration. That is the question worth asking, and
  it is answered without a single production branch.

FAILED AND PARTIAL CYCLES ARE THE DATASET. A cycle that stalled in
EXPANSION is a row. So is one invalidated at the origin. Keeping only
confirmed reversals would make the sample survivorship-biased toward
setups that worked, which is the easiest way to produce a study that
looks excellent and means nothing.

DETECTION IS INJECTED, NOT REBUILT. Sweeps, displacements and MSS events
are supplied by the caller. This module owns no detector: rebuilding one
here so the runner could be self-contained is precisely how two parts of
a system start disagreeing about what they detected.

CAUSALITY. Observations at bar N are computed from bars[<= N] only, and
events are consumed in index order. A test replays a truncated series
and asserts the observations are identical.

WHY THIS LIVES IN vo.valco AND NOT vo.research. It observes the LRX
strategy layer, and vo.research sits BELOW vo.valco -- research is
imported by vo.observation, so it cannot import the thing it would be
observing without inverting the dependency. Raising vo.research above
vo.valco to accommodate one module would have made every existing
report a potential strategy consumer. This is LRX-specific calibration
rather than general research infrastructure, so it belongs beside the
engines it measures.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from vo.market.bar import Bar
from vo.valco.lrx_cerr import (
    CerrConfig,
    CerrCycle,
    CerrState,
    begin_cycle,
    observe_bar,
    observe_displacement,
    observe_reversal,
)
from vo.valco.lrx_consolidation import (
    ConsolidationConfig,
    ConsolidationQualification,
    build_consolidation_event,
    detect_consolidation,
    measure_consolidation,
)
from vo.valco.lrx_displacement import DisplacementEvent
from vo.valco.lrx_mss import MssEvent
from vo.valco.lrx_sweep import SweepEvent


@dataclass(frozen=True, slots=True)
class ConsolidationObservation:
    """One measured window, classified or not.

    A flat row on purpose: this goes straight to JSONL and then into
    whatever does the statistics. Nested structures would have to be
    flattened by every consumer, differently.
    """

    window_index: int
    start_index: int
    end_index: int
    start_time: str
    end_time: str
    bar_count: int

    range_points: float
    range_atr: float | None
    net_move_points: float
    net_move_atr: float | None
    total_path_points: float
    mean_range_points: float
    mean_range_atr: float | None
    kaufman_efficiency_ratio: float | None
    body_efficiency_ratio: float | None

    qualification: str
    """Under whatever config produced this row -- normally UNCONFIGURED
    for the distribution pass, which is the honest label for "measured,
    nothing decided"."""


@dataclass(frozen=True, slots=True)
class CycleObservation:
    """One CERR episode, whatever became of it."""

    cycle_id: str
    consolidation_id: str
    final_state: str
    invalidation_reason: str | None
    direction: str | None

    started_at: str
    ended_at: str
    duration_bars: int

    reached_expansion: bool
    reached_retracement: bool
    reached_reversal: bool

    expansion_id: str | None
    displacement_id: str | None
    expansion_distance_points: float | None
    expansion_distance_atr: float | None
    bars_to_expansion: int | None
    boundary_breached: float | None
    """The consolidation edge the leg cleared -- range_high for a bullish
    expansion, range_low for a bearish one."""

    retracement_id: str | None
    retracement_depth_points: float | None
    retracement_depth_atr: float | None
    bars_to_retracement: int | None

    reversal_sweep_id: str | None
    reversal_sweep_side: str | None
    reversal_displacement_id: str | None
    reversal_mss_id: str | None
    bars_to_reversal: int | None
    reversal_confirmed_at: str | None

    transition_count: int
    transition_path: str
    """e.g. "CONSOLIDATION>EXPANSION>INVALIDATED". The whole episode in
    one sortable, groupable field."""


def observe_consolidations(
    bars: Sequence[Bar],
    config: ConsolidationConfig,
    *,
    tick_size: float,
    step: int = 1,
) -> tuple[ConsolidationObservation, ...]:
    """
    Measure every window in the series.

    Threshold-free by construction: `measure_consolidation` needs no
    limits, so the distribution can be collected before anyone has
    chosen any. With an UNCONFIGURED config every row is labelled
    UNCONFIGURED, which is the honest label for "measured, nothing
    decided".

    `step` thins the sweep for long histories. Overlapping windows are
    expected and are not deduplicated -- the distribution of what the
    detector would see is the point, not a set of disjoint episodes.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")

    rows: list[ConsolidationObservation] = []
    for index in range(0, len(bars), step):
        measurement = measure_consolidation(bars, index, config, tick_size=tick_size)
        if measurement is None:
            continue
        verdict = detect_consolidation(bars, index, config, tick_size=tick_size)
        rows.append(
            ConsolidationObservation(
                window_index=len(rows),
                start_index=measurement.start_index,
                end_index=measurement.end_index,
                start_time=measurement.start_time.isoformat(),
                end_time=measurement.end_time.isoformat(),
                bar_count=measurement.bar_count,
                range_points=measurement.range_points,
                range_atr=measurement.range_atr,
                net_move_points=measurement.net_move_points,
                net_move_atr=measurement.net_move_atr,
                total_path_points=measurement.total_path_points,
                mean_range_points=measurement.mean_range_points,
                mean_range_atr=measurement.mean_range_atr,
                kaufman_efficiency_ratio=measurement.kaufman_efficiency_ratio,
                body_efficiency_ratio=measurement.body_efficiency_ratio,
                qualification=str(
                    verdict.qualification
                    if verdict is not None
                    else ConsolidationQualification.UNMEASURABLE
                ),
            )
        )
    return tuple(rows)


def _index_by_confirmation(
    displacements: Sequence[DisplacementEvent],
) -> dict[int, list[DisplacementEvent]]:
    """Keyed on the bar that CONFIRMED the leg, not the one that started
    it.

    A displacement is not knowable until its final bar closes -- its
    extreme is the extreme of the whole leg, which the first bar has not
    made yet. Keying on start_index and then replaying the leg's own
    bars fed the expansion's interior back into the machine as though it
    were subsequent price action, and the first bar's low read as an
    enormous retracement from an extreme that had not happened.
    """
    grouped: dict[int, list[DisplacementEvent]] = {}
    for event in displacements:
        grouped.setdefault(event.end_index, []).append(event)
    return grouped


def _mss_by_break(events: Sequence[MssEvent]) -> dict[int, list[MssEvent]]:
    grouped: dict[int, list[MssEvent]] = {}
    for event in events:
        grouped.setdefault(event.break_index, []).append(event)
    return grouped


def _summarise_cycle(
    cycle: CerrCycle,
    bars: Sequence[Bar],
    sweeps: dict[str, SweepEvent],
    displacements: dict[str, DisplacementEvent],
) -> CycleObservation:
    consolidation = cycle.consolidation
    assert consolidation is not None  # begin_cycle guarantees it

    states = [t.new_state for t in cycle.transitions]
    expansion = cycle.expansion
    retracement = cycle.retracement

    def bars_between(start: datetime, end: datetime) -> int:
        return sum(1 for bar in bars if start < bar.open_time_utc <= end)

    boundary = None
    distance_points = None
    distance_atr = None
    bars_to_expansion = None
    if expansion is not None:
        source = displacements.get(expansion.displacement_id)
        boundary = (
            consolidation.range_high
            if expansion.direction.value == "UP"
            else consolidation.range_low
        )
        distance_points = abs(expansion.extreme_price - boundary)
        distance_atr = source.range_atr if source is not None else None
        bars_to_expansion = bars_between(consolidation.end_time, expansion.start_time)

    bars_to_retracement = None
    if retracement is not None and expansion is not None:
        bars_to_retracement = bars_between(
            expansion.start_time, retracement.start_time
        )

    bars_to_reversal = None
    confirmed_at = None
    sweep_side = None
    if cycle.state is CerrState.REVERSAL_CONFIRMED:
        final = cycle.transitions[-1]
        confirmed_at = final.transition_time.isoformat()
        if retracement is not None:
            bars_to_reversal = bars_between(
                retracement.start_time, final.transition_time
            )
        if cycle.reversal_sweep_id is not None:
            found = sweeps.get(cycle.reversal_sweep_id)
            sweep_side = None if found is None else str(found.side)

    return CycleObservation(
        cycle_id=cycle.cycle_id,
        consolidation_id=consolidation.consolidation_id,
        final_state=str(cycle.state),
        invalidation_reason=(
            None
            if cycle.invalidation_reason is None
            else str(cycle.invalidation_reason)
        ),
        direction=None if cycle.direction is None else str(cycle.direction),
        started_at=cycle.created_at.isoformat(),
        ended_at=cycle.updated_at.isoformat(),
        duration_bars=bars_between(cycle.created_at, cycle.updated_at),
        reached_expansion=CerrState.EXPANSION in states,
        reached_retracement=CerrState.RETRACEMENT in states,
        reached_reversal=CerrState.REVERSAL_CONFIRMED in states,
        expansion_id=cycle.expansion_id,
        displacement_id=cycle.displacement_id,
        expansion_distance_points=distance_points,
        expansion_distance_atr=distance_atr,
        bars_to_expansion=bars_to_expansion,
        boundary_breached=boundary,
        retracement_id=cycle.retracement_id,
        retracement_depth_points=(
            None if retracement is None else retracement.depth_points
        ),
        retracement_depth_atr=(
            None if retracement is None else retracement.depth_atr
        ),
        bars_to_retracement=bars_to_retracement,
        reversal_sweep_id=cycle.reversal_sweep_id,
        reversal_sweep_side=sweep_side,
        reversal_displacement_id=cycle.reversal_displacement_id,
        reversal_mss_id=cycle.reversal_mss_id,
        bars_to_reversal=bars_to_reversal,
        reversal_confirmed_at=confirmed_at,
        transition_count=len(cycle.transitions),
        transition_path=">".join(str(s) for s in states),
    )


def observe_cycles(
    bars: Sequence[Bar],
    *,
    consolidation_config: ConsolidationConfig,
    cerr_config: CerrConfig,
    displacements: Sequence[DisplacementEvent] = (),
    sweeps: Sequence[SweepEvent] = (),
    mss_events: Sequence[MssEvent] = (),
    tick_size: float,
    step: int = 1,
) -> tuple[CycleObservation, ...]:
    """
    Replay the series through the UNMODIFIED production state machine and
    record every episode it produces.

    One call = one candidate configuration. Sweeping candidates is how
    the funnel gets measured; nothing here relaxes a production rule to
    make an episode happen.

    Events are consumed in index order: a displacement when the bar that
    CONFIRMED it is reached (not the one it started on -- a leg is not
    knowable until its last bar closes), an MSS when its break bar is
    reached. Nothing reads past the bar being observed.

    A cycle only starts where the consolidation config qualifies a
    window, so with an UNCONFIGURED config this correctly returns
    nothing -- that is the finding, not a failure.
    """
    by_confirmation = _index_by_confirmation(displacements)
    by_break = _mss_by_break(mss_events)
    sweep_by_id = {s.sweep_id: s for s in sweeps}
    displacement_by_id = {d.displacement_id: d for d in displacements}

    rows: list[CycleObservation] = []
    consumed_through = -1

    for index in range(0, len(bars), step):
        if index <= consumed_through:
            continue
        verdict = detect_consolidation(
            bars, index, consolidation_config, tick_size=tick_size
        )
        if verdict is None or not verdict.is_consolidation:
            continue

        cycle = begin_cycle(build_consolidation_event(verdict))
        cursor = index

        while cursor + 1 < len(bars) and cycle.active:
            cursor += 1
            confirmed_here = by_confirmation.get(cursor, ())
            if confirmed_here:
                for displacement in confirmed_here:
                    cycle = observe_displacement(cycle, displacement)
                # The leg's own bars are already accounted for by the
                # displacement's measurements. Feeding the confirming bar
                # through observe_bar as well would measure a retracement
                # from the extreme that same bar just set.
                continue
            cycle = observe_bar(cycle, bars, cursor, cerr_config, tick_size=tick_size)
            if not cycle.active:
                break
            for mss in by_break.get(cursor, ()):
                sweep = sweep_by_id.get(mss.sweep_id)
                reversal_leg = displacement_by_id.get(mss.displacement_id)
                if sweep is None or reversal_leg is None:
                    continue
                cycle = observe_reversal(cycle, sweep, reversal_leg, mss)
                if not cycle.active:
                    break

        rows.append(_summarise_cycle(cycle, bars, sweep_by_id, displacement_by_id))
        # An episode owns its bars: the next cycle starts after it ended,
        # so overlapping cycles cannot double-count the same history.
        consumed_through = cursor

    return tuple(rows)


@dataclass(frozen=True, slots=True)
class Distribution:
    """Descriptive statistics for one measured field. No thresholds are
    suggested and none are implied."""

    field: str
    count: int
    missing: int
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None


def describe(
    rows: Sequence[ConsolidationObservation], fields: Sequence[str]
) -> tuple[Distribution, ...]:
    """Quantiles for each named field, counting how often it was
    unmeasurable.

    `missing` is reported rather than dropped silently: a field that is
    None a third of the time is a fact about the data, and a quantile
    computed over the remainder without saying so would misrepresent it.
    """
    out: list[Distribution] = []
    for field in fields:
        values = [getattr(row, field) for row in rows]
        present = sorted(v for v in values if v is not None)
        if not present:
            out.append(
                Distribution(field, 0, len(values), None, None, None, None, None)
            )
            continue
        quantiles = (
            statistics.quantiles(present, n=4)
            if len(present) > 1
            else [present[0], present[0], present[0]]
        )
        out.append(
            Distribution(
                field=field,
                count=len(present),
                missing=len(values) - len(present),
                minimum=present[0],
                p25=quantiles[0],
                median=quantiles[1],
                p75=quantiles[2],
                maximum=present[-1],
            )
        )
    return tuple(out)


def funnel(rows: Sequence[CycleObservation]) -> dict[str, int]:
    """How far episodes got. The counts a threshold decision needs, with
    failures included by construction."""
    counts = {
        "cycles": len(rows),
        "reached_expansion": sum(1 for r in rows if r.reached_expansion),
        "reached_retracement": sum(1 for r in rows if r.reached_retracement),
        "reached_reversal": sum(1 for r in rows if r.reached_reversal),
    }
    for row in rows:
        counts[f"final:{row.final_state}"] = (
            counts.get(f"final:{row.final_state}", 0) + 1
        )
    return counts


def write_jsonl(rows: Iterable[object], path: Path) -> int:
    """One JSON object per line -- the project's existing wire format, no
    new dependency. Written to a temporary file and moved into place, so
    an interrupted run cannot leave a half-written dataset that looks
    complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    written = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), sort_keys=True))  # type: ignore[call-overload]
            handle.write("\n")
            written += 1
    temporary.replace(path)
    return written
