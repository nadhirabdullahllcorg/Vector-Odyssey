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

SESSION BREAKDOWN. The classifier itself has no session concept -- it
sees only a bounded CandleWindow (gate G3) and decides regime purely from
structure. That is deliberate, not an oversight, but it also means
nothing here could previously say WHETHER regime duration actually
differs by session, only that the classifier does not care. build_session_
breakdown answers that empirically: it attributes each bar's already-
computed regime to the session `vo.time.sessions.session_at` reports for
that same instant -- the SAME config the live EA runtime applies
(VOTimeEngine.context_for uses the identical utc.astimezone(zone) ->
session_at lookup), so backtest and live cannot silently disagree about
what session an instant belongs to. This is additive telemetry only
(gate G2): it changes nothing about how a regime is classified, only how
the report slices what already happened. Whether the classifier itself
should ever treat a session boundary as meaningful is a separate, G6-
gated design question this module does not answer.

DURATION IS MEASURED IN TRADING MINUTES, NOT WALL-CLOCK. A segment's
[start_utc, end_utc) can span a weekend or the daily inter-session gap
(the market has no bars at all during either) -- subtracting timestamps
directly would silently count that closed-market time as if the regime
had "lasted" through it, which measurably distorted every duration
figure this module reports (found empirically: on a real 100k-bar
backtest, total reported minutes across all regimes summed to within
rounding of the entire multi-month CALENDAR span, even though only
~69% of that span had any bars at all). When `bars` is supplied, a
segment's duration is the count of bars that actually fall inside it,
times `minutes_per_bar` -- never a timestamp subtraction. Without
`bars` (some unit tests exercise the arithmetic on hand-built segments
with no real bar data), the old wall-clock subtraction remains as an
explicit fallback -- approximate, and it will overcount a segment that
happens to span a gap.

LEAN SCORING (2026-09-18 audit, finding A2). A pullback's lean is refreshed
whenever it changes (RegimeEngine._maybe_refresh_lean), and the lean is a
function of pullback depth -- the same geometry that DEFINES the outcome
(REVERSAL = close beyond the defining swing, depth > 1; RETRACEMENT = new
extreme, depth back to 0). Scoring the lean one supersedes-hop back from
the resolution therefore scores it at an outcome-adjacent instant: near-
tautological for 1-2 bar pullbacks, anti-correlated for long ones, and
the blend of the two is not an "accuracy". build_lean_chains /
score_lean_chains are the single implementation both this module and
vo.telemetry.regime_validation use: the lean is scored at the ORIGIN
record (a fixed information point) and, in the validation report, at
fixed k-bar horizons on the pullbacks that survived past bar k; the
last-refresh number is retained only as a labelled diagnostic.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.regime import (
    AnticipatedResolution,
    RegimeState,
    RegimeTransition,
    RegimeType,
)
from vo.telemetry.regime_feed import RegimeSegment
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, session_at

_ER_FEATURE = "efficiency_ratio"
_HURST_FEATURE = "hurst_exponent"


@dataclass(frozen=True)
class RegimeStats:
    """One regime's share of the backtest.

    `is_momentary` is set for a regime that genuinely has zero measured
    duration by the engine's own design (RETRACEMENT/REVERSAL: an
    instantaneous resolution, immediately followed by re-entering
    EXPANSION in the same bar -- never a period the market spends time
    in) rather than one that simply never occurred. For those,
    `segment_count` is the count of RegimeState occurrences (there is no
    drawable "segment" to count instead); total/mean/median minutes are
    0.0 and excluded from any other regime's `share` by construction
    (duration accounting only ever sums non-momentary segments)."""

    regime: RegimeType
    segment_count: int
    total_minutes: float
    share: float  # of total classified time, [0, 1]
    mean_minutes: float
    median_minutes: float
    mean_efficiency_ratio: float | None
    mean_hurst: float | None
    is_momentary: bool = False
    state_records: int = 0
    """Every RegimeState record carrying this regime, INCLUDING lean-refresh
    records (a PULLBACK_UNRESOLVED re-emitted because its lean changed).
    Unit-of-analysis note (2026-09-18 audit): mean ER/Hurst above are taken
    over segment-ENTRY records only (refreshes excluded), so a long pullback
    that refreshed its lean forty times weighs the same as a two-bar one."""


