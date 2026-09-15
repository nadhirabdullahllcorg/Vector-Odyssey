"""
Infer a broker's timezone rules from probe observations.

VO_BrokerTimeProbe.mq5 observes; this module concludes. Keeping the conclusion
on this side means it can be tested against synthetic brokers whose answers are
known, rather than only against the one live terminal.

THE DISCRIMINATOR.
A CFD week opens at a fixed New York wall-clock time. Expressed as server wall
time that instant is:

    server_open = T_ny + (S - N)

where S is the server's UTC offset and N is New York's. Each hypothesis leaves a
different fingerprint on ``S - N`` across a year:

    server follows US DST   S and N move on the same dates, so S - N never
                            changes.  ->  FLAT all year

    server follows EU DST   they move on different dates, so S - N differs only
                            during the gaps: ~3 weeks each March, ~1 week each
                            Oct/Nov.  ->  BRIEF one-hour excursions

    server offset is fixed  only N moves, so S - N is one value all winter and
                            another all summer.  ->  TWO LONG PLATEAUS switching
                            on the US dates

Those three shapes are distinguishable, which is the whole point. Anything that
does not match one of them returns UNKNOWN. A timezone rule guessed wrong is an
hour of silent error in every session label downstream, so declining to answer
is the correct answer when the evidence does not support one.

This module contains no market interpretation and no trading logic.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

# A weekly open drifts by a few minutes depending on when the first tick lands.
# A DST shift is 60. This tolerance separates them comfortably.
JITTER_TOLERANCE_MINUTES = 15
SHIFT_MINUTES = 60

# How close a shift must fall to a calendar boundary to count as explained by it.
BOUNDARY_TOLERANCE_DAYS = 10


class DstCalendar(Enum):
    US = "US"
    EU = "EU"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


class Confidence(Enum):
    OBSERVED = "OBSERVED"        # a transition was seen in the data
    PROVISIONAL = "PROVISIONAL"  # consistent, but no transition covered
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class WeeklyOpen:
    server_time: datetime  # server wall clock, no timezone claim
    minute_of_day: int


@dataclass(frozen=True)
class ClockReading:
    server_name: str
    time_gmt: datetime
    offset_tradeserver_minus_gmt_seconds: int
    offset_timecurrent_minus_gmt_seconds: int

    @property
    def readings_agree(self) -> bool:
        """
        TimeTradeServer() and TimeCurrent() should imply the same offset.

        They are computed differently — one advances the last known server time
        with local elapsed time, the other comes from tick data — so a
        disagreement means one of them has drifted and neither should be
        trusted as an authority.
        """
        delta = abs(
            self.offset_tradeserver_minus_gmt_seconds
            - self.offset_timecurrent_minus_gmt_seconds
        )
        return delta <= 120


@dataclass(frozen=True)
class ProbeObservations:
    clock: ClockReading | None = None
    weekly_opens: tuple[WeeklyOpen, ...] = ()
    daily_open_minutes: tuple[int, ...] = ()
    history_chunks_failed: int = 0


@dataclass(frozen=True)
class ProbeResult:
    calendar: DstCalendar
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)
    shift_dates: list[date] = field(default_factory=list)
    measured_offset_hours: float | None = None
    implied_daily_close_ny: str | None = None

    @property
    def is_conclusive(self) -> bool:
        return (
            self.calendar is not DstCalendar.UNKNOWN
            and self.confidence is Confidence.OBSERVED
        )


# ── DST boundaries ─────────────────────────────────────────────────────────


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 6:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def us_dst_bounds(year: int) -> tuple[date, date]:
    """Second Sunday in March to first Sunday in November."""
    return _nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)


def eu_dst_bounds(year: int) -> tuple[date, date]:
    """Last Sunday in March to last Sunday in October."""
    return _last_sunday(year, 3), _last_sunday(year, 10)


def gap_windows(year: int) -> tuple[tuple[date, date], tuple[date, date]]:
    """
    The stretches where the US and EU calendars disagree.

    A server on EU rules shows a one-hour excursion exactly here and nowhere
    else. That narrow, twice-yearly signature is what identifies it.
    """
    us_start, us_end = us_dst_bounds(year)
    eu_start, eu_end = eu_dst_bounds(year)
    return (us_start, eu_start), (eu_end, us_end)


# ── parsing ────────────────────────────────────────────────────────────────


def _parse_wall(value: str) -> datetime:
    return datetime.strptime(value.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")


def parse_probe_output(lines: list[str]) -> ProbeObservations:
    """Read the probe's JSON lines. Anything unrecognised is ignored, not guessed at."""
    clock: ClockReading | None = None
    opens: list[WeeklyOpen] = []
    daily: list[int] = []
    failed = 0

    for raw in lines:
        stripped = raw.strip()

        if not stripped.startswith("{"):
            continue

        try:
            row: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        kind = row.get("record_type")

        if kind == "probe_clock":
            clock = ClockReading(
                server_name=str(row.get("server_name", "")),
                time_gmt=_parse_wall(str(row["time_gmt"])),
                offset_tradeserver_minus_gmt_seconds=int(
                    row["offset_tradeserver_minus_gmt_seconds"]
                ),
                offset_timecurrent_minus_gmt_seconds=int(
                    row["offset_timecurrent_minus_gmt_seconds"]
                ),
            )
        elif kind == "probe_weekly_open":
            opens.append(
                WeeklyOpen(
                    server_time=_parse_wall(str(row["server_time"])),
                    minute_of_day=int(row["minute_of_day"]),
                )
            )
        elif kind == "probe_daily_open":
            daily.append(int(row["minute_of_day"]))
        elif kind == "probe_summary":
            failed = int(row.get("history_chunks_failed", 0))

    opens.sort(key=lambda o: o.server_time)

    return ProbeObservations(
        clock=clock,
        weekly_opens=tuple(opens),
        daily_open_minutes=tuple(daily),
        history_chunks_failed=failed,
    )


