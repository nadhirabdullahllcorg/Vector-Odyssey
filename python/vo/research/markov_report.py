"""
Conditional Markov transition study -- Phase 17 (vo.research, layer 5).

WHY THIS EXISTS, AND HOW IT RELATES TO THE OTHER THREE REPORTS. Third of
the user's four independent-characterization reports (Hurst 15a, ER 15b,
Markov 17, HMM 17a), same agreed order, same rule stated twice: "Don't
let the quantitative models vote on the regime. At least initially."
vo.telemetry.regime_validation's own build_transition_matrix (v33) already
answers the UNCONDITIONAL question -- "how have observed regimes
transitioned, overall, across this whole run." This module answers the
user's fuller ask (architecture/vo-phase-plan.md's §13-notes and §15-notes,
Phase 17's own row in §5): "given the observed VO state sequence, what
transitions actually occurred historically, CONDITIONED on session,
Hurst bucket, ER bucket, and preceding-pullback duration." Markov NEVER
redefines the canonical regime -- every matrix built here is read-only
description of the ALREADY-DECIDED, structure-classified RegimeTransition
log (vo.observation.regime, layer 3); nothing here feeds back into
RegimeEngine, and no cell is ever called predictive (Phase 17's own gate,
per the phase-plan: "Sample size per cell; no cell called predictive").

DOES NOT decide, classify, or vote. Same G2 posture as hurst_report.py
and efficiency_ratio_report.py: read-only research over an already-
completed backtest run, several layers removed from any decision path.

LAYERING. vo.research is layer 5; vo.telemetry is layer 7 -- see
hurst_report.py's own LAYERING note, which applies identically. The
TransitionMatrix type and its builder now live in vo.research.transitions
(moved out of vo.telemetry.regime_validation in the same change that
introduced this module), so this module and regime_validation.py both
import from that one shared home rather than either depending on the
other. nearest_sample_before (also vo.research.transitions) is the same
bisect lookup hurst_report.py/efficiency_ratio_report.py already use for
their own "preceding transitions" sections -- reused here a third time,
not reimplemented.

REUSE, NOT REIMPLEMENTATION. This module never computes a rolling
Hurst/ER series itself -- build_value_bucket_matrices below is generic
over whatever `samples: Sequence[tuple[datetime, float]]` the caller
passes it. The orchestration script (scripts/backtest_regime.py) is the
one that calls build_rolling_hurst (hurst_report.py) / build_rolling_er
(efficiency_ratio_report.py) directly and hands the result in -- the
exact same rolling series those two reports already compute for their
own section 1 and section 3; no second rolling-sampler exists anywhere,
and this module stays decoupled from how the series is produced.

CONDITIONING DIMENSIONS, MATCHING THE PHASE-PLAN'S OWN LIST.
  1. Baseline (unconditional) -- the same matrix regime_validation.py's
     own build_transition_matrix produces, rendered here too so every
     conditioned matrix in this report has something to compare against
     without leaving this document. Also checks the Month 1 transition-
     structure constraint explicitly (CONSOLIDATION never transitions
     directly into RETRACEMENT/REVERSAL -- see vo-phase-plan.md's
     §13-notes) rather than leaving it implicit in a zero cell.
  2. By session -- which session each transition's observed_at fell in.
  3. By Hurst bucket -- tercile (Low/Mid/High) of the nearest Hurst
     rolling sample at or before the transition, cut points computed
     from the OVERALL sample population so a bucket means the same thing
     throughout the report, not just "low among transitions."
  4. By Efficiency Ratio bucket -- same shape as (3), over ER.
  5. By preceding pullback duration -- quartile of how long the
     resolving PULLBACK_UNRESOLVED ran before resolving into RETRACEMENT
     or REVERSAL. Only transitions FROM PULLBACK_UNRESOLVED qualify; a
     transition's own resolution RegimeState is matched by
     (to_state, observed_at) -- RegimeEngine._emit_state/_emit_transition
     both stamp the SAME bar's open_time_utc as observed_at when a
     transition accompanies a state emission, so this match is exact,
     not a nearest-time guess. The pullback's own origin is found by
     walking `supersedes` back through however many lean-refresh records
     preceded the resolution, mirroring regime_validation.build_
     accuracy_validation's own nested `_pullback_origin` helper --
     duplicated here rather than extracted, since that helper is a small
     closure over a caller-supplied states_by_id, not a standalone
     formula (this project's tiny-helper duplication allowance, see
     vo.telemetry.regime_feed's own `_feature_value`).

SAMPLE SIZE. Every conditioned matrix necessarily has less data per cell
than the baseline -- conditioning by definition subdivides one dataset,
never adds to it. Every rendered matrix's rows below `min_cell_n`
observations are flagged, both inline and again in a consolidated
section, per Phase 17's own gate: "no cell called predictive."

OUT-OF-SAMPLE. Not attempted here. A conditional transition study is
already close to the limit of what this run's sample size supports once
subdivided three ways; a proper out-of-sample check belongs to the
eventual comparative study (§15-notes), after Markov and HMM both exist,
not to this standalone characterization.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime

from vo.observation.regime import RegimeState, RegimeTransition, RegimeType
from vo.research.transitions import (
    TransitionMatrix,
    build_transition_matrix,
    nearest_sample_before,
)
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, is_rth, session_at

DEFAULT_MIN_CELL_N = 30

_ALL_REGIMES: tuple[RegimeType, ...] = tuple(RegimeType)

# CONSOLIDATION must never transition directly into RETRACEMENT/REVERSAL
# per Month 1's transition structure (vo-phase-plan.md's §13-notes) --
# checked explicitly in section 1 rather than left as an implicit zero.
_FORBIDDEN_DIRECT_TRANSITIONS: tuple[tuple[RegimeType, RegimeType], ...] = (
    (RegimeType.CONSOLIDATION, RegimeType.RETRACEMENT),
    (RegimeType.CONSOLIDATION, RegimeType.REVERSAL),
)


# ── 2. by session ─────────────────────────────────────────────────────────


def build_session_matrices(
    transitions_log: Sequence[RegimeTransition], session_config: SessionConfig
) -> dict[str, TransitionMatrix]:
    buckets: dict[str, list[RegimeTransition]] = {}
    for transition in transitions_log:
        window = session_at(transition.observed_at.astimezone(session_config.zone), session_config)
        label = window.name if window is not None else OFF_SESSION_LABEL
        buckets.setdefault(label, []).append(transition)
    return {label: build_transition_matrix(ts) for label, ts in sorted(buckets.items())}


# ── 2b. by RTH vs non-RTH (added 2026-09-18, at the user's request) ──────


def build_rth_matrices(
    transitions_log: Sequence[RegimeTransition], session_config: SessionConfig
) -> dict[str, TransitionMatrix]:
    """Same telemetry-only posture as build_session_matrices -- a
    coarser, binary cut across the same transitions the four-way
    session breakdown already covers, not a replacement for it."""
    buckets: dict[str, list[RegimeTransition]] = {"RTH": [], "NON_RTH": []}
    for transition in transitions_log:
        label = (
            "RTH"
            if is_rth(transition.observed_at.astimezone(session_config.zone), session_config)
            else "NON_RTH"
        )
        buckets[label].append(transition)
    return {label: build_transition_matrix(ts) for label, ts in buckets.items() if ts}


# ── 3/4. by Hurst or ER tercile bucket ───────────────────────────────────


def _tercile_label(value: float, cuts: tuple[float, float]) -> str:
    c1, c2 = cuts
    if value <= c1:
        return "Low"
    if value <= c2:
        return "Mid"
    return "High"


def build_value_bucket_matrices(
    transitions_log: Sequence[RegimeTransition],
    samples: Sequence[tuple[datetime, float]],
    *,
    max_gap_minutes: float,
) -> tuple[dict[str, TransitionMatrix], tuple[float, float] | None]:
    """Conditions `transitions_log` by the nearest sample in `samples` at
    or before each transition's observed_at, bucketed into tercile
    Low/Mid/High -- cuts computed from `samples`' OWN overall
    distribution, not the near-transition subset. `samples` is expected
    to be one window length's rolling series (e.g.
    hurst_report.build_rolling_hurst(...)[period]) -- this function is
    generic over Hurst or ER, whichever series the caller passes in.
    Transitions with no qualifying nearby sample (data gap, or before the
    series' own warmup) are excluded, not guessed at. Returns ({}, None)
    when there are too few samples to cut into terciles at all."""
    if len(samples) < 3:
        return {}, None
    values = [v for _t, v in samples]
    c1, c2 = statistics.quantiles(values, n=3, method="inclusive")

    buckets: dict[str, list[RegimeTransition]] = {}
    for transition in transitions_log:
        found = nearest_sample_before(
            samples, transition.observed_at, max_gap_minutes=max_gap_minutes
        )
        if found is None:
            continue
        _when, value = found
        label = _tercile_label(value, (c1, c2))
        buckets.setdefault(label, []).append(transition)

    order = ["Low", "Mid", "High"]
    matrices = {
        label: build_transition_matrix(buckets[label]) for label in order if label in buckets
    }
    return matrices, (c1, c2)


# ── 5. by preceding pullback duration ────────────────────────────────────


def _quartile_label(value: float, cuts: tuple[float, float, float]) -> str:
    q1, q2, q3 = cuts
    if value <= q1:
        return "Q1 (lowest)"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4 (highest)"


def _pullback_origin(state: RegimeState, states_by_id: dict[str, RegimeState]) -> RegimeState:
    """Walk `supersedes` back through however many lean-refresh records
    preceded this one, to the record where the pullback FIRST began --
    see this module's own docstring for why this is duplicated from
    regime_validation.build_accuracy_validation's nested helper of the
    same name rather than extracted."""
    cur = state
    while cur.supersedes is not None:
        prior = states_by_id.get(cur.supersedes)
        if prior is None or prior.regime is not RegimeType.PULLBACK_UNRESOLVED:
            break
        cur = prior
    return cur


def build_preceding_pullback_duration_matrices(
    transitions_log: Sequence[RegimeTransition], states: Sequence[RegimeState]
) -> tuple[dict[str, TransitionMatrix], tuple[float, float, float] | None]:
    """Conditions RETRACEMENT/REVERSAL resolutions (transitions FROM
    PULLBACK_UNRESOLVED) by how long, in minutes, the resolving pullback
    ran before resolving -- quartiles of the resolving transitions' own
    duration population. A transition's resolution RegimeState is looked
    up by (to_state, observed_at), an exact match given how RegimeEngine
    emits the two together (see this module's own docstring); a
    transition whose match is missing is skipped, honestly, rather than
    guessed at -- should not happen given the engine's own contract, but
    not assumed. Returns ({}, None) when fewer than 4 transitions
    qualify (too few to cut into quartiles at all)."""
    states_by_key = {(s.regime, s.observed_at): s for s in states}
    states_by_id = {s.object_id: s for s in states}

    qualifying: list[tuple[RegimeTransition, float]] = []
    for transition in transitions_log:
        if transition.from_state is not RegimeType.PULLBACK_UNRESOLVED:
            continue
        resolution = states_by_key.get((transition.to_state, transition.observed_at))
        if resolution is None:
            continue
        origin = _pullback_origin(resolution, states_by_id)
        duration_minutes = (transition.observed_at - origin.observed_at).total_seconds() / 60.0
        qualifying.append((transition, duration_minutes))

    if len(qualifying) < 4:
        return {}, None

    durations = [d for _t, d in qualifying]
    q1, q2, q3 = statistics.quantiles(durations, n=4, method="inclusive")
    buckets: dict[str, list[RegimeTransition]] = {}
    for transition, duration in qualifying:
        label = _quartile_label(duration, (q1, q2, q3))
        buckets.setdefault(label, []).append(transition)

    order = ["Q1 (lowest)", "Q2", "Q3", "Q4 (highest)"]
    matrices = {
        label: build_transition_matrix(buckets[label]) for label in order if label in buckets
    }
    return matrices, (q1, q2, q3)


# ── sample-size flagging ─────────────────────────────────────────────────


def small_cell_rows(
    matrix: TransitionMatrix, *, min_n: int = DEFAULT_MIN_CELL_N
) -> tuple[str, ...]:
    """Row labels ("FROM (n=...)") whose total transition count falls
    below `min_n` -- Phase 17's own gate (vo-phase-plan.md: "Sample size
    per cell; no cell called predictive") means a thin row's
    probabilities get flagged as unreliable rather than silently
    rendered alongside solid ones."""
    return tuple(
        f"{frm.name} (n={total})"
        for frm, total in sorted(matrix.row_totals.items(), key=lambda kv: kv[0].name)
        if total < min_n
    )


# ── rendering ─────────────────────────────────────────────────────────


def _render_matrix_table(matrix: TransitionMatrix) -> list[str]:
    lines: list[str] = []
    header = "| From \\\\ To | " + " | ".join(r.name for r in _ALL_REGIMES) + " | Row total |"
    lines.append(header)
    lines.append("|---|" + "--:|" * (len(_ALL_REGIMES) + 1))
    for frm in _ALL_REGIMES:
        row_total = matrix.row_totals.get(frm, 0)
        cells = []
        for to in _ALL_REGIMES:
            count = matrix.counts.get((frm, to), 0)
            prob = matrix.probability(frm, to)
            prob_str = f" ({prob * 100:.0f}%)" if prob else ""
            cells.append(f"{count}{prob_str}")
        lines.append(f"| {frm.name} | " + " | ".join(cells) + f" | {row_total} |")
    return lines


def _render_bucketed_section(
    lines: list[str], matrices: dict[str, TransitionMatrix], *, min_cell_n: int
) -> None:
    if not matrices:
        lines.append("_No transitions qualified for this conditioning -- nothing to render._")
        lines.append("")
        return
    for label, matrix in matrices.items():
        total = sum(matrix.row_totals.values())
        lines.append(f"### {label} (n={total})")
        lines.append("")
        lines.extend(_render_matrix_table(matrix))
        warnings = small_cell_rows(matrix, min_n=min_cell_n)
        if warnings:
            lines.append("")
            lines.append(f"_Thin row(s), read cautiously: {', '.join(warnings)}._")
        lines.append("")


def render_markov_report_markdown(
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    baseline: TransitionMatrix,
    by_session: dict[str, TransitionMatrix],
    by_hurst: dict[str, TransitionMatrix],
    hurst_cuts: tuple[float, float] | None,
    hurst_window: int,
    by_er: dict[str, TransitionMatrix],
    er_cuts: tuple[float, float] | None,
    er_window: int,
    by_pullback_duration: dict[str, TransitionMatrix],
    pullback_duration_cuts: tuple[float, float, float] | None,
    by_rth: dict[str, TransitionMatrix] | None = None,
    min_cell_n: int = DEFAULT_MIN_CELL_N,
) -> str:
    lines: list[str] = []
    lines.append(
        f"# Conditional Markov Transition Study v1 (Phase 17) — "
        f"{instrument_key} {timeframe_canonical}"
    )
    lines.append("")
    lines.append(f"Generated: {generated_utc.isoformat()} (UTC)")
    lines.append("")
    lines.append(
        "Conditions the already-decided, structure-classified RegimeTransition log "
        "against the frozen 1,000,000-bar regime baseline -- by session, by Hurst "
        "bucket, by Efficiency Ratio bucket, and by how long a resolving pullback "
        "ran before resolving. Markov never redefines the canonical regime: every "
        "matrix below describes transitions the Basic Regime Engine already decided "
        "on structure alone: nothing here feeds back into RegimeEngine, and no cell "
        "is a predictive rule. Conditioning necessarily thins the data per cell -- "
        "see section 6 for every row too small to trust."
    )
    lines.append("")

    # 1. baseline
    lines.append("## 1. Baseline (unconditional) transition matrix")
    lines.append("")
    lines.extend(_render_matrix_table(baseline))
    lines.append("")
    violations = [
        (frm, to) for frm, to in _FORBIDDEN_DIRECT_TRANSITIONS if baseline.counts.get((frm, to), 0)
    ]
    if violations:
        detail = ", ".join(
            f"{frm.name}→{to.name} (n={baseline.counts[(frm, to)]})" for frm, to in violations
        )
        lines.append(
            f"**Month 1 structural constraint check: VIOLATED.** CONSOLIDATION should "
            f"never transition directly into RETRACEMENT/REVERSAL "
            f"(vo-phase-plan.md §13-notes), but this run shows: {detail}."
        )
    else:
        lines.append(
            "Month 1 structural constraint check: **HELD.** No direct "
            "CONSOLIDATION→RETRACEMENT or CONSOLIDATION→REVERSAL transition in this run."
        )
    lines.append("")
    baseline_warnings = small_cell_rows(baseline, min_n=min_cell_n)
    if baseline_warnings:
        lines.append(f"_Thin row(s), read cautiously: {', '.join(baseline_warnings)}._")
        lines.append("")

    # 2. by session
    lines.append("## 2. Conditioned by session")
    lines.append("")
    lines.append(
        "Telemetry only -- the classifier never sees session boundaries; this asks "
        "whether the OBSERVED transition mix looks different by session, the same "
        "posture as every other report's own session breakdown."
    )
    lines.append("")
    _render_bucketed_section(lines, by_session, min_cell_n=min_cell_n)

    # 2b. by RTH vs non-RTH
    if by_rth:
        lines.append("## 2b. Conditioned by RTH vs non-RTH")
        lines.append("")
        lines.append(
            "Same telemetry-only posture as section 2 -- a coarser, binary cut "
            "across the same transitions, not a replacement for the four-way "
            "session breakdown above."
        )
        lines.append("")
        _render_bucketed_section(lines, by_rth, min_cell_n=min_cell_n)

    # 3. by Hurst
    lines.append(f"## 3. Conditioned by Hurst (tercile, window={hurst_window})")
    lines.append("")
    if hurst_cuts is not None:
        c1, c2 = hurst_cuts
        lines.append(f"Cuts: Low ≤ {c1:.3f} < Mid ≤ {c2:.3f} < High.")
        lines.append("")
    _render_bucketed_section(lines, by_hurst, min_cell_n=min_cell_n)

    # 4. by ER
    lines.append(f"## 4. Conditioned by Efficiency Ratio (tercile, window={er_window})")
    lines.append("")
    if er_cuts is not None:
        c1, c2 = er_cuts
        lines.append(f"Cuts: Low ≤ {c1:.3f} < Mid ≤ {c2:.3f} < High.")
        lines.append("")
    _render_bucketed_section(lines, by_er, min_cell_n=min_cell_n)

    # 5. by preceding pullback duration
    lines.append("## 5. Conditioned by preceding pullback duration")
    lines.append("")
    lines.append(
        "RETRACEMENT/REVERSAL resolutions only (transitions FROM "
        "PULLBACK_UNRESOLVED), quartiled by how many minutes the resolving pullback "
        "ran before resolving."
    )
    lines.append("")
    if pullback_duration_cuts is not None:
        q1, q2, q3 = pullback_duration_cuts
        lines.append(
            f"Cuts (minutes): Q1 ≤ {q1:.1f} < Q2 ≤ {q2:.1f} < Q3 ≤ {q3:.1f} < Q4."
        )
        lines.append("")
    _render_bucketed_section(lines, by_pullback_duration, min_cell_n=min_cell_n)

    # 6. consolidated sample-size caveats
    lines.append("## 6. Sample-size caveats, consolidated")
    lines.append("")
    all_warnings: list[str] = []
    for section_name, matrices in (
        ("Baseline", {"baseline": baseline}),
        ("By session", by_session),
        ("By RTH", by_rth or {}),
        ("By Hurst", by_hurst or {}),
        ("By ER", by_er),
        ("By pullback duration", by_pullback_duration),
    ):
        for label, matrix in matrices.items():
            warnings = small_cell_rows(matrix, min_n=min_cell_n)
            if warnings:
                all_warnings.append(f"{section_name} / {label}: {', '.join(warnings)}")
    if all_warnings:
        for line in all_warnings:
            lines.append(f"- {line}")
    else:
        lines.append(f"No row anywhere in this report falls below n={min_cell_n}.")
    lines.append("")

    lines.append("## Conclusions this report deliberately does not draw")
    lines.append("")
    lines.append(
        "This report does not propose a transition rule, does not call any cell "
        "predictive regardless of its probability, and does not recommend any "
        "change to RegimeEngine's structure-only classifier. A conditioned "
        "probability that looks different from the baseline is worth investigating "
        "further, never treated as validated -- especially in any row flagged in "
        "section 6. The next step in the user's own plan is HMM (§17a), now that "
        "Hurst, Efficiency Ratio, and Markov all exist standalone -- only after "
        "that does a comparative/relationship study become appropriate, and even "
        "then: \"Don't let the quantitative models vote on the regime. At least "
        "initially.\""
    )

    return "\n".join(lines)
