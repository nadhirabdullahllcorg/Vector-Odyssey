"""
TemporalStatus is how the engine admits what it does not know
(architecture/vo-time-engine.md §3, §9 "Status"): VALID is the only
status a consumer should build a decision on.
"""

from datetime import datetime

from vo.time.brokers import BrokerProfile, resolve_broker_utc
from vo.time.context import TemporalStatus
from vo.time.probe import Confidence, DstCalendar


def test_unknown_calendar_yields_unknown_status_not_a_guessed_valid():
    profile = BrokerProfile(
        server_name="Mystery-Server",
        dst_calendar=DstCalendar.UNKNOWN,
        standard_utc_offset_hours=2,
        dst_utc_offset_hours=2,
        confidence=Confidence.UNKNOWN,
    )

    resolution = resolve_broker_utc(datetime(2026, 7, 1, 12, 0, 0), profile)

    assert resolution.status is TemporalStatus.UNKNOWN


def test_none_calendar_is_a_confident_fixed_offset_not_unknown():
    """NONE means 'no DST at all', a positive fact - it must not be
    confused with UNKNOWN, 'we don't know the rule'."""
    profile = BrokerProfile(
        server_name="Fixed-Server",
        dst_calendar=DstCalendar.NONE,
        standard_utc_offset_hours=3,
        dst_utc_offset_hours=3,
        confidence=Confidence.OBSERVED,
    )

    resolution = resolve_broker_utc(datetime(2026, 7, 1, 12, 0, 0), profile)

    assert resolution.status is TemporalStatus.VALID


def test_unknown_and_none_give_different_status_for_the_same_offset_values():
    """Two profiles with identical numeric offsets must still disagree on
    status when their dst_calendar differs - the status is about how much
    the rule is trusted, not about the numbers themselves."""
    common_kwargs = {
        "server_name": "Server",
        "standard_utc_offset_hours": 2.0,
        "dst_utc_offset_hours": 2.0,
        "confidence": Confidence.PROVISIONAL,
    }
    unknown_profile = BrokerProfile(dst_calendar=DstCalendar.UNKNOWN, **common_kwargs)
    none_profile = BrokerProfile(dst_calendar=DstCalendar.NONE, **common_kwargs)

    at = datetime(2026, 7, 1, 12, 0, 0)
    assert resolve_broker_utc(at, unknown_profile).status is TemporalStatus.UNKNOWN
    assert resolve_broker_utc(at, none_profile).status is TemporalStatus.VALID


def test_time_engine_session_contexts_are_always_valid():
    """VOTimeEngine.context_for never itself produces UNKNOWN/AMBIGUOUS/
    INVALID - those only arise upstream, in broker -> UTC resolution
    (resolve_broker_utc). Once an instant is honestly UTC, NY-session
    lookup via zoneinfo has nothing left to be unsure about."""
    from datetime import UTC as _UTC
    from pathlib import Path
    from zoneinfo import ZoneInfo

    from vo.market.identity import InstrumentId
    from vo.time.engine import VOTimeEngine
    from vo.time.sessions import load_session_configs

    repo_root = Path(__file__).resolve().parents[2]
    configs = load_session_configs(repo_root / "config" / "settings" / "sessions.yaml")
    engine = VOTimeEngine(configs)
    instrument = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")

    utc = datetime(2026, 11, 1, 9, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(_UTC)
    context = engine.context_for(utc, instrument)

    assert context.status is TemporalStatus.VALID
