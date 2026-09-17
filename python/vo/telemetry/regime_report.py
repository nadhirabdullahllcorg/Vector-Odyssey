"""
Regime backtest report -- Phase 13a follow-up (accuracy over deep history).

Turns a RegimeEngine run over a long history into an inspectable summary:
how much time each regime held, how regimes transitioned, the ER/Hurst
evidence each regime carried, and -- the real accuracy question -- how
often the [VO-H] anticipation lean on a live pullback matched the way
that pullback actually resolved. Consumes the engine's own output
(RegimeSegments + the RegimeState log); invents no classification of its
own (gate G2). Layer 7 (telemetry); pure and testable, the markdown
rendering kept separate from the computation.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from vo.observation.regime import (
    AnticipatedResolution,
    RegimeState,
    RegimeType,
)
from vo.telemetry.regime_feed import RegimeSegment

_ER_FEATURE = "efficiency_ratio"
_HURST_FEATURE = "hurst_exponent"


@dataclass(frozen=True)
class RegimeStats:
    """One regime's share of the backtest."""

    regime: RegimeType
    segment_count: int
    total_minutes: float
    share: float  # of total classified time, [0, 1]
    mean_minutes: float
    median_minutes: float
    mean_efficiency_ratio: float | None
    mean_hurst: float | None


@dataclass(frozen=True)
class AnticipationAccuracy:
    """How the [VO-H] lean on PULLBACK_UNRESOLVED fared against the actual
    resolution. `leaned` counts resolutions whose superseded pullback
    carried a directional lean (RETRACEMENT/REVERSAL, not UNCLEAR/none);
    `matched` counts those the outcome agreed with. `rate` is
    matched/leaned, or None when nothing leaned (no denominator to divide)."""

    resolutions: int  # total RETRACEMENT + REVERSAL resolutions
    to_retracement: int
    to_reversal: int
    leaned: int
    matched: int
    rate: float | None


@dataclass(frozen=True)
class RegimeReport:
    instrument_key: str
    timeframe_canonical: str
    first_utc: datetime | None
    last_utc: datetime | None
    bar_count: int
    total_segments: int
    per_regime: tuple[RegimeStats, ...]
    transitions: tuple[tuple[RegimeType, RegimeType, int], ...]
    anticipation: AnticipationAccuracy


def _feature_value(state: RegimeState, name: str) -> float | None:
    for feature in state.supporting_features:
        if feature.name == name:
            return feature.value
    return None


def _mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def build_regime_report(
    segments: tuple[RegimeSegment, ...],
    states: tuple[RegimeState, ...],
    *,
    history_end_utc: datetime | None,
    minutes_per_bar: float = 1.0,
    bar_count: int = 0,
) -> RegimeReport:
    """Summarize a regime backtest. `history_end_utc` closes the final,
    open-ended segment for duration accounting (its own end_utc is None);
    without it that segment is skipped from the time totals. `minutes_per_bar`
    labels durations (1.0 for M1)."""
    del minutes_per_bar  # durations are wall-clock; kept for signature clarity

    # Duration per segment, in minutes of wall-clock.
    durations: dict[RegimeType, list[float]] = {}
    for seg in segments:
        end = seg.end_utc if seg.end_utc is not None else history_end_utc
        if end is None:
            continue
        minutes = (end - seg.start_utc).total_seconds() / 60.0
        durations.setdefault(seg.regime, []).append(minutes)

    grand_total = sum(sum(v) for v in durations.values()) or 1.0

    # ER / Hurst evidence per regime, from the state log.
    er_by_regime: dict[RegimeType, list[float]] = {}
    hurst_by_regime: dict[RegimeType, list[float]] = {}
    for state in states:
        er = _feature_value(state, _ER_FEATURE)
        if er is not None:
            er_by_regime.setdefault(state.regime, []).append(er)
        hurst = _feature_value(state, _HURST_FEATURE)
        if hurst is not None:
            hurst_by_regime.setdefault(state.regime, []).append(hurst)

    seg_counts = Counter(seg.regime for seg in segments)
    per_regime: list[RegimeStats] = []
    for regime in sorted({seg.regime for seg in segments}, key=lambda r: r.name):
        mins = durations.get(regime, [])
        total = sum(mins)
        per_regime.append(
            RegimeStats(
                regime=regime,
                segment_count=seg_counts[regime],
                total_minutes=total,
                share=total / grand_total,
                mean_minutes=statistics.fmean(mins) if mins else 0.0,
                median_minutes=statistics.median(mins) if mins else 0.0,
                mean_efficiency_ratio=_mean_or_none(er_by_regime.get(regime, [])),
                mean_hurst=_mean_or_none(hurst_by_regime.get(regime, [])),
            )
        )

    # Transitions: consecutive segments (already time-ordered).
    trans_counter: Counter[tuple[RegimeType, RegimeType]] = Counter()
    for prev, nxt in pairwise(segments):
        trans_counter[(prev.regime, nxt.regime)] += 1
    transitions = tuple(
        (frm, to, count)
        for (frm, to), count in sorted(
            trans_counter.items(), key=lambda kv: (-kv[1], kv[0][0].name, kv[0][1].name)
        )
    )

    anticipation = _anticipation_accuracy(states)

    return RegimeReport(
        instrument_key=segments[0].instrument_key if segments else "?",
        timeframe_canonical=segments[0].timeframe_canonical if segments else "?",
        first_utc=segments[0].start_utc if segments else None,
        last_utc=history_end_utc,
        bar_count=bar_count,
        total_segments=len(segments),
        per_regime=tuple(per_regime),
        transitions=transitions,
        anticipation=anticipation,
    )


