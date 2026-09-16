import pytest

from vo.market.ingestion import read_jsonl


def test_read_jsonl_rejects_invalid_record(tmp_path) -> None:

    fixture_path = tmp_path / "invalid.jsonl"



    fixture_path.write_text(

        '{"record_type":"bar","timestamp":"not-a-timestamp"}\n',

        encoding="utf-8",

    )



    with pytest.raises((TypeError, ValueError)):

        list(read_jsonl(fixture_path))

