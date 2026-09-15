from datetime import datetime, timezone



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

            2026, 9, 10, 9, 35, 0, tzinfo=timezone.utc

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