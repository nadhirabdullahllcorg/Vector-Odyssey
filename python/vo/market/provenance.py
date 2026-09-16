"""
Provenance — what a Bar's timestamp is actually built from.

architecture/vo-candle-layer.md §2 lists `provenance: Provenance` on Bar as
"raw broker ts, offset, tz, schema_version". Phase 5 deferred it entirely:
`resolved_utc_offset_hours`/`broker_tz_calendar` cannot be filled in
honestly before the Time Engine (Phase 6) exists to resolve them.

Phase 6 can now populate it, but Bar itself still cannot import anything
from vo.time (layer 2) — vo.market is layer 1, and the architecture test
forbids upward imports even for an Optional field. So this stays a plain
vo.market value type with no dependency on vo.time's richer types: where
vo.time needs to record which DST calendar applied, it writes the calendar
name as a plain string ("US"/"EU"/"NONE"), not the vo.time.probe.DstCalendar
enum itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    schema_version: int
    platform: str
    broker_server: str
    raw_broker_timestamp: str
    """The wire timestamp exactly as received, before any conversion —
    kept for traceability even after resolution."""
    resolved_utc_offset_hours: float | None = None
    """None when the timestamp was already UTC on the wire (schema v1) or
    has not been resolved yet."""
    broker_tz_calendar: str | None = None
    """"US" / "EU" / "NONE" — which DST calendar the broker was resolved to
    follow, or None when not yet known."""
