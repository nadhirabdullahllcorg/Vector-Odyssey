from pathlib import Path

from vo.market import SymbolRecord
from vo.market.deserialization import json_to_record


def test_mt5_symbol_fixture_deserializes() -> None:

    fixture_path = (

        Path(__file__).resolve().parents[1]

        / "fixtures"

        / "mt5_symbol_us100n.jsonl"

    )



    line = fixture_path.read_text(encoding="utf-8").strip()



    record = json_to_record(line)



    assert isinstance(record, SymbolRecord)

    assert record.broker_symbol == "US100.n"

    assert record.description == "E-mini Nasdaq 100/spot"

    assert record.digits == 2

    assert record.point == 0.01

    assert record.tick_size == 0.01

    assert record.tick_value == 0.01

    assert record.contract_size == 1.0

    assert record.source == "MT5"