"""
RuntimeStatePublisher -- the EA-side half of the TCP contract
dashboard/backend/ea_link.py already speaks against.

ea_link.py is explicit about who is the server: "The EA process is the
server (it must be able to run 'fully functional with the dashboard
closed' ... so it cannot depend on a dashboard being there to connect
to); this dashboard backend is the client." This module is that server:
a loopback-only asyncio TCP listener that accepts any number of
dashboard connections and pushes one newline-delimited RuntimeState JSON
line per publish() call to every connected one.

Fire-and-forget, per the architecture audit's R11 mitigation
("RuntimeState publishing is fire-and-forget"): a slow or gone client is
dropped rather than allowed to block a publish meant for everyone else,
and a publish with zero connected clients is a normal, silent no-op --
"nobody is listening yet" is an expected, ordinary state, not an error.
"""

from __future__ import annotations

import asyncio
import json
import logging

from vo.telemetry.state import RuntimeState

logger = logging.getLogger("vo_ea.telemetry")


def _state_to_wire(state: RuntimeState) -> str:
    """
    Matches exactly what dashboard/backend/ea_link.py::_parse_line reads:
    schema_version (required), ea_phase and detail (both optional via
    .get). connection/emitted_at are deliberately NOT sent -- the
    receiving side stamps CONNECTED and its own receipt time itself,
    precisely so a stale clock on this process can never be mistaken for
    a live one on that side.
    """
    payload = {
        "schema_version": state.schema_version,
        "ea_phase": state.ea_phase,
        "detail": state.detail,
    }
    return json.dumps(payload)


class RuntimeStatePublisher:
    """Owns the TCP listener. start(), then publish(state) as often as
    the pipeline produces a new one, then stop() on shutdown."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def client_count(self) -> int:
        return len(self._writers)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        logger.info("telemetry publisher listening on %s:%s", self._host, self._port)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("dashboard connected: %s", peer)
        self._writers.add(writer)
        try:
            # Publish-only connection: nothing is expected from the client
            # except EOF/disconnect, which read() surfaces as b"".
            await reader.read()
        except (ConnectionError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            logger.info("dashboard disconnected: %s", peer)

    async def publish(self, state: RuntimeState) -> None:
        if not self._writers:
            return

        line = (_state_to_wire(state) + "\n").encode("utf-8")
        dead: list[asyncio.StreamWriter] = []

        for writer in self._writers:
            try:
                writer.write(line)
                await asyncio.wait_for(writer.drain(), timeout=1.0)
            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.warning("dropping unresponsive dashboard client: %s", exc)
                dead.append(writer)

        for writer in dead:
            self._writers.discard(writer)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
