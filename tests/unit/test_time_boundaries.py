"""
Trading-day / week / month / year boundaries, including where the UTC
calendar date, the NY calendar date and the trading_day genuinely
disagree (architecture/vo-time-engine.md §2, §9 "Boundaries").

US100.n's trading_day_opens is 18:00 NY (config/settings/sessions.yaml) -
a nearly-24h instrument, deliberately chosen for this test file because it
is the case where trading_day and the NY calendar date disagree for six
hours out of every twenty-four. VO uses the CME trade-date convention: a
session opening 18:00 ET is dated to the NEXT calendar day (see
vo.time.calendars' module docstring), so the disagreement is the 18:00 ->
midnight window, where the trading day has already rolled to tomorrow.
"""

from datetime import UTC, date
from pathlib import Path
from zoneinfo import ZoneInfo

from vo.market.identity import InstrumentId
from vo.time.engine import VOTimeEngine
from vo.time.sessions import load_session_configs

_NY = ZoneInfo("America/New_York")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")


def _engine() -> VOTimeEngine:
    configs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    return VOTimeEngine(configs)


def _ny_local(year, month, day, hour, minute=0):
    return __import__("datetime").datetime(year, month, day, hour, minute, tzinfo=_NY)


def test_trading_day_before_open_is_today():
    engine = _engine()
    utc = _ny_local(2026, 7, 1, 10, 0).astimezone(UTC)  # 10:00 NY, before 18:00 open

    context = engine.context_for(utc, _INSTRUMENT)

    # Before the open we are still inside the session that opened the
    # evening before - which, CME trade-date, is dated to today.
    assert context.trading_day == date(2026, 7, 1)
    assert context.ny_timestamp.date() == date(2026, 7, 1)


def test_trading_day_at_and_after_open_rolls_to_next_date():
    engine = _engine()
    at_open = engine.context_for(_ny_local(2026, 7, 1, 18, 0).astimezone(UTC), _INSTRUMENT)
    just_after = engine.context_for(_ny_local(2026, 7, 1, 18, 1).astimezone(UTC), _INSTRUMENT)

    # The session opening 18:00 ET on the 1st is the 2nd's trading day.
    assert at_open.trading_day == date(2026, 7, 2)
    assert just_after.trading_day == date(2026, 7, 2)
    # NY calendar date is still the 1st here, so it is no stand-in for it.
    assert at_open.ny_timestamp.date() == date(2026, 7, 1)
    assert at_open.utc_timestamp.date() != at_open.trading_day


def test_utc_calendar_date_can_differ_from_ny_calendar_date():
    """Early UTC morning is still the previous NY evening (EDT is behind
    UTC). At 22:00 NY on 30 June the trading day has already rolled to
    1 July (past the 18:00 open) while the NY calendar date is still
    30 June - three different dates in play at once."""
    engine = _engine()
    utc = _ny_local(2026, 6, 30, 22, 0).astimezone(UTC)  # 22:00 NY, 30 June, after open

    context = engine.context_for(utc, _INSTRUMENT)

    assert context.utc_timestamp.date() == date(2026, 7, 1)
    assert context.ny_timestamp.date() == date(2026, 6, 30)
    assert context.trading_day == date(2026, 7, 1)  # after 18:00 open -> next date


def test_iso_week_can_carry_a_different_year_than_trading_year():
    """2027-01-02 is ISO week 53 of 2026 - trading_year (calendar year of
    trading_day) and trading_week's own ISO year deliberately need not
    match."""
    engine = _engine()
    utc = _ny_local(2027, 1, 1, 19, 0).astimezone(UTC)  # after open -> trading_day = 2027-01-02

    context = engine.context_for(utc, _INSTRUMENT)

    assert context.trading_day == date(2027, 1, 2)
    assert context.trading_year == 2027
    assert context.trading_week == (2026, 53)


def test_month_and_year_rollover_at_the_open():
    engine = _engine()
    before_open = engine.context_for(_ny_local(2026, 12, 31, 10, 0).astimezone(UTC), _INSTRUMENT)
    after_open = engine.context_for(_ny_local(2026, 12, 31, 20, 0).astimezone(UTC), _INSTRUMENT)

    # Before the 31st's open: still trading day 2026-12-31.
    assert before_open.trading_day == date(2026, 12, 31)
    assert before_open.trading_month == 12
    assert before_open.trading_year == 2026

    # After it: the session opening 18:00 on 2026-12-31 is 2027-01-01's
    # trading day - month and year both roll forward at the open.
    assert after_open.trading_day == date(2027, 1, 1)
    assert after_open.trading_month == 1
    assert after_open.trading_year == 2027


def test_day_of_week_matches_trading_day_not_ny_calendar_date():
    engine = _engine()
    # 20:00 NY on Tuesday 2027-01-05 is already trading_day Wednesday
    # 2027-01-06 (past the 18:00 open).
    context = engine.context_for(_ny_local(2027, 1, 5, 20, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2027, 1, 6)
    assert context.day_of_week == date(2027, 1, 6).weekday()
    assert context.day_of_week != context.ny_timestamp.weekday()


def test_leap_day_is_reached_by_ordinal_arithmetic_not_skipped():
    """20:00 NY on 2028-02-28 (after open) must roll forward to trading_day
    2028-02-29, not 2028-03-01 - proving trading_day_of's ordinal addition
    lands on the leap day rather than stepping over it."""
    engine = _engine()
    context = engine.context_for(_ny_local(2028, 2, 28, 20, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2028, 2, 29)


def test_leap_day_before_open_is_its_own_trading_day():
    engine = _engine()
    context = engine.context_for(_ny_local(2028, 2, 29, 10, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2028, 2, 29)
