"""
DST both ways, both directions (architecture/vo-time-engine.md §9 "DST").
"""

from datetime import UTC, datetime

import pytest

from vo.time.brokers import BrokerProfile, dst_transition_instants, resolve_broker_utc
from vo.time.context import TemporalStatus
from vo.time.probe import Confidence, DstCalendar

US_BROKER = BrokerProfile(
    server_name="1xTrade-Server",
    dst_calendar=DstCalendar.US,
    standard_utc_offset_hours=2,
    dst_utc_offset_hours=3,
    confidence=Confidence.OBSERVED,
)

EU_BROKER = BrokerProfile(
    server_name="EU-Server",
    dst_calendar=DstCalendar.EU,
    standard_utc_offset_hours=0,
    dst_utc_offset_hours=1,
    confidence=Confidence.OBSERVED,
)


def test_us_transition_instants_match_america_new_york():
    """US calendar transitions are, by construction, the same UTC instants
    America/New_York itself changes at - not merely the same dates."""
    spring, fall = dst_transition_instants(DstCalendar.US, 2026)

    assert spring == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 2am EST
    assert fall == datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # 2am EDT


def test_eu_transition_instants_are_01_00_utc():
    spring, fall = dst_transition_instants(DstCalendar.EU, 2026)

    assert spring == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert fall == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)


def test_spring_forward_gap_is_invalid():
    """This broker's own local clock never reads 09:00-10:00 on the spring
    date: at the real-world transition instant it jumps from standard
    (UTC+2, reading 09:00) straight to dst (UTC+3, reading 10:00). Not
    "2am local" - that is NY's own gap, and this synthetic broker's
    offsets (+2/+3) are not NY's (-5/-4)."""
    naive_in_gap = datetime(2026, 3, 8, 9, 30, 0)
    resolution = resolve_broker_utc(naive_in_gap, US_BROKER)

    assert resolution.status is TemporalStatus.INVALID


def test_just_before_and_just_after_the_spring_gap_are_valid():
    just_before = resolve_broker_utc(datetime(2026, 3, 8, 8, 59, 0), US_BROKER)
    just_after = resolve_broker_utc(datetime(2026, 3, 8, 10, 0, 0), US_BROKER)

    assert just_before.status is TemporalStatus.VALID
    assert just_before.offset_hours_applied == 2
    assert just_after.status is TemporalStatus.VALID
    assert just_after.offset_hours_applied == 3


def test_fall_back_repeat_is_ambiguous():
    """This broker's own local clock reads 08:00-09:00 twice on the
    fall-back date - once under dst (UTC+3), once under standard (UTC+2) -
    the mirror image of the spring gap above."""
    naive_in_repeat = datetime(2026, 11, 1, 8, 30, 0)
    resolution = resolve_broker_utc(naive_in_repeat, US_BROKER)

    assert resolution.status is TemporalStatus.AMBIGUOUS


def test_just_before_and_just_after_the_fall_repeat_are_valid():
    just_before = resolve_broker_utc(datetime(2026, 11, 1, 7, 59, 0), US_BROKER)
    just_after = resolve_broker_utc(datetime(2026, 11, 1, 9, 0, 0), US_BROKER)

    assert just_before.status is TemporalStatus.VALID
    assert just_before.offset_hours_applied == 3
    assert just_after.status is TemporalStatus.VALID
    assert just_after.offset_hours_applied == 2


def test_eu_broker_dst_boundaries_are_its_own_dates_not_us_dates():
    """A EU-calendar broker's gap/repeat sit on the EU dates, not the US ones."""
    on_us_date_not_eu = resolve_broker_utc(datetime(2026, 3, 8, 2, 30, 0), EU_BROKER)
    assert on_us_date_not_eu.status is TemporalStatus.VALID  # not a EU transition date

    on_eu_date = resolve_broker_utc(datetime(2026, 3, 29, 1, 30, 0), EU_BROKER)
    assert on_eu_date.status is TemporalStatus.INVALID


def test_fixed_offset_broker_has_no_dst_and_is_always_valid():
    fixed = BrokerProfile(
        server_name="Fixed-Server",
        dst_calendar=DstCalendar.NONE,
        standard_utc_offset_hours=2,
        dst_utc_offset_hours=2,
        confidence=Confidence.OBSERVED,
    )

    winter = resolve_broker_utc(datetime(2026, 1, 15, 12, 0, 0), fixed)
    summer = resolve_broker_utc(datetime(2026, 7, 1, 12, 0, 0), fixed)

    assert winter.status is TemporalStatus.VALID
    assert summer.status is TemporalStatus.VALID
    assert winter.offset_hours_applied == summer.offset_hours_applied == 2


def test_resolve_broker_utc_rejects_aware_input():
    with pytest.raises(ValueError):
        resolve_broker_utc(datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC), US_BROKER)
