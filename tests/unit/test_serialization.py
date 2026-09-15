from datetime import datetime, timezone

from vo.market import BarRecord, SymbolRecord, TickRecord
from vo.market.serialization import record_to_dict, record_to_json


def test_tick_record_to_dict():
    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",
    )

    result = record_to_dict(record)

    assert result["record_type"] == "tick"
    assert result["timestamp_ms"] == 1789025880000
    assert result["bid"] == 29445.84
    assert result["ask"] == 29446.61
    assert result["source"] == "MT5:US100.n"


def test_bar_record_to_dict():
    record = BarRecord(
        timestamp=datetime(2026, 9, 10, 9, 35, 0, tzinfo=timezone.utc),
        open=29443.74,
        high=29449.86,
        low=29442.10,
        close=29445.86,
        tick_volume=527,
        real_volume=0,
        timeframe="M5",
        source="MT5:US100.n",
    )

    result = record_to_dict(record)

    assert result["record_type"] == "bar"
    assert result["timestamp"] == "2026-09-10T09:35:00Z"
    assert result["open"] == 29443.74
    assert result["high"] == 29449.86
    assert result["low"] == 29442.10
    assert result["close"] == 29445.86
    assert result["tick_volume"] == 527
    assert result["real_volume"] == 0
    assert result["timeframe"] == "M5"
    assert result["source"] == "MT5:US100.n"


def test_symbol_record_to_dict():
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

    result = record_to_dict(record)

    assert result["record_type"] == "symbol"
    assert result["broker_symbol"] == "US100.n"
    assert result["description"] == "NASDAQ 100"
    assert result["digits"] == 2
    assert result["point"] == 0.01
    assert result["source"] == "MT5"


def test_record_to_json_produces_valid_json():
    record = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",
    )

    result = record_to_json(record)

    assert '"record_type":"tick"' in result
    assert '"timestamp_ms":1789025880000' in result