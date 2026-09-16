"""
Broker -> UTC -> NY conversion (architecture/vo-time-engine.md §1, §9
"Conversion"): never `broker_time - fixed_hours`.
"""

from datetime import UTC, datetime

from vo.time.brokers import BrokerProfile, resolve_broker_utc
from vo.time.context import TemporalStatus
from vo.time.probe import Confidence, DstCalendar

US_BROKER = BrokerProfile(
    server_name="1xTrade-Server",
    dst_calendar=DstCalendar.US,
    standard_utc_offset_hours=2,
    dst_utc_offset_hours=3,
    confidence=Confidence.OBSERVED,
)


def test_broker_to_utc_in_summer_uses_dst_offset():
    # 2026-07-01 is deep summer for both NY and a US-calendar broker.
    naive = datetime(2026, 7, 1, 12, 0, 0)
    resolution = resolve_broker_utc(naive, US_BROKER)

    assert resolution.status is TemporalStatus.VALID
    assert resolution.offset_hours_applied == 3
    assert resolution.utc == datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC)


def test_broker_to_utc_in_winter_uses_standard_offset():
    naive = datetime(2026, 1, 15, 12, 0, 0)
    resolution = resolve_broker_utc(naive, US_BROKER)

    assert resolution.status is TemporalStatus.VALID
    assert resolution.offset_hours_applied == 2
    assert resolution.utc == datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)


def test_utc_to_ny_round_trips_through_zoneinfo():
    """UTC -> NY -> UTC via VOTimeEngine.context_for is lossless (§1's
    "zoneinfo, never a hardcoded -5 or -4")."""
    from pathlib import Path

    from vo.market.identity import InstrumentId
    from vo.time.engine import VOTimeEngine
    from vo.time.sessions import load_session_configs

    repo_root = Path(__file__).resolve().parents[2]
    configs = load_session_configs(repo_root / "config" / "settings" / "sessions.yaml")
    engine = VOTimeEngine(configs)
    instrument = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")

    utc = datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC)
    context = engine.context_for(utc, instrument)

    assert context.ny_timestamp.astimezone(UTC) == utc
    assert context.ny_utc_offset_seconds == -4 * 3600  # EDT
