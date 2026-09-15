"""
The broker time classifier, tested against brokers whose answers are known.

The live terminal can only ever show one broker. These synthetic ones cover the
three real hypotheses plus the cases where the honest answer is "I cannot tell",
which is the outcome that matters most — a timezone rule guessed wrong is an
hour of silent error in every session label downstream.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vo.time.probe import (
    Confidence,
    DstCalendar,
    ProbeObservations,
    WeeklyOpen,
    classify,
    eu_dst_bounds,
    gap_windows,
    parse_probe_output,
    us_dst_bounds,
)

NY = ZoneInfo("America/New_York")

# A CFD week opens at this New York wall-clock time.
WEEK_OPEN_NY_HOUR = 17


def _server_offset_hours(day, calendar: str) -> int:
    """Standard +2, summer +3, on whichever calendar the server keeps."""
    if calendar == "US":
        start, end = us_dst_bounds(day.year)
    elif calendar == "EU":
        start, end = eu_dst_bounds(day.year)
    else:
        return 3  # fixed, year-round

    return 3 if start <= day < end else 2


def _weekly_opens(calendar: str, weeks: int = 70, start_year: int = 2025) -> tuple[WeeklyOpen, ...]:
    """
    Generate what the probe would observe from a broker on this calendar.

    Anchored to a real New York instant and converted through real DST rules,
    so the fixture is arithmetic rather than assumption.
    """
    sunday = datetime(start_year, 1, 5)  # a Sunday
    out: list[WeeklyOpen] = []

    for _ in range(weeks):
        open_ny = sunday.replace(hour=WEEK_OPEN_NY_HOUR, tzinfo=NY)
        open_utc = open_ny.astimezone(UTC)

        offset = _server_offset_hours(sunday.date(), calendar)
        server_wall = open_utc.replace(tzinfo=None) + timedelta(hours=offset)

        out.append(
            WeeklyOpen(
                server_time=server_wall,
                minute_of_day=server_wall.hour * 60 + server_wall.minute,
            )
        )
        sunday += timedelta(weeks=1)

    return tuple(out)


def _observations(calendar: str, **kw) -> ProbeObservations:
    return ProbeObservations(weekly_opens=_weekly_opens(calendar, **kw))


# ── the three real hypotheses ──────────────────────────────────────────────


def test_server_on_us_rules_shows_no_shift_at_all() -> None:
    """
    Server and New York change on the same dates, so their difference never
    moves and the weekly open sits at one server time all year.
    """
    result = classify(_observations("US"))

    assert result.calendar is DstCalendar.US
    assert result.confidence is Confidence.OBSERVED
    assert result.is_conclusive


def test_server_on_eu_rules_shows_brief_excursions_in_the_gap_weeks() -> None:
    """
    The distinctive signature: an hour out only while the two calendars
    disagree — about three weeks each March and one each Oct/Nov.
    """
    result = classify(_observations("EU"))

    assert result.calendar is DstCalendar.EU
    assert result.confidence is Confidence.OBSERVED

    # Every shifted week must land in a gap window, which is what makes it EU
    # rather than merely "not flat".
    for day in result.shift_dates:
        windows = gap_windows(day.year)
        assert any(
            start - timedelta(days=10) <= day <= end + timedelta(days=10)
            for start, end in windows
        ), f"{day} is not in a US/EU gap window"


def test_fixed_offset_server_shows_two_season_long_plateaus() -> None:
    """Only New York moves, so roughly half the year sits at each value."""
    result = classify(_observations("NONE"))

    assert result.calendar is DstCalendar.NONE
    assert result.confidence is Confidence.OBSERVED


@pytest.mark.parametrize(
    ("calendar", "expected"),
    [("US", DstCalendar.US), ("EU", DstCalendar.EU), ("NONE", DstCalendar.NONE)],
)
def test_each_hypothesis_is_identified_uniquely(calendar: str, expected: DstCalendar) -> None:
    """No two hypotheses may produce the same verdict, or the probe proves nothing."""
    assert classify(_observations(calendar)).calendar is expected


# ── midnight, the case plain subtraction gets wrong ────────────────────────


def test_a_shift_across_midnight_is_measured_the_short_way() -> None:
    """
    A week opening at 00:00 and one at 23:00 are an hour apart, not 23. Servers
    whose week opens near midnight are the common case — +2/+3 with a 17:00 NY
    open lands exactly there — so getting this wrong would misclassify the most
    likely broker of all.
    """
    opens = tuple(
        WeeklyOpen(server_time=datetime(2026, 1, 4) + timedelta(weeks=i), minute_of_day=m)
        for i, m in enumerate([0] * 50 + [1380] * 3 + [0] * 7)
    )

    result = classify(ProbeObservations(weekly_opens=opens))

    # 3 shifted weeks out of 60 — brief, not a plateau. It must not read as
    # "flat" and must not read as "fixed offset".
    assert result.calendar is not DstCalendar.US
    assert result.calendar is not DstCalendar.NONE


# ── declining to answer ────────────────────────────────────────────────────


def test_too_little_history_returns_unknown_not_a_guess() -> None:
    result = classify(_observations("US", weeks=12))

    assert result.calendar is DstCalendar.UNKNOWN
    assert result.confidence is Confidence.UNKNOWN
    assert any("weekly opens observed" in e for e in result.evidence)


def test_incoherent_data_returns_unknown() -> None:
    """Shifts scattered at random match no calendar, and are reported as such."""
    opens = tuple(
        WeeklyOpen(
            server_time=datetime(2026, 1, 4) + timedelta(weeks=i),
            minute_of_day=0 if i % 3 else 60,
        )
        for i in range(60)
    )

    result = classify(ProbeObservations(weekly_opens=opens))

    assert result.calendar is DstCalendar.UNKNOWN


def test_incomplete_history_is_reported_in_the_evidence() -> None:
    observations = ProbeObservations(
        weekly_opens=_weekly_opens("US"),
        history_chunks_failed=3,
    )

    result = classify(observations)

    assert any("history chunk" in e for e in result.evidence)


def test_disagreeing_clock_readings_are_flagged() -> None:
    """
    TimeTradeServer() and TimeCurrent() imply the same offset when healthy.
    When they do not, neither is an authority and the evidence says so.
    """
    lines = [
        json.dumps(
            {
                "record_type": "probe_clock",
                "server_name": "1xTrade-Server",
                "time_gmt": "2026-09-15T12:00:00Z",
                "offset_tradeserver_minus_gmt_seconds": 10800,
                "offset_timecurrent_minus_gmt_seconds": 14400,  # an hour apart
            }
        )
    ]

    observations = parse_probe_output(lines)

    assert observations.clock is not None
    assert observations.clock.readings_agree is False

    result = classify(
        ProbeObservations(clock=observations.clock, weekly_opens=_weekly_opens("US"))
    )

    assert any("reliable authority" in e for e in result.evidence)


# ── parsing ────────────────────────────────────────────────────────────────


def test_parses_the_probe_output_format() -> None:
    lines = [
        "VO BROKER TIME PROBE COMPLETE. 61 weekly opens.",  # noise, ignored
        json.dumps(
            {
                "record_type": "probe_clock",
                "server_name": "1xTrade-Server",
                "time_gmt": "2026-09-15T12:00:00Z",
                "offset_tradeserver_minus_gmt_seconds": 10800,
                "offset_timecurrent_minus_gmt_seconds": 10800,
            }
        ),
        json.dumps(
            {
                "record_type": "probe_weekly_open",
                "server_time": "2026-09-14T00:00:00",
                "minute_of_day": 0,
                "day_of_week": 1,
                "gap_seconds": 176400,
                "prev_bar_server_time": "2026-09-12T00:00:00",
            }
        ),
        json.dumps({"record_type": "probe_daily_open", "minute_of_day": 0}),
        json.dumps({"record_type": "probe_summary", "history_chunks_failed": 0}),
        "not json at all",
    ]

    observations = parse_probe_output(lines)

    assert observations.clock is not None
    assert observations.clock.server_name == "1xTrade-Server"
    assert observations.clock.readings_agree is True
    assert len(observations.weekly_opens) == 1
    assert observations.weekly_opens[0].minute_of_day == 0
    assert observations.daily_open_minutes == (0,)
    assert observations.history_chunks_failed == 0


def test_malformed_lines_are_skipped_not_fatal() -> None:
    lines = ['{"record_type":"probe_weekly_open", BROKEN', "{}", ""]

    observations = parse_probe_output(lines)

    assert observations.weekly_opens == ()
    assert observations.clock is None


# ── the 2026 boundary dates, stated explicitly ─────────────────────────────


def test_2026_dst_boundaries() -> None:
    """
    These are the dates the whole method turns on, so they are asserted rather
    than assumed. US and EU disagree for a full week each autumn.
    """
    assert us_dst_bounds(2026) == (datetime(2026, 3, 8).date(), datetime(2026, 11, 1).date())
    assert eu_dst_bounds(2026) == (datetime(2026, 3, 29).date(), datetime(2026, 10, 25).date())

    spring, autumn = gap_windows(2026)

    assert spring == (datetime(2026, 3, 8).date(), datetime(2026, 3, 29).date())
    assert autumn == (datetime(2026, 10, 25).date(), datetime(2026, 11, 1).date())
