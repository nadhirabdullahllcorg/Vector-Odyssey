"""
Standalone Efficiency Ratio characterization report -- Phase 15b
(vo.research, layer 5).

WHY THIS EXISTS, AND HOW IT RELATES TO HURST_REPORT.PY. Same rationale,
same user-set order (architecture/vo-phase-plan.md's S15-notes): Hurst,
Efficiency Ratio (ER), Markov, and HMM must each be characterized
INDEPENDENTLY against the frozen 1,000,000-bar baseline before any
comparative/ensemble step. hurst_report.py (Phase 15a) did this for
Hurst first ("cheapest and most useful first" -- the user's own framing,
since hurst_exponent already took arbitrary window lengths). This module
is the very next step in that same order, for
vo.observation.efficiency_ratio.efficiency_ratio -- same six sections,
same reuse-not-reimplementation posture, same refusal to propose a
threshold. Where the two reports differ is only in what is worth saying
about ER specifically (see WHAT ER MEANS HERE below); the mechanics
(rolling sampling, regime/session/transition conditioning, out-of-sample
split) are shared code, not shared prose -- see REUSE, NOT
REIMPLEMENTATION.

DOES NOT decide, classify, or vote. Same G2 posture as hurst_report.py:
read-only research over an already-completed backtest run, several
layers removed from any decision path. Per the user's own explicit rule
for this round: "Don't let the quantitative models vote on the regime.
At least initially."

WHAT ER MEANS HERE, AND WHY THE EXISTING CLASSIFIER THRESHOLDS ARE NOT
THIS REPORT'S BUSINESS. efficiency_ratio.py already documents the
formula: net distance travelled / total path length, bounded to [0, 1]
by construction (0 = pure chop, 1 = a perfectly straight run).
config/settings/regime.yaml ALREADY uses two ER thresholds
(anticipation.er_trend_threshold=0.5, anticipation.er_chop_threshold=0.3)
to set the [VO-H] anticipation LEAN on an unresolved pullback -- but
that is production classifier code, already shipped, already gated
(G6), and explicitly NOT what this report is characterizing or
validating. This report describes ER's own distribution -- where it
actually falls, by window length, by regime, by session, before/after a
transition -- as independent evidence for the user's own later
comparative study; it does not check whether 0.5/0.3 are good cutoffs,
and finding that ER commonly sits above or below those two numbers in
some bucket is not, by itself, a reason to change them.

LAYERING. vo.research is layer 5; vo.telemetry is layer 7 -- see
hurst_report.py's own LAYERING note for the full explanation, which
applies identically here. RegimeInterval and its membership lookup live
in vo.research.regime_windows, shared with hurst_report.py rather than
defined a second time.

REUSE, NOT REIMPLEMENTATION. vo.observation.efficiency_ratio.
efficiency_ratio(bars, index, *, period) already computes a single ER
estimate at an arbitrary (index, period) pair, with no lookahead
(bars[<= index] only) -- everything below is orchestration over that one
already-verified function, exactly mirroring build_rolling_hurst's own
structure. The window-distribution/stability read (WindowStats/
build_window_distributions) and the out-of-sample split (OutOfSampleRow/
build_out_of_sample_report) are fully generic over any windowed rolling
series and live in vo.research.statistics -- introduced there precisely
so this module would not need to redefine them (see that module's own
docstring). Only the regime/session/transition orchestration below is
new, ER-specific code, and even that mirrors hurst_report.py's shape
closely enough that a future reader should read the two side by side.

WHY A STRIDE, NOT EVERY BAR. Same reasoning as hurst_report.py: a
periodic sample gives a large, representative distributional read at a
small fraction of the cost of computing at every bar. efficiency_ratio's
own cost is O(period) per call (one pass summing path length), cheaper
than hurst_exponent's O(period^2) -- but the same stride is used anyway,
for a like-for-like comparison against the Hurst report's own sampling
and because 1,000,000 bars at even O(period) per sample across several
window lengths is still worth not doing at every bar for a distributional
read that does not need every bar.

SECTIONS, MATCHING HURST_REPORT.PY'S OWN LIST (AND THE USER'S ORIGINAL
ONE).
  1. Rolling ER distribution + window-length sensitivity/stability.
  2. ER by regime, at every tested window length (median-only
     sensitivity table) plus full distributions + pairwise Mann-Whitney
     tests at the ONE configured/"primary" window length.
  3. ER preceding regime transitions, grouped by the transition's
     to_state, at the primary window length.
  4. ER by session, at the primary window length.
  5. Out-of-sample stability: an independent Section-1-style read on the
     chronological first `split_fraction` of samples vs. the rest, per
     window length.
  6. ER by timeframe: explicitly NOT built -- same reason as
     hurst_report.py (no multi-timeframe bar-aggregation utility yet).

OUT-OF-SAMPLE CAVEAT. Same caveat as hurst_report.py: the split in
section 5 is a single fixed chronological cut, not a proper walk-forward
validation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.efficiency_ratio import efficiency_ratio
from vo.observation.regime import RegimeTransition, RegimeType
from vo.research.regime_windows import RegimeInterval
from vo.research.regime_windows import interval_at as _interval_at
from vo.research.statistics import (
    GroupStats,
    MannWhitneyResult,
    OutOfSampleRow,
    WindowStats,
    describe,
    mann_whitney_u,
)
from vo.research.transitions import nearest_sample_before
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, session_at

DEFAULT_WINDOW_LENGTHS: tuple[int, ...] = (5, 10, 20, 40)
DEFAULT_PRIMARY_WINDOW = 10  # matches config/settings/regime.yaml's efficiency_ratio_period default
DEFAULT_STRIDE = 30

_ALL_REGIMES: tuple[RegimeType, ...] = tuple(RegimeType)


# ── 1. rolling ER distribution + window-length sensitivity ──────────────


def build_rolling_er(
    bars: Sequence[Bar],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
    stride: int = DEFAULT_STRIDE,
) -> dict[int, list[tuple[datetime, float]]]:
    """Sample efficiency_ratio at every `stride`-th bar index, for each
    period in `window_lengths`. Returns, per period, a chronological list
    of (bar open_time_utc, ER value) -- None estimates (warmup window)
    are dropped, never coerced to a fake number. Unlike Hurst, ER never
    returns None for a flat window (it is 0.0 there by definition, per
    efficiency_ratio's own docstring), so the only samples dropped here
    are true insufficient-history warmup."""
    if stride < 1:
        raise ValueError(f"stride must be >= 1, got {stride}")

    out: dict[int, list[tuple[datetime, float]]] = {period: [] for period in window_lengths}
    for index in range(0, len(bars), stride):
        for period in window_lengths:
            value = efficiency_ratio(bars, index, period=period)
            if value is not None:
                out[period].append((bars[index].open_time_utc, value))
    return out


# ── 2. ER by regime, across window lengths + full detail at the primary ──


def build_er_by_regime(
    samples: Sequence[tuple[datetime, float]], intervals: Sequence[RegimeInterval]
) -> dict[RegimeType, list[float]]:
    """Groups one window length's rolling samples by whichever regime
    interval covered each sample's time -- identical grouping logic to
    hurst_report.build_hurst_by_regime, via the shared
    vo.research.regime_windows.interval_at lookup."""
    starts = [interval.start_utc for interval in intervals]
    out: dict[RegimeType, list[float]] = {}
    for when, value in samples:
        interval = _interval_at(intervals, starts, when)
        if interval is None:
            continue
        out.setdefault(interval.regime, []).append(value)
    return out


@dataclass(frozen=True)
class ERSensitivityRow:
    """One window length's median ER per regime -- the compact,
    median-only cross-window view (full distributions would be too much
    to render for every window length; see `by_regime_detail` for the
    one primary window length's full picture)."""

    window: int
    median_by_regime: dict[RegimeType, float]


@dataclass(frozen=True)
class ERByRegime:
    sensitivity: tuple[ERSensitivityRow, ...]
    by_regime_detail: tuple[GroupStats, ...]  # primary window only, one per regime with data
    pairwise: tuple[MannWhitneyResult, ...]  # primary window only, all regime pairs with data


def build_er_by_regime_report(
    rolling: dict[int, list[tuple[datetime, float]]],
    intervals: Sequence[RegimeInterval],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
    primary_window: int = DEFAULT_PRIMARY_WINDOW,
) -> ERByRegime:
    sensitivity: list[ERSensitivityRow] = []
    for period in window_lengths:
        grouped = build_er_by_regime(rolling.get(period, []), intervals)
        medians = {
            regime: describe(values, regime.name).median
            for regime, values in grouped.items()
            if values
        }
        if medians:
            sensitivity.append(ERSensitivityRow(window=period, median_by_regime=medians))

    primary_grouped = build_er_by_regime(rolling.get(primary_window, []), intervals)
    detail = tuple(
        describe(primary_grouped[regime], regime.name)
        for regime in _ALL_REGIMES
        if primary_grouped.get(regime)
    )

    pairwise: list[MannWhitneyResult] = []
    present = [regime for regime in _ALL_REGIMES if primary_grouped.get(regime)]
    for i, regime_a in enumerate(present):
        for regime_b in present[i + 1 :]:
            pairwise.append(
                mann_whitney_u(
                    primary_grouped[regime_a],
                    primary_grouped[regime_b],
                    label=f"efficiency_ratio: {regime_a.name} vs {regime_b.name} "
                    f"(window={primary_window})",
                )
            )

    return ERByRegime(
        sensitivity=tuple(sensitivity), by_regime_detail=detail, pairwise=tuple(pairwise)
    )


# ── 3. ER preceding regime transitions ───────────────────────────────────


def build_er_preceding_transitions(
    samples: Sequence[tuple[datetime, float]],
    transitions_log: Sequence[RegimeTransition],
    *,
    max_gap_minutes: float,
) -> dict[RegimeType, list[float]]:
    """For each transition, the nearest ER sample AT OR BEFORE its
    observed_at (no lookahead across the transition itself), grouped by
    the transition's to_state -- "what did ER look like right before the
    market resolved into this regime." Skips a transition when its
    nearest earlier sample is more than `max_gap_minutes` away (a real
    data gap, e.g. a weekend, rather than a stale match). Identical
    matching logic to hurst_report.build_hurst_preceding_transitions."""
    out: dict[RegimeType, list[float]] = {}
    for transition in transitions_log:
        found = nearest_sample_before(
            samples, transition.observed_at, max_gap_minutes=max_gap_minutes
        )
        if found is None:
            continue
        _when, value = found
        out.setdefault(transition.to_state, []).append(value)
    return out


@dataclass(frozen=True)
class TransitionERReport:
    by_to_state: tuple[GroupStats, ...]
    pairwise: tuple[MannWhitneyResult, ...]


def build_transition_er_report(
    samples: Sequence[tuple[datetime, float]],
    transitions_log: Sequence[RegimeTransition],
    *,
    max_gap_minutes: float,
) -> TransitionERReport:
    grouped = build_er_preceding_transitions(
        samples, transitions_log, max_gap_minutes=max_gap_minutes
    )
    by_to_state = tuple(
        describe(grouped[regime], regime.name) for regime in _ALL_REGIMES if grouped.get(regime)
    )
    present = [regime for regime in _ALL_REGIMES if grouped.get(regime)]
    pairwise = []
    for i, regime_a in enumerate(present):
        for regime_b in present[i + 1 :]:
            pairwise.append(
                mann_whitney_u(
                    grouped[regime_a],
                    grouped[regime_b],
                    label=f"efficiency_ratio preceding transition to: "
                    f"{regime_a.name} vs {regime_b.name}",
                )
            )
    return TransitionERReport(by_to_state=by_to_state, pairwise=tuple(pairwise))


# ── 4. ER by session ──────────────────────────────────────────────────────


def build_er_by_session(
    samples: Sequence[tuple[datetime, float]], session_config: SessionConfig
) -> tuple[GroupStats, ...]:
    grouped: dict[str, list[float]] = {}
    for when, value in samples:
        window = session_at(when.astimezone(session_config.zone), session_config)
        label = window.name if window is not None else OFF_SESSION_LABEL
        grouped.setdefault(label, []).append(value)
    return tuple(describe(values, label) for label, values in grouped.items() if values)


# ── 5. out-of-sample stability is vo.research.statistics.build_out_of_
# sample_report -- fully generic, reused as-is (see this module's own
# REUSE, NOT REIMPLEMENTATION note). No local wrapper here.


# ── rendering ─────────────────────────────────────────────────────────


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _fmt_p(p_value: float | None) -> str:
    if p_value is None:
        return "n/a"
    return "<0.0001" if p_value < 0.0001 else f"{p_value:.4f}"


def render_er_report_markdown(
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    primary_window: int,
    stride: int,
    window_distributions: Sequence[WindowStats],
    by_regime: ERByRegime,
    transitions: TransitionERReport,
    by_session: Sequence[GroupStats],
    out_of_sample: Sequence[OutOfSampleRow],
) -> str:
    lines: list[str] = []
    lines.append(
        f"# Efficiency Ratio Research Report v1 (Phase 15b) — "
        f"{instrument_key} {timeframe_canonical}"
    )
    lines.append("")
    lines.append(f"Generated: {generated_utc.isoformat()} (UTC)")
    lines.append("")
    lines.append(
        "Standalone characterization of vo.observation.efficiency_ratio.efficiency_ratio "
        "against the frozen 1,000,000-bar regime baseline -- read alone, before any "
        "comparison to Hurst, Markov, or the VO regime classifier itself. ER is bounded "
        "to [0, 1] by construction: 0 is pure chop (a long path that ends where it "
        "started), 1 is a perfectly straight run. Nothing here decides or classifies "
        "anything, and nothing here checks or proposes the anticipation-lean thresholds "
        "already in config/settings/regime.yaml (er_trend_threshold=0.5, "
        "er_chop_threshold=0.3) -- see this module's own docstring for why. Read every "
        "comparison below as hypothesis-generating, never as a threshold rule -- "
        "'IF ER > X THEN Y' is exactly what this report deliberately does not propose."
    )
    lines.append("")
    lines.append(
        f"Sampling: every {stride}-th bar; primary/configured window length "
        f"**{primary_window}** bars (matches config/settings/regime.yaml's "
        f"efficiency_ratio_period)."
    )
    lines.append("")

    # 1. window-length sensitivity
    lines.append("## 1. Rolling Efficiency Ratio distribution & window-length sensitivity")
    lines.append("")
    lines.append("| Window | n | Mean | Median | Stdev | P25 | P75 | Lag-1 autocorr. |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for row in window_distributions:
        s = row.stats
        lines.append(
            f"| {row.window} | {s.n} | {_fmt(s.mean)} | {_fmt(s.median)} | "
            f"{_fmt(s.stdev)} | {_fmt(s.p25)} | {_fmt(s.p75)} | "
            f"{_fmt(row.stability_lag1_autocorrelation)} |"
        )
    lines.append("")
    lines.append(
        "Lag-1 autocorrelation is a stability read on consecutive rolling estimates "
        "at that window length -- closer to 1.0 means the estimate moves smoothly "
        "bar-to-bar (more trustworthy as a slow-moving regime feature); closer to 0 "
        "means it is noisy at that window length."
    )
    lines.append("")

    # 2. by regime
    lines.append("## 2. Efficiency Ratio by regime")
    lines.append("")
    lines.append("### Sensitivity: median ER per regime, across window lengths")
    lines.append("")
    header = ["Window", *[r.name for r in _ALL_REGIMES]]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for sens_row in by_regime.sensitivity:
        cells = [str(sens_row.window)]
        for regime in _ALL_REGIMES:
            has_regime = regime in sens_row.median_by_regime
            cells.append(_fmt(sens_row.median_by_regime.get(regime), 3) if has_regime else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(f"### Full distributions at the primary window ({primary_window})")
    lines.append("")
    lines.append("| Regime | n | Mean | Median | Stdev | P25 | P75 |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for s in by_regime.by_regime_detail:
        lines.append(
            f"| {s.label} | {s.n} | {_fmt(s.mean)} | {_fmt(s.median)} | "
            f"{_fmt(s.stdev)} | {_fmt(s.p25)} | {_fmt(s.p75)} |"
        )
    lines.append("")
    lines.append("| Comparison | n1 | n2 | U | z | p-value |")
    lines.append("|---|--:|--:|--:|--:|--:|")
    for mw in by_regime.pairwise:
        lines.append(
            f"| {mw.label} | {mw.n1} | {mw.n2} | {_fmt(mw.u, 1)} | "
            f"{_fmt(mw.z, 2)} | {_fmt_p(mw.p_value)} |"
        )
    lines.append("")

    # 3. preceding transitions
    lines.append("## 3. Efficiency Ratio preceding regime transitions")
    lines.append("")
    lines.append(
        "Nearest ER sample at or before each transition's observed_at, grouped by "
        "the transition's `to_state` -- \"what did ER look like right before the "
        "market resolved into this regime.\""
    )
    lines.append("")
    lines.append("| Resolving into | n | Mean | Median | Stdev | P25 | P75 |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for s in transitions.by_to_state:
        lines.append(
            f"| {s.label} | {s.n} | {_fmt(s.mean)} | {_fmt(s.median)} | "
            f"{_fmt(s.stdev)} | {_fmt(s.p25)} | {_fmt(s.p75)} |"
        )
    lines.append("")
    if transitions.pairwise:
        lines.append("| Comparison | n1 | n2 | U | z | p-value |")
        lines.append("|---|--:|--:|--:|--:|--:|")
        for mw in transitions.pairwise:
            lines.append(
                f"| {mw.label} | {mw.n1} | {mw.n2} | {_fmt(mw.u, 1)} | "
                f"{_fmt(mw.z, 2)} | {_fmt_p(mw.p_value)} |"
            )
        lines.append("")

    # 4. by session
    lines.append("## 4. Efficiency Ratio by session")
    lines.append("")
    lines.append(
        "Telemetry only -- vo.observation.regime's classifier never sees session "
        "boundaries; this is an observation about where ER's OWN values fall, not "
        "evidence session should become a classifier input (same posture as "
        "hurst_report.py's and regime_validation.py's own session breakdowns)."
    )
    lines.append("")
    lines.append("| Session | n | Mean | Median | Stdev | P25 | P75 |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for s in by_session:
        lines.append(
            f"| {s.label} | {s.n} | {_fmt(s.mean)} | {_fmt(s.median)} | "
            f"{_fmt(s.stdev)} | {_fmt(s.p25)} | {_fmt(s.p75)} |"
        )
    lines.append("")

    # 5. out-of-sample
    lines.append("## 5. Out-of-sample stability")
    lines.append("")
    lines.append(
        "A single fixed chronological split (see this module's OUT-OF-SAMPLE "
        "CAVEAT in its own docstring) -- NOT a substitute for a real walk-forward "
        "protocol once any threshold is ever proposed."
    )
    lines.append("")
    lines.append("| Window | Train n | Train median | Test n | Test median | Median delta |")
    lines.append("|---|--:|--:|--:|--:|--:|")
    for oos_row in out_of_sample:
        lines.append(
            f"| {oos_row.window} | {oos_row.train.n} | {_fmt(oos_row.train.median)} | "
            f"{oos_row.test.n} | {_fmt(oos_row.test.median)} | {_fmt(oos_row.median_delta)} |"
        )
    lines.append("")

    # 6. deferred
    lines.append("## 6. Efficiency Ratio by timeframe — deferred")
    lines.append("")
    lines.append(
        "Not built: this pipeline runs entirely on the native M1 bar stream and "
        "has no multi-timeframe bar-aggregation utility yet. Flagged here rather "
        "than silently skipped -- see architecture/vo-phase-plan.md's S15-notes "
        "and S9 open items."
    )
    lines.append("")

    lines.append("## Conclusions this report deliberately does not draw")
    lines.append("")
    lines.append(
        "This report does not propose a threshold rule, does not declare ER "
        "'separates' the regimes, does not check the existing anticipation-lean "
        "thresholds (er_trend_threshold/er_chop_threshold), and does not recommend "
        "any change to the classifier. Read the Mann-Whitney comparisons above as "
        "\"worth investigating further,\" never as \"validated.\" The next step in "
        "the user's own plan is the conditional Markov transition study (Phase 17), "
        "then HMM (S17a) -- only after all four independent characterizations exist "
        "does a comparative/relationship study become appropriate, and even then: "
        "\"Don't let the quantitative models vote on the regime. At least initially.\""
    )

    return "\n".join(lines)