def _anticipation_accuracy(states: tuple[RegimeState, ...]) -> AnticipationAccuracy:
    by_id = {s.object_id: s for s in states}
    resolutions = 0
    to_ret = 0
    to_rev = 0
    leaned = 0
    matched = 0
    for state in states:
        if state.regime is RegimeType.RETRACEMENT:
            resolutions += 1
            to_ret += 1
        elif state.regime is RegimeType.REVERSAL:
            resolutions += 1
            to_rev += 1
        else:
            continue
        # Did the pullback this resolution superseded carry a directional lean?
        prior = by_id.get(state.supersedes) if state.supersedes else None
        lean = prior.anticipated_resolution if prior is not None else None
        if lean in (AnticipatedResolution.RETRACEMENT, AnticipatedResolution.REVERSAL):
            leaned += 1
            if (
                lean is AnticipatedResolution.RETRACEMENT
                and state.regime is RegimeType.RETRACEMENT
            ) or (
                lean is AnticipatedResolution.REVERSAL
                and state.regime is RegimeType.REVERSAL
            ):
                matched += 1
    rate = (matched / leaned) if leaned else None
    return AnticipationAccuracy(
        resolutions=resolutions,
        to_retracement=to_ret,
        to_reversal=to_rev,
        leaned=leaned,
        matched=matched,
        rate=rate,
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_report_markdown(report: RegimeReport) -> str:
    """A human-readable markdown report (tables), for a file or the console."""
    lines: list[str] = []
    lines.append(f"# Regime backtest — {report.instrument_key} {report.timeframe_canonical}")
    lines.append("")
    span = "n/a"
    if report.first_utc is not None and report.last_utc is not None:
        span = f"{report.first_utc.isoformat()} → {report.last_utc.isoformat()} (UTC)"
    lines.append(f"- Bars: **{report.bar_count}**")
    lines.append(f"- Span: {span}")
    lines.append(f"- Regime segments: **{report.total_segments}**")
    lines.append("")

    lines.append("## Regime distribution")
    lines.append("")
    lines.append(
        "| Regime | Segments | Share | Total min | Mean min | "
        "Median min | Mean ER | Mean Hurst |"
    )
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for s in report.per_regime:
        lines.append(
            f"| {s.regime.name} | {s.segment_count} | {s.share * 100:.1f}% | "
            f"{s.total_minutes:.0f} | {s.mean_minutes:.1f} | {s.median_minutes:.1f} | "
            f"{_fmt(s.mean_efficiency_ratio)} | {_fmt(s.mean_hurst)} |"
        )
    lines.append("")

    lines.append("## Transitions (most frequent first)")
    lines.append("")
    if report.transitions:
        lines.append("| From | To | Count |")
        lines.append("|---|---|--:|")
        for frm, to, count in report.transitions:
            lines.append(f"| {frm.name} | {to.name} | {count} |")
    else:
        lines.append("_No regime changes in this history._")
    lines.append("")

    a = report.anticipation
    lines.append("## Anticipation lean accuracy")
    lines.append("")
    lines.append(
        "The [VO-H] lean is recorded on a live PULLBACK_UNRESOLVED, never acted on "
        "(gate G2). This is how often it agreed with the eventual resolution."
    )
    lines.append("")
    lines.append(
        f"- Resolutions: **{a.resolutions}** "
        f"({a.to_retracement} retracement, {a.to_reversal} reversal)"
    )
    lines.append(f"- Carried a directional lean: **{a.leaned}**")
    lines.append(f"- Lean matched outcome: **{a.matched}**")
    lines.append(f"- Match rate: **{_fmt(a.rate, 2) if a.rate is not None else 'n/a'}**")
    lines.append("")
    return "\n".join(lines)
