from datetime import UTC, datetime

import pytest

from vo.market import Tick


def test_tick_creation():
    timestamp = datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC)

    tick = Tick(
    timestamp=timestamp,
    bid=21432.50,
    ask=21432.75,
    last=21432.50,
    volume=1.0,
    source="test",
    )

    assert tick.timestamp == timestamp
    assert tick.bid == 21432.50
    assert tick.ask == 21432.75
    assert tick.last == 21432.50
    assert tick.volume == 1.0
    assert tick.source == "test"
def test_tick_rejects_negative_volume():
    with pytest.raises(ValueError):
        Tick(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC),
            bid=21432.50,
            ask=21432.75,
            last=21432.50,
            volume=-1.0,
            source="test",
        )
def test_tick_rejects_ask_below_bid():
    with pytest.raises(ValueError):
        Tick(
            timestamp=datetime(2026, 9, 10, 7, 0, 0, tzinfo=UTC),
            bid=21432.75,
            ask=21432.50,
            last=21432.50,
            volume=1.0,
            source="test",
        )
def test_tick_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        Tick(
            timestamp=datetime(2026, 9, 10, 7, 0, 0),
            bid=21432.50,
            ask=21432.75,
            last=21432.50,
            volume=1.0,
            source="test",
        )
