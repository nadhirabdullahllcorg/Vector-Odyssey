"""
Gaussian Hidden Markov persistence-character classifier -- Phase 17a's
`IHMMEngine`, vo.research (layer 5).

WHY THIS EXISTS AND WHAT IT IS NOT. This answers a DIFFERENT question
from vo.research.transitions' TransitionMatrix (the existing empirical
Markov study, Phase 17, unaffected by this module): that one counts how
VO's own Phase-13 `RegimeType` states (CONSOLIDATION/EXPANSION/
RETRACEMENT/REVERSAL/PULLBACK_UNRESOLVED) transition into each other.
This module classifies a DIFFERENT axis entirely -- persistence
character, via an unsupervised Gaussian HMM trained on
[return normalized by ATR, rolling Efficiency Ratio] -- the exact
feature vector and 3-state design the user's own supplied quant-trading
reference specifies (architecture/vo-trade-logic-and-brain-plan.md
Sec 5.7-revision, 2026-09-18: Hurst is the "Compass", this HMM is the
"Map", ATR is the "Ruler").

TERMINOLOGY, DELIBERATE. The reference's own vocabulary for the 3
hidden states -- "Trending" / "Mean-Reverting" / "Random" -- was
rejected on the user's own instruction, same day, as conceptually
meaningless outside a generic quant-textbook frame. The states are
instead named for what they look like in this project's own ICT-
grounded vocabulary:

    DISPLACEMENT  -- high persistence, directional (high Hurst, high ER)
    ACCUMULATION  -- low persistence, range-bound/sweep character (low
                     Hurst, low ER)
    INDECISION    -- no clear persistence either way (Hurst near 0.5,
                     ER unclustered)

DISPLACEMENT is a real, standard ICT term (aggressive, efficient,
one-directional price delivery -- the mechanism that creates FVGs).
ACCUMULATION and INDECISION are ICT-*adjacent*, not literal Month-1
transcript vocabulary the way `[ICT]`-tagged concepts elsewhere in this
codebase are -- so, per this project's own G1 tagging discipline, this
whole labeling scheme is `[VO-H]`: a VO-authored hypothesis about how to
name an unsupervised statistical output meaningfully, not a source-
verified ICT concept. It is NOT Phase 13's RegimeType vocabulary, and
DELIBERATELY avoids reusing CONSOLIDATION/EXPANSION/RETRACEMENT/REVERSAL
for these states -- reusing those words for a different, unsupervised
model would be exactly the failure Sec 5.6 already forbids outright: "An
HMM latent state must never silently replace CONSOLIDATION/EXPANSION/
RETRACEMENT/REVERSAL." Two different axes, two different vocabularies,
on purpose.

NEVER a decision path. Same rule as always (Sec 5.6, G2): this output
never touches `RegimeState`, `RegimeTransition`, or `Trade Decision`
directly. It is a `[VO-H]` hypothesis until back-tested and promoted via
Phase 30 (Statistical Evaluation) / Phase 31 (human validation), exactly
like any other `[VO-H]`/`[VO-D]` interpretation in this codebase.

hmmlearn is lazily imported (see _hmmlearn() below) -- the same pattern
vo.market.mt5 uses for MetaTrader5: an optional dependency
(pyproject.toml's [hmm] extra) so the core test/CI environment never
needs it installed, with a clear error instead of a bare ImportError.

SESSION SCOPE, FLAGGED. The user's supplied testing framework scopes
this to a strict NY_AM killzone window, 08:30-11:30 America/New_York --
NOT sessions.yaml's own NY_AM (09:30-12:00). This is a real, still-
unreconciled conflict (see vo-trade-logic-and-brain-plan.md
Sec 5.7-revision's own flag), so the killzone window is its own explicit
constant here, never borrowed from or written into SessionConfig --
reconciling the two boundary sets is a separate decision.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from vo.market.bar import Bar

NY_ZONE = ZoneInfo("America/New_York")

# [VO-D] -- the user-supplied killzone window for this specific validation
# pipeline, deliberately kept separate from config/settings/sessions.yaml's
# NY_AM (09:30-12:00 ET) until that conflict is reconciled -- see this
# module's docstring.
KILLZONE_NY_AM_START = time(8, 30)
KILLZONE_NY_AM_END = time(11, 30)

# Confirmed validation benchmarks, vo-trade-logic-and-brain-plan.md
# Sec 5.7-revision (2026-09-18) -- targets to test against, not facts
# assumed true.
SELF_TRANSITION_DISPLACEMENT_TARGET = 0.80
DISPLACEMENT_ER_TARGET = 0.60
DISPLACEMENT_HURST_TARGET = 0.55
ACCUMULATION_ER_TARGET = 0.35
ACCUMULATION_HURST_TARGET = 0.45

DEFAULT_N_STATES = 3
DEFAULT_MIN_SAMPLES_PER_STATE = 10


def _hmmlearn() -> Any:
    """Lazily import hmmlearn.hmm.GaussianHMM -- see this module's
    docstring. Isolated here so the whole module imports without the
    (heavier, optional) package present, and so a missing install
    produces a clear message, not a bare ImportError from deep in a
    call."""
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError(
            "hmmlearn is not installed. It is an optional dependency: "
            "install with `pip install hmmlearn` (see pyproject.toml's "
            "[hmm] extra)."
        ) from exc
    return GaussianHMM


@dataclass(frozen=True)
class HMMSample:
    """One aligned input row: a bar's timestamp, its raw log return, the
    ATR-normalized log return (the HMM's actual input feature), and the
    Efficiency Ratio at that bar -- the [norm_return, ER] pair the user's
    reference specifies as the model's input vector."""

    when: datetime
    log_return: float
    norm_return: float
    er: float


@dataclass(frozen=True)
class HMMStateProfile:
    """Per-hidden-state alignment summary -- mirrors the user's own
    reference read-the-printout table: what does each unsupervised state
    actually look like once checked against ER/Hurst/ATR/return
    volatility? `mean_hurst` is None when no Hurst sample fell within
    max_gap of any bar assigned to this state -- an honest gap, not a
    guessed 0.5."""

    state: int
    n: int
    mean_er: float
    mean_hurst: float | None
    mean_atr: float
    return_volatility: float


@dataclass(frozen=True)
class HMMValidationReport:
    instrument_key: str
    timeframe_canonical: str
    generated_utc: datetime
    window_label: str
    n_samples: int
    n_states: int
    transition_matrix: tuple[tuple[float, ...], ...]
    profiles: tuple[HMMStateProfile, ...]
    displacement_state: int | None
    accumulation_state: int | None
    self_transition_displacement_ok: bool | None
    displacement_clustering_ok: bool | None
    accumulation_clustering_ok: bool | None


def _in_killzone(when_utc: datetime, *, window_start: time, window_end: time) -> bool:
    local = when_utc.astimezone(NY_ZONE).timetz().replace(tzinfo=None)
    if window_start <= window_end:
        return window_start <= local <= window_end
    return local >= window_start or local <= window_end  # overnight wrap, if ever needed


def _nearest_at_or_before(
    sorted_times: list[datetime], table: dict[datetime, float], at: datetime
) -> float | None:
    idx = bisect.bisect_right(sorted_times, at) - 1
    if idx < 0:
        return None
    return table[sorted_times[idx]]


def build_killzone_samples(
    bars: Sequence[Bar],
    atr_samples: Sequence[tuple[datetime, float]],
    er_samples: Sequence[tuple[datetime, float]],
    *,
    window_start: time = KILLZONE_NY_AM_START,
    window_end: time = KILLZONE_NY_AM_END,
) -> tuple[HMMSample, ...]:
    """
    Build the aligned [log_return / ATR, ER] feature series, restricted to
    the killzone window (NY-local time-of-day, same-day only). ATR/ER
    samples are matched to the nearest one at or before each bar's own
    timestamp (they may come from a coarser stride than `bars` itself);
    log_return is computed directly from `bars`' own consecutive closes,
    never resampled. A bar missing an ATR or ER match, a non-positive
    close, or a zero/negative ATR is dropped -- same "insufficient
    information, do not guess" discipline as atr_ticks/hurst_exponent/
    efficiency_ratio returning None.
    """
    atr_by_time = dict(atr_samples)
    er_by_time = dict(er_samples)
    atr_times = sorted(atr_by_time)
    er_times = sorted(er_by_time)

    samples: list[HMMSample] = []
    for i in range(1, len(bars)):
        bar = bars[i]
        prev = bars[i - 1]
        if not _in_killzone(bar.open_time_utc, window_start=window_start, window_end=window_end):
            continue
        if prev.close <= 0.0 or bar.close <= 0.0:
            continue
        atr = _nearest_at_or_before(atr_times, atr_by_time, bar.open_time_utc)
        er = _nearest_at_or_before(er_times, er_by_time, bar.open_time_utc)
        if atr is None or er is None or atr <= 0.0:
            continue
        log_return = math.log(bar.close / prev.close)
        samples.append(
            HMMSample(
                when=bar.open_time_utc,
                log_return=log_return,
                norm_return=log_return / atr,
                er=er,
            )
        )
    return tuple(samples)


def train_hmm(
    samples: Sequence[HMMSample],
    *,
    n_states: int = DEFAULT_N_STATES,
    min_samples_per_state: int = DEFAULT_MIN_SAMPLES_PER_STATE,
    random_state: int = 42,
) -> tuple[Any, tuple[int, ...]]:
    """
    Train the unsupervised Gaussian HMM on [norm_return, ER] and return
    (fitted model, hidden state assigned to each sample, in order) --
    the user's reference's own GaussianHMM(n_components=3,
    covariance_type="diag", n_iter=100, random_state=42) call, as a
    named, independently testable function.

    Raises ValueError rather than fitting a model on too little data to
    mean anything -- ` n_states * min_samples_per_state` is a floor, not
    a target; a real killzone-window backtest run should clear it by a
    wide margin.
    """
    if n_states < 2:
        raise ValueError(f"n_states must be >= 2, got {n_states}")
    floor = n_states * min_samples_per_state
    if len(samples) < floor:
        raise ValueError(
            f"need at least {floor} killzone samples to fit a {n_states}-state "
            f"HMM without overfitting noise, got {len(samples)}"
        )

    GaussianHMM = _hmmlearn()
    x = [[s.norm_return, s.er] for s in samples]
    model = GaussianHMM(
        n_components=n_states, covariance_type="diag", n_iter=100, random_state=random_state
    )
    model.fit(x)
    states = tuple(int(v) for v in model.predict(x))
    return model, states


def build_state_profiles(
    samples: Sequence[HMMSample],
    states: Sequence[int],
    hurst_samples: Sequence[tuple[datetime, float]],
    *,
    max_gap_minutes: float = 90.0,
) -> tuple[HMMStateProfile, ...]:
    """Group samples by their assigned hidden state and summarize ER
    (from the samples themselves), Hurst (looked up separately -- Hurst
    is never part of the HMM's own input vector, only used here to
    validate what each state looks like), an ATR proxy (norm_return's
    own scale, recovered as log_return / norm_return where finite), and
    return volatility (stdev of raw log_return) -- mirrors the user's own
    reference `state_analysis` groupby."""
    if len(samples) != len(states):
        raise ValueError(f"samples ({len(samples)}) and states ({len(states)}) must align 1:1")

    hurst_by_time = dict(hurst_samples)
    hurst_times = sorted(hurst_by_time)
    max_gap = max_gap_minutes * 60.0

    by_state: dict[int, list[HMMSample]] = {}
    for sample, state in zip(samples, states, strict=True):
        by_state.setdefault(state, []).append(sample)

    profiles: list[HMMStateProfile] = []
    for state in sorted(by_state):
        group = by_state[state]
        ers = [s.er for s in group]
        log_returns = [s.log_return for s in group]
        atrs = [s.log_return / s.norm_return for s in group if s.norm_return != 0.0]

        hurst_values: list[float] = []
        for s in group:
            idx = bisect.bisect_right(hurst_times, s.when) - 1
            if idx < 0:
                continue
            candidate_time = hurst_times[idx]
            if (s.when - candidate_time).total_seconds() <= max_gap:
                hurst_values.append(hurst_by_time[candidate_time])

        profiles.append(
            HMMStateProfile(
                state=state,
                n=len(group),
                mean_er=statistics.fmean(ers) if ers else 0.0,
                mean_hurst=statistics.fmean(hurst_values) if hurst_values else None,
                mean_atr=statistics.fmean(atrs) if atrs else 0.0,
                return_volatility=statistics.pstdev(log_returns) if len(log_returns) > 1 else 0.0,
            )
        )
    return tuple(profiles)


def build_hmm_validation_report(
    *,
    instrument_key: str,
    timeframe_canonical: str,
    generated_utc: datetime,
    window_label: str,
    model: Any,
    samples: Sequence[HMMSample],
    profiles: Sequence[HMMStateProfile],
) -> HMMValidationReport:
    """Assemble the report, including the confirmed validation checks
    (Sec 5.7-revision benchmarks) -- reported honestly as pass/fail
    against the target, never asserted true because a state merely
    exists. `displacement_state`/`accumulation_state` are None when no
    profile clearly leads on mean ER (a tie or a degenerate fit) -- an
    honest "cannot identify," not a forced pick."""
    transition_matrix = tuple(tuple(float(v) for v in row) for row in model.transmat_)

    displacement_state: int | None = None
    accumulation_state: int | None = None
    if profiles:
        by_er = sorted(profiles, key=lambda p: p.mean_er)
        if len(by_er) >= 2 and by_er[0].mean_er < by_er[-1].mean_er:
            accumulation_state = by_er[0].state
            displacement_state = by_er[-1].state

    self_transition_ok: bool | None = None
    if displacement_state is not None:
        self_transition_ok = (
            transition_matrix[displacement_state][displacement_state]
            > SELF_TRANSITION_DISPLACEMENT_TARGET
        )

    displacement_ok: bool | None = None
    if displacement_state is not None:
        profile = next(p for p in profiles if p.state == displacement_state)
        displacement_ok = profile.mean_er > DISPLACEMENT_ER_TARGET and (
            profile.mean_hurst is not None and profile.mean_hurst > DISPLACEMENT_HURST_TARGET
        )

    accumulation_ok: bool | None = None
    if accumulation_state is not None:
        profile = next(p for p in profiles if p.state == accumulation_state)
        accumulation_ok = profile.mean_er < ACCUMULATION_ER_TARGET and (
            profile.mean_hurst is not None and profile.mean_hurst < ACCUMULATION_HURST_TARGET
        )

    return HMMValidationReport(
        instrument_key=instrument_key,
        timeframe_canonical=timeframe_canonical,
        generated_utc=generated_utc,
        window_label=window_label,
        n_samples=len(samples),
        n_states=len(profiles),
        transition_matrix=transition_matrix,
        profiles=tuple(profiles),
        displacement_state=displacement_state,
        accumulation_state=accumulation_state,
        self_transition_displacement_ok=self_transition_ok,
        displacement_clustering_ok=displacement_ok,
        accumulation_clustering_ok=accumulation_ok,
    )


def _fmt_check(ok: bool | None) -> str:
    if ok is None:
        return "n/a (state not identified)"
    return "PASS" if ok else "FAIL"


def render_hmm_report_markdown(report: HMMValidationReport) -> str:
    """Render Phase 17a's IHMMEngine validation report. Same disclosure
    style as render_hurst_report_markdown/render_er_report_markdown/
    render_markov_report_markdown: everything an [VO-H] output needs
    attached to be inspectable, never a bare state label."""
    lines: list[str] = []
    lines.append(f"# HMM Persistence-Character Validation Report -- {report.instrument_key}")
    lines.append("")
    lines.append(f"Generated: {report.generated_utc.isoformat()}")
    lines.append(f"Timeframe: {report.timeframe_canonical}")
    lines.append(f"Window: {report.window_label}")
    lines.append(f"Samples: {report.n_samples}")
    lines.append(f"Hidden states: {report.n_states}")
    lines.append("")
    lines.append(
        "Phase 17a `IHMMEngine`, `[VO-H]` by construction (see this module's "
        "docstring). DISPLACEMENT/ACCUMULATION are VO-authored labels for the "
        "unsupervised states' persistence character, not Phase 13 `RegimeType` "
        "values -- this output never touches `RegimeState`/`Trade Decision` "
        "directly and is not trusted for any role until promoted via Phase 30/31."
    )
    lines.append("")
    lines.append("## 1. Transition matrix")
    lines.append("")
    header = "| state |" + "".join(f" -> {i} |" for i in range(report.n_states))
    lines.append(header)
    lines.append("|" + "---|" * (report.n_states + 1))
    for i, row in enumerate(report.transition_matrix):
        cells = "".join(f" {v:.3f} |" for v in row)
        lines.append(f"| {i} |{cells}")
    lines.append("")
    lines.append("## 2. State profiles (ER/Hurst/ATR/return-volatility alignment)")
    lines.append("")
    lines.append("| state | n | mean ER | mean Hurst | mean ATR (price) | return volatility |")
    lines.append("|---|---|---|---|---|---|")
    for p in report.profiles:
        hurst_cell = f"{p.mean_hurst:.3f}" if p.mean_hurst is not None else "n/a"
        role = ""
        if p.state == report.displacement_state:
            role = " (DISPLACEMENT)"
        elif p.state == report.accumulation_state:
            role = " (ACCUMULATION)"
        lines.append(
            f"| {p.state}{role} | {p.n} | {p.mean_er:.3f} | {hurst_cell} | "
            f"{p.mean_atr:.5f} | {p.return_volatility:.6f} |"
        )
    lines.append("")
    lines.append("## 3. Confirmed validation benchmarks (Sec 5.7-revision, 2026-09-18)")
    lines.append("")
    lines.append("| check | target | result |")
    lines.append("|---|---|---|")
    lines.append(
        f"| DISPLACEMENT self-transition probability | "
        f"> {SELF_TRANSITION_DISPLACEMENT_TARGET:.2f} | "
        f"{_fmt_check(report.self_transition_displacement_ok)} |"
    )
    lines.append(
        f"| DISPLACEMENT clustering (ER > {DISPLACEMENT_ER_TARGET:.2f}, "
        f"Hurst > {DISPLACEMENT_HURST_TARGET:.2f}) | both | "
        f"{_fmt_check(report.displacement_clustering_ok)} |"
    )
    lines.append(
        f"| ACCUMULATION clustering (ER < {ACCUMULATION_ER_TARGET:.2f}, "
        f"Hurst < {ACCUMULATION_HURST_TARGET:.2f}) | both | "
        f"{_fmt_check(report.accumulation_clustering_ok)} |"
    )
    lines.append("")
    lines.append(
        "A FAIL here is information, not a bug to silence -- it means this "
        "window/feature/state-count combination has not (yet) demonstrated the "
        "separation the confirmed benchmarks require, per this project's G2 "
        "discipline (nothing trusted without a back-test)."
    )
    return "\n".join(lines)