@dataclass(frozen=True)
class SessionRegimeStats:
    """One (session, regime) cell of the backtest, attributed bar-by-bar
    using the SAME vo.time.sessions config the live EA runtime applies --
    no new session logic, purely additive telemetry (gate G2). Answers
    whether regime time concentrates in particular sessions, which the
    aggregate distribution table above cannot show, without changing what
    "regime" means or how it is classified.

    `regime` is None for bars the engine had not yet classified (before
    its first RegimeState -- e.g. swing/ER/Hurst warmup at the very start
    of the history). `session` is "OFF_SESSION" for a bar whose New York
    wall-clock time falls outside every configured window (US100 has one:
    the daily ~16:00-18:00 ET gap between NY_PM and ASIA in
    config/settings/sessions.yaml). `segments_started` counts distinct
    regime runs that began in this session -- a cheap proxy for "how often
    does a regime change originate here", separate from how much time it
    then occupies."""

    session: str
    regime: RegimeType | None
    bar_count: int
    total_minutes: float
    share_of_session: float  # of this session's own classified bar-time, [0, 1]
    segments_started: int


@dataclass(frozen=True)
class AnticipationAccuracy:
    """How the [VO-H] lean on PULLBACK_UNRESOLVED fared against the actual
    resolution, scored at TWO information points (see the module
    docstring's LEAN SCORING section, added after the 2026-09-18 audit):

    - `leaned`/`matched`/`rate`: the lean in effect at the LAST refresh
      before resolution (one supersedes-hop back from the resolution
      record). Outcome-adjacent: the lean is a function of pullback depth
      and the outcome is defined on the same depth, so this number is
      near-tautological for short pullbacks. Kept as a diagnostic, and
      labelled as such wherever it is rendered -- never as "accuracy".
    - `origin_leaned`/`origin_matched`/`origin_rate`: the lean recorded on
      the FIRST PULLBACK_UNRESOLVED record of the chain, i.e. what the
      engine believed the moment the pullback began, before any of the
      pullback's own bars could inform it. This is the honest fixed-
      information-point score.

    `leaned` counts resolutions whose chain carried a directional lean
    (RETRACEMENT/REVERSAL, not UNCLEAR/none) at that point; `matched`
    counts those the outcome agreed with; `rate` is matched/leaned, or
    None when nothing leaned (no denominator to divide)."""

    resolutions: int  # total RETRACEMENT + REVERSAL resolutions
    to_retracement: int
    to_reversal: int
    leaned: int
    matched: int
    rate: float | None
    origin_leaned: int = 0
    origin_matched: int = 0
    origin_rate: float | None = None


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
    per_session: tuple[SessionRegimeStats, ...] = ()


def _feature_value(state: RegimeState, name: str) -> float | None:
    for feature in state.supporting_features:
        if feature.name == name:
            return feature.value
    return None


def _mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


# ── lean chains: the ONE place a lean is matched to its outcome ───────────


@dataclass(frozen=True)
class LeanChain:
    """One pullback, origin to resolution: its PULLBACK_UNRESOLVED records
    in time order (origin first; each later one a lean refresh that
    supersedes the previous -- see RegimeEngine._maybe_refresh_lean) and
    the RETRACEMENT/REVERSAL record that resolved it. Every lean-accuracy
    figure in this module and in vo.telemetry.regime_validation is scored
    from these chains, so the two reports cannot score a lean differently."""

    records: tuple[RegimeState, ...]
    resolution: RegimeState

    @property
    def origin(self) -> RegimeState:
        return self.records[0]

    @property
    def last(self) -> RegimeState:
        return self.records[-1]


@dataclass(frozen=True)
class HorizonScore:
    """The lean scored at one information point. `eligible` is how many
    chains were still unresolved at that point (for a k-bar horizon, only
    pullbacks that lasted more than k bars -- stated, not hidden: a
    horizon-k score is a score on the survivors). `majority_baseline_rate`
    is what always guessing the majority outcome scores on exactly those
    eligible chains, so each horizon carries its own honest baseline."""

    label: str
    eligible: int
    leaned: int
    matched: int
    rate: float | None
    majority_baseline_rate: float | None


