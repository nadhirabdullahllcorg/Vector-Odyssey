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

FEED_VERSION = 1
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


def feed_header(
    segments: Sequence[RegimeSegment],
    *,
    generated_utc: datetime,
) -> str:
    """A single comment line the indicator skips -- provenance only."""
    instrument = segments[0].instrument_key if segments else "?"
    timeframe = segments[0].timeframe_canonical if segments else "?"
    return (
        f"{_COMMENT_PREFIX} VO_REGIME_FEED v{FEED_VERSION} "
        f"instrument={instrument} timeframe={timeframe} "
        f"generated_utc={generated_utc.isoformat()} segments={len(segments)}"
    )


def feed_line(segment: RegimeSegment, *, start_epoch: int, end_epoch: int) -> str:
    """One pipe-delimited band line: nine fields, MQL5-parseable.

    Fields: object_id | regime | direction | start_epoch | end_epoch |
    high | low | confidence | anticipated. `direction` is NONE when
    absent, `anticipated` is empty when absent, `end_epoch` is 0 for the
    open-ended final band. Broker-server epoch seconds are supplied by
    the caller; this module never converts time itself.
    """
    if FEED_DELIMITER in segment.object_id:
        raise ValueError(
            f"object_id {segment.object_id!r} contains the feed delimiter "
            f"{FEED_DELIMITER!r} -- would corrupt the line"
        )
    direction = segment.direction.name if segment.direction is not None else "NONE"
    anticipated = segment.anticipated.name if segment.anticipated is not None else ""
    fields = [
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


def render_feed_lines(
    segments: Sequence[RegimeSegment],
    *,
    epoch_of: Callable[[datetime], int],
    generated_utc: datetime,
) -> list[str]:
    """Full feed content: one provenance header then one line per band.

    `epoch_of` maps a resolved UTC instant to the broker-server epoch
    seconds MT5 draws in (the identity `int(dt.timestamp())` is fine for
    tests that don't care about broker offset). The open-ended final band
    emits end_epoch 0.
    """
    lines = [feed_header(segments, generated_utc=generated_utc)]
    for segment in segments:
        start_epoch = epoch_of(segment.start_utc)
        end_epoch = epoch_of(segment.end_utc) if segment.end_utc is not None else 0
        lines.append(feed_line(segment, start_epoch=start_epoch, end_epoch=end_epoch))
    return lines
