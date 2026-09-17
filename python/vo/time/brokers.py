"""
BrokerTimeProvider — broker wall-clock -> UTC, date-aware (architecture/
vo-time-engine.md §1, §4b).

The broker's timezone rules are a configured, versioned fact
(config/settings/brokers.yaml, seeded from a real VO_BrokerTimeProbe.mq5
run — see scripts/seed_broker_profile.py), never re-derived from a live
PC clock reading. That live reading is exactly what F2 was: `TimeTradeServer()
- TimeGMT()`, computed from the workstation's own clock and discarded after
use — a measurement mistaken for a definition.

`dst_calendar` (vo.time.probe.DstCalendar — reused, not redefined, so there
is only ever one model of "which calendar a broker follows") is the fact an
offset reading alone cannot give you: US and EU calendars disagree by about
a week every autumn, and a system that guesses wrong is an hour out for
that entire week, every year, silently.

DST transition instants are computed two ways, both anchored to a real,
external authority rather than a hand-maintained rule:

  US calendar   the broker is assumed to change its clock at the exact same
                UTC instant America/New_York does (not merely "the same
                calendar dates") - this is what the probe's own flat-all-year
                signature actually measures (see probe.py's module docstring),
                so it is the only interpretation consistent with how brokers
                are classified as "US" in the first place. The instant is
                found via zoneinfo, not a hardcoded 06:00/07:00 UTC.

  EU calendar   the EU's DST transitions are legislated directly in UTC
                (01:00 UTC on the given date), so no zoneinfo lookup is
                needed - it is already a UTC-native rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from vo.market.provenance import Provenance
from vo.time.context import TemporalStatus
from vo.time.probe import Confidence, DstCalendar, eu_dst_bounds, us_dst_bounds

_NEW_YORK = ZoneInfo("America/New_York")


class BrokerProfileError(ValueError):
    """Raised when config/settings/brokers.yaml cannot be parsed into a
    usable profile — never silently defaulted."""


def _find_ny_transition_instant(day: date) -> datetime:
    """Scan the given UTC calendar day minute by minute for the instant
    America/New_York's utcoffset changes. 2am ET is always within the same
    UTC calendar day (6/7am UTC), so one day's scan is always enough."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    previous_offset = start.astimezone(_NEW_YORK).utcoffset()

    for minute in range(1, 24 * 60):
        instant = start + timedelta(minutes=minute)
        offset = instant.astimezone(_NEW_YORK).utcoffset()
        if offset != previous_offset:
            return instant
        previous_offset = offset

    raise BrokerProfileError(f"No America/New_York DST transition found on {day}")


@cache
def dst_transition_instants(calendar: DstCalendar, year: int) -> tuple[datetime, datetime] | None:
    """(spring_forward_utc, fall_back_utc) for the given calendar and year,
    or None when the calendar has no transitions (NONE/UNKNOWN).

    Cached: a pure function of (calendar, year) -- US resolution scans two
    full days minute-by-minute (_find_ny_transition_instant) to find the
    exact transition instant, and resolve_broker_utc calls this once per
    bar. Uncached, a deep-history backtest (one call per bar, almost
    always the same 1-2 years) redid that scan hundreds of thousands of
    times over -- the same invisible-at-500-bars, hours-at-100k-bars shape
    as the BarSequence fix beside this one. DstCalendar is a small,
    finite Enum and year is a plain int, so the cache is unbounded-safe."""
    if calendar is DstCalendar.US:
        start_date, end_date = us_dst_bounds(year)
        return _find_ny_transition_instant(start_date), _find_ny_transition_instant(end_date)

    if calendar is DstCalendar.EU:
        start_date, end_date = eu_dst_bounds(year)
        spring = datetime(start_date.year, start_date.month, start_date.day, 1, 0, tzinfo=UTC)
        fall = datetime(end_date.year, end_date.month, end_date.day, 1, 0, tzinfo=UTC)
        return spring, fall

    return None


