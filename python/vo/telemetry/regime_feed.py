"""
Regime chart feed -- Phase 13a viz-only channel (Python computes, the
MQL5 indicator reads a file).

WHAT THIS IS. VO_Regime.mq5 draws regime bands on an MT5 chart. It does
NOT re-implement the regime classifier -- that would be a second,
drifting copy of vo.observation.regime. Instead the REAL RegimeEngine
runs in Python (scripts/publish_regime.py), and this module turns its
emitted RegimeState log into a compact, line-oriented feed the indicator
can parse with nothing more than StringSplit. The indicator stays dumb;
the one classifier of record stays in Python (same discipline as the
VO_Swings.mq5 / SwingEngine split, gate G6).

WHY A DELIMITED FEED, NOT THE CANONICAL JSONL WIRE. This is a display
feed, not the canonical wire. MQL5 has no JSON parser and hand-rolling
one is how bugs get into the terminal; a fixed pipe-delimited line is
trivially and robustly parsed there. The feed is derived output, never
an input to any decision path (gate G2), so it does not have to be the
canonical record format -- but every line still carries the real
RegimeState.object_id it came from (gate G8: chart objects are traceable
to a canonical record).

SEGMENTS, NOT PER-BAR RECORDS. The engine emits a RegimeState only when
the picture changes (a new regime, a resolution, or a refreshed
anticipation lean on an unresolved pullback). A band is the run of time
one regime was in force: consecutive records sharing the same
`regime` collapse into one segment spanning from the first record's bar
to the next different record's bar. A PULLBACK_UNRESOLVED run and the
RETRACEMENT/REVERSAL it later resolves into stay DISTINCT bands -- that
is the honest retrospective ("here the engine was unsure, here it
resolved"), not a smoothed-over rewrite. The representative record of a
run is its LAST (freshest, non-superseded) record: its object_id,
confidence and anticipation lean label the band; the band's start is the
run's FIRST record's bar.

TIME. RegimeState.observed_at is a resolved UTC instant (== a bar's
open_time_utc). MT5 chart time is broker-server wall-clock, so the feed
carries broker-server epoch seconds, produced by the caller's
`epoch_of` (scripts/publish_regime.py maps UTC back to the broker-local
wall-clock the bridge captured). This module never guesses that mapping
-- it is handed one, exactly as generate_swing_reference.py is.

MARKERS (v2). RETRACEMENT/REVERSAL are not periods the market spends
time in -- the engine resolves an ambiguous pullback and re-enters
EXPANSION in the SAME bar (see vo.observation.regime's module
docstring: "... -> RETRACEMENT -> EXPANSION"). A run of exactly one such
record has start_utc == the very next run's start_utc, so it has no
bars in [start_utc, end_utc) to bound a band with -- build_regime_segments
correctly drops it rather than draw a degenerate zero-width rectangle.
That does not mean the moment should be invisible: it is the single
instant the [VO-H] anticipation lean gets checked against reality.
build_regime_markers() emits a point event for each one instead of a
band -- one bar, one price (that bar's close), one RegimeState it traces
back to (gate G8). Every feed line (band or marker) now starts with an
explicit "BAND"/"MARK" tag rather than relying on field-count alone to
tell them apart -- the field count still differs too, but the tag is
the primary, more robust discriminant on the MQL5 side.

SESSION BOUNDARIES (v3). A session transition is "a clock event, nothing
more" (architecture/vo-time-engine.md §4) -- it carries no canonical
record and plays no role in RegimeEngine (gate G2: the classifier never
sees it). build_session_boundaries walks the same bars against
vo.time.sessions.session_at (the identical lookup
vo.telemetry.regime_report's session breakdown and the live EA runtime's
VOTimeEngine both use, so no two surfaces can silently disagree about
what session an instant is in) and emits one SessionBoundary wherever
consecutive bars' sessions differ. VO_Regime.mq5 draws these as thin
vertical lines so a regime band or marker can be read directly against
the session it fell in on the same chart -- purely an aid to reading the
chart, changing nothing about how a regime is classified.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.market.bar import Bar
from vo.observation.regime import (
    AnticipatedResolution,
    RegimeDirection,
    RegimeState,
    RegimeType,
)
from vo.time.sessions import OFF_SESSION_LABEL, SessionConfig, session_at

FEED_VERSION = 3
FEED_DELIMITER = "|"
_COMMENT_PREFIX = "#"


@dataclass(frozen=True)
class RegimeSegment:
    """One contiguous run of a single regime, ready to draw as one band.

    start_utc/end_utc are resolved UTC instants (end_utc is None while the
    run is still the current, open-ended regime). high/low bound the
    price action across the run's bars -- the vertical extent of the band.
    object_id/confidence/anticipated come from the run's freshest record
    (gate G8 traceability); direction is that record's direction.
    """

    regime: RegimeType
    direction: RegimeDirection | None
    start_utc: datetime
    end_utc: datetime | None
    high: float
    low: float
    confidence: float
    anticipated: AnticipatedResolution | None
    object_id: str
    methodology_version: int
    instrument_key: str
    timeframe_canonical: str


def build_regime_segments(
    states: Sequence[RegimeState],
    bars: Sequence[Bar],
) -> tuple[RegimeSegment, ...]:
    """Collapse an emitted RegimeState log into drawable regime bands.

    Records are ordered by observed_at (stable), then consecutive records
    sharing `regime` are merged into one segment. Each segment's price
    extent is the high/low of the bars whose open_time_utc falls in
    [start_utc, end_utc) -- or [start_utc, last-bar] for the final,
    open-ended run. A segment with no bars in range (possible only for a
    zero-width run where two records share observed_at) is dropped rather
    than drawn as a degenerate band.
    """
    if not states:
        return ()

    ordered = sorted(states, key=lambda s: s.observed_at)

    # Group indices of consecutive same-regime records into runs.
    runs: list[list[RegimeState]] = []
    for state in ordered:
        if runs and runs[-1][-1].regime is state.regime:
            runs[-1].append(state)
        else:
            runs.append([state])

    # Precompute a sorted (open_time_utc, high, low) view for range scans.
    bar_view = sorted(
        ((b.open_time_utc, b.high, b.low) for b in bars),
        key=lambda row: row[0],
    )

    segments: list[RegimeSegment] = []
    for i, run in enumerate(runs):
        start_utc = run[0].observed_at
        end_utc = runs[i + 1][0].observed_at if i + 1 < len(runs) else None
        rep = run[-1]  # freshest, non-superseded record of the run

        highs = [
            h
            for (t, h, _low) in bar_view
            if t >= start_utc and (end_utc is None or t < end_utc)
        ]
        lows = [
            low
            for (t, _h, low) in bar_view
            if t >= start_utc and (end_utc is None or t < end_utc)
        ]
        if not highs or not lows:
            # Zero-width run (two records at one bar) -- nothing to draw.
            continue

        segments.append(
            RegimeSegment(
                regime=rep.regime,
                direction=rep.direction,
                start_utc=start_utc,
                # The final run stays open-ended (end_utc None -> end_epoch 0);
                # the indicator extends that band to the live chart edge, which
                # tracks the current bar more closely than the last fed bar.
                end_utc=end_utc,
                high=max(highs),
                low=min(lows),
                confidence=rep.confidence,
                anticipated=rep.anticipated_resolution,
                object_id=rep.object_id,
                methodology_version=rep.methodology_version,
                instrument_key=rep.instrument_id.key,
                timeframe_canonical=rep.timeframe.canonical,
            )
        )

    return tuple(segments)


@dataclass(frozen=True)
class RegimeMarker:
    """One instantaneous resolution event (RETRACEMENT or REVERSAL) --
    see the module docstring's MARKERS section for why this exists
    alongside RegimeSegment rather than as a zero-width one. Anchored at
    the bar matching the source RegimeState's observed_at; priced at that
    bar's close (always present, unlike a band's high/low which need a
    bar range to bound). object_id/confidence trace back to the real
    RegimeState (gate G8); direction is that record's direction."""

    regime: RegimeType
    direction: RegimeDirection | None
    at_utc: datetime
    price: float
    confidence: float
    object_id: str
    methodology_version: int
    instrument_key: str
    timeframe_canonical: str


