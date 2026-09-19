"""VOEaRuntime's calendar cache -- the live-loop wiring for
VO_CalendarBridge.mq5's snapshot file (see vo.telemetry.ea_runtime's own
module docstring, "Calendar cache").

What is deliberately NOT tested here, because it deliberately does not
exist: any effect of the cached events on a decision. Nothing in
ea_runtime reads latest_calendar_events into ComplianceEngine.on_snapshot
-- that is Phase 18's wiring, and these tests assert only that a fresh
tuple is kept available for it."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vo.core.config import EAConfig, LoggingConfig, TelemetryConfig, WireConfig
from vo.telemetry.ea_runtime import VOEaRuntime
from vo.time.brokers import load_broker_profiles
from vo.time.sessions import load_session_configs

_REPO_ROOT = Path(__file__).resolve().parents[2]

_EVENT_LINE = (
    '{"record_type":"economic_event","schema_version":1,'
    '"event_id":"NFP-2026-10","name":"Non-Farm Payrolls","currency":"USD",'
    '"importance":"HIGH","scheduled_at_utc":"2026-10-02T12:30:00",'
    '"duration_minutes":0.0,"actual_value":null,"forecast_value":null,'
    '"previous_value":null,"revision_value":null}'
)


def _runtime(tmp_path: Path, *, calendar_refresh_seconds: float = 60.0) -> VOEaRuntime:
    wire_dir = tmp_path / "wire"
    wire_dir.mkdir(exist_ok=True)

    config = EAConfig(
        broker_symbol="US100.n",
        wire=WireConfig(
            dir=wire_dir,
            poll_interval_seconds=0.05,
            calendar_refresh_seconds=calendar_refresh_seconds,
        ),
        brokers_path=_REPO_ROOT / "config" / "settings" / "brokers.yaml",
        sessions_path=_REPO_ROOT / "config" / "settings" / "sessions.yaml",
        telemetry=TelemetryConfig(host="127.0.0.1", port=0),
        logging=LoggingConfig(path=tmp_path / "vo_ea.log", level="INFO"),
        ea_phase="10",
    )
    return VOEaRuntime(
        config,
        load_broker_profiles(config.brokers_path),
        load_session_configs(config.sessions_path),
    )


def test_a_missing_calendar_file_leaves_the_cache_empty_without_raising(tmp_path: Path) -> None:
    """The bridge may not have run yet on this terminal -- an absent file
    is an expected state, not a failure."""
    runtime = _runtime(tmp_path)

    runtime._refresh_calendar_if_due(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))

    assert runtime.latest_calendar_events == ()
    assert runtime.calendar_quarantine == ()


def test_refresh_reads_the_snapshot_into_the_cache(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._calendar_path.write_text(_EVENT_LINE + "\n", encoding="utf-8")

    runtime._refresh_calendar_if_due(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))

    assert len(runtime.latest_calendar_events) == 1
    assert runtime.latest_calendar_events[0].event_id == "NFP-2026-10"


def test_refresh_is_skipped_until_the_interval_has_elapsed(tmp_path: Path) -> None:
    """The file is rewritten on the bridge's own timer, so re-reading it
    on every 1-second poll would be pure waste."""
    runtime = _runtime(tmp_path, calendar_refresh_seconds=60.0)
    start = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    runtime._calendar_path.write_text(_EVENT_LINE + "\n", encoding="utf-8")
    runtime._refresh_calendar_if_due(start)
    assert len(runtime.latest_calendar_events) == 1

    # The bridge rewrites the file, but not enough time has passed.
    runtime._calendar_path.write_text("", encoding="utf-8")
    runtime._refresh_calendar_if_due(start + timedelta(seconds=59))
    assert len(runtime.latest_calendar_events) == 1, "re-read before the interval elapsed"

    # Past the interval, the new (now empty) snapshot is picked up.
    runtime._refresh_calendar_if_due(start + timedelta(seconds=61))
    assert runtime.latest_calendar_events == ()


def test_a_rewritten_snapshot_replaces_the_cache_rather_than_accumulating(
    tmp_path: Path,
) -> None:
    """Snapshot semantics, not tail semantics: the bridge truncates and
    rewrites, so a stale event must not survive into the next read."""
    runtime = _runtime(tmp_path, calendar_refresh_seconds=0.0001)
    start = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    runtime._calendar_path.write_text(_EVENT_LINE + "\n", encoding="utf-8")
    runtime._refresh_calendar_if_due(start)

    replaced = _EVENT_LINE.replace("NFP-2026-10", "CPI-2026-10")
    runtime._calendar_path.write_text(replaced + "\n", encoding="utf-8")
    runtime._refresh_calendar_if_due(start + timedelta(seconds=1))

    assert len(runtime.latest_calendar_events) == 1
    assert runtime.latest_calendar_events[0].event_id == "CPI-2026-10"


def test_a_malformed_line_is_quarantined_on_the_runtime_not_raised(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._calendar_path.write_text(
        "{not json\n" + _EVENT_LINE + "\n", encoding="utf-8"
    )

    runtime._refresh_calendar_if_due(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))

    assert len(runtime.latest_calendar_events) == 1
    assert len(runtime.calendar_quarantine) == 1


def test_poll_once_refreshes_the_calendar_even_with_no_new_wire_lines(tmp_path: Path) -> None:
    """The calendar file has its own cadence -- it must not depend on a
    bar or tick having arrived."""
    runtime = _runtime(tmp_path)
    runtime._calendar_path.write_text(_EVENT_LINE + "\n", encoding="utf-8")

    async def body() -> None:
        new_count = await runtime.poll_once()
        assert new_count == 0, "no wire lines were written in this test"

    asyncio.run(body())

    assert len(runtime.latest_calendar_events) == 1
