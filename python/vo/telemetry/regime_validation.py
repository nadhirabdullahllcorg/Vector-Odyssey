"""
Regime Engine Validation Report -- v1 (Phase 13a research follow-up).

WHY THIS EXISTS. scripts/backtest_regime.py's own report (regime_report.py)
answers "what happened, in aggregate, over this one run." It does not
answer whether that aggregate is trustworthy: is the anticipation lean's
0.53 match rate meaningfully above a naive baseline once class imbalance
is accounted for, does the engine behave similarly across different market
periods, and do ER/Hurst actually separate RETRACEMENT from REVERSAL or
only appear to on the means alone. This module answers those questions,
from the SAME already-emitted RegimeState/RegimeSegment/RegimeTransition
log a backtest run already produced -- no new computation reaches the
classifier, no new [ICT]/[VO-D]/[VO-H] concept is introduced, and nothing
here can become a decision-path input (gate G2 unchanged: this is research
analysis of recorded evidence, several layers removed from any Signal).

WHAT THIS DELIBERATELY DOES NOT DO. It does not propose a threshold rule
("IF ER > x THEN REVERSAL"). A statistically real difference in means
between two overlapping distributions is not by itself a usable decision
rule -- that requires calibration, out-of-sample validation, and a
G6-style design review, none of which this module performs. Read its
Mann-Whitney comparison as "worth investigating further," never as
"validated."

CLASS IMBALANCE. The reported 0.53 anticipation match rate in the plain
backtest report reads as "better than a coin flip," but RETRACEMENT and
REVERSAL do not occur with equal frequency (roughly 56%/44% in the first
runs of this report) -- so the correct baseline to beat is NOT 50%, it is
"always guess the majority outcome." build_accuracy_validation reports
both comparisons explicitly, because a lean that merely tracks the base
rate is not adding information.

STATISTICS USED, AND WHY THEY NEED NO NEW DEPENDENCY. Everything here is
pure `statistics`/`math` stdlib, matching this project's existing minimal-
dependency discipline (pyproject.toml carries no numpy/scipy/pandas).
Percentiles use `statistics.quantiles(..., n=100, method="inclusive")`.
The two-group comparison (RETRACEMENT vs REVERSAL, on ER and on Hurst) is
a Mann-Whitney U test with the standard tie-corrected normal
approximation -- appropriate here because ER/Hurst are bounded, often
skewed ratios, and the sample sizes (thousands) make the normal
approximation to the U distribution accurate. The anticipation-rate
significance checks use the normal approximation to the binomial
(again accurate at n in the thousands) plus a Wilson score interval,
which is the standard well-behaved interval for a sample proportion
(unlike the naive Wald interval, it stays inside [0, 1] and is not
misleading near 0/1 or at these sample sizes).

TEMPORAL STABILITY (build_period_breakdown). Splits the SAME backtest run
by calendar year (UTC) and re-runs regime_report.build_regime_report on
each year's slice, reusing that module's own duration/ER/Hurst/transition/
anticipation accounting rather than re-deriving it -- one dataset, sliced,
not a second computation that could quietly disagree. Two known
approximations, both named rather than hidden: (1) a segment spanning a
year boundary is duration-attributed correctly (bar-counted per slice,
never wall-clock), but its `segment_count`/`total_segments` bookkeeping
in the per-period RegimeReport counts it once per year it touches, since
segments are not literally split at the boundary; (2) anticipation
accuracy for a resolution within a few bars of a year boundary is
attributed to the year the RESOLUTION itself occurred in, even if its
originating pullback began in the prior year -- a handful of instances
per boundary, immaterial at these sample sizes. This period breakdown is
also the closest thing to an out-of-sample check available before any
threshold has actually been tuned: if the most recent complete period's
figures diverge sharply from earlier ones, that is real signal; it is
NOT a substitute for holding out data after tuning starts.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.regime import AnticipatedResolution, RegimeState, RegimeTransition, RegimeType
from vo.research.statistics import (
    BinomialTest,
    MannWhitneyResult,
    binomial_test,
    mann_whitney_u,
    percentile,
    wilson_score_interval,
)
from vo.research.transitions import TransitionMatrix
from vo.telemetry.regime_feed import RegimeSegment
from vo.telemetry.regime_report import RegimeReport, build_regime_report
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, is_rth, session_at

_ER_FEATURE = "efficiency_ratio"
_HURST_FEATURE = "hurst_exponent"

# All five regimes, in the same declaration order as RegimeType -- the
# fixed row/column order every rendered matrix and table uses, so two
# reports are always visually comparable.
_ALL_REGIMES: tuple[RegimeType, ...] = tuple(RegimeType)


def _feature_value(state: RegimeState, name: str) -> float | None:
    """Same lookup as vo.telemetry.regime_report._feature_value and
    vo.telemetry.regime_feed._feature_value -- duplicated locally (not
    imported) to avoid a runtime import cycle, the same tradeoff already
    made twice elsewhere in this package."""
    for feature in state.supporting_features:
        if feature.name == name:
            return feature.value
    return None


# ── 1. temporal stability ────────────────────────────────────────────────


@dataclass(frozen=True)
class PeriodReport:
    """One calendar-year (UTC) slice of a backtest run, reusing
    regime_report.RegimeReport for everything within it -- see this
    module's docstring for what "year" attribution means at a boundary."""

    label: str  # "2023", "2024", ... or "2026 (YTD)" for an incomplete final year
    report: RegimeReport


