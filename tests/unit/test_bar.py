from datetime import datetime, timezone

import pytest

from vo.market import Bar


def test_bar_creation():
    timestamp = datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc)

    bar = Bar(
        timestamp=timestamp,
        open=21430.00,
        high=21440.00,
        low=21425.00,
        close=21437.50,
        tick_volume=1500,
        real_volume=0,
        timeframe="M1",
    )

    assert bar.timestamp == timestamp
    assert bar.open == 21430.00
    assert bar.high == 21440.00
    assert bar.low == 21425.00
    assert bar.close == 21437.50
    assert bar.tick_volume == 1500
    assert bar.real_volume == 0
    assert bar.timeframe == "M1"


def test_bar_rejects_high_below_low():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21430.00,
            high=21420.00,
            low=21425.00,
            close=21427.50,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )


def test_bar_rejects_open_above_high():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21450.00,
            high=21440.00,
            low=21425.00,
            close=21437.50,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_open_below_low():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21420.00,
            high=21440.00,
            low=21425.00,
            close=21437.50,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_close_above_high():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21430.00,
            high=21440.00,
            low=21425.00,
            close=21450.00,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_close_below_low():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21430.00,
            high=21440.00,
            low=21425.00,
            close=21420.00,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0),
            open=21430.00,
            high=21440.00,
            low=21425.00,
            close=21437.50,
            tick_volume=1500,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_negative_tick_volume():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21430.00,
            high=21440.00,
            low=21425.00,
            close=21437.50,
            tick_volume=-1,
            real_volume=0,
            timeframe="M1",
        )
def test_bar_rejects_empty_timeframe():
    with pytest.raises(ValueError):
        Bar(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=timezone.utc),
            open=21430.00,
            high=21440.00,
            low=21425.00,
            close=21437.50,
            tick_volume=1500,
            real_volume=0,
            timeframe="",
        )