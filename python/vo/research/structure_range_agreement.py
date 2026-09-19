"""
StructureRangeEngine ([VO-H], Phase 13b deliverable (C)) vs Phase 13's
real regime engine -- agreement study, vo.research (layer 5). VALIDATION
ONLY, same discipline as vo.research.phase_agreement (Phase 17a's own
agreement study): confirmed with the user (2026-09-19) that "directly
influencing the regime engine and its backtest" means wiring this rule
into the backtest REPORT only -- RegimeEngine's own code and behavior are
untouched, and this module's output never reaches vo.signals/vo.risk/
vo.execution. See vo.observation.structure_range's module docstring for
the rule itself.

WHAT THIS ANSWERS: at every bar, does the user's swing/internal range
rule's own delivery-phase label agree with what Phase 13's real,
ICT-structurally-grounded engine has already decided? A cross-tabulation
by Phase 13's actual state, same shape as phase_agreement.py's HMM study.

COMPARISON IS BY NAME, NOT BY SHARED ENUM: RangeStructureType has no
PULLBACK_UNRESOLVED counterpart (see vo.observation.structure_range's own
module docstring for why -- this rule's RETRACEMENT/REVERSAL triggers are
each directly checkable, so there is no honest "unresolved" interim
state to carry). A Phase 13 PULLBACK_UNRESOLVED bar can therefore never
register as "agreed" under this comparison -- it always lands in the
mismatch bucket, and `guessed_instead` for that row shows what this rule
was seeing while Phase 13 was still undecided, which is itself the more
interesting half of that particular row.

LAYERING. vo.research (layer 5) may import vo.observation (layer 3) and
vo.market (layer 1); this module additionally imports its own sibling
vo.research.regime_windows, the same same-layer pattern hurst_report.py/
markov_report.py/phase_agreement.py already use.

DELIBERATE DIVERGENCE FROM phase_agreement.py's SHAPE: no `window_label`
field. phase_agreement.py samples only inside specific killzone windows
(its HMM samples are windowed by construction); this comparison instead
replays StructureRangeEngine over every bar of the full backtest span,
so there is no narrower window to name -- `n_samples`/`n_comparable`
already say how much of the run this covers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.sequence import BarSequence
from vo.observation.regime import RegimeType
from vo.observation.structure_range import RangeStructureType, StructureRangeEngine
from vo.research.regime_windows import RegimeInterval, interval_at


@dataclass(frozen=True)
class RangeComparisonSample:
    when: datetime
    actual_phase: RegimeType | None
    range_phase: RangeStructureType | None


@dataclass(frozen=True)
class RangeAgreementBreakdown:
    actual_phase: RegimeType
    n: int
    agreed: int
    agreement_rate: float
    guessed_instead: dict[str, int]


@dataclass(frozen=True)
class RangeAgreementReport:
    instrument_key: str
    timeframe_canonical: str
    generated_utc: datetime
    n_samples: int
    n_comparable: int
    overall_agreement_rate: float | None
    by_phase: tuple[RangeAgreementBreakdown, ...]


def replay_range_states(
    sequence: BarSequence, engine: StructureRangeEngine
) -> tuple[tuple[datetime, RangeStructureType], ...]:
    """One (bar_time, state) row per bar -- the engine's *current* state
    immediately after processing that bar. Bar-granularity rather than a
    full segment-builder (regime_feed.build_regime_segments's own
    approach): this comparison only ever needs "what did each engine
    think at this instant", never a drawn band, so building a second
    segment-merging implementation just for this report would duplicate
    real logic for no reader -- see vo.research.regime_windows's own
    "one-off helper vs shared home" duplication rule for why this stays
    a plain loop instead."""
    out: list[tuple[datetime, RangeStructureType]] = []
    for index in range(len(sequence)):
        window = sequence.window_at(index)
        engine.on_bar(window)
        out.append((window.current.open_time_utc, engine.current_state()))
    return tuple(out)


def build_range_comparison_samples(
    intervals: Sequence[RegimeInterval],
    range_states: Sequence[tuple[datetime, RangeStructureType]],
) -> tuple[RangeComparisonSample, ...]:
    """Pairs each bar's range-rule phase with the real Phase 13 phase
    covering its timestamp (`interval_at`, the same lookup phase_
    agreement.py/hurst_report.py already use)."""
    starts = [iv.start_utc for iv in intervals]
    out: list[RangeComparisonSample] = []
    for when, range_phase in range_states:
        interval = interval_at(intervals, starts, when)
        actual_phase = interval.regime if interval is not None else None
        out.append(
            RangeComparisonSample(when=when, actual_phase=actual_phase, range_phase=range_phase)
        )
    return tuple(out)


def build_range_agreement_report(
    samples: Sequence[RangeComparisonSample],
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
) -> RangeAgreementReport:
    """Only samples with a known `actual_phase` (Phase 13 covered that
    timestamp) count toward the breakdown -- mirrors
    phase_agreement.build_phase_agreement_report exactly."""
    comparable = [s for s in samples if s.actual_phase is not None]

    by_phase_samples: dict[RegimeType, list[RangeComparisonSample]] = {}
    for s in comparable:
        assert s.actual_phase is not None  # narrowed by the filter above
        by_phase_samples.setdefault(s.actual_phase, []).append(s)

    breakdowns: list[RangeAgreementBreakdown] = []
    total_agreed = 0
    for phase in RegimeType:
        group = by_phase_samples.get(phase)
        if not group:
            continue
        agreed = sum(
            1 for s in group if s.range_phase is not None and s.range_phase.value == phase.value
        )
        total_agreed += agreed
        guessed_instead: dict[str, int] = {}
        for s in group:
            if s.range_phase is not None and s.range_phase.value == phase.value:
                continue
            label = s.range_phase.value if s.range_phase is not None else "NO RANGE ESTABLISHED"
            guessed_instead[label] = guessed_instead.get(label, 0) + 1
        breakdowns.append(
            RangeAgreementBreakdown(
                actual_phase=phase,
                n=len(group),
                agreed=agreed,
                agreement_rate=agreed / len(group),
                guessed_instead=guessed_instead,
            )
        )

    overall_rate = (total_agreed / len(comparable)) if comparable else None

    return RangeAgreementReport(
        instrument_key=instrument_key,
        timeframe_canonical=timeframe_canonical,
        generated_utc=generated_utc,
        n_samples=len(samples),
        n_comparable=len(comparable),
        overall_agreement_rate=overall_rate,
        by_phase=tuple(breakdowns),
    )


def render_range_agreement_markdown(report: RangeAgreementReport) -> str:
    """VALIDATION-ONLY disclosure, same discipline as
    render_phase_agreement_markdown: this report is not a decision path,
    and says so up front, every time it is rendered."""
    lines: list[str] = []
    lines.append(
        f"# Structure-Range Rule vs Phase 13 Phase-Agreement Report -- {report.instrument_key}"
    )
    lines.append("")
    lines.append(f"Generated: {report.generated_utc.isoformat()}")
    lines.append(f"Timeframe: {report.timeframe_canonical}")
    lines.append(
        f"Samples: {report.n_samples} (comparable against Phase 13: {report.n_comparable})"
    )
    lines.append("")
    lines.append(
        "**VALIDATION ONLY -- confirmed 2026-09-19.** This report measures whether the "
        "user's swing/internal asymmetric-range rule (vo.observation.structure_range, "
        "[VO-H], Phase 13b deliverable (C)) agrees with Phase 13's real, "
        "ICT-structurally-grounded regime engine. It is not a classifier in its own "
        "right on any decision path: StructureRangeEngine output never produces a "
        "RegimeState, never reaches Trade Decision, and Phase 13's own code and "
        "behavior are completely unchanged by this report existing."
    )
    lines.append("")
    if report.overall_agreement_rate is not None:
        lines.append(f"**Overall agreement rate: {report.overall_agreement_rate:.1%}**")
    else:
        lines.append("**Overall agreement rate: n/a (no comparable samples)**")
    lines.append("")
    lines.append("## By actual (Phase 13) phase")
    lines.append("")
    lines.append("| Phase 13 phase | n | rule agreement | rule said instead |")
    lines.append("|---|---|---|---|")
    for b in report.by_phase:
        instead = (
            ", ".join(f"{label}: {count}" for label, count in sorted(b.guessed_instead.items()))
            or "(always agreed)"
        )
        lines.append(f"| {b.actual_phase.value} | {b.n} | {b.agreement_rate:.1%} | {instead} |")
    lines.append("")
    lines.append(
        "Phase 13's PULLBACK_UNRESOLVED can never show as 'agreed' here -- this rule has "
        "no equivalent interim state (see vo.observation.structure_range's module "
        "docstring) -- so that row's 'rule said instead' column is the actually useful "
        "reading: what the rule was seeing while Phase 13 was still undecided."
    )
    lines.append("")
    lines.append(
        "A low agreement rate is information, not a bug to silence -- it means this rule "
        "does not yet track Phase 13's real structural classification for this "
        "window/instrument, per this project's G2 discipline (nothing trusted without a "
        "back-test, including this comparison itself)."
    )
    return "\n".join(lines)