def _parse_iso_z(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.fromisoformat(value[:-1] + "+00:00")
    return datetime.fromisoformat(value)


def _infer_standard_and_dst_hours(
    measured_offset_hours: float, calendar: DstCalendar, measured_at_utc: datetime
) -> tuple[float, float]:
    """
    config/settings/brokers.yaml (as seed_broker_profile.py writes it today)
    records a single `measured_utc_offset_hours` from one probe run, not a
    pre-split standard/dst pair. Recover the pair by checking whether the
    measurement instant itself fell inside that calendar's DST window.
    """
    if calendar in (DstCalendar.NONE, DstCalendar.UNKNOWN):
        return measured_offset_hours, measured_offset_hours

    transitions = dst_transition_instants(calendar, measured_at_utc.year)
    assert transitions is not None  # calendar is US or EU here
    spring_utc, fall_utc = transitions

    if spring_utc <= measured_at_utc < fall_utc:
        return measured_offset_hours - 1, measured_offset_hours  # measured was DST
    return measured_offset_hours, measured_offset_hours + 1  # measured was standard


class BrokerProfile:
    """A broker's resolved (or provisional) timezone rules."""

    def __init__(
        self,
        server_name: str,
        dst_calendar: DstCalendar,
        standard_utc_offset_hours: float,
        dst_utc_offset_hours: float,
        confidence: Confidence,
        implied_daily_close_ny: str | None = None,
    ) -> None:
        self.server_name = server_name
        self.dst_calendar = dst_calendar
        self.standard_utc_offset_hours = standard_utc_offset_hours
        self.dst_utc_offset_hours = dst_utc_offset_hours
        self.confidence = confidence
        self.implied_daily_close_ny = implied_daily_close_ny

    def __repr__(self) -> str:
        return (
            f"BrokerProfile({self.server_name!r}, calendar={self.dst_calendar}, "
            f"std={self.standard_utc_offset_hours}, dst={self.dst_utc_offset_hours}, "
            f"confidence={self.confidence})"
        )


def _entry_to_profile(name: str, entry: dict[str, Any]) -> BrokerProfile:
    if "dst_calendar" not in entry or "confidence" not in entry:
        raise BrokerProfileError(f"Broker {name!r} is missing dst_calendar/confidence")

    calendar = DstCalendar(str(entry["dst_calendar"]))
    confidence = Confidence(str(entry["confidence"]))
    implied_daily_close_ny = entry.get("implied_daily_close_ny")

    if "standard_utc_offset_hours" in entry and "dst_utc_offset_hours" in entry:
        standard_hours = float(entry["standard_utc_offset_hours"])
        dst_hours = float(entry["dst_utc_offset_hours"])
    elif "measured_utc_offset_hours" in entry:
        measured = float(entry["measured_utc_offset_hours"])
        provenance_block = entry.get("provenance") or {}
        probe_run_at = provenance_block.get("probe_run_at")
        if probe_run_at is None:
            raise BrokerProfileError(
                f"Broker {name!r} has measured_utc_offset_hours but no "
                f"provenance.probe_run_at to determine which DST phase it was measured in"
            )
        standard_hours, dst_hours = _infer_standard_and_dst_hours(
            measured, calendar, _parse_iso_z(str(probe_run_at))
        )
    else:
        raise BrokerProfileError(
            f"Broker {name!r} has neither standard_utc_offset_hours/dst_utc_offset_hours "
            f"nor measured_utc_offset_hours"
        )

    return BrokerProfile(
        server_name=name,
        dst_calendar=calendar,
        standard_utc_offset_hours=standard_hours,
        dst_utc_offset_hours=dst_hours,
        confidence=confidence,
        implied_daily_close_ny=str(implied_daily_close_ny) if implied_daily_close_ny else None,
    )


def load_broker_profiles(path: str | Path) -> dict[str, BrokerProfile]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise BrokerProfileError(f"{path}: expected a YAML mapping at the top level")

    brokers = raw.get("brokers")
    if not isinstance(brokers, dict):
        raise BrokerProfileError(f"{path}: expected a top-level 'brokers' mapping")

    return {name: _entry_to_profile(str(name), entry) for name, entry in brokers.items()}


@dataclass(frozen=True)
class BrokerResolution:
    utc: datetime
    status: TemporalStatus
    offset_hours_applied: float
    """Whichever of standard/dst was actually used for this instant - not
    necessarily profile.dst_utc_offset_hours; see make_wire_provenance."""


def resolve_broker_utc(naive_broker_time: datetime, profile: BrokerProfile) -> BrokerResolution:
    """
    Broker wall-clock (naive - no tzinfo, exactly as the wire sends it) ->
    a UTC instant, plus how much to trust it.

    Never `broker_time - fixed_hours` (§1): the offset is a function of the
    date via profile.dst_calendar, resolved against dst_transition_instants,
    not a constant.
    """
    if naive_broker_time.tzinfo is not None:
        raise ValueError("naive_broker_time must have no tzinfo - broker wall-clock, not UTC")

    if profile.dst_calendar in (DstCalendar.NONE, DstCalendar.UNKNOWN):
        status = (
            TemporalStatus.VALID
            if profile.dst_calendar is DstCalendar.NONE
            else TemporalStatus.UNKNOWN
        )
        offset = profile.standard_utc_offset_hours
        utc_naive = naive_broker_time - timedelta(hours=offset)
        return BrokerResolution(utc_naive.replace(tzinfo=UTC), status, offset)

    transitions = dst_transition_instants(profile.dst_calendar, naive_broker_time.year)
    assert transitions is not None
    spring_utc, fall_utc = transitions

    std_h = profile.standard_utc_offset_hours
    dst_h = profile.dst_utc_offset_hours

    # Local wall-clock boundaries, derived by converting each UTC transition
    # instant into broker-local time under each offset in turn (see the
    # module docstring's algorithm notes for the gap/repeat derivation).
    gap_start = (spring_utc + timedelta(hours=std_h)).replace(tzinfo=None)
    gap_end = (spring_utc + timedelta(hours=dst_h)).replace(tzinfo=None)
    repeat_start = (fall_utc + timedelta(hours=std_h)).replace(tzinfo=None)
    repeat_end = (fall_utc + timedelta(hours=dst_h)).replace(tzinfo=None)

    if gap_start <= naive_broker_time < gap_end:
        # This local time never occurred. Best-effort: assume the DST side.
        utc_naive = naive_broker_time - timedelta(hours=dst_h)
        return BrokerResolution(utc_naive.replace(tzinfo=UTC), TemporalStatus.INVALID, dst_h)

    if repeat_start <= naive_broker_time < repeat_end:
        # This local time occurred twice. Best-effort: assume the DST
        # (earlier) occurrence.
        utc_naive = naive_broker_time - timedelta(hours=dst_h)
        return BrokerResolution(utc_naive.replace(tzinfo=UTC), TemporalStatus.AMBIGUOUS, dst_h)

    offset = dst_h if gap_end <= naive_broker_time < repeat_start else std_h

    utc_naive = naive_broker_time - timedelta(hours=offset)
    return BrokerResolution(utc_naive.replace(tzinfo=UTC), TemporalStatus.VALID, offset)


def make_wire_provenance(
    *,
    schema_version: int,
    platform: str,
    broker_server: str,
    raw_broker_timestamp: str,
    profile: BrokerProfile | None,
    resolution: BrokerResolution | None,
) -> Provenance:
    """Build vo.market.Provenance from a resolved (or absent) broker
    profile - the calendar enum is converted to a plain string here so
    vo.market never has to import vo.time.probe."""
    return Provenance(
        schema_version=schema_version,
        platform=platform,
        broker_server=broker_server,
        raw_broker_timestamp=raw_broker_timestamp,
        resolved_utc_offset_hours=(
            resolution.offset_hours_applied if resolution is not None else None
        ),
        broker_tz_calendar=str(profile.dst_calendar) if profile is not None else None,
    )
