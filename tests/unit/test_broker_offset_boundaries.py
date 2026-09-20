"""The measured broker offset is 2:59:59, not 3:00:00 -- and the
session classifier must be pinned against the value that was MEASURED.

brokers.yaml records 2.999722222222222 hours: two hours, fifty-nine
minutes, fifty-nine seconds. That is what VO_BrokerTimeProbe observed,
so it is what the profile says. Every bar in a replay is therefore
shifted one second from a round hour, which is why real US100 history
arrives with timestamps like 03:51:01.

WHY THIS IS PINNED RATHER THAN NOTED. A one-second discrepancy is
invisible until it lands on a boundary, and then it silently reclassifies
a bar: 09:29:59 ET is not in the RTH session and 09:30:00 is. A later
developer "tidying" the profile to a round +3:00:00 would change the
historical classification of every boundary bar without any test going
red -- which is exactly the kind of reproducibility break that makes an
old study impossible to reproduce.

These tests do not assert that 2:59:59 is CORRECT. They assert that the
system behaves according to the measured value, and that the boundary
cases behave the way the session model says they should. If the probe is
ever re-run and measures something else, these tests should be updated
deliberately, with the new evidence recorded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vo.market.identity import InstrumentId
from vo.time.brokers import load_broker_profiles, resolve_broker_utc
from vo.time.engine import VOTimeEngine
from vo.time.sessions import load_session_configs

_REPO = Path(__file__).resolve().parents[2]
_SETTINGS = _REPO / "config" / "settings"
_SERVER = "1xTrade-Server"

_MEASURED_SECONDS = 2 * 3600 + 59 * 60 + 59  # 2:59:59


def _profiles():
    return load_broker_profiles(_SETTINGS / "brokers.yaml")


def _engine() -> VOTimeEngine:
    return VOTimeEngine(load_session_configs(_SETTINGS / "sessions.yaml"))


def _instrument(symbol: str = "US100") -> InstrumentId:
    return InstrumentId(
        platform="MT5", broker_server=_SERVER, broker_symbol=symbol
    )


def test_the_shipped_profile_records_the_measured_offset_not_a_round_one() -> None:
    profile = _profiles()[_SERVER]

    dst_seconds = round(profile.dst_utc_offset_hours * 3600)
    standard_seconds = round(profile.standard_utc_offset_hours * 3600)

    assert dst_seconds == _MEASURED_SECONDS, (
        "brokers.yaml no longer records the measured 2:59:59 DST offset. "
        "If the probe was re-run, update this test with the new evidence; "
        "do not round it to 3:00:00."
    )
    assert standard_seconds == _MEASURED_SECONDS - 3600
    assert dst_seconds != 3 * 3600


def test_the_offset_is_applied_exactly_when_resolving_a_bar() -> None:
    """A broker timestamp on a round minute resolves one second off a
    round minute in UTC. That is the visible consequence of the measured
    offset and it is deliberate."""
    profile = _profiles()[_SERVER]

    # Mid-July: New York is on daylight time, so the DST offset applies.
    naive_server = datetime(2026, 7, 15, 12, 0, 0)
    resolved = resolve_broker_utc(naive_server, profile)

    assert resolved.utc == datetime(2026, 7, 15, 9, 0, 1, tzinfo=UTC)
    assert resolved.utc.second == 1


def test_the_offset_is_dst_aware_and_not_hardcoded() -> None:
    """January and July resolve through different offsets, which is what
    the US DST calendar on the profile is for."""
    profile = _profiles()[_SERVER]
    naive = datetime(2026, 1, 15, 12, 0, 0)
    winter = resolve_broker_utc(naive, profile).utc
    summer = resolve_broker_utc(naive.replace(month=7), profile).utc

    assert winter.hour - 12 != summer.hour - 12 or winter.month == summer.month
    # One hour apart in the offset applied, per the US calendar.
    winter_offset = (datetime(2026, 1, 15, 12, tzinfo=UTC) - winter).total_seconds()
    summer_offset = (datetime(2026, 7, 15, 12, tzinfo=UTC) - summer).total_seconds()
    assert summer_offset - winter_offset == 3600.0


# ── boundary classification ───────────────────────────────────────────────


@pytest.mark.parametrize("symbol", ["US100", "US100.n"])
def test_the_rth_boundaries_classify_as_the_session_model_says(symbol: str) -> None:
    """RTH is 09:30-16:00 New York. One second either side of each edge
    must fall on the expected side -- this is precisely where a
    one-second offset would show up."""
    engine = _engine()
    instrument = _instrument(symbol)

    def session_at(ny_naive: datetime) -> str:
        # New York wall time -> UTC, via the same zone the config names.
        from zoneinfo import ZoneInfo

        aware = ny_naive.replace(tzinfo=ZoneInfo("America/New_York"))
        return str(engine.context_for(aware.astimezone(UTC), instrument).session)

    open_edge = datetime(2026, 9, 15, 9, 30, 0)
    close_edge = datetime(2026, 9, 15, 16, 0, 0)

    assert session_at(open_edge) == "NY_AM"
    assert session_at(open_edge - timedelta(seconds=1)) != "NY_AM"
    assert session_at(close_edge - timedelta(seconds=1)) == "NY_PM"
    assert session_at(close_edge) != "NY_PM"


def test_the_trading_day_rolls_at_the_configured_hour() -> None:
    """The trading day opens 18:00 New York and is dated to the NEXT
    calendar day -- the CME convention. A bar one second before the roll
    belongs to the previous trading day."""
    from zoneinfo import ZoneInfo

    engine = _engine()
    instrument = _instrument()
    roll = datetime(2026, 9, 15, 18, 0, 0, tzinfo=ZoneInfo("America/New_York"))

    before = engine.context_for(
        (roll - timedelta(seconds=1)).astimezone(UTC), instrument
    ).trading_day
    after = engine.context_for(roll.astimezone(UTC), instrument).trading_day

    assert after != before
    assert (after - before).days == 1


def test_a_one_second_shift_can_cross_a_session_boundary() -> None:
    """The whole reason the offset is pinned. A bar labelled one second
    early sits in a different session, and nothing else in the system
    would notice."""
    from zoneinfo import ZoneInfo

    engine = _engine()
    instrument = _instrument()
    edge = datetime(2026, 9, 15, 9, 30, 0, tzinfo=ZoneInfo("America/New_York"))

    at_edge = str(engine.context_for(edge.astimezone(UTC), instrument).session)
    one_second_early = str(
        engine.context_for(
            (edge - timedelta(seconds=1)).astimezone(UTC), instrument
        ).session
    )

    assert at_edge != one_second_early