def build_period_breakdown(
    segments: Sequence[RegimeSegment],
    states: Sequence[RegimeState],
    transitions_log: Sequence[RegimeTransition],
    bars: Sequence[Bar],
    *,
    minutes_per_bar: float = 1.0,
) -> tuple[PeriodReport, ...]:
    """Split a backtest run by calendar year (UTC, from each bar's own
    open_time_utc) and build a full RegimeReport for each year. Years are
    discovered from the data itself -- never a hardcoded list -- so this
    generalizes to any run without editing. The final period is labeled
    "(YTD)" when its last bar falls before December of its year (a real
    calendar year still in progress at the time of the run, not a
    completed one)."""
    if not bars:
        return ()
    ordered_bars = sorted(bars, key=lambda b: b.open_time_utc)
    years = sorted({b.open_time_utc.year for b in ordered_bars})

    periods: list[PeriodReport] = []
    for year in years:
        year_bars = [b for b in ordered_bars if b.open_time_utc.year == year]
        year_states = tuple(s for s in states if s.observed_at.year == year)
        year_transitions = tuple(t for t in transitions_log if t.observed_at.year == year)
        last_bar = year_bars[-1]
        sub = build_regime_report(
            segments=tuple(segments),  # full list -- a boundary-spanning segment still
            states=year_states,  # attributes its in-year bars correctly (see docstring)
            transitions_log=year_transitions,
            history_end_utc=last_bar.open_time_utc,
            minutes_per_bar=minutes_per_bar,
            bar_count=len(year_bars),
            bars=year_bars,
        )
        is_complete_year = last_bar.open_time_utc.month == 12 and last_bar.open_time_utc.day >= 28
        label = f"{year}" if is_complete_year else f"{year} (YTD)"
        periods.append(PeriodReport(label=label, report=sub))
    return tuple(periods)


# ── 2. transition matrix ─────────────────────────────────────────────────


# ── 3. duration distributions ────────────────────────────────────────────


@dataclass(frozen=True)
class DurationDistribution:
    """A regime's duration spread, in minutes -- the mean alone hides a
    right-skewed distribution (most runs short, a few very long), which
    is exactly what a mean noticeably above the median already implies."""

    regime: RegimeType
    n: int
    median: float
    p25: float
    p75: float
    p90: float
    p95: float
    minimum: float
    maximum: float


