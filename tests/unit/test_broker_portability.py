"""
Broker portability (architecture/vo-time-engine.md §9 "Portability"):
three brokers at three different offsets, each reporting the *same* real
moment in their own wall-clock, must converge on one identical UTC
instant - and therefore one identical NY timestamp and session - once
resolved. This is the payoff of "never broker_time - fixed_hours": the
canonical layer must not be able to tell which broker a bar came from.
"""

from datetime import UTC, datetime
from pathlib import Path

from vo.market.identity import InstrumentId
from vo.time.brokers import BrokerProfile, resolve_broker_utc
from vo.time.engine import VOTimeEngine
from vo.time.probe import Confidence, DstCalendar
from vo.time.sessions import load_session_configs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")

# The one real moment all three brokers are independently reporting.
_TARGET_UTC = datetime(2026, 7, 1, 15, 0, 0, tzinfo=UTC)

_UTC_BROKER = BrokerProfile(
    server_name="UTC-Server",
    dst_calendar=DstCalendar.NONE,
    standard_utc_offset_hours=0,
    dst_utc_offset_hours=0,
    confidence=Confidence.OBSERVED,
)
_US_BROKER = BrokerProfile(
    server_name="US-Server",
    dst_calendar=DstCalendar.US,
    standard_utc_offset_hours=2,
    dst_utc_offset_hours=3,
    confidence=Confidence.OBSERVED,
)
_EU_BROKER = BrokerProfile(
    server_name="EU-Server",
    dst_calendar=DstCalendar.EU,
    standard_utc_offset_hours=1,
    dst_utc_offset_hours=2,
    confidence=Confidence.OBSERVED,
)

# What each broker's own clock would show at _TARGET_UTC, given its
# (summer, i.e. DST-side) offset - naive, exactly as it would arrive on the wire.
_UTC_BROKER_LOCAL = datetime(2026, 7, 1, 15, 0, 0)
_US_BROKER_LOCAL = datetime(2026, 7, 1, 18, 0, 0)  # UTC+3
_EU_BROKER_LOCAL = datetime(2026, 7, 1, 17, 0, 0)  # UTC+2


def test_three_brokers_at_different_offsets_resolve_to_the_same_utc_instant():
    utc_resolution = resolve_broker_utc(_UTC_BROKER_LOCAL, _UTC_BROKER)
    us_resolution = resolve_broker_utc(_US_BROKER_LOCAL, _US_BROKER)
    eu_resolution = resolve_broker_utc(_EU_BROKER_LOCAL, _EU_BROKER)

    assert utc_resolution.utc == _TARGET_UTC
    assert us_resolution.utc == _TARGET_UTC
    assert eu_resolution.utc == _TARGET_UTC


def test_three_brokers_converge_on_identical_ny_timestamp_and_session():
    configs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    engine = VOTimeEngine(configs)

    resolutions = [
        resolve_broker_utc(_UTC_BROKER_LOCAL, _UTC_BROKER),
        resolve_broker_utc(_US_BROKER_LOCAL, _US_BROKER),
        resolve_broker_utc(_EU_BROKER_LOCAL, _EU_BROKER),
    ]
    contexts = [engine.context_for(resolution.utc, _INSTRUMENT) for resolution in resolutions]

    ny_timestamps = {context.ny_timestamp for context in contexts}
    sessions = {context.session for context in contexts}
    trading_days = {context.trading_day for context in contexts}

    assert len(ny_timestamps) == 1
    assert len(sessions) == 1
    assert len(trading_days) == 1
