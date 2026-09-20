"""
CERR threshold sensitivity -- is there a stable definition, or does the
population move under your feet?

The question is NOT "which threshold makes the strategy profitable".
Nothing here computes P&L, ranks a candidate, or calls a region
optimal. The question is whether some broad region of definitions
produces a coherent, non-fragile market-state population -- and if the
surface turns out to be chaotic, that is a finding about CERR worth
having before another engine is built on top of it.

MEASURE ONCE, QUALIFY MANY TIMES. A window's measurements do not depend
on any threshold, so the expensive part is done once per window length
and every candidate is then judged against those same numbers using
`qualify_consolidation` -- the PRODUCTION function, not a copy. A
screening routine with its own reimplementation of the rule would
eventually disagree with the detector it claims to be studying, and the
disagreement would be invisible.

WINDOW LENGTH IS A CONFOUND UNTIL PROVEN OTHERWISE. A threshold like
`range_atr < 5` can encode duration rather than structure: longer
windows mechanically span more range. So profiles are produced per
window length, and a candidate is always recorded WITH the length it
was screened at.

THE SCREEN IS NOT THE FUNNEL. Screening is arithmetic over measured
windows and is cheap. Running the causal CERR replay for a candidate is
not, so only candidates passing a population sanity band go through --
and that band is about population SIZE, never about outcome. Selecting
which candidates to examine by how good their results looked would be
the same error as optimising, arrived at more slowly.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from vo.market.bar import Bar
from vo.valco.lrx_consolidation import (
    ConsolidationConfig,
    ConsolidationMeasurement,
    ConsolidationQualification,
    EfficiencyMeasure,
    measure_consolidation,
    qualify_consolidation,
)


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    """One candidate definition of consolidation, recorded in full with
    every result it produces."""

    window_bars: int
    max_range_atr: float
    max_net_move_atr: float
    max_efficiency_ratio: float
    efficiency_measure: EfficiencyMeasure

    @property
    def label(self) -> str:
        return (
            f"w{self.window_bars}"
            f"/r{self.max_range_atr:g}"
            f"/n{self.max_net_move_atr:g}"
            f"/e{self.max_efficiency_ratio:g}"
            f"/{self.efficiency_measure}"
        )

    def to_consolidation_config(
        self, *, minimum_bars: int, atr_period: int
    ) -> ConsolidationConfig:
        return ConsolidationConfig(
            window_bars=self.window_bars,
            minimum_bars=min(minimum_bars, self.window_bars),
            max_range_atr=self.max_range_atr,
            max_net_move_atr=self.max_net_move_atr,
            max_efficiency_ratio=self.max_efficiency_ratio,
            efficiency_measure=self.efficiency_measure,
            atr_period=atr_period,
        )


class Population(Enum):
    """How a candidate's qualifying population sits, judged on SIZE
    alone. Outcome plays no part -- that is the line between sensitivity
    analysis and optimisation."""

    EMPTY = "EMPTY"
    SPARSE = "SPARSE"
    USABLE = "USABLE"
    PERMISSIVE = "PERMISSIVE"
    DEGENERATE = "DEGENERATE"
    """Admits nearly everything, so it defines nothing."""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ScreenResult:
    candidate: CandidateConfig
    windows_measured: int
    qualifying: int
    qualifying_pct: float
    unmeasurable: int
    population: Population
    median_range_atr: float | None
    median_net_move_atr: float | None
    median_efficiency: float | None
    median_bar_count: float | None


@dataclass(frozen=True, slots=True)
class WindowProfile:
    """Distributions at one window length, so duration can be separated
    from structure."""

    window_bars: int
    n: int
    range_atr: tuple[float | None, float | None, float | None]
    net_move_atr: tuple[float | None, float | None, float | None]
    kaufman_efficiency_ratio: tuple[float | None, float | None, float | None]
    body_efficiency_ratio: tuple[float | None, float | None, float | None]
    mean_range_atr: tuple[float | None, float | None, float | None]
    body_er_above_one: int


def _quartiles(
    values: Sequence[float | None],
) -> tuple[float | None, float | None, float | None]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return (None, None, None)
    if len(present) == 1:
        return (present[0], present[0], present[0])
    q = statistics.quantiles(present, n=4)
    return (q[0], q[1], q[2])


def _median(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def classify_population(qualifying_pct: float) -> Population:
    """Bands are about SIZE and are deliberately crude.

    A definition admitting under a tenth of a percent cannot support a
    study; one admitting over 90% is not a definition at all, it is a
    description of "bars existed". The middle is where a threshold can
    be argued about.
    """
    if qualifying_pct <= 0.0:
        return Population.EMPTY
    if qualifying_pct < 0.1:
        return Population.SPARSE
    if qualifying_pct <= 60.0:
        return Population.USABLE
    if qualifying_pct <= 90.0:
        return Population.PERMISSIVE
    return Population.DEGENERATE


def screen_candidate(
    measurements: Sequence[ConsolidationMeasurement],
    candidate: CandidateConfig,
    *,
    minimum_bars: int,
    atr_period: int,
) -> ScreenResult:
    """Judge already-measured windows against one candidate definition,
    using the production qualification function."""
    config = candidate.to_consolidation_config(
        minimum_bars=minimum_bars, atr_period=atr_period
    )
    qualifying: list[ConsolidationMeasurement] = []
    unmeasurable = 0
    for measurement in measurements:
        verdict = qualify_consolidation(measurement, config)
        if verdict.qualification is ConsolidationQualification.CONSOLIDATION:
            qualifying.append(measurement)
        elif verdict.qualification is ConsolidationQualification.UNMEASURABLE:
            unmeasurable += 1

    total = len(measurements)
    pct = 100.0 * len(qualifying) / total if total else 0.0
    return ScreenResult(
        candidate=candidate,
        windows_measured=total,
        qualifying=len(qualifying),
        qualifying_pct=pct,
        unmeasurable=unmeasurable,
        population=classify_population(pct),
        median_range_atr=_median([m.range_atr for m in qualifying]),
        median_net_move_atr=_median([m.net_move_atr for m in qualifying]),
        median_efficiency=_median(
            [m.efficiency(candidate.efficiency_measure) for m in qualifying]
        ),
        median_bar_count=(
            statistics.median([float(m.bar_count) for m in qualifying])
            if qualifying
            else None
        ),
    )


def measure_windows(
    bars: Sequence[Bar],
    *,
    window_bars: int,
    minimum_bars: int,
    atr_period: int,
    tick_size: float,
    step: int = 1,
) -> tuple[ConsolidationMeasurement, ...]:
    """Every window of one length, measured once. Threshold-free."""
    config = ConsolidationConfig(
        window_bars=window_bars,
        minimum_bars=min(minimum_bars, window_bars),
        atr_period=atr_period,
    )
    out: list[ConsolidationMeasurement] = []
    for index in range(0, len(bars), max(1, step)):
        measurement = measure_consolidation(bars, index, config, tick_size=tick_size)
        if measurement is not None:
            out.append(measurement)
    return tuple(out)


def profile_window_length(
    measurements: Sequence[ConsolidationMeasurement], *, window_bars: int
) -> WindowProfile:
    """Distributions at one length, plus how often the body ratio
    exceeded one there."""
    return WindowProfile(
        window_bars=window_bars,
        n=len(measurements),
        range_atr=_quartiles([m.range_atr for m in measurements]),
        net_move_atr=_quartiles([m.net_move_atr for m in measurements]),
        kaufman_efficiency_ratio=_quartiles(
            [m.kaufman_efficiency_ratio for m in measurements]
        ),
        body_efficiency_ratio=_quartiles(
            [m.body_efficiency_ratio for m in measurements]
        ),
        mean_range_atr=_quartiles([m.mean_range_atr for m in measurements]),
        body_er_above_one=sum(
            1
            for m in measurements
            if m.body_efficiency_ratio is not None
            and m.body_efficiency_ratio > 1.0
        ),
    )


@dataclass(frozen=True, slots=True)
class BodyErAnomaly:
    """One window where the body ratio exceeded one, with the arithmetic
    that produced it."""

    start_time: str
    bar_count: int
    net_move_points: float
    total_path_points: float
    body_efficiency_ratio: float
    kaufman_efficiency_ratio: float | None


def body_er_anomalies(
    measurements: Sequence[ConsolidationMeasurement], *, limit: int = 10
) -> tuple[BodyErAnomaly, ...]:
    """
    Windows where body efficiency exceeded 1.0.

    WHY THIS HAPPENS, AND WHY IT IS NOT A BUG IN THE ARITHMETIC. The two
    ratios measure different paths:

        kaufman = |close_n - close_0|      / sum|close_i - close_i-1|
        body    = |close_n - open_0|       / sum|close_i - open_i|

    Kaufman's denominator is the SAME path as its numerator, walked step
    by step, so the ratio cannot exceed one. The body ratio's
    denominator sums only candle BODIES and never sees the gaps between
    one bar's close and the next bar's open. When a window contains
    gaps -- a session break, a weekend, a fast market -- the numerator
    crosses distance the denominator never counted, and the ratio can
    exceed one without any step being mismeasured.

    So it is not bounded path efficiency. It is BODY-ONLY directional
    efficiency, and on gapped instruments it is not on a comparable
    scale to Kaufman. Recorded, documented, never silently clamped: a
    clamp would hide exactly the windows where the two measures disagree
    most, which are the interesting ones.
    """
    rows = [
        BodyErAnomaly(
            start_time=m.start_time.isoformat(),
            bar_count=m.bar_count,
            net_move_points=m.net_move_points,
            total_path_points=m.total_path_points,
            body_efficiency_ratio=m.body_efficiency_ratio,
            kaufman_efficiency_ratio=m.kaufman_efficiency_ratio,
        )
        for m in measurements
        if m.body_efficiency_ratio is not None and m.body_efficiency_ratio > 1.0
    ]
    rows.sort(key=lambda r: r.body_efficiency_ratio, reverse=True)
    return tuple(rows[:limit])


def build_grid(
    *,
    window_bars: Sequence[int],
    range_atr: Sequence[float],
    net_move_atr: Sequence[float],
    efficiency: Sequence[float],
    measures: Sequence[EfficiencyMeasure],
) -> tuple[CandidateConfig, ...]:
    """The full Cartesian product, in a deterministic order."""
    return tuple(
        CandidateConfig(
            window_bars=w,
            max_range_atr=r,
            max_net_move_atr=n,
            max_efficiency_ratio=e,
            efficiency_measure=m,
        )
        for w in window_bars
        for r in range_atr
        for n in net_move_atr
        for e in efficiency
        for m in measures
    )


def select_for_funnel(
    screens: Sequence[ScreenResult], *, limit: int
) -> tuple[ScreenResult, ...]:
    """Which candidates are worth the cost of a causal replay.

    Chosen by population SIZE and then spread evenly across the
    remaining grid in its deterministic order. Never by outcome:
    selecting which candidates to examine by how good their results
    looked would be optimisation arrived at more slowly.
    """
    usable = [s for s in screens if s.population is Population.USABLE]
    if len(usable) <= limit or limit <= 0:
        return tuple(usable)
    stride = len(usable) / limit
    return tuple(usable[int(i * stride)] for i in range(limit))