def is_lean_refresh(state: RegimeState, by_id: Mapping[str, RegimeState]) -> bool:
    """True for a PULLBACK_UNRESOLVED record that merely re-recorded the
    lean of a still-open pullback (supersedes another PULLBACK_UNRESOLVED
    record); False for the record where a pullback began."""
    if state.regime is not RegimeType.PULLBACK_UNRESOLVED or state.supersedes is None:
        return False
    prior = by_id.get(state.supersedes)
    return prior is not None and prior.regime is RegimeType.PULLBACK_UNRESOLVED


def build_lean_chains(states: Sequence[RegimeState]) -> tuple[LeanChain, ...]:
    """Walk every RETRACEMENT/REVERSAL record back through its supersedes
    chain to the record where the pullback began. Resolutions whose chain
    is empty (no PULLBACK_UNRESOLVED behind them) are skipped -- there is
    no lean to score."""
    by_id = {s.object_id: s for s in states}
    chains: list[LeanChain] = []
    for state in states:
        if state.regime not in (RegimeType.RETRACEMENT, RegimeType.REVERSAL):
            continue
        if state.supersedes is None:
            continue
        cur = by_id.get(state.supersedes)
        chain: list[RegimeState] = []
        while cur is not None and cur.regime is RegimeType.PULLBACK_UNRESOLVED:
            chain.append(cur)
            cur = by_id.get(cur.supersedes) if cur.supersedes else None
        if not chain:
            continue
        chain.reverse()
        chains.append(LeanChain(records=tuple(chain), resolution=state))
    return tuple(chains)


def _lean_matches(lean: AnticipatedResolution | None, resolution: RegimeType) -> bool:
    return (lean is AnticipatedResolution.RETRACEMENT and resolution is RegimeType.RETRACEMENT) or (
        lean is AnticipatedResolution.REVERSAL and resolution is RegimeType.REVERSAL
    )


def _is_directional(lean: AnticipatedResolution | None) -> bool:
    return lean in (AnticipatedResolution.RETRACEMENT, AnticipatedResolution.REVERSAL)


def bar_offset(
    record: RegimeState,
    origin: RegimeState,
    bar_index: Mapping[datetime, int],
    minutes_per_bar: float,
) -> int:
    """Bars from `origin` to `record`. Exact (bar-counted, closures do not
    count) when both instants are known bar open times; otherwise the
    wall-clock fallback in units of minutes_per_bar, same fallback
    discipline as durations_by_regime."""
    a = bar_index.get(origin.observed_at)
    b = bar_index.get(record.observed_at)
    if a is not None and b is not None:
        return b - a
    return round((record.observed_at - origin.observed_at).total_seconds() / 60.0 / minutes_per_bar)


def score_lean_chains(
    chains: Sequence[LeanChain],
    *,
    horizon_bars: int | None,
    bar_index: Mapping[datetime, int] | None = None,
    minutes_per_bar: float = 1.0,
) -> HorizonScore:
    """Score the lean at one information point.

    horizon_bars=None  -> the lean at the LAST refresh before resolution
                          (outcome-adjacent diagnostic; every chain eligible).
    horizon_bars=0     -> the ORIGIN lean (every chain eligible).
    horizon_bars=k>0   -> the lean in effect k bars after origin, scored only
                          on chains that were still unresolved after bar k
                          (the survivors), so the score cannot borrow from
                          the outcome.
    """
    index = bar_index or {}
    eligible = 0
    leaned = 0
    matched = 0
    outcomes: Counter[RegimeType] = Counter()
    for chain in chains:
        if horizon_bars is None:
            record: RegimeState | None = chain.last
        elif horizon_bars == 0:
            record = chain.origin
        else:
            if bar_offset(chain.resolution, chain.origin, index, minutes_per_bar) <= horizon_bars:
                continue  # resolved at or before the horizon -- not a forecast
            record = None
            for candidate in chain.records:
                if bar_offset(candidate, chain.origin, index, minutes_per_bar) <= horizon_bars:
                    record = candidate
                else:
                    break
        if record is None:
            continue
        eligible += 1
        outcomes[chain.resolution.regime] += 1
        lean = record.anticipated_resolution
        if not _is_directional(lean):
            continue
        leaned += 1
        if _lean_matches(lean, chain.resolution.regime):
            matched += 1
    if horizon_bars is None:
        label = "last refresh before resolution (outcome-adjacent diagnostic)"
    elif horizon_bars == 0:
        label = "origin (lean recorded when the pullback began)"
    else:
        label = f"+{horizon_bars} bars after origin (survivors only)"
    return HorizonScore(
        label=label,
        eligible=eligible,
        leaned=leaned,
        matched=matched,
        rate=(matched / leaned) if leaned else None,
        majority_baseline_rate=(max(outcomes.values()) / eligible) if eligible else None,
    )



