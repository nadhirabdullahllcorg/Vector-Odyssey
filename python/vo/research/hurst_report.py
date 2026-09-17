"""
Standalone Hurst characterization report -- Phase 15a (vo.research, layer 5).

WHY THIS EXISTS, AND WHY IT IS SEPARATE FROM regime_validation.py. The
user's own architecture for this next round of work (recorded in
architecture/vo-phase-plan.md's v33 and its new S15-notes section) is
explicit: Hurst, Efficiency Ratio, Markov, and HMM must each be
characterized INDEPENDENTLY against the frozen 1,000,000-bar baseline --
rolling distributions, window-length sensitivity, regime/session/
transition conditioning, out-of-sample stability -- before any
comparative/ensemble step. regime_validation.py's build_evidence_
comparison already looked at ER/Hurst, but only at the ONE window length
baked into config/settings/regime.yaml (hurst_period), and only as a
RETRACEMENT-vs-REVERSAL mean/Mann-Whitney snapshot -- that is the FINAL
"comparative study" stage of the user's own diagram, not the standalone
characterization stage this module performs first. This module answers a
different, prior question: does Hurst itself behave like a stable,
meaningful measurement across window lengths, regimes, sessions, and
time, before any comparison to ER or to the regime label is trusted.

DOES NOT decide, classify, or vote. This module never writes a
RegimeState and is never imported by vo.observation (a downward-only
import would be needed for that anyway -- see the layering note below).
It is read-only research over an already-completed backtest run,
several layers removed from any decision path -- the same G2 posture
regime_validation.py already established. Per the user's own explicit
rule for this round: "Don't let the quantitative models vote on the
regime. At least initially." Nothing here proposes "IF HURST > X THEN
Y" -- see the CONCLUSIONS note at the end of the rendered report.

LAYERING. vo.research is layer 5; vo.telemetry (regime_feed, regime_
report, regime_validation) is layer 7 -- a HIGHER layer, so this module
may not import it (upward imports are structurally forbidden, see
tests/unit/test_architecture.py). That means this module cannot take a
vo.telemetry.regime_feed.RegimeSegment directly. Instead it defines its
own minimal RegimeInterval (regime + start_utc + end_utc only) and
expects the CALLER -- scripts/backtest_regime.py, or any other layer-7+
orchestrator -- to map its already-built RegimeSegments into these
before calling in. This is a thin, one-line adapter at the call site,
not a duplicated computation: no segment-building/merging logic is
reimplemented here.

REUSE, NOT REIMPLEMENTATION. vo.observation.hurst.hurst_exponent(bars,
index, *, period) already computes a single Hurst estimate at an
arbitrary (index, period) pair, with no lookahead (bars[<= index] only).
Everything below is orchestration over that one already-verified
function -- calling it repeatedly across a stride of bar indices and
several period values -- never a second estimator. vo.research.
statistics supplies every distributional primitive (percentile,
describe/GroupStats, mann_whitney_u, lag1_autocorrelation); nothing here
hand-rolls a formula regime_validation.py or vo.research.statistics
would also need.

WHY A STRIDE, NOT EVERY BAR. hurst_exponent's cost is roughly
O(period^2) (it fits a line across O(period) lags, each an O(period)
pass over the window). At the frozen baseline's 1,000,000 bars, computing
it at every single bar for several window lengths would be needlessly
slow for a distributional read that does not need every bar -- a
periodic sample (`stride` bars apart) gives a large, representative
sample (tens of thousands of points) at a small fraction of the cost.
This is a deliberate, named sampling choice, not a silent shortcut.

SECTIONS, MATCHING THE USER'S OWN LIST.
  1. Rolling Hurst distribution + window-length sensitivity/stability.
  2. Hurst by regime, at every tested window length (median-only
     sensitivity table) plus full distributions + pairwise Mann-Whitney
     tests at the ONE configured/"primary" window length.
  3. Hurst preceding regime transitions, grouped by the transition's
     to_state, at the primary window length.
  4. Hurst by session, at the primary window length.
  5. Out-of-sample stability: an independent Section-1-style read on the
     chronological first `split_fraction` of samples vs. the rest, per
     window length.
  6. Hurst by timeframe: explicitly NOT built -- this pipeline has no
     multi-timeframe bar-aggregation utility yet (everything here runs
     on the native M1 stream). Flagged, not silently skipped.

OUT-OF-SAMPLE CAVEAT. The split in section 5 is a single fixed
chronological cut, not a proper walk-forward validation -- it answers
"does the read from the first period look like the read from the rest,"
which is a reasonable first check and NOT a substitute for a real
out-of-sample protocol once any threshold is ever proposed. Named here,
not hidden, matching regime_validation.py's own convention for its
period-breakdown approximations.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.hurst import hurst_exponent
from vo.observation.regime import RegimeTransition, RegimeType
from vo.research.statistics import (
    GroupStats,
    MannWhitneyResult,
    describe,
    lag1_autocorrelation,
    mann_whitney_u,
)
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, session_at

DEFAULT_WINDOW_LENGTHS: tuple[int, ...] = (10, 20, 40, 80)
DEFAULT_PRIMARY_WINDOW = 20  # matches config/settings/regime.yaml's hurst_period default
DEFAULT_STRIDE = 30
DEFAULT_OUT_OF_SAMPLE_SPLIT = 0.7

_ALL_REGIMES: tuple[RegimeType, ...] = tuple(RegimeType)


@dataclass(frozen=True)
class RegimeInterval:
    """A plain (regime, start_utc, end_utc) span -- see the module
    docstring's LAYERING note for why this exists instead of taking a
    vo.telemetry.regime_feed.RegimeSegment directly. end_utc is None for
    the final, still-open interval (mirrors RegimeSegment's own
    convention). Callers build these from their own already-computed
    segments, e.g.:

        intervals = tuple(
            RegimeInterval(regime=seg.regime, start_utc=seg.start_utc, end_utc=seg.end_utc)
            for seg in segments
        )
    """

    regime: RegimeType
    start_utc: datetime
    end_utc: datetime | None


# ── 1. rolling Hurst distribution + window-length sensitivity ───────────


def build_rolling_hurst(
    bars: Sequence[Bar],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
    stride: int = DEFAULT_STRIDE,
) -> dict[int, list[tuple[datetime, float]]]:
    """Sample hurst_exponent at every `stride`-th bar index, for each
    period in `window_lengths`. Returns, per period, a chronological list
    of (bar open_time_utc, hurst value) -- None estimates (warmup window,
    degenerate/flat window) are dropped, never coerced to a fake number."""
    if stride < 1:
        raise ValueError(f"stride must be >= 1, got {stride}")

    out: dict[int, list[tuple[datetime, float]]] = {period: [] for period in window_lengths}
    for index in range(0, len(bars), stride):
        for period in window_lengths:
            value = hurst_exponent(bars, index, period=period)
            if value is not None:
                out[period].append((bars[index].open_time_utc, value))
    return out


@dataclass(frozen=True)
class HurstWindowStats:
    """One window length's overall distribution, plus a lag-1
    autocorrelation of its own consecutive rolling estimates as a simple
    "how noisy is this window length's read" stability signal -- higher
    means consecutive samples agree more, lower/near-zero means the
    estimate is jumpier at that window length."""

    window: int
    stats: GroupStats
    stability_lag1_autocorrelation: float | None


def build_window_distributions(
    rolling: dict[int, list[tuple[datetime, float]]],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
) -> tuple[HurstWindowStats, ...]:
    out: list[HurstWindowStats] = []
    for period in window_lengths:
        samples = rolling.get(period, [])
        if not samples:
            continue
        values = [v for _t, v in samples]
        out.append(
            HurstWindowStats(
                window=period,
                stats=describe(values, f"window={period}"),
                stability_lag1_autocorrelation=lag1_autocorrelation(values),
            )
        )
    return tuple(out)


# ── regime-interval membership lookup, shared by sections 2 and 3 ───────


def _interval_at(
    intervals: Sequence[RegimeInterval], starts: Sequence[datetime], when: datetime
) -> RegimeInterval | None:
    """Which interval (if any) covers `when`, assuming `intervals` is
    chronologically ordered and contiguous (the same contract regime_feed.
    build_regime_segments already guarantees its own output). `starts`
    is intervals' own start_utc values, precomputed once by the caller so
    a whole report doesn't re-derive it per lookup."""
    if not intervals:
        return None
    idx = bisect.bisect_right(starts, when) - 1
    if idx < 0:
        return None
    interval = intervals[idx]
    if interval.end_utc is not None and when >= interval.end_utc:
        return None
    return interval