def build_regime_markers(
    states: Sequence[RegimeState],
    bars: Sequence[Bar],
) -> tuple[RegimeMarker, ...]:
    """One RegimeMarker per RETRACEMENT/REVERSAL state -- the resolution
    instants build_regime_segments necessarily drops (see its own
    docstring's zero-width-run note). `bars` is used only to look up the
    price at each resolution's bar; a state whose observed_at does not
    match any bar's open_time_utc is skipped rather than given an
    invented price (should not happen -- observed_at is always set from
    a real bar's open_time_utc by RegimeEngine._emit_state)."""
    bar_by_open_time = {b.open_time_utc: b for b in bars}
    markers: list[RegimeMarker] = []
    for state in states:
        if state.regime not in (RegimeType.RETRACEMENT, RegimeType.REVERSAL):
            continue
        bar = bar_by_open_time.get(state.observed_at)
        if bar is None:
            continue
        markers.append(
            RegimeMarker(
                regime=state.regime,
                direction=state.direction,
                at_utc=state.observed_at,
                price=bar.close,
                confidence=state.confidence,
                object_id=state.object_id,
                methodology_version=state.methodology_version,
                instrument_key=state.instrument_id.key,
                timeframe_canonical=state.timeframe.canonical,
            )
        )
    return tuple(markers)


