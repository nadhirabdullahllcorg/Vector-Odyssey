from datetime import UTC, datetime

from vo.market import BarRecord, SymbolRecord, TickRecord


def test_tick_record_creation():

    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",
    )
    
    assert record.timestamp_ms == 1789025880000
    assert record.bid == 29445.84
    assert record.ask == 29446.61
    assert record.last == 29445.84
    assert record.volume == 1.0
    assert record.source == "MT5:US100.n"

def test_bar_record_creation():
    record = BarRecord(
    timestamp=datetime(2026, 9, 10, 9, 35, 0, tzinfo=UTC),
    open=29443.74,
    high=29449.86,
    low=29442.10,
    close=29445.86,
    tick_volume=527,
    real_volume=0,
    timeframe="M5",
    source="MT5:US100.n",
    )

    assert record.open == 29443.74
    assert record.high == 29449.86
    assert record.low == 29442.10
    assert record.close == 29445.86
    assert record.tick_volume == 527
    assert record.real_volume == 0
    assert record.timeframe == "M5"
    assert record.source == "MT5:US100.n"

def test_symbol_record_creation():
    record = SymbolRecord(
    broker_symbol="US100.n",
    description="NASDAQ 100",
    digits=2,
    point=0.01,
    tick_size=0.01,
    tick_value=0.01,
    contract_size=1.0,
    source="MT5",
    )

    assert record.broker_symbol == "US100.n"
    assert record.description == "NASDAQ 100"
    assert record.digits == 2
    assert record.point == 0.01
    assert record.tick_size == 0.01
    assert record.tick_value == 0.01
    assert record.contract_size == 1.0
    assert record.source == "MT5"