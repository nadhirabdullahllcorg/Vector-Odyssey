"""
Statistical (Hurst+ER+HMM) 4-phase agreement study vs Phase 13's real
ICT-structural regime engine -- Phase 17a's IHMMEngine, vo.research
(layer 5). VALIDATION ONLY -- confirmed 2026-09-18, same day as the
vo-trade-logic-and-brain-plan.md Sec 5.7-revision itself.

WHAT WAS ASKED. The user asked whether Hurst/Markov math could be
applied directly to classify which of the 4 ICT Price Delivery phases
(CONSOLIDATION/EXPANSION/RETRACEMENT/REVERSAL) is active, using only
statistical thresholds on Hurst, Efficiency Ratio, and the HMM's
DISPLACEMENT/ACCUMULATION state (vo.research.hmm_report) -- no swing
structure, no Phase 13 involvement at all -- and supplied a reference
script that does exactly that, then proposes trade-execution rules
keyed to the result ("When ICT_Phase == 'Expansion': look for FVGs...").

Asked directly whether this should ship as a standalone second
classifier (the reference's own shape) or as a validation study
measuring agreement against Phase 13's real classifier, the user
confirmed: validation study only.

WHAT THIS IS NOT. This module NEVER produces a `RegimeState` or
`RegimeTransition`. `statistical_phase_guess()`'s output never reaches
`Trade Decision`, is never written to the live feed, and is not read by
anything outside this report. It exists to answer exactly one question:
how often does a pure Hurst/ER/HMM statistical guess agree with what
Phase 13's real, ICT-structurally-grounded engine already decided, bar
by bar? -- a Phase 30 (Statistical Evaluation)-style artifact, gated
the same way every other `[VO-H]` output in this codebase is (G2:
nothing trusted, let alone promoted, without back-tested evidence). A
high agreement rate would be a genuinely interesting finding worth its
own, separate conversation about whether a fast statistical proxy has
any role anywhere -- that conversation has not happened, and this
module does not presume its outcome.

THRESHOLDS. Adapted directly from the user's own supplied reference (a
pandas `classify_ict_phase()` function), translated into this project's
None-over-guessing discipline: a bar that does not clearly land in one
of the four threshold profiles returns None ("statistically ambiguous"),
never a forced/defaulted label -- the reference's own catch-all
"Transition/Unknown" string, made honest rather than silently coerced
into one of the four real phase names.

LAYERING. vo.research (layer 5); imports vo.observation.regime
(layer 3, RegimeType only -- no engine logic) and its own sibling
vo.research.regime_windows/hmm_report -- both already layer 5, so this
is a same-layer import, same pattern hurst_report.py/markov_report.py
already use for vo.research.transitions/regime_windows/statistics.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.observation.regime import RegimeType
from vo.research.hmm_report import HMMSample
from vo.research.regime_windows import RegimeInterval, interval_at


@dataclass(frozen=True)
class PhaseComparisonSample:
    when: datetime
    actual_phase: RegimeType | None
    statistical_phase: RegimeType | None


@dataclass(frozen=True)
class PhaseAgreementBreakdown:
    actual_phase: RegimeType
    n: int
    agreed: int
    agreement_rate: float
    guessed_instead: dict[str, int]


@dataclass(frozen=True)
class PhaseAgreementReport:
    instrument_key: str
    timeframe_canonical: str
    generated_utc: datetime
    window_label: str
    n_samples: int
    n_comparable: int
    n_statistically_ambiguous: int
    overall_agreement_rate: float | None
    by_phase: tuple[PhaseAgreementBreakdown, ...]


def statistical_phase_guess(
    *,
    hurst: float | None,
    er: float,
    is_displacement_state: bool,
    is_accumulation_state: bool,
) -> RegimeType | None:
    """
    The user-supplied reference's 4-phase threshold logic, translated
    1:1 (see this module's docstring) -- returns None rather than a
    forced default when no profile clearly matches (the reference's own
    "Transition/Unknown" bucket).

    `hurst=None` (a real possibility -- hurst_exponent's own None
    convention, e.g. a flat/degenerate window) always yields None: no
    threshold check below can honestly evaluate without it.
    """
    if hurst is None:
        return None

    if is_accumulation_state or (hurst < 0.45 and er < 0.35):
        return RegimeType.CONSOLIDATION
    if is_displacement_state and hurst >= 0.55 and er >= 0.60:
        return RegimeType.EXPANSION
    if not is_displacement_state and 0.35 <= er < 0.60 and 0.45 <= hurst <= 0.55:
        return RegimeType.RETRACEMENT
    if is_displacement_state and er < 0.50 and hurst < 0.50:
        return RegimeType.REVERSAL
    return None


def build_phase_comparison_samples(
    intervals: Sequence[RegimeInterval],
    hmm_samples: Sequence[HMMSample],
    hmm_states: Sequence[int],
    hurst_samples: Sequence[tuple[datetime, float]],
    *,
    displacement_state: int | None,
    accumulation_state: int | None,
    max_hurst_gap_minutes: float = 90.0,
) -> tuple[PhaseComparisonSample, ...]:
    """
    Pair each HMM-scored killzone sample with (a) the real Phase 13
    phase covering its timestamp (`interval_at`, the same lookup
    build_hurst_by_regime already uses) and (b) a statistical phase
    guess from `statistical_phase_guess`, using the nearest Hurst
    sample at or before it (within `max_hurst_gap_minutes`, same
    discipline as hmm_report.build_state_profiles).
    """
    if len(hmm_samples) != len(hmm_states):
        raise ValueError(
            f"hmm_samples ({len(hmm_samples)}) and hmm_states ({len(hmm_states)}) must align 1:1"
        )

    starts = [iv.start_utc for iv in intervals]
    hurst_by_time = dict(hurst_samples)
    hurst_times = sorted(hurst_by_time)
    max_gap = max_hurst_gap_minutes * 60.0

    out: list[PhaseComparisonSample] = []
    for sample, state in zip(hmm_samples, hmm_states, strict=True):
        interval = interval_at(intervals, starts, sample.when)
        actual_phase = interval.regime if interval is not None else None

        hurst_value: float | None = None
        idx = bisect.bisect_right(hurst_times, sample.when) - 1
        if idx >= 0 and (sample.when - hurst_times[idx]).total_seconds() <= max_gap:
            hurst_value = hurst_by_time[hurst_times[idx]]

        guess = statistical_phase_guess(
            hurst=hurst_value,
            er=sample.er,
            is_displacement_state=(state == displacement_state),
            is_accumulation_state=(state == accumulation_state),
        )
        out.append(
            PhaseComparisonSample(
                when=sample.when, actual_phase=actual_phase, statistical_phase=guess
            )
        )
    return tuple(out)


def build_phase_agreement_report(
    samples: Sequence[PhaseComparisonSample],
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    window_label: str,
) -> PhaseAgreementReport:
    """Only samples with a known `actual_phase` (Phase 13 covered that
    timestamp) count toward the breakdown -- a bar Phase 13 has no
    opinion on (outside any segment) cannot be scored as agree/disagree
    either way."""
    comparable = [s for s in samples if s.actual_phase is not None]
    ambiguous = sum(1 for s in comparable if s.statistical_phase is None)

    by_phase_samples: dict[RegimeType, list[PhaseComparisonSample]] = {}
    for s in comparable:
        assert s.actual_phase is not None  # narrowed by the filter above
        by_phase_samples.setdefault(s.actual_phase, []).append(s)

    breakdowns: list[PhaseAgreementBreakdown] = []
    total_agreed = 0
    for phase in RegimeType:
        group = by_phase_samples.get(phase)
        if not group:
            continue
        agreed = sum(1 for s in group if s.statistical_phase == phase)
        total_agreed += agreed
        guessed_instead: dict[str, int] = {}
        for s in group:
            if s.statistical_phase == phase:
                continue
            label = s.statistical_phase.value if s.statistical_phase is not None else "AMBIGUOUS"
            guessed_instead[label] = guessed_instead.get(label, 0) + 1
        breakdowns.append(
            PhaseAgreementBreakdown(
                actual_phase=phase,
                n=len(group),
                agreed=agreed,
                agreement_rate=agreed / len(group),
                guessed_instead=guessed_instead,
            )
        )

    overall_rate = (total_agreed / len(comparable)) if comparable else None

    return PhaseAgreementReport(
        instrument_key=instrument_key,
        timeframe_canonical=timeframe_canonical,
        generated_utc=generated_utc,
        window_label=window_label,
        n_samples=len(samples),
        n_comparable=len(comparable),
        n_statistically_ambiguous=ambiguous,
        overall_agreement_rate=overall_rate,
        by_phase=tuple(breakdowns),
    )


def render_phase_agreement_markdown(report: PhaseAgreementReport) -> str:
    """VALIDATION-ONLY disclosure, same discipline as
    render_hmm_report_markdown: this report is not a decision path, and
    says so up front, every time it is rendered."""
    lines: list[str] = []
    lines.append(f"# Statistical vs Phase 13 Phase-Agreement Report -- {report.instrument_key}")
    lines.append("")
    lines.append(f"Generated: {report.generated_utc.isoformat()}")
    lines.append(f"Timeframe: {report.timeframe_canonical}")
    lines.append(f"Window: {report.window_label}")
    lines.append(
        f"Samples: {report.n_samples} (comparable against Phase 13: {report.n_comparable})"
    )
    lines.append(
        f"Statistically ambiguous (no clear threshold match): {report.n_statistically_ambiguous}"
    )
    lines.append("")
    lines.append(
        "**VALIDATION ONLY -- confirmed 2026-09-18.** This report measures whether a "
        "pure Hurst+Efficiency-Ratio+HMM statistical guess agrees with Phase 13's real, "
        "ICT-structurally-grounded regime engine. It is not a classifier in its own "
        "right: `statistical_phase_guess()` output never produces a `RegimeState`, "
        "never reaches `Trade Decision`, and is not read by anything outside this "
        "report. See vo.research.phase_agreement's module docstring."
    )
    lines.append("")
    if report.overall_agreement_rate is not None:
        lines.append(f"**Overall agreement rate: {report.overall_agreement_rate:.1%}**")
    else:
        lines.append("**Overall agreement rate: n/a (no comparable samples)**")
    lines.append("")
    lines.append("## By actual (Phase 13) phase")
    lines.append("")
    lines.append("| Phase 13 phase | n | statistical agreement | guessed instead |")
    lines.append("|---|---|---|---|")
    for b in report.by_phase:
        instead = (
            ", ".join(f"{label}: {count}" for label, count in sorted(b.guessed_instead.items()))
            or "(always agreed)"
        )
        lines.append(f"| {b.actual_phase.value} | {b.n} | {b.agreement_rate:.1%} | {instead} |")
    lines.append("")
    lines.append(
        "A low agreement rate is information, not a bug to silence -- it means the "
        "statistical proxy does not yet track Phase 13's real structural classification "
        "for this window/instrument, per this project's G2 discipline (nothing trusted "
        "without a back-test, including this comparison itself)."
    )
    return "\n".join(lines)