def build_session_breakdown(
    segments: Sequence[RegimeSegment],
    bars: Sequence[Bar],
    session_config: SessionConfig,
    *,
    minutes_per_bar: float = 1.0,
) -> tuple[SessionRegimeStats, ...]:
    """Bar-level (session, regime) attribution -- see the module and
    SessionRegimeStats docstrings for what this is and why it is safe
    (gate G2: telemetry only, invents no session or classifier logic).

    Segments tile the bars contiguously, in chronological order, by
    build_regime_segments' own contract (each run's end_utc == the next
    run's start_utc; the final run's end_utc is None and extends to
    whatever the last bar is). This does one linear merge pass over
    `bars` against that tiling: a bar's regime is whichever segment's
    [start_utc, end_utc) contains its open_time_utc, or None if it falls
    before the first segment ever starts (or there are no segments at
    all -- an empty history, or one still warming up)."""
    ordered_segments = sorted(segments, key=lambda s: s.start_utc)
    n_segs = len(ordered_segments)

    bar_counts: Counter[tuple[str, RegimeType | None]] = Counter()
    seg_idx = 0
    for bar in bars:
        t = bar.open_time_utc
        while seg_idx < n_segs - 1:
            current_end = ordered_segments[seg_idx].end_utc
            if current_end is None or t < current_end:
                break
            seg_idx += 1

        regime: RegimeType | None = None
        if seg_idx < n_segs and t >= ordered_segments[seg_idx].start_utc:
            seg = ordered_segments[seg_idx]
            if seg.end_utc is None or t < seg.end_utc:
                regime = seg.regime

        window = session_at(t.astimezone(session_config.zone), session_config)
        session_name = window.name if window is not None else OFF_SESSION_LABEL
        bar_counts[(session_name, regime)] += 1

    starts: Counter[tuple[str, RegimeType]] = Counter()
    for seg in ordered_segments:
        window = session_at(seg.start_utc.astimezone(session_config.zone), session_config)
        session_name = window.name if window is not None else OFF_SESSION_LABEL
        starts[(session_name, seg.regime)] += 1

    session_totals: Counter[str] = Counter()
    for (session_name, _regime), n in bar_counts.items():
        session_totals[session_name] += n

    stats: list[SessionRegimeStats] = []
    for (session_name, regime), bar_count in sorted(
        bar_counts.items(),
        key=lambda kv: (kv[0][0], kv[0][1].name if kv[0][1] is not None else ""),
    ):
        total = session_totals[session_name]
        stats.append(
            SessionRegimeStats(
                session=session_name,
                regime=regime,
                bar_count=bar_count,
                total_minutes=bar_count * minutes_per_bar,
                share_of_session=(bar_count / total) if total else 0.0,
                segments_started=starts.get((session_name, regime), 0) if regime else 0,
            )
        )
    return tuple(stats)