def build_duration_distributions(
    durations_by_regime: dict[RegimeType, list[float]],
) -> tuple[DurationDistribution, ...]:
    """Takes the same per-regime duration lists
    regime_report.durations_by_regime produces -- callers build those once
    (bar-counted, trading-minute-accurate) and pass them in here, so this
    module never re-derives duration accounting of its own."""
    out: list[DurationDistribution] = []
    for regime in _ALL_REGIMES:
        values = durations_by_regime.get(regime, [])
        if not values:
            continue
        out.append(
            DurationDistribution(
                regime=regime,
                n=len(values),
                median=percentile(values, 50),
                p25=percentile(values, 25),
                p75=percentile(values, 75),
                p90=percentile(values, 90),
                p95=percentile(values, 95),
                minimum=min(values),
                maximum=max(values),
            )
        )
    return tuple(out)


# ── 4. ER/Hurst relationship ─────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceGroupStats:
    """Descriptive stats for one (regime, feature) group -- ER or Hurst,
    within one regime's recorded RegimeState population."""

    regime: RegimeType
    feature: str  # "efficiency_ratio" or "hurst_exponent"
    n: int
    mean: float
    median: float
    stdev: float | None  # None when n < 2 -- statistics.stdev needs 2+ points
    p25: float
    p75: float


@dataclass(frozen=True)
class EvidenceComparison:
    er_by_regime: tuple[EvidenceGroupStats, ...]
    hurst_by_regime: tuple[EvidenceGroupStats, ...]
    er_retracement_vs_reversal: MannWhitneyResult
    hurst_retracement_vs_reversal: MannWhitneyResult


def _group_stats(regime: RegimeType, feature: str, values: list[float]) -> EvidenceGroupStats:
    return EvidenceGroupStats(
        regime=regime,
        feature=feature,
        n=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        stdev=statistics.stdev(values) if len(values) >= 2 else None,
        p25=percentile(values, 25),
        p75=percentile(values, 75),
    )


def build_evidence_comparison(states: Sequence[RegimeState]) -> EvidenceComparison:
    """Per-regime ER/Hurst distributions, plus the one comparison the
    plain backtest report's mean-only table cannot answer: is the
    RETRACEMENT/REVERSAL mean-ER gap it shows a real distributional
    difference, or two heavily overlapping distributions with different
    means. See the module docstring: this is hypothesis-generating, not
    a rule."""
    er_values: dict[RegimeType, list[float]] = {}
    hurst_values: dict[RegimeType, list[float]] = {}
    for state in states:
        er = _feature_value(state, _ER_FEATURE)
        if er is not None:
            er_values.setdefault(state.regime, []).append(er)
        hurst = _feature_value(state, _HURST_FEATURE)
        if hurst is not None:
            hurst_values.setdefault(state.regime, []).append(hurst)

    er_stats = tuple(
        _group_stats(regime, _ER_FEATURE, er_values[regime])
        for regime in _ALL_REGIMES
        if er_values.get(regime)
    )
    hurst_stats = tuple(
        _group_stats(regime, _HURST_FEATURE, hurst_values[regime])
        for regime in _ALL_REGIMES
        if hurst_values.get(regime)
    )

    er_mw = mann_whitney_u(
        er_values.get(RegimeType.RETRACEMENT, []),
        er_values.get(RegimeType.REVERSAL, []),
        label="efficiency_ratio: RETRACEMENT vs REVERSAL",
    )
    hurst_mw = mann_whitney_u(
        hurst_values.get(RegimeType.RETRACEMENT, []),
        hurst_values.get(RegimeType.REVERSAL, []),
        label="hurst_exponent: RETRACEMENT vs REVERSAL",
    )
    return EvidenceComparison(
        er_by_regime=er_stats,
        hurst_by_regime=hurst_stats,
        er_retracement_vs_reversal=er_mw,
        hurst_retracement_vs_reversal=hurst_mw,
    )


# ── 5. anticipation accuracy, class-imbalance-aware and sliced ──────────


