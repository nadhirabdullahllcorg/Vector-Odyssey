from pathlib import Path



import pytest



from vo.market.ingestion import read_jsonl





def test_read_jsonl_rejects_missing_file(tmp_path: Path) -> None:

    missing_path = tmp_path / "does_not_exist.jsonl"



    with pytest.raises(FileNotFoundError):

        list(read_jsonl(missing_path))