# ── classification ─────────────────────────────────────────────────────────


def _baseline_minute(opens: tuple[WeeklyOpen, ...]) -> int:
    """The most common opening minute, rounded into buckets to absorb jitter."""
    buckets = Counter(o.minute_of_day // JITTER_TOLERANCE_MINUTES for o in opens)
    modal_bucket, _ = buckets.most_common(1)[0]

    in_bucket = [
        o.minute_of_day
        for o in opens
        if o.minute_of_day // JITTER_TOLERANCE_MINUTES == modal_bucket
    ]
    return round(sum(in_bucket) / len(in_bucket))


def _circular_delta(a: int, b: int) -> int:
    """
    Distance between two times of day, the short way round the clock.

    A shift across midnight reads as 00:00 versus 23:00 — 1380 minutes apart
    arithmetically, 60 apart in reality. Plain subtraction gets this wrong, and
    a server whose week opens near midnight is the common case, not the
    exotic one.
    """
    delta = abs(a - b)
    return min(delta, 1440 - delta)


def _shifted(opens: tuple[WeeklyOpen, ...], baseline: int) -> list[WeeklyOpen]:
    return [
        o
        for o in opens
        if _circular_delta(o.minute_of_day, baseline)
        >= SHIFT_MINUTES - JITTER_TOLERANCE_MINUTES
    ]


def _count_runs(opens: tuple[WeeklyOpen, ...], baseline: int) -> int:
    """
    How many times the series switches between shifted and unshifted.

    Fraction alone cannot tell a season-long plateau from random noise: a
    fixed-offset server and a feed that alternates every other week both spend
    about half their weeks shifted. Structure is what separates them. A fixed
    offset over a year produces two or three runs; noise produces dozens.
    """
    runs = 0
    previous: bool | None = None

    for o in opens:
        state = _circular_delta(o.minute_of_day, baseline) >= (
            SHIFT_MINUTES - JITTER_TOLERANCE_MINUTES
        )
        if previous is not None and state != previous:
            runs += 1
        previous = state

    return runs


# A fixed-offset server switches twice a year. Allow headroom for a longer
# scan and the odd missing week, but not for noise.
MAX_PLATEAU_RUNS = 6


def _within(day: date, window: tuple[date, date]) -> bool:
    start, end = window
    return (
        start - timedelta(days=BOUNDARY_TOLERANCE_DAYS)
        <= day
        <= end + timedelta(days=BOUNDARY_TOLERANCE_DAYS)
    )


def classify(observations: ProbeObservations) -> ProbeResult:
    """
    Name the broker's calendar, or decline to.

    Declining is a real outcome. An hour of silent error in every session label
    is worse than a profile marked UNKNOWN.
    """
    opens = observations.weekly_opens
    evidence: list[str] = []

    if observations.history_chunks_failed:
        evidence.append(
            f"{observations.history_chunks_failed} history chunk(s) returned no "
            f"bars — coverage is incomplete"
        )

    if observations.clock and not observations.clock.readings_agree:
        evidence.append(
            "TimeTradeServer() and TimeCurrent() imply different offsets; "
            "neither is a reliable authority"
        )

    measured = None
    if observations.clock:
        measured = observations.clock.offset_timecurrent_minus_gmt_seconds / 3600.0

    if len(opens) < 30:
        evidence.append(
            f"only {len(opens)} weekly opens observed; a full DST cycle needs "
            f"at least ~55 weeks of history"
        )
        return ProbeResult(
            calendar=DstCalendar.UNKNOWN,
            confidence=Confidence.UNKNOWN,
            evidence=evidence,
            measured_offset_hours=measured,
            implied_daily_close_ny=_daily_close_ny(observations, measured),
        )

    baseline = _baseline_minute(opens)
    shifted = _shifted(opens, baseline)

    evidence.append(
        f"{len(opens)} weekly opens, baseline {baseline // 60:02d}:"
        f"{baseline % 60:02d} server time, {len(shifted)} shifted by ~1h"
    )

    span_days = (opens[-1].server_time - opens[0].server_time).days
    covers_a_year = span_days >= 330

    # ── flat all year: the server moves with New York ──
    if not shifted:
        return ProbeResult(
            calendar=DstCalendar.US,
            confidence=Confidence.OBSERVED if covers_a_year else Confidence.PROVISIONAL,
            evidence=[
                *evidence,
                "weekly open never moves in server time, so the server's DST "
                "changes coincide with New York's",
            ],
            measured_offset_hours=measured,
            implied_daily_close_ny=_daily_close_ny(observations, measured),
        )

    shift_days = [o.server_time.date() for o in shifted]
    years = sorted({d.year for d in shift_days})

    all_gaps: list[tuple[date, date]] = []
    for y in years:
        all_gaps.extend(gap_windows(y))

    in_gap = [d for d in shift_days if any(_within(d, w) for w in all_gaps)]

    # ── brief excursions confined to the US/EU gaps: EU rules ──
    if len(in_gap) == len(shift_days):
        return ProbeResult(
            calendar=DstCalendar.EU,
            confidence=Confidence.OBSERVED,
            evidence=[
                *evidence,
                f"every shifted week ({len(shift_days)}) falls in a window where "
                f"the US and EU calendars disagree — the EU signature",
            ],
            shift_dates=shift_days,
            measured_offset_hours=measured,
            implied_daily_close_ny=_daily_close_ny(observations, measured),
        )

    # ── long plateaus: the server does not move, New York does ──
    shifted_fraction = len(shifted) / len(opens)
    runs = _count_runs(opens, baseline)

    evidence.append(f"{runs} transition(s) between shifted and unshifted")

    if 0.3 <= shifted_fraction <= 0.7 and runs <= MAX_PLATEAU_RUNS:
        return ProbeResult(
            calendar=DstCalendar.NONE,
            confidence=Confidence.OBSERVED,
            evidence=[
                *evidence,
                f"{shifted_fraction:.0%} of weeks sit at the shifted value across "
                f"{runs} contiguous run(s) — a season-long plateau, so the server "
                f"offset is fixed and only New York moves",
            ],
            shift_dates=shift_days,
            measured_offset_hours=measured,
            implied_daily_close_ny=_daily_close_ny(observations, measured),
        )

    return ProbeResult(
        calendar=DstCalendar.UNKNOWN,
        confidence=Confidence.UNKNOWN,
        evidence=[
            *evidence,
            f"{len(shifted)} shifted weeks across {runs} transition(s) match no "
            f"known pattern: not flat, not confined to the US/EU gaps, not a "
            f"contiguous seasonal plateau",
        ],
        shift_dates=shift_days,
        measured_offset_hours=measured,
        implied_daily_close_ny=_daily_close_ny(observations, measured),
    )


def _daily_close_ny(
    observations: ProbeObservations, measured_offset_hours: float | None
) -> str | None:
    """
    What New York time the daily bar boundary falls on.

    A cross-check on the offset pair rather than a conclusion in its own right:
    +2/+3 puts it at 17:00 NY (the common forex day), +3/+4 at 16:00 NY (the
    index cash close).
    """
    if not observations.daily_open_minutes or measured_offset_hours is None:
        return None

    modal, _ = Counter(observations.daily_open_minutes).most_common(1)[0]

    utc_minutes = modal - round(measured_offset_hours * 60)

    # New York in summer is UTC-4; the probe's clock reading is a single
    # instant, so this is only ever an approximate cross-check.
    ny_minutes = (utc_minutes - 4 * 60) % (24 * 60)

    return f"{ny_minutes // 60:02d}:{ny_minutes % 60:02d}"
