from datetime import UTC, datetime

import pytest

from vo.market import BarRecord, SymbolRecord, TickRecord
from vo.market.deserialization import dict_to_record, json_to_record


def test_tick_dict_to_record():

    data = {

        "record_type": "tick",

        "timestamp_ms": 1789025880000,

        "bid": 29445.84,

        "ask": 29446.61,

        "last": 29445.84,

        "volume": 1.0,

        "source": "MT5:US100.n",

    }



    record = dict_to_record(data)



    assert isinstance(record, TickRecord)

    assert record.timestamp_ms == 1789025880000

    assert record.bid == 29445.84

    assert record.source == "MT5:US100.n"





def test_bar_dict_to_record():

    data = {

        "record_type": "bar",

        "timestamp": "2026-09-10T09:35:00Z",

        "open": 29443.74,

        "high": 29449.86,

        "low": 29442.10,

        "close": 29445.86,

        "tick_volume": 527,

        "real_volume": 0,

        "timeframe": "M5",

        "source": "MT5:US100.n",

    }



    record = dict_to_record(data)



    assert isinstance(record, BarRecord)

    assert record.timestamp == datetime(2026, 9, 10, 9, 35, 0, tzinfo=UTC)

    assert record.close == 29445.86

    assert record.real_volume == 0

    assert record.timeframe == "M5"





def test_symbol_dict_to_record():

    data = {

        "record_type": "symbol",

        "broker_symbol": "US100.n",

        "description": "NASDAQ 100",

        "digits": 2,

        "point": 0.01,

        "tick_size": 0.01,

        "tick_value": 0.01,

        "contract_size": 1.0,

        "source": "MT5",

    }



    record = dict_to_record(data)



    assert isinstance(record, SymbolRecord)

    assert record.broker_symbol == "US100.n"

    assert record.digits == 2

    assert record.point == 0.01





def test_json_to_tick_record():

    value = (

        '{"record_type":"tick",'

        '"timestamp_ms":1789025880000,'

        '"bid":29445.84,'

        '"ask":29446.61,'

        '"last":29445.84,'

        '"volume":1.0,'

        '"source":"MT5:US100.n"}'

    )



    record = json_to_record(value)



    assert isinstance(record, TickRecord)

    assert record.bid == 29445.84





def test_missing_required_field_is_rejected():

    data = {

        "record_type": "tick",

        "timestamp_ms": 1789025880000,

        "bid": 29445.84,

        "ask": 29446.61,

        "last": 29445.84,

        "volume": 1.0,

    }



    with pytest.raises(ValueError):

        dict_to_record(data)





def test_unknown_record_type_is_rejected():

    data = {

        "record_type": "unknown",

    }



    with pytest.raises(ValueError):

        dict_to_record(data)
