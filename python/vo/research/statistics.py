"""
Shared pure-stdlib statistics primitives -- Phase 15/17/17a's common
foundation (vo.research, layer 5).

WHY THIS EXISTS. vo.telemetry.regime_validation (Phase 13a's Validation
Report v1) needed percentiles, a two-group rank-sum comparison, and a
proportion-vs-null-rate significance test, and wrote them itself as
private helpers since nothing else needed them yet. Every research module
this project is now adding on top of the frozen regime baseline -- a
standalone Hurst characterization (Phase 15a), Efficiency Ratio (Phase
15b), and the conditional Markov transition study (Phase 17) -- needs the
exact same primitives again. Rather than duplicate a third and fourth
time (this project's own convention only condones duplicating something
genuinely tiny, like vo.telemetry's shared `_feature_value` lookup, to
dodge a real import cycle -- these are not that: percentiles and a
tie-corrected Mann-Whitney U are real, non-trivial, hand-verified
formulas, and drift between two copies would be a real correctness bug,
not a cosmetic one), this module is their one shared, single-verified
home. vo.telemetry.regime_validation was updated in the same change to
import from here instead of defining its own copies -- a behavior-
preserving refactor, not a second implementation.

WHY PURE STDLIB, STILL. This project's dependency discipline
(pyproject.toml carries no numpy/scipy/pandas; see audit R10 -- keep the
Python 3.13 pin's risk surface low) holds for every module in this layer
except where the user has explicitly approved an exception for a specific
one (HMM, see vo.research's own package docstring and
architecture/vo-phase-plan.md's open items -- numpy + hmmlearn approved
for HMM alone, not a blanket change). Percentiles, Mann-Whitney, the
binomial test and the Wilson interval have simple, well-known closed-form
or normal-approximation formulas that do not need numpy's help; every one
of them below is independently verified against hand-computed exact
values in tests/unit/test_research_statistics.py.

WindowStats/build_window_distributions and OutOfSampleRow/build_out_of_
sample_report (added alongside Phase 15b) generalize a second pattern
that showed up identically in Phase 15a's Hurst report and would have
been copy-pasted a second time for Efficiency Ratio: "describe this
window length's rolling samples, plus their own lag-1 stability" and "a
single fixed chronological train/test split per window length." Both are
generic over any dict[int, list[tuple[datetime, float]]] rolling-sample
series -- nothing Hurst- or ER-specific about the shape -- so they moved
here rather than being defined a second time in
vo.research.efficiency_ratio_report.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

Z95 = 1.959963984540054  # two-sided 95% normal quantile (for the Wilson interval)


def percentile(values: Sequence[float], pct: int) -> float:
    """The `pct`-th percentile (1-99) of `values`. Falls back to the
    single value for n=1 (statistics.quantiles requires n>=2); callers
    with n=0 must not call this."""
    if len(values) == 1:
        return values[0]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[pct - 1]


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class MannWhitneyResult:
    """Two-group rank-sum comparison (tie-corrected normal approximation
    -- see this module's docstring for why this test and why the
    approximation is appropriate here). `u` is U for the FIRST group
    passed in. A small `p_value` means the two groups' distributions are
    unlikely to be identical -- it says nothing about how much they
    overlap or whether the difference is large enough to act on; read
    alongside each group's own descriptive stats (e.g. p25/p75) for
    that."""

    label: str
    n1: int
    n2: int
    u: float
    z: float | None
    p_value: float | None


def mann_whitney_u(x: Sequence[float], y: Sequence[float], *, label: str) -> MannWhitneyResult:
    """Standard Mann-Whitney U with mid-rank tie correction and a normal
    approximation for the p-value (two-sided). Pure stdlib; no scipy.
    Accurate at these sample sizes (typically hundreds to thousands per
    group) -- the normal approximation to the U distribution is
    excellent well before n1, n2 reach the low hundreds."""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return MannWhitneyResult(label=label, n1=n1, n2=n2, u=float("nan"), z=None, p_value=None)

    combined = sorted([(v, 0) for v in x] + [(v, 1) for v in y], key=lambda p: p[0])
    n = n1 + n2
    ranks = [0.0] * n
    tie_term = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0  # 1-based, averaged across the tie block
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        t = j - i + 1
        if t > 1:
            tie_term += t**3 - t
        i = j + 1

    rank_sum_x = sum(r for r, (_v, grp) in zip(ranks, combined, strict=True) if grp == 0)
    u1 = rank_sum_x - n1 * (n1 + 1) / 2.0

    mean_u = n1 * n2 / 2.0
    var_u = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0

    if var_u <= 0:
        z, p_value = None, None
    else:
        z = (u1 - mean_u) / math.sqrt(var_u)
        p_value = min(1.0, 2.0 * (1.0 - normal_cdf(abs(z))))

    return MannWhitneyResult(label=label, n1=n1, n2=n2, u=u1, z=z, p_value=p_value)


@dataclass(frozen=True)
class BinomialTest:
    """Normal-approximation two-sided test of an observed proportion
    against a stated null rate -- accurate at these sample sizes."""

    null_rate: float
    z: float | None
    p_value: float | None


def binomial_test(successes: int, n: int, null_rate: float) -> BinomialTest | None:
    if n == 0:
        return BinomialTest(null_rate=null_rate, z=None, p_value=None)
    mean = n * null_rate
    var = n * null_rate * (1.0 - null_rate)
    if var <= 0:
        return BinomialTest(null_rate=null_rate, z=None, p_value=None)
    # Continuity correction: move the observed count half a step toward
    # the null mean before standardizing.
    observed = successes + (0.5 if successes < mean else -0.5 if successes > mean else 0.0)
    z = (observed - mean) / math.sqrt(var)
    p_value = min(1.0, 2.0 * (1.0 - normal_cdf(abs(z))))
    return BinomialTest(null_rate=null_rate, z=z, p_value=p_value)


def wilson_score_interval(
    successes: int, n: int, *, z: float = Z95
) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return (max(0.0, low), min(1.0, high))


@dataclass(frozen=True)
class GroupStats:
    """Descriptive stats for one labeled group of values -- the generic
    shape every research report's "distribution of X within bucket Y"
    table needs (by regime, by session, by window length, by
    before/after an out-of-sample split, ...). Introduced for Phase 15a's
    Hurst report; regime_validation.py's own EvidenceGroupStats (a
    narrower, ER/Hurst-specific predecessor) stays as-is rather than
    being migrated mid-flight, since it is already shipped and verified
    -- new callers should use this one."""

    label: str
    n: int
    mean: float
    median: float
    stdev: float | None  # None when n < 2 -- statistics.stdev needs 2+ points
    p25: float
    p75: float
    minimum: float
    maximum: float


def describe(values: Sequence[float], label: str) -> GroupStats:
    """Build a GroupStats from a non-empty sequence of values. Callers
    must not call this with an empty sequence (mean/median/min/max are
    undefined) -- skip empty groups before calling, same convention as
    regime_validation.py's own _group_stats."""
    return GroupStats(
        label=label,
        n=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        stdev=statistics.stdev(values) if len(values) >= 2 else None,
        p25=percentile(values, 25),
        p75=percentile(values, 75),
        minimum=min(values),
        maximum=max(values),
    )