@dataclass(frozen=True)
class SessionBoundary:
    """One session-to-session transition instant -- a pure clock fact
    (architecture/vo-time-engine.md §4: "session transitions are
    temporal events, nothing more"), not a canonical record and not an
    input to RegimeEngine (gate G2). `from_session`/`to_session` are
    session names, or OFF_SESSION_LABEL for a bar outside every
    configured window (e.g. US100's daily ~16:00-18:00 ET gap)."""

    at_utc: datetime
    from_session: str
    to_session: str


def build_session_boundaries(
    bars: Sequence[Bar],
    session_config: SessionConfig,
) -> tuple[SessionBoundary, ...]:
    """One SessionBoundary per bar whose session differs from the bar
    immediately before it. Uses the exact same vo.time.sessions.session_at
    lookup as vo.telemetry.regime_report's session breakdown and the live
    EA runtime's VOTimeEngine -- no second session concept invented here,
    so the chart lines and the report table can never disagree about what
    session an instant belongs to."""
    boundaries: list[SessionBoundary] = []
    previous: str | None = None
    for bar in bars:
        window = session_at(bar.open_time_utc.astimezone(session_config.zone), session_config)
        current = window.name if window is not None else OFF_SESSION_LABEL
        if previous is not None and current != previous:
            boundaries.append(
                SessionBoundary(at_utc=bar.open_time_utc, from_session=previous, to_session=current)
            )
        previous = current
    return tuple(boundaries)


_BAND_TAG = "BAND"
_MARK_TAG = "MARK"
_SESN_TAG = "SESN"


def feed_header(
    segments: Sequence[RegimeSegment],
    markers: Sequence[RegimeMarker] = (),
    boundaries: Sequence[SessionBoundary] = (),
    *,
    generated_utc: datetime,
) -> str:
    """A single comment line the indicator skips -- provenance only."""
    instrument = segments[0].instrument_key if segments else (
        markers[0].instrument_key if markers else "?"
    )
    timeframe = segments[0].timeframe_canonical if segments else (
        markers[0].timeframe_canonical if markers else "?"
    )
    return (
        f"{_COMMENT_PREFIX} VO_REGIME_FEED v{FEED_VERSION} "
        f"instrument={instrument} timeframe={timeframe} "
        f"generated_utc={generated_utc.isoformat()} "
        f"segments={len(segments)} markers={len(markers)} boundaries={len(boundaries)}"
    )