# ── 2. Hurst by regime, across window lengths + full detail at the primary ──


def build_hurst_by_regime(
    samples: Sequence[tuple[datetime, float]], intervals: Sequence[RegimeInterval]
) -> dict[RegimeType, list[float]]:
    """Groups one window length's rolling samples by whichever regime
    interval covered each sample's time. Samples falling outside every
    interval (before the first, or in a gap -- should not happen given
    the contiguous-segments contract, but not assumed) are dropped."""
    starts = [interval.start_utc for interval in intervals]
    out: dict[RegimeType, list[float]] = {}
    for when, value in samples:
        interval = _interval_at(intervals, starts, when)
        if interval is None:
            continue
        out.setdefault(interval.regime, []).append(value)
    return out


@dataclass(frozen=True)
class RegimeSensitivityRow:
    """One window length's median Hurst per regime -- the compact,
    median-only cross-window view (full distributions would be too much
    to render for every window length; see `by_regime_detail` for the
    one primary window length's full picture)."""

    window: int
    median_by_regime: dict[RegimeType, float]


@dataclass(frozen=True)
class HurstByRegime:
    sensitivity: tuple[RegimeSensitivityRow, ...]
    by_regime_detail: tuple[GroupStats, ...]  # primary window only, one per regime with data
    pairwise: tuple[MannWhitneyResult, ...]  # primary window only, all regime pairs with data


