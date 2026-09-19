"""Unit tests for vo.market.economic_calendar_ingestion, the reader for
VO_CalendarBridge.mq5's snapshot file. Fabricated JSONL fixtures only --
the MQL5 side itself cannot be exercised here (no MetaEditor/Windows in
this sandbox); it carries this project's standing "not yet compiled/
verified live" caveat until the user compiles and runs it."""

from __future__ import annotations

from pathlib import Path

from vo.interfaces.economic_events import EventImportance
from vo.market.economic_calendar_ingestion import (
    read_calendar_snapshot,
)

_VALID_LINE = (
    '{"record_type":"economic_event","schema_version":1,'
    '"event_id":"NFP-2026-10","name":"Non-Farm Payrolls","currency":"USD",'
    '"importance":"HIGH","scheduled_at_utc":"2026-10-02T12:30:00",'
    '"duration_minutes":0.0,"actual_value":null,"forecast_value":null,'
    '"previous_value":null,"revision_value":null}'
)


def test_missing_file_returns_an_empty_snapshot_not_an_error(tmp_path: Path) -> None:
    result = read_calendar_snapshot(tmp_path / "does-not-exist.jsonl")

    assert result.events == ()
    assert result.quarantined == ()


def test_reads_a_valid_line_into_an_economic_event(tmp_path: Path) -> None:
    path = tmp_path / "calendar.jsonl"
    path.write_text(_VALID_LINE + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert result.quarantined == ()
    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_id == "NFP-2026-10"
    assert event.name == "Non-Farm Payrolls"
    assert event.currency == "USD"
    assert event.importance == EventImportance.HIGH
    assert event.duration_minutes == 0.0
    assert event.source == "VO_CalendarBridge.mq5"
    assert event.scheduled_at_utc.isoformat() == "2026-10-02T12:30:00+00:00"
    assert event.actual_value is None
    assert event.forecast_value is None
    assert event.previous_value is None
    assert event.revision_value is None


def test_blank_lines_are_skipped_without_being_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "calendar.jsonl"
    path.write_text(f"\n{_VALID_LINE}\n\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert len(result.events) == 1
    assert result.quarantined == ()


def test_reads_a_snapshot_as_a_whole_file_not_a_tail(tmp_path: Path) -> None:
    """Unlike JsonlTailer, calling read_calendar_snapshot twice against
    a rewritten (truncated) file must see the NEW contents in full, not
    just whatever was appended -- there is no byte-offset state to
    carry between calls."""
    path = tmp_path / "calendar.jsonl"
    path.write_text(_VALID_LINE + "\n", encoding="utf-8")

    first = read_calendar_snapshot(path)
    assert len(first.events) == 1

    other_line = _VALID_LINE.replace("NFP-2026-10", "CPI-2026-10")
    path.write_text(other_line + "\n", encoding="utf-8")

    second = read_calendar_snapshot(path)

    assert len(second.events) == 1
    assert second.events[0].event_id == "CPI-2026-10"


def test_malformed_json_is_quarantined_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "calendar.jsonl"
    path.write_text("{not valid json\n" + _VALID_LINE + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert len(result.events) == 1
    assert len(result.quarantined) == 1
    assert result.quarantined[0].line == "{not valid json"


def test_unexpected_record_type_is_quarantined(tmp_path: Path) -> None:
    line = _VALID_LINE.replace('"economic_event"', '"tick"')
    path = tmp_path / "calendar.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert result.events == ()
    assert len(result.quarantined) == 1
    assert "record_type" in result.quarantined[0].reason


def test_unsupported_schema_version_is_quarantined(tmp_path: Path) -> None:
    line = _VALID_LINE.replace('"schema_version":1', '"schema_version":99')
    path = tmp_path / "calendar.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert result.events == ()
    assert len(result.quarantined) == 1
    assert "schema_version" in result.quarantined[0].reason


def test_missing_required_field_is_quarantined(tmp_path: Path) -> None:
    line = _VALID_LINE.replace('"currency":"USD",', "")
    path = tmp_path / "calendar.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert result.events == ()
    assert len(result.quarantined) == 1


def test_invalid_importance_value_is_quarantined(tmp_path: Path) -> None:
    line = _VALID_LINE.replace('"importance":"HIGH"', '"importance":"EXTREME"')
    path = tmp_path / "calendar.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert result.events == ()
    assert len(result.quarantined) == 1


def test_populated_actual_and_forecast_values_are_parsed(tmp_path: Path) -> None:
    line = _VALID_LINE.replace('"actual_value":null', '"actual_value":180000.0').replace(
        '"forecast_value":null', '"forecast_value":175000.0'
    )
    path = tmp_path / "calendar.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert len(result.events) == 1
    assert result.events[0].actual_value == 180000.0
    assert result.events[0].forecast_value == 175000.0


def test_multiple_events_in_one_snapshot(tmp_path: Path) -> None:
    other_line = _VALID_LINE.replace("NFP-2026-10", "CPI-2026-10")
    path = tmp_path / "calendar.jsonl"
    path.write_text(_VALID_LINE + "\n" + other_line + "\n", encoding="utf-8")

    result = read_calendar_snapshot(path)

    assert len(result.events) == 2
    assert {event.event_id for event in result.events} == {"NFP-2026-10", "CPI-2026-10"}
