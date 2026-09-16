from datetime import UTC, datetime

from vo.market import BarRecord, SymbolRecord, TickRecord
from vo.market.deserialization import json_to_record
from vo.market.serialization import record_to_json


def test_tick_round_trip():

    original = TickRecord(
        timestamp_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        source="MT5:US100.n",

    )



    encoded = record_to_json(original)

    restored = json_to_record(encoded)



    assert restored == original





def test_bar_round_trip():

    original = BarRecord(

        timestamp=datetime(

            2026, 9, 10, 9, 35, 0, tzinfo=UTC

        ),

        open=29443.74,

        high=29449.86,

        low=29442.10,

        close=29445.86,

        tick_volume=527,

        real_volume=0,

        timeframe="M5",

        source="MT5:US100.n",

    )



    encoded = record_to_json(original)

    restored = json_to_record(encoded)



    assert restored == original





def test_symbol_round_trip():

    original = SymbolRecord(

        broker_symbol="US100.n",

        description="NASDAQ 100",

        digits=2,

        point=0.01,

        tick_size=0.01,

        tick_value=0.01,

        contract_size=1.0,

        source="MT5",

    )



    encoded = record_to_json(original)

    restored = json_to_record(encoded)



    assert restored == original

def test_tick_v2_round_trip():
    from vo.market.records import TickRecordV2

    original = TickRecordV2(
        timestamp_server_ms=1789025880000,
        bid=29445.84,
        ask=29446.61,
        last=29445.84,
        volume=1.0,
        volume_real=0.0,
        flags=6,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    encoded = record_to_json(original)
    restored = json_to_record(encoded)

    assert restored == original


def test_bar_v2_round_trip():
    from vo.market.records import BarRecordV2

    original = BarRecordV2(
        # naive: server wall-clock, no UTC claim (see schema.py / F2).
        timestamp=datetime(2026, 9, 10, 18, 49, 0),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        spread=12,
        timeframe="PERIOD_M1",
        seq=4821,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )

    encoded = record_to_json(original)
    restored = json_to_record(encoded)

    assert restored == original


def test_symbol_v2_round_trip():
    from vo.market.records import SymbolRecordV2

    original = SymbolRecordV2(
        broker_symbol="US100.n",
        description="NASDAQ 100",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        platform="MT5",
        broker_server="1xTrade-Server",
    )

    encoded = record_to_json(original)
    restored = json_to_record(encoded)

    assert restored == original


def test_source_capabilities_round_trip():
    from vo.market.records import SourceCapabilitiesRecord

    original = SourceCapabilitiesRecord(
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
        real_volume_available=False,
        tick_level_available=True,
    )

    encoded = record_to_json(original)
    restored = json_to_record(encoded)

    assert restored == original
