"""
vo.time.mapping.resolve_record - closing Phase 4's UnresolvedServerTimeError
gap for schema-v2 records once a broker profile exists, while leaving
vo.market.mapping.record_to_domain's own refusal completely unchanged
(architecture/vo-time-engine.md; see also vo/market/mapping.py's
UnresolvedServerTimeError docstring).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from vo.market.bar import DataQuality
from vo.market.mapping import UnresolvedServerTimeError
from vo.market.records import BarRecord, BarRecordV2, TickRecordV2
from vo.time.brokers import BrokerProfile, load_broker_profiles
from vo.time.context import TemporalStatus
from vo.time.mapping import resolve_record
from vo.time.probe import Confidence, DstCalendar

_REPO_ROOT = Path(__file__).resolve().parents[2]

_GAP_PROFILE = BrokerProfile(
    server_name="Gap-Server",
    dst_calendar=DstCalendar.US,
    standard_utc_offset_hours=2,
    dst_utc_offset_hours=3,
    confidence=Confidence.OBSERVED,
)


def _real_broker_profiles() -> dict[str, BrokerProfile]:
    return load_broker_profiles(_REPO_ROOT / "config" / "settings" / "brokers.yaml")


def _bar_v2(**overrides) -> BarRecordV2:
    fields = dict(
        timestamp=datetime(2026, 9, 10, 18, 49, 0),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        spread=12,
        timeframe="PERIOD_M1",
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )
    fields.update(overrides)
    return BarRecordV2(**fields)


def test_resolve_record_resolves_bar_v2_when_a_broker_profile_exists():
    bar = resolve_record(_bar_v2(), _real_broker_profiles())

    assert bar.quality is DataQuality.VALID
    assert bar.provenance is not None
    assert bar.provenance.broker_server == "1xTrade-Server"
    assert bar.provenance.platform == "MT5"
    assert bar.provenance.schema_version == 2


def test_resolve_record_raises_unresolved_when_no_profile_exists_for_that_broker():
    bar = _bar_v2(broker_server="Nobody-Has-Heard-Of-This-Server")

    with pytest.raises(UnresolvedServerTimeError):
        resolve_record(bar, _real_broker_profiles())


def test_resolve_record_raises_unresolved_when_no_profiles_configured_at_all():
    with pytest.raises(UnresolvedServerTimeError):
        resolve_record(_bar_v2(), {})


def test_resolve_record_marks_quality_suspect_for_an_invalid_dst_gap_timestamp():
    naive_in_spring_gap = datetime(2026, 3, 8, 9, 30, 0)  # see test_time_dst.py's gap math
    bar = resolve_record(
        _bar_v2(broker_server="Gap-Server", timestamp=naive_in_spring_gap),
        {"Gap-Server": _GAP_PROFILE},
    )

    assert bar.quality is DataQuality.SUSPECT
    assert bar.quality_reason is not None
    assert TemporalStatus.INVALID.name in bar.quality_reason


def test_resolve_record_marks_quality_suspect_for_an_ambiguous_dst_repeat_timestamp():
    naive_in_fall_repeat = datetime(2026, 11, 1, 8, 30, 0)  # see test_time_dst.py's repeat math
    bar = resolve_record(
        _bar_v2(broker_server="Gap-Server", timestamp=naive_in_fall_repeat),
        {"Gap-Server": _GAP_PROFILE},
    )

    assert bar.quality is DataQuality.SUSPECT
    assert bar.quality_reason is not None
    assert TemporalStatus.AMBIGUOUS.name in bar.quality_reason


def test_resolve_record_resolves_tick_v2_when_a_broker_profile_exists():
    tick_record = TickRecordV2(
        timestamp_server_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        volume_real=0.0,
        flags=0,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    tick = resolve_record(tick_record, _real_broker_profiles())

    assert tick.source == "MT5:US100.n"


def test_resolve_record_delegates_v1_records_unchanged():
    """A v1 record never raises UnresolvedServerTimeError in the first
    place, so resolve_record must return it via plain record_to_domain
    without needing any broker profile at all."""
    v1_bar = BarRecord(
        source="MT5:US100.n",
        timestamp=datetime(2026, 9, 10, 18, 49, 0, tzinfo=UTC),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        timeframe="PERIOD_M1",
    )

    bar = resolve_record(v1_bar, {})

    assert bar.open == 29132.30