def build_hurst_by_regime_report(
    rolling: dict[int, list[tuple[datetime, float]]],
    intervals: Sequence[RegimeInterval],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
    primary_window: int = DEFAULT_PRIMARY_WINDOW,
) -> HurstByRegime:
    sensitivity: list[RegimeSensitivityRow] = []
    for period in window_lengths:
        grouped = build_hurst_by_regime(rolling.get(period, []), intervals)
        medians = {
            regime: describe(values, regime.name).median
            for regime, values in grouped.items()
            if values
        }
        if medians:
            sensitivity.append(RegimeSensitivityRow(window=period, median_by_regime=medians))

    primary_grouped = build_hurst_by_regime(rolling.get(primary_window, []), intervals)
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
                    label=f"hurst: {regime_a.name} vs {regime_b.name} (window={primary_window})",
                )
            )

    return HurstByRegime(
        sensitivity=tuple(sensitivity), by_regime_detail=detail, pairwise=tuple(pairwise)
    )


# ── 3. Hurst preceding regime transitions ────────────────────────────────


def build_hurst_preceding_transitions(
    samples: Sequence[tuple[datetime, float]],
    transitions_log: Sequence[RegimeTransition],
    *,
    max_gap_minutes: float,
) -> dict[RegimeType, list[float]]:
    """For each transition, the nearest Hurst sample AT OR BEFORE its
    observed_at (no lookahead across the transition itself), grouped by
    the transition's to_state -- "what did Hurst look like right before
    the market resolved into this regime." Skips a transition when its
    nearest earlier sample is more than `max_gap_minutes` away (a real
    data gap, e.g. a weekend, rather than a stale match)."""
    sample_times = [t for t, _v in samples]
    out: dict[RegimeType, list[float]] = {}
    for transition in transitions_log:
        idx = bisect.bisect_right(sample_times, transition.observed_at) - 1
        if idx < 0:
            continue
        when, value = samples[idx]
        gap_minutes = (transition.observed_at - when).total_seconds() / 60.0
        if gap_minutes < 0 or gap_minutes > max_gap_minutes:
            continue
        out.setdefault(transition.to_state, []).append(value)
    return out


@dataclass(frozen=True)
class TransitionHurstReport:
    by_to_state: tuple[GroupStats, ...]
    pairwise: tuple[MannWhitneyResult, ...]


def build_transition_hurst_report(
    samples: Sequence[tuple[datetime, float]],
    transitions_log: Sequence[RegimeTransition],
    *,
    max_gap_minutes: float,
) -> TransitionHurstReport:
    grouped = build_hurst_preceding_transitions(
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
                    label=f"hurst preceding transition to: {regime_a.name} vs {regime_b.name}",
                )
            )
    return TransitionHurstReport(by_to_state=by_to_state, pairwise=tuple(pairwise))


# ── 4. Hurst by session ──────────────────────────────────────────────────


