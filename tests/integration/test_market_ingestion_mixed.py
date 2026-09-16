from pathlib import Path

from vo.market import BarRecord
from vo.market.ingestion import read_jsonl


def test_read_jsonl_returns_canonical_records() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "mt5_bar_us100n.jsonl"
    )

    records = list(read_jsonl(fixture_path))
    assert len(records) == 1

    record = records[0]
    assert isinstance(record, BarRecord)
    assert record.open == 29132.30
    assert record.high == 29159.39
    assert record.low == 29131.81
    assert record.close == 29141.15
    assert record.tick_volume == 231
    assert record.real_volume == 0
    assert record.timeframe == "PERIOD_M1"
    assert record.source == "MT5:US100.n"

