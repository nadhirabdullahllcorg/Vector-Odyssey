"""
Session boundaries and transitions (architecture/vo-time-engine.md §4, §9
"Sessions"): just-before/at/inside/just-after each edge, a genuine gap
between configured windows, and a transition firing at exactly one instant
- including across a DST change, where only zoneinfo (not a fixed offset)
gets the UTC instant of a fixed NY wall-clock boundary right.
"""

from datetime import UTC, datetime, timedelta
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


def _ny(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(UTC)


def test_just_before_at_inside_and_just_after_the_london_to_ny_am_boundary():
    engine = _engine()

    just_before = engine.context_for(_ny(2026, 7, 1, 9, 29), _INSTRUMENT)
    at_boundary = engine.context_for(_ny(2026, 7, 1, 9, 30), _INSTRUMENT)
    inside = engine.context_for(_ny(2026, 7, 1, 10, 0), _INSTRUMENT)
    just_after_next_hour = engine.context_for(_ny(2026, 7, 1, 9, 31), _INSTRUMENT)

    assert just_before.session == "LONDON"
    assert at_boundary.session == "NY_AM"
    assert inside.session == "NY_AM"
    assert just_after_next_hour.session == "NY_AM"


def test_transition_fires_exactly_at_the_boundary_instant_and_nowhere_else():
    engine = _engine()

    at_boundary = engine.context_for(_ny(2026, 7, 1, 9, 30), _INSTRUMENT)
    one_minute_before = engine.context_for(_ny(2026, 7, 1, 9, 29), _INSTRUMENT)
    one_minute_after = engine.context_for(_ny(2026, 7, 1, 9, 31), _INSTRUMENT)

    assert at_boundary.session_transition is not None
    assert at_boundary.session_transition.previous_session == "LONDON"
    assert at_boundary.session_transition.session == "NY_AM"
    assert at_boundary.transition_timestamp == _ny(2026, 7, 1, 9, 30)

    assert one_minute_before.session_transition is None
    assert one_minute_after.session_transition is None


def test_session_started_at_and_ends_at_bracket_the_window():
    engine = _engine()
    context = engine.context_for(_ny(2026, 7, 1, 10, 0), _INSTRUMENT)  # inside NY_AM

    assert context.session_started_at == _ny(2026, 7, 1, 9, 30)
    assert context.session_ends_at == _ny(2026, 7, 1, 12, 0)


def test_midnight_wrapping_asia_session_is_contiguous_across_the_calendar_date():
    engine = _engine()
    late_evening = engine.context_for(_ny(2026, 7, 1, 23, 0), _INSTRUMENT)
    just_after_midnight = engine.context_for(_ny(2026, 7, 2, 0, 30), _INSTRUMENT)

    assert late_evening.session == "ASIA"
    assert just_after_midnight.session == "ASIA"
    # The wrapping window's bounds are the occurrence containing *this* instant,
    # not a fixed calendar-day pair.
    assert late_evening.session_started_at == _ny(2026, 7, 1, 18, 0)
    assert late_evening.session_ends_at == _ny(2026, 7, 2, 3, 0)
    assert just_after_midnight.session_started_at == _ny(2026, 7, 1, 18, 0)
    assert just_after_midnight.session_ends_at == _ny(2026, 7, 2, 3, 0)


def test_gap_between_ny_pm_close_and_asia_open_has_no_session():
    engine = _engine()
    context = engine.context_for(_ny(2026, 7, 1, 17, 0), _INSTRUMENT)  # 16:00-18:00 gap

    assert context.session is None
    assert context.is_rth is False


def test_is_rth_true_only_inside_the_configured_rth_window():
    engine = _engine()
    assert engine.is_rth(_ny(2026, 7, 1, 10, 0), _INSTRUMENT) is True  # NY_AM, inside RTH
    assert engine.is_rth(_ny(2026, 7, 1, 13, 0), _INSTRUMENT) is True  # NY_PM, inside RTH
    assert engine.is_rth(_ny(2026, 7, 1, 5, 0), _INSTRUMENT) is False  # LONDON
    assert engine.is_rth(_ny(2026, 7, 1, 20, 0), _INSTRUMENT) is False  # ASIA


def test_session_transition_fires_exactly_once_across_a_dst_change():
    """The 09:30 NY_AM boundary on the US fall-back date (2026-11-01) - a
    fixed NY wall-clock time whose UTC instant only zoneinfo, not a fixed
    offset, gets right on the day the offset itself changes."""
    engine = _engine()
    boundary_utc = _ny(2026, 11, 1, 9, 30)

    transition_instants = [
        utc
        for utc in (boundary_utc + timedelta(minutes=offset) for offset in range(-5, 6))
        if engine.context_for(utc, _INSTRUMENT).session_transition is not None
    ]

    assert transition_instants == [boundary_utc]
