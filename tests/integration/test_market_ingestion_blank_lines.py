from pathlib import Path

from vo.market import BarRecord
from vo.market.ingestion import read_jsonl


def test_read_jsonl_ignores_blank_lines() -> None:

    fixture_path = (

        Path(__file__).resolve().parents[1]

        / "fixtures"

        / "mt5_bar_blank_lines.jsonl"

    )



    records = list(read_jsonl(fixture_path))



    assert len(records) == 1

    assert isinstance(records[0], BarRecord)

    assert records[0].source == "MT5:US100.n"

    assert records[0].timeframe == "PERIOD_M1"