def build_hurst_by_session(
    samples: Sequence[tuple[datetime, float]], session_config: SessionConfig
) -> tuple[GroupStats, ...]:
    grouped: dict[str, list[float]] = {}
    for when, value in samples:
        window = session_at(when.astimezone(session_config.zone), session_config)
        label = window.name if window is not None else OFF_SESSION_LABEL
        grouped.setdefault(label, []).append(value)
    return tuple(describe(values, label) for label, values in grouped.items() if values)


# ── 5. out-of-sample stability ───────────────────────────────────────────


@dataclass(frozen=True)
class OutOfSampleRow:
    window: int
    train: GroupStats
    test: GroupStats
    median_delta: float  # test.median - train.median, signed


def build_out_of_sample_report(
    rolling: dict[int, list[tuple[datetime, float]]],
    *,
    window_lengths: Sequence[int] = DEFAULT_WINDOW_LENGTHS,
    split_fraction: float = DEFAULT_OUT_OF_SAMPLE_SPLIT,
) -> tuple[OutOfSampleRow, ...]:
    """A single fixed chronological split per window length -- see the
    module docstring's OUT-OF-SAMPLE CAVEAT. Samples are already
    chronological (build_rolling_hurst walks bars in order); the split
    point is by SAMPLE COUNT, not calendar time, so both halves have
    comparable statistical power even if trading activity is uneven."""
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


# ── rendering ─────────────────────────────────────────────────────────


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _fmt_p(p_value: float | None) -> str:
    if p_value is None:
        return "n/a"
    return "<0.0001" if p_value < 0.0001 else f"{p_value:.4f}"


def render_hurst_report_markdown(
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    primary_window: int,
    stride: int,
    window_distributions: Sequence[HurstWindowStats],
    by_regime: HurstByRegime,
    transitions: TransitionHurstReport,
    by_session: Sequence[GroupStats],
    out_of_sample: Sequence[OutOfSampleRow],
) -> str:
    lines: list[str] = []
    lines.append(
        f"# Hurst Exponent Research Report v1 (Phase 15a) — "
        f"{instrument_key} {timeframe_canonical}"
    )
    lines.append("")
    lines.append(f"Generated: {generated_utc.isoformat()} (UTC)")
    lines.append("")
    lines.append(
        "Standalone characterization of vo.observation.hurst.hurst_exponent against "
        "the frozen 1,000,000-bar regime baseline -- read alone, before any "
        "comparison to Efficiency Ratio, Markov, or the VO regime classifier itself. "
        "Nothing here decides or classifies anything; this is research analysis of "
        "an already-completed backtest run (see this module's own docstring for the "
        "full rationale). Read every comparison below as hypothesis-generating, "
        "never as a threshold rule -- 'IF HURST > X THEN Y' is exactly what this "
        "report deliberately does not propose."
    )
    lines.append("")
    lines.append(
        f"Sampling: every {stride}-th bar; primary/configured window length "
        f"**{primary_window}** bars (matches config/settings/regime.yaml's "
        f"hurst_period)."
    )
    lines.append("")

    # 1. window-length sensitivity
    lines.append("## 1. Rolling Hurst distribution & window-length sensitivity")
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
    lines.append("## 2. Hurst by regime")
    lines.append("")
    lines.append("### Sensitivity: median Hurst per regime, across window lengths")
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
    lines.append("## 3. Hurst preceding regime transitions")
    lines.append("")
    lines.append(
        "Nearest Hurst sample at or before each transition's observed_at, grouped "
        "by the transition's `to_state` -- \"what did Hurst look like right before "
        "the market resolved into this regime.\""
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
    lines.append("## 4. Hurst by session")
    lines.append("")
    lines.append(
        "Telemetry only -- vo.observation.regime's classifier never sees session "
        "boundaries; this is an observation about where Hurst's OWN values fall, "
        "not evidence session should become a classifier input (same posture as "
        "regime_validation.py's own session breakdown)."
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
    lines.append("## 6. Hurst by timeframe — deferred")
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
        "This report does not propose a threshold rule, does not declare Hurst "
        "'separates' the regimes, and does not recommend any change to the "
        "classifier. Read the Mann-Whitney comparisons above as \"worth "
        "investigating further,\" never as \"validated.\" The next step in the "
        "user's own plan is the same standalone characterization for Efficiency "
        "Ratio (Phase 15b), then a conditional Markov study (Phase 17), then HMM "
        "(S17a) -- only after all four exist does a comparative/relationship study "
        "become appropriate, and even then: \"Don't let the quantitative models "
        "vote on the regime. At least initially.\""
    )

    return "\n".join(lines)