@dataclass(frozen=True)
class AccuracySlice:
    """One bucket of a conditional-accuracy breakdown (by session,
    confidence quartile, or pullback-duration quartile)."""

    label: str
    n: int
    matched: int
    rate: float | None


@dataclass(frozen=True)
class AccuracyValidation:
    resolutions: int
    to_retracement: int
    to_reversal: int
    leaned: int
    matched: int
    rate: float | None
    majority_class: RegimeType | None
    majority_baseline_rate: float | None
    vs_coin_flip: BinomialTest | None
    vs_majority_baseline: BinomialTest | None
    wilson_ci: tuple[float, float] | None
    by_session: tuple[AccuracySlice, ...]
    by_rth: tuple[AccuracySlice, ...]
    by_confidence_quartile: tuple[AccuracySlice, ...]
    by_pullback_duration_quartile: tuple[AccuracySlice, ...]


def _quartile_label(value: float, cuts: tuple[float, float, float]) -> str:
    q1, q2, q3 = cuts
    if value <= q1:
        return "Q1 (lowest)"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4 (highest)"


def build_accuracy_validation(
    states: Sequence[RegimeState],
    *,
    session_config: SessionConfig | None = None,
) -> AccuracyValidation:
    """Everything regime_report._anticipation_accuracy computes (same
    one-hop supersedes lookup, same "leaned"/"matched"/"rate" definitions
    -- this does not change what those numbers mean), plus: the class-
    imbalance-aware baseline comparison, significance tests against BOTH
    50% and the majority-class baseline, a Wilson interval, and three
    conditional-accuracy slices (session, the pullback's own recorded
    confidence, and how long the pullback ran before resolving)."""
    by_id = {s.object_id: s for s in states}

    def _pullback_origin(state: RegimeState) -> RegimeState:
        """Walk supersedes back through however many lean-refresh records
        preceded this one, to the record where the pullback FIRST began
        (see the module docstring on why: a lean can be re-recorded
        several times before resolution, each superseding the last)."""
        cur = state
        while cur.supersedes is not None:
            prior = by_id.get(cur.supersedes)
            if prior is None or prior.regime is not RegimeType.PULLBACK_UNRESOLVED:
                break
            cur = prior
        return cur

    resolutions = 0
    to_ret = 0
    to_rev = 0
    leaned = 0
    matched = 0
    session_hits: dict[str, list[bool]] = {}
    rth_hits: dict[str, list[bool]] = {}
    confidence_values: list[float] = []
    duration_values: list[float] = []
    leaned_records: list[tuple[RegimeState, RegimeState, bool]] = []  # (resolution, prior, matched)

    for state in states:
        if state.regime is RegimeType.RETRACEMENT:
            resolutions += 1
            to_ret += 1
        elif state.regime is RegimeType.REVERSAL:
            resolutions += 1
            to_rev += 1
        else:
            continue
        prior = by_id.get(state.supersedes) if state.supersedes else None
        lean = prior.anticipated_resolution if prior is not None else None
        if lean not in (AnticipatedResolution.RETRACEMENT, AnticipatedResolution.REVERSAL):
            continue
        leaned += 1
        is_match = (
            lean is AnticipatedResolution.RETRACEMENT and state.regime is RegimeType.RETRACEMENT
        ) or (lean is AnticipatedResolution.REVERSAL and state.regime is RegimeType.REVERSAL)
        if is_match:
            matched += 1
        leaned_records.append((state, prior, is_match))  # type: ignore[arg-type]

        if session_config is not None:
            window = session_at(state.observed_at.astimezone(session_config.zone), session_config)
            session_name = window.name if window is not None else OFF_SESSION_LABEL
            session_hits.setdefault(session_name, []).append(is_match)
            rth_label = (
                "RTH"
                if is_rth(state.observed_at.astimezone(session_config.zone), session_config)
                else "NON_RTH"
            )
            rth_hits.setdefault(rth_label, []).append(is_match)

        confidence_values.append(prior.confidence)  # type: ignore[union-attr]
        origin = _pullback_origin(prior)  # type: ignore[arg-type]
        duration_minutes = (state.observed_at - origin.observed_at).total_seconds() / 60.0
        duration_values.append(duration_minutes)

    rate = (matched / leaned) if leaned else None

    majority_class: RegimeType | None = None
    majority_baseline_rate: float | None = None
    vs_majority: BinomialTest | None = None
    if resolutions:
        majority_class = RegimeType.RETRACEMENT if to_ret >= to_rev else RegimeType.REVERSAL
        majority_baseline_rate = max(to_ret, to_rev) / resolutions
    if leaned:
        vs_coin_flip = binomial_test(matched, leaned, 0.5)
        if majority_baseline_rate is not None:
            vs_majority = binomial_test(matched, leaned, majority_baseline_rate)
        wilson = wilson_score_interval(matched, leaned)
    else:
        vs_coin_flip = None
        wilson = None

    by_session = tuple(
        AccuracySlice(
            label=name,
            n=len(hits),
            matched=sum(hits),
            rate=(sum(hits) / len(hits)) if hits else None,
        )
        for name, hits in sorted(session_hits.items())
    )

    by_rth = tuple(
        AccuracySlice(
            label=name,
            n=len(hits),
            matched=sum(hits),
            rate=(sum(hits) / len(hits)) if hits else None,
        )
        for name, hits in sorted(rth_hits.items())
    )

    confidence_pairs = zip(leaned_records, confidence_values, strict=True)
    by_confidence_quartile = _quartile_slices(
        [(conf, is_match) for (_s, _p, is_match), conf in confidence_pairs]
    )
    duration_pairs = zip(leaned_records, duration_values, strict=True)
    by_duration_quartile = _quartile_slices(
        [(dur, is_match) for (_s, _p, is_match), dur in duration_pairs]
    )

    return AccuracyValidation(
        resolutions=resolutions,
        to_retracement=to_ret,
        to_reversal=to_rev,
        leaned=leaned,
        matched=matched,
        rate=rate,
        majority_class=majority_class,
        majority_baseline_rate=majority_baseline_rate,
        vs_coin_flip=vs_coin_flip,
        vs_majority_baseline=vs_majority,
        wilson_ci=wilson,
        by_session=by_session,
        by_rth=by_rth,
        by_confidence_quartile=by_confidence_quartile,
        by_pullback_duration_quartile=by_duration_quartile,
    )


