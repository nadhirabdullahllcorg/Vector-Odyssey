"""
Trading-day / week / month / year boundaries, including where the UTC
calendar date, the NY calendar date and the trading_day genuinely
disagree (architecture/vo-time-engine.md §2, §9 "Boundaries").

US100.n's trading_day_opens is 18:00 NY (config/settings/sessions.yaml) -
a nearly-24h instrument, deliberately chosen for this test file because it
is the case where trading_day and the NY calendar date disagree for six
hours out of every twenty-four (see vo.time.calendars' module docstring).
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


def test_trading_day_before_open_is_still_yesterday():
    engine = _engine()
    utc = _ny_local(2026, 7, 1, 10, 0).astimezone(UTC)  # 10:00 NY, before 18:00 open

    context = engine.context_for(utc, _INSTRUMENT)

    assert context.trading_day == date(2026, 6, 30)
    assert context.ny_timestamp.date() == date(2026, 7, 1)  # NY calendar date has already rolled
    assert context.utc_timestamp.date() != context.trading_day


def test_trading_day_at_and_after_open_rolls_forward():
    engine = _engine()
    at_open = engine.context_for(_ny_local(2026, 7, 1, 18, 0).astimezone(UTC), _INSTRUMENT)
    just_after = engine.context_for(_ny_local(2026, 7, 1, 18, 1).astimezone(UTC), _INSTRUMENT)

    assert at_open.trading_day == date(2026, 7, 1)
    assert just_after.trading_day == date(2026, 7, 1)


def test_utc_calendar_date_can_differ_from_ny_calendar_date():
    """Late UTC evening is already the next NY morning is *not* the case
    here - EDT is behind UTC, so early UTC morning is still the previous
    NY evening. Either direction proves utc_timestamp.date() is not a
    safe stand-in for ny_timestamp.date()."""
    engine = _engine()
    utc = _ny_local(2026, 6, 30, 22, 0).astimezone(UTC)  # 22:00 NY, 30 June

    context = engine.context_for(utc, _INSTRUMENT)

    assert context.utc_timestamp.date() == date(2026, 7, 1)
    assert context.ny_timestamp.date() == date(2026, 6, 30)
    assert context.trading_day == date(2026, 6, 30)  # after 18:00 open, same as NY date here


def test_iso_week_can_carry_a_different_year_than_trading_year():
    """2027-01-01 is ISO week 53 of 2026 - trading_year (calendar year of
    trading_day) and trading_week's own ISO year deliberately need not
    match."""
    engine = _engine()
    utc = _ny_local(2027, 1, 1, 19, 0).astimezone(UTC)  # after open -> trading_day = 2027-01-01

    context = engine.context_for(utc, _INSTRUMENT)

    assert context.trading_day == date(2027, 1, 1)
    assert context.trading_year == 2027
    assert context.trading_week == (2026, 53)


def test_month_and_year_rollover_at_the_open():
    engine = _engine()
    dec_31_utc = _ny_local(2026, 12, 31, 20, 0).astimezone(UTC)
    jan_1_utc = _ny_local(2027, 1, 1, 19, 0).astimezone(UTC)
    last_bar_of_year = engine.context_for(dec_31_utc, _INSTRUMENT)
    first_bar_of_year = engine.context_for(jan_1_utc, _INSTRUMENT)

    assert last_bar_of_year.trading_day == date(2026, 12, 31)
    assert last_bar_of_year.trading_month == 12
    assert last_bar_of_year.trading_year == 2026

    assert first_bar_of_year.trading_day == date(2027, 1, 1)
    assert first_bar_of_year.trading_month == 1
    assert first_bar_of_year.trading_year == 2027


def test_day_of_week_matches_trading_day_not_ny_calendar_date():
    engine = _engine()
    # 10:00 NY on Wednesday 2027-01-06 is still trading_day Tuesday 2027-01-05.
    context = engine.context_for(_ny_local(2027, 1, 6, 10, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2027, 1, 5)
    assert context.day_of_week == date(2027, 1, 5).weekday()
    assert context.day_of_week != context.ny_timestamp.weekday()


def test_leap_day_is_reached_by_ordinal_arithmetic_not_skipped():
    """10:00 NY on 2028-03-01 (before open) must roll back to trading_day
    2028-02-29, not 2028-02-28 - proving trading_day_of's ordinal
    subtraction lands on the leap day rather than stepping over it."""
    engine = _engine()
    context = engine.context_for(_ny_local(2028, 3, 1, 10, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2028, 2, 29)


def test_leap_day_itself_after_open_is_its_own_trading_day():
    engine = _engine()
    context = engine.context_for(_ny_local(2028, 2, 29, 20, 0).astimezone(UTC), _INSTRUMENT)

    assert context.trading_day == date(2028, 2, 29)