def lag1_autocorrelation(values: Sequence[float]) -> float | None:
    """Correlation between consecutive values in `values` (assumed
    already in their natural, e.g. chronological, order) -- a simple,
    honest read on how noisy vs. stable a rolling estimate series is at
    a given configuration (window length, stride, ...). None when there
    are fewer than 3 points (statistics.correlation needs 2+ pairs) or
    either half is constant (undefined correlation, not zero)."""
    if len(values) < 3:
        return None
    try:
        return statistics.correlation(values[:-1], values[1:])
    except statistics.StatisticsError:
        return None


@dataclass(frozen=True)
class WindowStats:
    """One window length's overall distribution, plus a lag-1
    autocorrelation of its own consecutive rolling estimates as a simple
    "how noisy is this window length's read" stability signal -- higher
    means consecutive samples agree more, lower/near-zero means the
    estimate is jumpier at that window length. Generic over any bounded
    or continuous per-bar measurement sampled across window lengths --
    introduced for Phase 15a's Hurst report, reused as-is by Phase 15b's
    Efficiency Ratio report rather than redefined a second time."""

    window: int
    stats: GroupStats
    stability_lag1_autocorrelation: float | None


def build_window_distributions(
    rolling: dict[int, list[tuple[object, float]]], *, window_lengths: Sequence[int]
) -> tuple[WindowStats, ...]:
    """Describe each window length's rolling sample series in `rolling`
    (period -> chronological (timestamp, value) pairs, e.g. the output of
    a module's own build_rolling_<metric> function). Window lengths with
    no samples are skipped, never coerced into an empty/fake row."""
    out: list[WindowStats] = []
    for period in window_lengths:
        samples = rolling.get(period, [])
        if not samples:
            continue
        values = [v for _t, v in samples]
        out.append(
            WindowStats(
                window=period,
                stats=describe(values, f"window={period}"),
                stability_lag1_autocorrelation=lag1_autocorrelation(values),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class OutOfSampleRow:
    """One window length's fixed chronological train/test split -- see
    each report module's own OUT-OF-SAMPLE CAVEAT for why this is a
    first honest check, not a real walk-forward protocol. Generic over
    any windowed rolling series, same reuse rationale as WindowStats
    above."""

    window: int
    train: GroupStats
    test: GroupStats
    median_delta: float  # test.median - train.median, signed


def build_out_of_sample_report(
    rolling: dict[int, list[tuple[object, float]]],
    *,
    window_lengths: Sequence[int],
    split_fraction: float = 0.7,
) -> tuple[OutOfSampleRow, ...]:
    """A single fixed chronological split per window length. Samples are
    assumed already chronological (the caller's own rolling-sample
    builder walks bars in order); the split point is by SAMPLE COUNT,
    not calendar time, so both halves have comparable statistical power
    even if trading activity is uneven."""
    if not 0.0 < split_fraction < 1.0:
        raise ValueError(f"split_fraction must be in (0, 1), got {split_fraction}")

    out: list[OutOfSampleRow] = []
    for period in window_lengths:
        samples = rolling.get(period, [])
        if len(samples) < 4:  # need at least 2 points per half to describe() meaningfully
            continue
        cut = int(len(samples) * split_fraction)
        cut = max(1, min(len(samples) - 1, cut))
        train_values = [v for _t, v in samples[:cut]]
        test_values = [v for _t, v in samples[cut:]]
        train = describe(train_values, f"window={period} train")
        test = describe(test_values, f"window={period} test")
        out.append(
            OutOfSampleRow(
                window=period, train=train, test=test, median_delta=test.median - train.median
            )
        )
    return tuple(out)
