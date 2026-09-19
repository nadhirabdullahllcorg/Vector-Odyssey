"""
Bar aggregation -- M1 into M5 / M15 / M30 / H1 / H4 / D1 (§13b, deliverable A).

Why this exists: everything above the candle layer has run on the native
M1 stream since Phase 5. ICT market structure is fractal -- a daily bias,
intermediate swings, internal swings -- and none of it can be observed
without higher-timeframe bars. This module makes them, and only makes
them; no swing, regime, or level logic lives here.

NO LOOKAHEAD, BY CONSTRUCTION (gate G3). An aggregated bar is emitted only
once a source bar belonging to a LATER bucket has been seen -- that is the
only proof, from bars alone, that every constituent of the bucket has
closed. The trailing, still-open bucket is never emitted as a bar; it is
exposed separately as `Aggregator.partial` (a plain snapshot, not a Bar)
so a live process can look at it without ever mistaking it for a closed
bar. tests/unit/test_aggregate.py drives this through
vo.core.replay.assert_no_lookahead like every other engine.

ALIGNMENT. Two modes, chosen by whether `day_opens`/`zone` are given:

  * clock alignment (default): bucket start = floor(open_time_utc /
    target.seconds) in UTC. Right for M5..H1 (every whole-hour timezone
    agrees on them) and used when no session model is available.
  * trading-day alignment: bucket boundaries are anchored at the trading
    day's open (`day_opens` in `zone`, e.g. 18:00 America/New_York) and
    stepped by the target's length inside that day -- so H4 buckets are
    18:00, 22:00, 02:00, 06:00, 10:00, 14:00 ET, and D1 IS the trading day
    (the CME trade-date convention vo.time.calendars.trading_day_of
    already implements; the anchor rule here is the same rule stated as an
    instant rather than a date, and a test pins the two against each
    other). This is the mode ObservationPipeline/backtests should use.

An aggregated bar's open_time_utc is the BUCKET START, not the first
constituent's open time -- the MT5 convention, and what makes two runs
with different gaps comparable. A bucket with missing minutes (closures,
feed gaps) is still one bar over the bars that exist; `constituents` on
the partial snapshot and the summed tick_volume make the gap visible.

Provenance: an aggregated bar is derived, so `provenance` is None and
`spread`/tick-provenance fields are None -- there is no single source
observation to cite. `quality` is the worst constituent quality (VALID <
SUSPECT < QUARANTINED), so a suspect minute taints the hour it sits in
rather than being averaged away.

Layer 1 (vo.market): takes `zone`/`day_opens` as plain stdlib values
rather than importing vo.time (layer 2); vo.time's own callers pass
SessionConfig.zone / .trading_day_opens straight through.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .bar import Bar, DataQuality
from .identity import InstrumentId
from .timeframe import Timeframe

SUPPORTED_TARGETS: frozenset[Timeframe] = frozenset(
    {Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1}
)

_QUALITY_RANK = {DataQuality.VALID: 0, DataQuality.SUSPECT: 1, DataQuality.QUARANTINED: 2}


class AggregationError(ValueError):
    pass


@dataclass(frozen=True)
class PartialBucket:
    """The still-open bucket: what the aggregator knows so far. Deliberately
    NOT a Bar -- nothing downstream can append it to a BarSequence or feed
    it to an engine by accident (finding: a stale open-ended band drawn as
    a live claim, 2026-09-19)."""

    bucket_start_utc: datetime
    bucket_end_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    real_volume: int
    constituents: int
    last_constituent_open_utc: datetime


def bucket_start(
    open_time_utc: datetime,
    target: Timeframe,
    *,
    zone: ZoneInfo | None = None,
    day_opens: time | None = None,
) -> datetime:
    """The UTC instant the `target` bucket containing `open_time_utc` starts.
    See the module docstring's ALIGNMENT section."""
    seconds = target.seconds
    if target not in SUPPORTED_TARGETS or seconds is None:
        raise AggregationError(f"unsupported aggregation target {target.canonical}")
    if open_time_utc.tzinfo is None:
        raise AggregationError("open_time_utc must be timezone-aware")

    if zone is None or day_opens is None:
        if target is Timeframe.D1:
            raise AggregationError(
                "D1 aggregation needs zone + day_opens (a trading day is not a UTC calendar day)"
            )
        epoch = int(open_time_utc.timestamp())
        return datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)

    local = open_time_utc.astimezone(zone)
    # The most recent trading-day open at or before this instant -- the same
    # rule as vo.time.calendars.trading_day_of, expressed as an instant.
    day_open_local = datetime.combine(local.date(), day_opens, tzinfo=zone)
    if local < day_open_local:
        day_open_local = datetime.combine(
            (local - timedelta(days=1)).date(), day_opens, tzinfo=zone
        )
    if target is Timeframe.D1:
        return day_open_local.astimezone(UTC)
    # Step inside the trading day, in wall-clock seconds of the zone -- a DST
    # change mid-day shifts the later buckets by an hour of UTC, exactly as
    # the session windows themselves do (vo.time.sessions is wall-clock).
    elapsed = (local - day_open_local).total_seconds()
    steps = int(elapsed // seconds)
    return (day_open_local + timedelta(seconds=steps * seconds)).astimezone(UTC)


class Aggregator:
    """Incremental M1 -> `target` aggregator. push() one source bar at a time,
    in order; a completed higher-timeframe Bar comes back only when the
    pushed bar opens a LATER bucket (the no-lookahead rule above)."""

    def __init__(
        self,
        target: Timeframe,
        *,
        zone: ZoneInfo | None = None,
        day_opens: time | None = None,
    ) -> None:
        if target not in SUPPORTED_TARGETS:
            raise AggregationError(f"unsupported aggregation target {target.canonical}")
        self._target = target
        self._zone = zone
        self._day_opens = day_opens
        self._partial: PartialBucket | None = None
        self._worst_quality = DataQuality.VALID
        self._instrument: InstrumentId | None = None
        self._source_timeframe: Timeframe | None = None

    @property
    def target(self) -> Timeframe:
        return self._target

    @property
    def partial(self) -> PartialBucket | None:
        """The still-open bucket, or None before the first push."""
        return self._partial

    def push(self, bar: Bar) -> Bar | None:
        source_seconds = bar.timeframe.seconds
        target_seconds = self._target.seconds
        assert target_seconds is not None  # guarded in __init__
        if source_seconds is None or source_seconds >= target_seconds:
            raise AggregationError(
                f"cannot aggregate {bar.timeframe.canonical} into {self._target.canonical}"
            )
        if self._source_timeframe is None:
            self._source_timeframe = bar.timeframe
            self._instrument = bar.instrument_id
        elif bar.timeframe is not self._source_timeframe or bar.instrument_id != self._instrument:
            raise AggregationError("all source bars must share one instrument and timeframe")

        start = bucket_start(
            bar.open_time_utc, self._target, zone=self._zone, day_opens=self._day_opens
        )
        end = self._bucket_end(start)
        partial = self._partial
        if partial is not None and bar.open_time_utc <= partial.last_constituent_open_utc:
            raise AggregationError(
                f"source bars must be strictly increasing; {bar.open_time_utc.isoformat()} "
                f"is not after {partial.last_constituent_open_utc.isoformat()}"
            )

        completed: Bar | None = None
        if partial is not None and start != partial.bucket_start_utc:
            completed = self._close(partial)
            partial = None

        if partial is None:
            self._worst_quality = bar.quality
            self._partial = PartialBucket(
                bucket_start_utc=start,
                bucket_end_utc=end,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                tick_volume=bar.tick_volume,
                real_volume=bar.real_volume,
                constituents=1,
                last_constituent_open_utc=bar.open_time_utc,
            )
        else:
            if _QUALITY_RANK[bar.quality] > _QUALITY_RANK[self._worst_quality]:
                self._worst_quality = bar.quality
            self._partial = PartialBucket(
                bucket_start_utc=partial.bucket_start_utc,
                bucket_end_utc=partial.bucket_end_utc,
                open=partial.open,
                high=max(partial.high, bar.high),
                low=min(partial.low, bar.low),
                close=bar.close,
                tick_volume=partial.tick_volume + bar.tick_volume,
                real_volume=partial.real_volume + bar.real_volume,
                constituents=partial.constituents + 1,
                last_constituent_open_utc=bar.open_time_utc,
            )
        return completed

    def _bucket_end(self, start: datetime) -> datetime:
        if self._target is Timeframe.D1 and self._zone is not None and self._day_opens is not None:
            local = start.astimezone(self._zone)
            return datetime.combine(
                (local + timedelta(days=1)).date(), self._day_opens, tzinfo=self._zone
            ).astimezone(UTC)
        seconds = self._target.seconds
        assert seconds is not None
        if self._zone is not None:
            local = start.astimezone(self._zone)
            return (local + timedelta(seconds=seconds)).astimezone(UTC)
        return start + timedelta(seconds=seconds)

    def _close(self, partial: PartialBucket) -> Bar:
        assert self._instrument is not None
        return Bar(
            instrument_id=self._instrument,
            timeframe=self._target,
            open_time_utc=partial.bucket_start_utc,
            open=partial.open,
            high=partial.high,
            low=partial.low,
            close=partial.close,
            tick_volume=partial.tick_volume,
            real_volume=partial.real_volume,
            spread=None,
            quality=self._worst_quality,
            quality_reason=(
                None
                if self._worst_quality is DataQuality.VALID
                else f"worst constituent quality {self._worst_quality.name}"
            ),
            provenance=None,
        )


def aggregate_bars(
    bars: Iterable[Bar],
    target: Timeframe,
    *,
    zone: ZoneInfo | None = None,
    day_opens: time | None = None,
) -> tuple[Bar, ...]:
    """Batch form: every COMPLETED `target` bar in `bars`, in order. The
    trailing open bucket is not returned (use Aggregator.partial for it)."""
    aggregator = Aggregator(target, zone=zone, day_opens=day_opens)
    out: list[Bar] = []
    for bar in bars:
        completed = aggregator.push(bar)
        if completed is not None:
            out.append(completed)
    return tuple(out)


def aggregate_all(
    bars: Sequence[Bar],
    targets: Iterable[Timeframe],
    *,
    zone: ZoneInfo | None = None,
    day_opens: time | None = None,
) -> dict[Timeframe, tuple[Bar, ...]]:
    """Several targets from one pass over the source bars."""
    return {t: aggregate_bars(bars, t, zone=zone, day_opens=day_opens) for t in targets}
