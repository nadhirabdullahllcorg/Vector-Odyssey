"""
swing_reference_distances -- Phase 11 follow-up: a swing's distance to
the previous day/week/month's H/L/O/C, purely arithmetic, no strategy
interpretation (see vo.observation.swing_reference's module docstring).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.sequence import BarSequence
from vo.market.timeframe import Timeframe
from vo.observation.swing_reference import (
    LevelPosition,
    swing_reference_distances,
)
from vo.observation.swings import OBJECT_TYPE_SWING, SwingLevel, SwingPoint, SwingStatus, SwingType
from vo.time.engine import VOTimeEngine
from vo.time.levels import ReferenceLevelEngine
from vo.time.sessions import load_session_configs

_NY = ZoneInfo("America/New_York")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")


def _ny(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY).astimezone(UTC)


def _bar(ny_dt: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=ny_dt,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=10,
        real_volume=0,
    )


def _day_bars(trading_day: date, base: float) -> list[Bar]:
    next_day = date.fromordinal(trading_day.toordinal() + 1)
    return [
        _bar(
            _ny(trading_day.year, trading_day.month, trading_day.day, 18, 0),
            base, base + 1, base - 1, base + 0.5,
        ),
        _bar(
            _ny(next_day.year, next_day.month, next_day.day, 9, 30),
            base + 2, base + 3, base + 1, base + 2.5,
        ),
        _bar(
            _ny(next_day.year, next_day.month, next_day.day, 16, 13),
            base + 3, base + 5, base + 2, base + 4,
        ),
        _bar(
            _ny(next_day.year, next_day.month, next_day.day, 17, 59),
            base + 4, base + 6, base + 3, base + 5,
        ),
    ]


_DAY0 = date(2026, 6, 26)  # Friday
_DAY1 = date(2026, 6, 29)  # Monday, following week


def _engine_and_sequence() -> tuple[ReferenceLevelEngine, BarSequence]:
    bars = [*_day_bars(_DAY0, 100.0), *_day_bars(_DAY1, 200.0)]
    sequence = BarSequence()
    for bar in sorted(bars, key=lambda b: b.open_time_utc):
        sequence = sequence.append(bar)

    configs = load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")
    engine = ReferenceLevelEngine(VOTimeEngine(configs), sequence)
    return engine, sequence


def _swing(*, swing_type: SwingType, price: float) -> SwingPoint:
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    return SwingPoint(
        object_type=OBJECT_TYPE_SWING,
        object_id="TEST-SWING-1",
        observed_at=now,
        recorded_at=now,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        level=SwingLevel.INTERNAL,
        swing_type=swing_type,
        status=SwingStatus.CONFIRMED,
        price=price,
        pivot_bar_id="pivot",
        confirmed_at_bar_id="confirm",
        reversal_ticks=1,
        atr_ticks_at_pivot=1,
    )


# Day 0 (base=100) aggregate OHLC: open=100 (bar1), close=105 (bar4),
# high=106 (max of 101/103/105/106), low=99 (min of 99/101/102/103).
_PDH, _PDL, _PDO, _PDC = 106.0, 99.0, 100.0, 105.0


def test_previous_day_distances_are_exact_and_signed():
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.HIGH, price=103.0)

    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert context.swing_object_id == swing.object_id
    assert context.for_level("PDH") is not None
    assert context.for_level("PDH").price == _PDH
    assert context.for_level("PDH").distance_ticks == 3  # 106 - 103
    assert context.for_level("PDL").distance_ticks == -4  # 99 - 103
    assert context.for_level("PDO").distance_ticks == -3  # 100 - 103
    assert context.for_level("PDC").distance_ticks == 2  # 105 - 103


def test_position_for_a_high_swing_is_ahead_above_behind_below():
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.HIGH, price=103.0)
    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert context.for_level("PDH").position is LevelPosition.AHEAD  # 106 > 103
    assert context.for_level("PDC").position is LevelPosition.AHEAD  # 105 > 103
    assert context.for_level("PDL").position is LevelPosition.BEHIND  # 99 < 103
    assert context.for_level("PDO").position is LevelPosition.BEHIND  # 100 < 103


def test_position_for_a_low_swing_is_mirrored():
    """Same prices, a LOW swing instead -- AHEAD/BEHIND flip because the
    swing's own directional sense flips (down, not up)."""
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.LOW, price=103.0)
    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert context.for_level("PDH").position is LevelPosition.BEHIND
    assert context.for_level("PDC").position is LevelPosition.BEHIND
    assert context.for_level("PDL").position is LevelPosition.AHEAD
    assert context.for_level("PDO").position is LevelPosition.AHEAD


def test_position_is_at_on_an_exact_tick_tie():
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.HIGH, price=_PDH)
    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert context.for_level("PDH").distance_ticks == 0
    assert context.for_level("PDH").position is LevelPosition.AT


def test_missing_previous_month_is_omitted_not_guessed():
    """Both trading days fall in June 2026 -- there is no completed
    previous month in this fixture, so PM* entries are simply absent."""
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.HIGH, price=103.0)
    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert context.for_level("PMH") is None
    assert context.for_level("PML") is None
    assert context.for_level("PMO") is None
    assert context.for_level("PMC") is None


def test_first_ever_trading_day_yields_no_distances_at_all():
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=SwingType.HIGH, price=103.0)

    context = swing_reference_distances(swing, engine, _DAY0, tick_size=1.0)

    assert context.distances == ()


@pytest.mark.parametrize("swing_type", [SwingType.HIGH, SwingType.LOW])
def test_arithmetic_holds_for_every_entry_regardless_of_grouping(swing_type: SwingType):
    """A general, grouping-agnostic sanity check: whatever periods do
    resolve (PD/PW/PM), the returned distance/position must always be
    internally consistent -- this does not re-verify ReferenceLevelEngine's
    own grouping correctness (test_levels.py's job), only this module's
    arithmetic on top of it."""
    engine, _ = _engine_and_sequence()
    swing = _swing(swing_type=swing_type, price=103.0)
    context = swing_reference_distances(swing, engine, _DAY1, tick_size=1.0)

    assert len(context.distances) > 0

    for entry in context.distances:
        assert entry.distance_ticks == round(entry.price - swing.price)

        if entry.distance_ticks == 0:
            assert entry.position is LevelPosition.AT
        elif swing_type is SwingType.HIGH:
            expected = LevelPosition.AHEAD if entry.distance_ticks > 0 else LevelPosition.BEHIND
            assert entry.position is expected
        else:
            expected = LevelPosition.AHEAD if entry.distance_ticks < 0 else LevelPosition.BEHIND
            assert entry.position is expected
