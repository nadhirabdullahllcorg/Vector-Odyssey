"""
EaLink -- the dashboard's one connection out to a real EA process.

Transport, per your choice over a file-based one: a local TCP socket,
loopback only (127.0.0.1), carrying newline-delimited JSON -- one
RuntimeState per line. The EA process is the server (it must be able to run
"fully functional with the dashboard closed", per Phase 22's gate, so it
cannot depend on a dashboard being there to connect to); this dashboard
backend is the client, and it is written to make that connection attempt
fail constantly and safely, because until Phase 10 (`VO_EA` v1) exists there
is nothing listening on the other end at all.

No line of this file invents a RuntimeState. On any failure to connect, or
any failure to parse a line, or a clean disconnect, the hub is told
DISCONNECTED with a plain-language reason -- never silently reused as
"probably still fine" and never replaced with a plausible-looking guess.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from dashboard.backend.state_hub import StateHub
from vo.telemetry.state import ConnectionStatus, RuntimeState, disconnected_state

logger = logging.getLogger("vo.dashboard.ea_link")

RETRY_INTERVAL_SECONDS = 5.0
STALE_AFTER_SECONDS = 15.0


def _parse_line(raw: str) -> RuntimeState:
    """Parse one NDJSON line into a RuntimeState. Deliberately strict --
    a malformed line is a bug to surface, never a value to guess past."""

    payload = json.loads(raw)

    return RuntimeState(
        schema_version=int(payload["schema_version"]),
        connection=ConnectionStatus.CONNECTED,
        emitted_at=datetime.now(UTC),
        ea_phase=payload.get("ea_phase"),
        detail=payload.get("detail"),
    )


async def run_ea_link(hub: StateHub, host: str, port: int) -> None:
    """
    Runs forever: connect, stream lines into the hub, and on any failure
    report DISCONNECTED and retry after RETRY_INTERVAL_SECONDS. Intended to
    be launched once as a background task at backend startup.
    """

    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except (ConnectionRefusedError, OSError) as exc:
            await hub.publish(
                disconnected_state(
                    detail=f"no EA listening on {host}:{port} ({exc}) -- "
                    "expected until Phase 10 (VO_EA v1) exists"
                )
            )
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
            continue

        logger.info("connected to EA telemetry publisher at %s:%s", host, port)

        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(), timeout=STALE_AFTER_SECONDS
                    )
                except TimeoutError:
                    await hub.publish(
                        disconnected_state(
                            detail=f"no update from {host}:{port} in "
                            f"{STALE_AFTER_SECONDS:.0f}s"
                        )
                    )
                    continue

                if not line:
                    break

                text = line.decode("utf-8").strip()
                if not text:
                    continue

                try:
                    state = _parse_line(text)
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    logger.warning("dropping unparseable RuntimeState line: %s", exc)
                    continue

                await hub.publish(state)
        finally:
            writer.close()
            await hub.publish(
                disconnected_state(detail=f"EA connection to {host}:{port} closed")
            )

        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