def feed_line(segment: RegimeSegment, *, start_epoch: int, end_epoch: int) -> str:
    """One pipe-delimited band line: a "BAND" tag then nine fields (ten
    total), MQL5-parseable.

    Fields: BAND | object_id | regime | direction | start_epoch |
    end_epoch | high | low | confidence | anticipated. `direction` is
    NONE when absent, `anticipated` is empty when absent, `end_epoch` is
    0 for the open-ended final band. Broker-server epoch seconds are
    supplied by the caller; this module never converts time itself.
    """
    if FEED_DELIMITER in segment.object_id:
        raise ValueError(
            f"object_id {segment.object_id!r} contains the feed delimiter "
            f"{FEED_DELIMITER!r} -- would corrupt the line"
        )
    direction = segment.direction.name if segment.direction is not None else "NONE"
    anticipated = segment.anticipated.name if segment.anticipated is not None else ""
    fields = [
        _BAND_TAG,
        segment.object_id,
        segment.regime.name,
        direction,
        str(start_epoch),
        str(end_epoch),
        repr(segment.high),
        repr(segment.low),
        repr(segment.confidence),
        anticipated,
    ]
    return FEED_DELIMITER.join(fields)


def marker_line(marker: RegimeMarker, *, at_epoch: int) -> str:
    """One pipe-delimited marker line: a "MARK" tag then six fields
    (seven total) -- deliberately fewer fields than a band line (and a
    different tag), so a marker can never be mistaken for one even by a
    parser that only counts fields.

    Fields: MARK | object_id | regime | direction | at_epoch | price |
    confidence. `regime` is always RETRACEMENT or REVERSAL (see
    build_regime_markers).
    """
    if FEED_DELIMITER in marker.object_id:
        raise ValueError(
            f"object_id {marker.object_id!r} contains the feed delimiter "
            f"{FEED_DELIMITER!r} -- would corrupt the line"
        )
    direction = marker.direction.name if marker.direction is not None else "NONE"
    fields = [
        _MARK_TAG,
        marker.object_id,
        marker.regime.name,
        direction,
        str(at_epoch),
        repr(marker.price),
        repr(marker.confidence),
    ]
    return FEED_DELIMITER.join(fields)


def session_boundary_line(boundary: SessionBoundary, *, at_epoch: int) -> str:
    """One pipe-delimited session-boundary line: a "SESN" tag then three
    fields (four total) -- fewer fields than either BAND or MARK, so a
    parser counting fields alone still cannot confuse it with either.

    Fields: SESN | at_epoch | from_session | to_session. Carries no
    object_id: a session transition traces back to no canonical record
    (it is a clock fact, not an engine output) -- vo.time.sessions is
    the one source of truth for it, same as the report's session
    breakdown uses.
    """
    fields = [_SESN_TAG, str(at_epoch), boundary.from_session, boundary.to_session]
    return FEED_DELIMITER.join(fields)


def render_feed_lines(
    segments: Sequence[RegimeSegment],
    markers: Sequence[RegimeMarker] = (),
    boundaries: Sequence[SessionBoundary] = (),
    *,
    epoch_of: Callable[[datetime], int],
    generated_utc: datetime,
) -> list[str]:
    """Full feed content: one provenance header, then one line per band,
    marker, or session boundary, in chronological order (interleaved by
    their own time so the file reads sensibly top to bottom).

    `epoch_of` maps a resolved UTC instant to the broker-server epoch
    seconds MT5 draws in (the identity `int(dt.timestamp())` is fine for
    tests that don't care about broker offset). The open-ended final band
    emits end_epoch 0.
    """
    lines = [feed_header(segments, markers, boundaries, generated_utc=generated_utc)]

    events: list[tuple[datetime, str]] = []
    for segment in segments:
        start_epoch = epoch_of(segment.start_utc)
        end_epoch = epoch_of(segment.end_utc) if segment.end_utc is not None else 0
        events.append(
            (segment.start_utc, feed_line(segment, start_epoch=start_epoch, end_epoch=end_epoch))
        )
    for marker in markers:
        events.append((marker.at_utc, marker_line(marker, at_epoch=epoch_of(marker.at_utc))))
    for boundary in boundaries:
        events.append(
            (boundary.at_utc, session_boundary_line(boundary, at_epoch=epoch_of(boundary.at_utc)))
        )
    events.sort(key=lambda pair: pair[0])

    lines.extend(line for _when, line in events)
    return lines
