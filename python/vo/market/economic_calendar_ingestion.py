"""
vo.market.economic_calendar_ingestion -- reads vo.compliance.news_gate's
EconomicEvent input from the MQL5-side calendar bridge
(mql5/Experts/VectorOdyssey/Bridge/VO_CalendarBridge.mq5, built
2026-09-19 -- the user's own chosen data source: the Python
MetaTrader5 package has no native calendar access at all, but MQL5
itself does; see vo.interfaces.economic_events's own module docstring
for the full research trail and the alternatives that were ruled out).

DELIBERATELY SEPARATE from vo.market.ingestion/vo.market.schema's
canonical tick/bar/symbol pipeline (AnyRecord/json_to_record), not a
new branch of it. That pipeline is a tightly-coupled wire-schema
contract for the market-data records VO's own strategy layer trades
off of; an economic-calendar event is a different kind of fact from a
different bridge, with its own record_type and no relationship to
`Tick`/`Bar`/`SymbolInfo`. This mirrors the project's own existing
precedent of a second bridge format (the regime-feed pipeline) living
outside the canonical schema rather than being force-fit into it.

SNAPSHOT, NOT A TAIL -- the one thing that genuinely differs from
JsonlTailer's incremental read. VO_CalendarBridge.mq5 truncates and
rewrites its output file whole on every refresh (events get revised,
added, and age out of the lookahead window -- an append-only log would
accumulate stale duplicates forever), so there is no meaningful byte
offset to remember between calls. read_calendar_snapshot() is a plain,
stateless batch read of whatever the file currently contains; call it
again on whatever cadence the caller wants a fresh picture (e.g. once
per ComplianceEngine.on_snapshot cycle).

Same quarantine-not-abort discipline as vo.market.ingestion.JsonlTailer
and vo.market.mapping (E.7): one malformed line is recorded and
skipped, never allowed to abort the read of every other line in the
file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vo.interfaces.economic_events import EconomicEvent, EconomicEventError, EventImportance

_SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
_SOURCE_NAME = "VO_CalendarBridge.mq5"


@dataclass(frozen=True, slots=True)
class QuarantinedCalendarLine:
    """One line read_calendar_snapshot could not turn into an
    EconomicEvent, and why -- mirrors vo.market.ingestion.QuarantinedLine
    exactly, kept as a distinct type rather than reused because it
    belongs to a different (non-canonical) wire format."""

    line: str
    reason: str


@dataclass(frozen=True, slots=True)
class CalendarSnapshotResult:
    """One read_calendar_snapshot() call's worth of parsed events, plus
    whatever lines could not be parsed."""

    events: tuple[EconomicEvent, ...]
    quarantined: tuple[QuarantinedCalendarLine, ...]


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def _parse_line(line: str) -> EconomicEvent:
    raw = json.loads(line)

    if not isinstance(raw, dict):
        raise ValueError("calendar record must be a JSON object")

    if raw.get("record_type") != "economic_event":
        raise ValueError(f"unexpected record_type {raw.get('record_type')!r}")

    schema_version = raw.get("schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported schema_version {schema_version!r}")

    # VO_CalendarBridge.mq5 writes a bare "YYYY-MM-DDTHH:MM:SS" (no offset)
    # that fromisoformat parses as naive; treating it as UTC here is
    # deliberate and only honest once the bridge's own
    # InpCalendarUtcOffsetMinutes has actually been calibrated against a
    # known live event -- see that file's own header comment. This reader
    # does not and cannot verify that calibration; it trusts the field's
    # name.
    scheduled_at_utc = datetime.fromisoformat(str(raw["scheduled_at_utc"])).replace(tzinfo=UTC)

    return EconomicEvent(
        event_id=str(raw["event_id"]),
        name=str(raw["name"]),
        currency=str(raw["currency"]),
        scheduled_at_utc=scheduled_at_utc,
        importance=EventImportance(str(raw["importance"])),
        duration_minutes=float(raw.get("duration_minutes", 0.0)),
        source=_SOURCE_NAME,
        actual_value=_optional_float(raw.get("actual_value")),
        forecast_value=_optional_float(raw.get("forecast_value")),
        previous_value=_optional_float(raw.get("previous_value")),
        revision_value=_optional_float(raw.get("revision_value")),
    )


def read_calendar_snapshot(path: str | Path) -> CalendarSnapshotResult:
    """
    Read the CURRENT contents of the calendar bridge's snapshot file
    whole. Not a tail: the file is rewritten in full on every bridge
    refresh, so there is no byte offset to carry between calls.

    A missing file (the bridge hasn't run yet, or was never started on
    this terminal) is not an error -- it returns an empty, valid
    snapshot, matching this project's own "absence is a fact, not a
    crash" convention for optional live inputs (e.g.
    AccountState.margin_level=None, JsonlTailer on a not-yet-created
    wire file).
    """

    file_path = Path(path)

    if not file_path.exists():
        return CalendarSnapshotResult(events=(), quarantined=())

    events: list[EconomicEvent] = []
    quarantined: list[QuarantinedCalendarLine] = []

    with file_path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line:
                continue

            try:
                events.append(_parse_line(line))
            except (json.JSONDecodeError, KeyError, ValueError, EconomicEventError) as exc:
                quarantined.append(QuarantinedCalendarLine(line=line, reason=str(exc)))

    return CalendarSnapshotResult(events=tuple(events), quarantined=tuple(quarantined))
