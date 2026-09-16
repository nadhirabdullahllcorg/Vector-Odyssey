"""
JsonlTailer -- Phase 10's live "tail -f" reader, alongside read_jsonl's
existing batch reader.
"""

from __future__ import annotations

import json
from pathlib import Path

from vo.market.ingestion import JsonlTailer

_TICK = {
    "record_type": "tick",
    "timestamp_ms": 1_700_000_000_000,
    "bid": 100.0,
    "ask": 100.2,
    "last": 100.1,
    "volume": 1.0,
    "source": "MT5:US100.n",
}


def _line(**overrides) -> str:
    payload = dict(_TICK)
    payload.update(overrides)
    return json.dumps(payload)


def test_poll_on_a_file_that_does_not_exist_yet_returns_empty(tmp_path: Path) -> None:
    tailer = JsonlTailer(tmp_path / "does_not_exist.jsonl")

    result = tailer.poll()

    assert result.records == ()
    assert result.quarantined == ()
    assert tailer.offset == 0


def test_poll_reads_only_newly_appended_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "US100.n_ticks.jsonl"
    path.write_text(_line() + "\r\n", encoding="utf-8")

    tailer = JsonlTailer(path)
    first = tailer.poll()
    assert len(first.records) == 1
    assert first.quarantined == ()

    # Nothing new since the last poll.
    second = tailer.poll()
    assert second.records == ()
    assert second.quarantined == ()

    with path.open("a", encoding="utf-8") as file:
        file.write(_line(volume=2.0) + "\r\n")

    third = tailer.poll()
    assert len(third.records) == 1
    assert third.records[0].volume == 2.0


def test_a_trailing_partial_line_is_not_parsed_until_completed(tmp_path: Path) -> None:
    path = tmp_path / "US100.n_ticks.jsonl"
    path.write_text(_line() + "\r\n", encoding="utf-8")

    tailer = JsonlTailer(path)
    tailer.poll()

    # Simulate VO_Transport.mqh mid-write: bytes appended, no trailing
    # newline yet.
    partial = _line(volume=3.0)
    with path.open("a", encoding="utf-8") as file:
        file.write(partial[: len(partial) // 2])

    mid_write = tailer.poll()
    assert mid_write.records == ()
    assert mid_write.quarantined == ()

    with path.open("a", encoding="utf-8") as file:
        file.write(partial[len(partial) // 2 :] + "\r\n")

    completed = tailer.poll()
    assert len(completed.records) == 1
    assert completed.records[0].volume == 3.0


def test_a_malformed_line_is_quarantined_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "US100.n_ticks.jsonl"
    path.write_text("{not valid json\r\n" + _line() + "\r\n", encoding="utf-8")

    result = JsonlTailer(path).poll()

    assert len(result.records) == 1
    assert len(result.quarantined) == 1
    assert "not valid json" in result.quarantined[0].line


def test_a_shrunk_file_resets_the_offset_instead_of_reading_garbage(tmp_path: Path) -> None:
    path = tmp_path / "US100.n_ticks.jsonl"
    path.write_text(_line() + "\r\n" + _line(volume=2.0) + "\r\n", encoding="utf-8")

    tailer = JsonlTailer(path)
    tailer.poll()
    assert tailer.offset > 0

    # File rotated/truncated by something else since the last poll.
    path.write_text(_line(volume=9.0) + "\r\n", encoding="utf-8")

    result = tailer.poll()
    assert len(result.records) == 1
    assert result.records[0].volume == 9.0