def _bar_counts_per_segment(
    segments: Sequence[RegimeSegment], bars: Sequence[Bar]
) -> list[int]:
    """Parallel to `segments`: how many bars actually fall inside each
    segment's [start_utc, end_utc) span. Segments tile the bars
    contiguously and in chronological order (build_regime_segments' own
    contract), so one linear merge pass over sorted bars suffices --
    the same tiling walk build_session_breakdown uses, minus the session
    lookup, kept separate so this has no session_config dependency."""
    order = sorted(range(len(segments)), key=lambda i: segments[i].start_utc)
    counts = [0] * len(segments)
    n = len(order)
    pos = 0
    for bar in sorted(bars, key=lambda b: b.open_time_utc):
        t = bar.open_time_utc
        while pos < n - 1:
            current_end = segments[order[pos]].end_utc
            if current_end is None or t < current_end:
                break
            pos += 1
        if pos < n:
            seg = segments[order[pos]]
            if t >= seg.start_utc and (seg.end_utc is None or t < seg.end_utc):
                counts[order[pos]] += 1
    return counts


def durations_by_regime(
    segments: Sequence[RegimeSegment],
    *,
    history_end_utc: datetime | None = None,
    minutes_per_bar: float = 1.0,
    bars: Sequence[Bar] = (),
) -> dict[RegimeType, list[float]]:
    """Per-segment duration (minutes), grouped by regime -- the same
    computation build_regime_report does internally for its own per-regime
    table, factored out so other telemetry (e.g. vo.telemetry.regime_
    validation's duration-distribution/period-breakdown work) can reuse it
    without recomputing or re-deriving the bar-counting logic. See the
    module docstring's DURATION IS MEASURED IN TRADING MINUTES section --
    `bars` gives real trading-minute accounting; without it, falls back to
    wall-clock (approximate, overcounts a segment spanning a closed-market
    gap)."""
    durations: dict[RegimeType, list[float]] = {}
    if bars:
        bar_counts = _bar_counts_per_segment(segments, bars)
        for seg, count in zip(segments, bar_counts, strict=True):
            if count == 0:
                continue
            durations.setdefault(seg.regime, []).append(count * minutes_per_bar)
    else:
        for seg in segments:
            end = seg.end_utc if seg.end_utc is not None else history_end_utc
            if end is None:
                continue
            minutes = (end - seg.start_utc).total_seconds() / 60.0
            durations.setdefault(seg.regime, []).append(minutes)
    return durations