def _quartile_slices(paired: list[tuple[float, bool]]) -> tuple[AccuracySlice, ...]:
    """Bucket (value, matched) pairs into quartiles of `value` and report
    the match rate per bucket. Fewer than 4 distinct values collapses
    gracefully (statistics.quantiles still returns 3 cutpoints; ties at a
    cutpoint fall into the lower bucket, which is fine for a descriptive
    breakdown, not a rule)."""
    if len(paired) < 4:
        return ()
    values = [v for v, _m in paired]
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    buckets: dict[str, list[bool]] = {}
    for value, is_match in paired:
        label = _quartile_label(value, (q1, q2, q3))
        buckets.setdefault(label, []).append(is_match)
    order = ["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"]
    return tuple(
        AccuracySlice(
            label=label,
            n=len(buckets[label]),
            matched=sum(buckets[label]),
            rate=(sum(buckets[label]) / len(buckets[label])) if buckets[label] else None,
        )
        for label in order
        if label in buckets
    )


# ── rendering ─────────────────────────────────────────────────────────────


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _fmt_p(p_value: float | None) -> str:
    if p_value is None:
        return "n/a"
    if p_value < 0.0001:
        return "<0.0001"
    return f"{p_value:.4f}"


def render_validation_report_markdown(
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    periods: Sequence[PeriodReport],
    transition_matrix: TransitionMatrix,
    durations: Sequence[DurationDistribution],
    evidence: EvidenceComparison,
    accuracy: AccuracyValidation,
) -> str:
    lines: list[str] = []
    lines.append(f"# Regime Engine Validation Report v1 — {instrument_key} {timeframe_canonical}")
    lines.append("")
    lines.append(f"Generated: {generated_utc.isoformat()} (UTC)")
    lines.append("")
    lines.append(
        "This is a research validation pass over an already-completed backtest run "
        "(vo.telemetry.regime_feed/regime_report), not a new computation and not a "
        "change to the classifier. Gate G2 unchanged: nothing here reaches a trade "
        "decision. Read every comparison below as hypothesis-generating, never as a "
        "threshold rule to implement directly -- see this module's own docstring."
    )
    lines.append("")

    # 1. Temporal stability
    lines.append("## 1. Temporal stability")
    lines.append("")
    lines.append(
        "Same run, split by calendar year (UTC). If the engine behaves consistently "
        "across different market environments, these rows should look broadly "
        "similar; a period that stands out is worth a closer look before trusting "
        "any run-wide average."
    )
    lines.append("")
    lines.append(
        "| Period | Bars | EXPANSION share | PULLBACK share | Mean ER (EXP/PB) | "
        "Mean Hurst (EXP/PB) | Resolutions | Lean match rate |"
    )
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for period in periods:
        r = period.report
        by_regime = {s.regime: s for s in r.per_regime}
        exp = by_regime.get(RegimeType.EXPANSION)
        pb = by_regime.get(RegimeType.PULLBACK_UNRESOLVED)
        exp_share = f"{exp.share * 100:.1f}%" if exp else "n/a"
        pb_share = f"{pb.share * 100:.1f}%" if pb else "n/a"
        exp_er = _fmt(exp.mean_efficiency_ratio if exp else None, 2)
        pb_er = _fmt(pb.mean_efficiency_ratio if pb else None, 2)
        er_pair = f"{exp_er}/{pb_er}"
        exp_hurst = _fmt(exp.mean_hurst if exp else None, 2)
        pb_hurst = _fmt(pb.mean_hurst if pb else None, 2)
        hurst_pair = f"{exp_hurst}/{pb_hurst}"
        pa = r.anticipation
        rate_str = _fmt(pa.rate, 2) if pa.rate is not None else "n/a"
        lines.append(
            f"| {period.label} | {r.bar_count} | {exp_share} | {pb_share} | "
            f"{er_pair} | {hurst_pair} | {pa.resolutions} | {rate_str} |"
        )
    lines.append("")
    lines.append(
        "Per-period detail (regime distribution, transitions, anticipation accuracy) "
        "follows the same table format as the plain backtest report, one subsection "
        "per period:"
    )
    lines.append("")
    for period in periods:
        r = period.report
        lines.append(f"### {period.label}")
        lines.append("")
        span = "n/a"
        if r.first_utc is not None and r.last_utc is not None:
            span = f"{r.first_utc.isoformat()} → {r.last_utc.isoformat()} (UTC)"
        lines.append(f"- Bars: **{r.bar_count}** — Span: {span}")
        pa = r.anticipation
        lines.append(
            f"- Resolutions: **{pa.resolutions}** ({pa.to_retracement} retracement, "
            f"{pa.to_reversal} reversal) — leaned: {pa.leaned} — matched: {pa.matched} — "
            f"rate: {_fmt(pa.rate, 2) if pa.rate is not None else 'n/a'}"
        )
        lines.append("")
        lines.append("| Regime | Count | Share | Mean ER | Mean Hurst |")
        lines.append("|---|--:|--:|--:|--:|")
        for s in r.per_regime:
            share_str = "—" if s.is_momentary else f"{s.share * 100:.1f}%"
            lines.append(
                f"| {s.regime.name} | {s.segment_count} | {share_str} | "
                f"{_fmt(s.mean_efficiency_ratio)} | {_fmt(s.mean_hurst)} |"
            )
        lines.append("")

    # 2. Transition matrix
    lines.append("## 2. Transition matrix")
    lines.append("")
    lines.append(
        "Full from/to grid, all five regimes on both axes, including zero cells -- "
        "a zero here is itself evidence the Month 1 transition-structure constraint "
        "is holding (e.g. CONSOLIDATION should never transition directly into "
        "RETRACEMENT/REVERSAL; see vo-phase-plan.md §13-notes)."
    )
    lines.append("")
    header = "| From \\\\ To | " + " | ".join(r.name for r in _ALL_REGIMES) + " | Row total |"
    lines.append(header)
    lines.append("|---|" + "--:|" * (len(_ALL_REGIMES) + 1))
    for frm in _ALL_REGIMES:
        row_total = transition_matrix.row_totals.get(frm, 0)
        cells = []
        for to in _ALL_REGIMES:
            count = transition_matrix.counts.get((frm, to), 0)
            prob = transition_matrix.probability(frm, to)
            prob_str = f" ({prob * 100:.0f}%)" if prob else ""
            cells.append(f"{count}{prob_str}")
        lines.append(f"| {frm.name} | " + " | ".join(cells) + f" | {row_total} |")
    lines.append("")

    # 3. Duration distributions
    lines.append("## 3. Duration distributions")
    lines.append("")
    lines.append(
        "The mean alone hides skew. A mean noticeably above the median (as the "
        "plain backtest report's EXPANSION row already showed: 46.4 mean vs 33.0 "
        "median in the first runs of this report) means the distribution is "
        "right-skewed -- most runs are short, a few are long."
    )
    lines.append("")
    lines.append("| Regime | n | Median | P25 | P75 | P90 | P95 | Max |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for d in durations:
        lines.append(
            f"| {d.regime.name} | {d.n} | {d.median:.1f} | {d.p25:.1f} | {d.p75:.1f} | "
            f"{d.p90:.1f} | {d.p95:.1f} | {d.maximum:.1f} |"
        )
    lines.append("")

    # 4. ER/Hurst relationship
    lines.append("## 4. ER / Hurst relationship")
    lines.append("")
    lines.append("| Regime | Feature | n | Mean | Median | Stdev | P25 | P75 |")
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|")
    for group in (*evidence.er_by_regime, *evidence.hurst_by_regime):
        lines.append(
            f"| {group.regime.name} | {group.feature} | {group.n} | {group.mean:.3f} | "
            f"{group.median:.3f} | {_fmt(group.stdev)} | {group.p25:.3f} | {group.p75:.3f} |"
        )
    lines.append("")
    lines.append(
        "**RETRACEMENT vs REVERSAL** — the one comparison the mean-only table in "
        "the plain backtest report cannot answer: is a mean gap a real "
        "distributional difference, or two heavily overlapping distributions. "
        "Mann-Whitney U, two-sided, tie-corrected normal approximation (see this "
        "module's docstring for the method and why no new dependency was needed)."
    )
    lines.append("")
    lines.append("| Comparison | n1 (RETRACEMENT) | n2 (REVERSAL) | U | z | p-value |")
    lines.append("|---|--:|--:|--:|--:|--:|")
    for mw in (evidence.er_retracement_vs_reversal, evidence.hurst_retracement_vs_reversal):
        lines.append(
            f"| {mw.label} | {mw.n1} | {mw.n2} | {mw.u:.0f} | "
            f"{_fmt(mw.z, 2)} | {_fmt_p(mw.p_value)} |"
        )
    lines.append("")
    lines.append(
        "A small p-value says the two distributions are unlikely to be identical -- "
        "it does NOT say how separable they are, or that a threshold rule "
        "(`IF ER > x THEN REVERSAL`) would work. That needs the P25/P75 overlap "
        "above, out-of-sample validation, and a G6 design review before any such "
        "rule is implemented. This is a research observation, not a trading edge."
    )
    lines.append("")

    # 5. Anticipation accuracy, class-imbalance-aware
    lines.append("## 5. Anticipation lean accuracy — class-imbalance-aware")
    lines.append("")
    a = accuracy
    rate_display = _fmt(a.rate, 4) if a.rate is not None else "n/a"
    lines.append(
        f"- Resolutions: **{a.resolutions}** ({a.to_retracement} retracement, "
        f"{a.to_reversal} reversal) — carried a lean: **{a.leaned}** — "
        f"matched: **{a.matched}** — observed rate: **{rate_display}**"
    )
    if a.majority_class is not None and a.majority_baseline_rate is not None:
        lines.append(
            f"- Class balance: always guessing the majority outcome "
            f"({a.majority_class.name}) would score **{a.majority_baseline_rate:.4f}** "
            f"on resolutions alone — this, not 50%, is the baseline the lean actually "
            f"needs to beat to be adding information."
        )
    if a.wilson_ci is not None:
        lines.append(
            f"- 95% Wilson score interval on the observed rate: "
            f"[{a.wilson_ci[0]:.4f}, {a.wilson_ci[1]:.4f}]"
        )
    lines.append("")
    lines.append("| Null hypothesis | z | p-value |")
    lines.append("|---|--:|--:|")
    if a.vs_coin_flip is not None:
        lines.append(
            f"| rate = 0.50 (coin flip) | {_fmt(a.vs_coin_flip.z, 2)} | "
            f"{_fmt_p(a.vs_coin_flip.p_value)} |"
        )
    if a.vs_majority_baseline is not None:
        lines.append(
            f"| rate = {a.vs_majority_baseline.null_rate:.4f} (majority-class baseline) | "
            f"{_fmt(a.vs_majority_baseline.z, 2)} | {_fmt_p(a.vs_majority_baseline.p_value)} |"
        )
    lines.append("")
    if (
        a.rate is not None
        and a.majority_baseline_rate is not None
        and a.rate < a.majority_baseline_rate
    ):
        lines.append(
            f"**The lean's own match rate ({_fmt(a.rate, 4)}) is BELOW the trivial "
            f"majority-class baseline ({_fmt(a.majority_baseline_rate, 4)}).** Against "
            f"a coin flip this lean looks like it is adding information; against the "
            f"actual class balance it currently is not. Treat 0.53-style figures as a "
            f"research observation, not a trading edge, until this comparison is "
            f"re-checked."
        )
        lines.append("")

    for title, slices in (
        ("By session", a.by_session),
        ("By RTH", a.by_rth),
        ("By pullback confidence (quartile)", a.by_confidence_quartile),
        ("By pullback duration before resolution (quartile)", a.by_pullback_duration_quartile),
    ):
        if not slices:
            continue
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| Bucket | n | Matched | Rate |")
        lines.append("|---|--:|--:|--:|")
        for sl in slices:
            rate_str = _fmt(sl.rate, 4) if sl.rate is not None else "n/a"
            lines.append(f"| {sl.label} | {sl.n} | {sl.matched} | {rate_str} |")
        lines.append("")

    lines.append("## Not yet measurable — deferred to v2")
    lines.append("")
    lines.append(
        "The following were raised as worth testing but need new telemetry fields "
        "not currently recorded on any RegimeState/RegimeSegment/RegimeMarker, so "
        "they are named here rather than approximated: **magnitude of the eventual "
        "move** (would need a before/after price pair around each resolution, not "
        "just the marker's single point price); **volatility at resolution** (ATR "
        "is computed by the swing engine but never recorded as regime evidence); "
        "**true out-of-sample validation** (holding out data only becomes "
        "meaningful once thresholds are actually being tuned — see this run's "
        "per-period breakdown above as the closest available proxy today). "
        "**Regime preceding the pullback** is not included as a variable because "
        "the current state machine makes it structurally constant — every "
        "PULLBACK_UNRESOLVED follows EXPANSION, never any other regime — so there "
        "is nothing to compare it against yet."
    )
    lines.append("")
    return "\n".join(lines)