def build_regime_report(
    segments: tuple[RegimeSegment, ...],
    states: tuple[RegimeState, ...],
    *,
    transitions_log: Sequence[RegimeTransition],
    history_end_utc: datetime | None,
    minutes_per_bar: float = 1.0,
    bar_count: int = 0,
    bars: Sequence[Bar] = (),
    session_config: SessionConfig | None = None,
) -> RegimeReport:
    """Summarize a regime backtest. `history_end_utc` closes the final,
    open-ended segment for duration accounting (its own end_utc is None);
    without it that segment is skipped from the time totals. `minutes_per_bar`
    labels durations (1.0 for M1). `transitions_log` is the engine's own
    RegimeTransition log (engine.transitions.all()) -- required, not
    reconstructed from `segments`: a segment-pairwise reconstruction looks
    plausible but is wrong whenever a resolution (RETRACEMENT/REVERSAL) sits
    between two segments, because that resolution's own zero-width run was
    already dropped by build_regime_segments. Two real transitions
    (PULLBACK_UNRESOLVED -> RETRACEMENT, RETRACEMENT -> EXPANSION) would
    then look like one phantom PULLBACK_UNRESOLVED -> EXPANSION, or -- if
    the market re-entered a pullback immediately -- a PULLBACK_UNRESOLVED
    -> PULLBACK_UNRESOLVED that never actually happened as a state
    transition. The real log has no such gap.

    `bars` and `session_config` are both optional and additive: pass both
    to also get a per-session breakdown (see build_session_breakdown).
    `bars` alone (no session_config) still upgrades duration accounting
    from wall-clock to actual trading minutes -- see the module
    docstring's DURATION IS MEASURED IN TRADING MINUTES section. Leave
    both out and behavior is unchanged from before either existed."""
    # Duration per segment. RETRACEMENT/REVERSAL never appear here -- they
    # have no segments (see build_regime_segments' own docstring on
    # zero-width runs) -- which is exactly right, since they are momentary
    # by the engine's own design, not a duration.
    # Real trading minutes when `bars` is given (a weekend or the daily
    # off-session gap contributes zero bars, so zero minutes -- exactly
    # right); wall-clock fallback otherwise -- see durations_by_regime.
    durations = durations_by_regime(
        segments, history_end_utc=history_end_utc, minutes_per_bar=minutes_per_bar, bars=bars
    )

    grand_total = sum(sum(v) for v in durations.values()) or 1.0

    # ER / Hurst evidence per regime, from the state log.
    # ER / Hurst evidence per regime, from the state log -- over segment-
    # ENTRY records only. A lean refresh re-emits a PULLBACK_UNRESOLVED
    # record with the same regime; counting those would weight the
    # PULLBACK row by how often each pullback refreshed (2026-09-18 audit,
    # finding A4). state_counts still counts every record, and is reported
    # alongside so the two units are visible, never conflated.
    by_id = {s.object_id: s for s in states}
    er_by_regime: dict[RegimeType, list[float]] = {}
    hurst_by_regime: dict[RegimeType, list[float]] = {}
    for state in states:
        if is_lean_refresh(state, by_id):
            continue
        er = _feature_value(state, _ER_FEATURE)
        if er is not None:
            er_by_regime.setdefault(state.regime, []).append(er)
        hurst = _feature_value(state, _HURST_FEATURE)
        if hurst is not None:
            hurst_by_regime.setdefault(state.regime, []).append(hurst)

    seg_counts = Counter(seg.regime for seg in segments)
    state_counts = Counter(state.regime for state in states)
    # Every regime that ever appears as a drawable segment OR as a raw
    # state record -- so a momentary regime (no segments at all, only
    # states) still gets a row instead of silently disappearing.
    all_regimes = {seg.regime for seg in segments} | set(state_counts)

    per_regime: list[RegimeStats] = []
    for regime in sorted(all_regimes, key=lambda r: r.name):
        has_segments = regime in seg_counts
        mins = durations.get(regime, [])
        total = sum(mins)
        per_regime.append(
            RegimeStats(
                regime=regime,
                segment_count=seg_counts[regime] if has_segments else state_counts[regime],
                total_minutes=total,
                share=total / grand_total,
                mean_minutes=statistics.fmean(mins) if mins else 0.0,
                median_minutes=statistics.median(mins) if mins else 0.0,
                mean_efficiency_ratio=_mean_or_none(er_by_regime.get(regime, [])),
                mean_hurst=_mean_or_none(hurst_by_regime.get(regime, [])),
                is_momentary=not has_segments,
                state_records=state_counts[regime],
            )
        )

    # Transitions: the engine's own log, not a segment reconstruction --
    # see this function's own docstring for why the two can disagree.
    trans_counter: Counter[tuple[RegimeType, RegimeType]] = Counter(
        (t.from_state, t.to_state) for t in transitions_log
    )
    transitions = tuple(
        (frm, to, count)
        for (frm, to), count in sorted(
            trans_counter.items(), key=lambda kv: (-kv[1], kv[0][0].name, kv[0][1].name)
        )
    )

    anticipation = _anticipation_accuracy(states)

    per_session = (
        build_session_breakdown(segments, bars, session_config)
        if session_config is not None and bars
        else ()
    )

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
        per_session=per_session,
    )


def _anticipation_accuracy(states: Sequence[RegimeState]) -> AnticipationAccuracy:
    resolutions = sum(
        1 for s in states if s.regime in (RegimeType.RETRACEMENT, RegimeType.REVERSAL)
    )
    to_ret = sum(1 for s in states if s.regime is RegimeType.RETRACEMENT)
    to_rev = sum(1 for s in states if s.regime is RegimeType.REVERSAL)
    chains = build_lean_chains(states)
    last = score_lean_chains(chains, horizon_bars=None)
    origin = score_lean_chains(chains, horizon_bars=0)
    return AnticipationAccuracy(
        resolutions=resolutions,
        to_retracement=to_ret,
        to_reversal=to_rev,
        leaned=last.leaned,
        matched=last.matched,
        rate=last.rate,
        origin_leaned=origin.leaned,
        origin_matched=origin.matched,
        origin_rate=origin.rate,
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
        "Units: **Count** = segments (runs with at least one bar; a zero-bar run is "
        "dropped by build_regime_segments); **Records** = every RegimeState record "
        "carrying that regime, including lean refreshes -- for PULLBACK_UNRESOLVED "
        "these differ a lot. **Mean ER/Hurst** are over segment-entry records only "
        "(refreshes excluded), one sample per segment."
    )
    lines.append("")
    lines.append(
        "| Regime | Count (segments) | Records | Share | Total min | Mean min | "
        "Median min | Mean ER | Mean Hurst |"
    )
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    any_momentary = False
    for s in report.per_regime:
        if s.is_momentary:
            any_momentary = True
            name = f"{s.regime.name} *"
            share_str = total_str = mean_str = median_str = "—"
        else:
            name = s.regime.name
            share_str = f"{s.share * 100:.1f}%"
            total_str = f"{s.total_minutes:.0f}"
            mean_str = f"{s.mean_minutes:.1f}"
            median_str = f"{s.median_minutes:.1f}"
        lines.append(
            f"| {name} | {s.segment_count} | {s.state_records} | {share_str} | "
            f"{total_str} | {mean_str} | {median_str} | "
            f"{_fmt(s.mean_efficiency_ratio)} | {_fmt(s.mean_hurst)} |"
        )
    if any_momentary:
        lines.append("")
        lines.append(
            "\\* momentary: an instantaneous resolution (RETRACEMENT/REVERSAL) -- "
            "the engine confirms it and re-enters EXPANSION in the same bar, so "
            "there is no duration to measure. Count is how many times it happened, "
            "not a band count."
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

    if report.per_session:
        lines.append("## Session breakdown")
        lines.append("")
        lines.append(
            "Bar-by-bar attribution of already-classified regime time to the "
            "session it fell in (config/settings/sessions.yaml -- the same "
            "config the live EA runtime applies). Telemetry only (gate G2): "
            "the classifier above never sees session boundaries; this only "
            "shows whether its output happens to concentrate by session."
        )
        lines.append("")
        lines.append("| Session | Regime | Bars | Total min | Share of session | Runs started |")
        lines.append("|---|---|--:|--:|--:|--:|")
        for row in report.per_session:
            regime_name = row.regime.name if row.regime is not None else "(unclassified)"
            lines.append(
                f"| {row.session} | {regime_name} | {row.bar_count} | "
                f"{row.total_minutes:.0f} | {row.share_of_session * 100:.1f}% | "
                f"{row.segments_started} |"
            )
        lines.append("")

    a = report.anticipation
    lines.append("## Anticipation lean accuracy")
    lines.append("")
    lines.append(
        "The [VO-H] lean is recorded on a live PULLBACK_UNRESOLVED, never acted on "
        "(gate G2). Two scores, two information points -- read the ORIGIN score as "
        "the honest one. The lean is a function of pullback depth and the outcome is "
        "defined on that same depth, so the lean at the last refresh before "
        "resolution is outcome-adjacent (near-tautological for short pullbacks); it "
        "is kept only as a diagnostic (2026-09-18 audit, finding A2)."
    )
    lines.append("")
    lines.append(
        f"- Resolutions: **{a.resolutions}** "
        f"({a.to_retracement} retracement, {a.to_reversal} reversal)"
    )
    lines.append(
        f"- **Origin lean** (recorded when the pullback began): leaned "
        f"**{a.origin_leaned}**, matched **{a.origin_matched}**, rate "
        f"**{_fmt(a.origin_rate, 2) if a.origin_rate is not None else 'n/a'}**"
    )
    lines.append(
        f"- Last-refresh lean (outcome-adjacent diagnostic): leaned {a.leaned}, "
        f"matched {a.matched}, rate {_fmt(a.rate, 2) if a.rate is not None else 'n/a'}"
    )
    lines.append("")
    return "\n".join(lines)
