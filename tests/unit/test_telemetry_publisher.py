"""
RuntimeStatePublisher -- the EA-side TCP server dashboard/backend/
ea_link.py's client has, until now, only ever seen refuse the connection
(ConnectionRefusedError). This is the first time anything in this repo
actually opens that socket and sends a real line across it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from vo.telemetry.publisher import RuntimeStatePublisher
from vo.telemetry.state import ConnectionStatus, RuntimeState

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


async def _run(coro):
    return await asyncio.wait_for(coro, timeout=5.0)


def _state(**overrides) -> RuntimeState:
    from datetime import UTC, datetime

    fields = dict(
        schema_version=0,
        connection=ConnectionStatus.CONNECTED,
        emitted_at=datetime.now(UTC),
        ea_phase="10",
        detail="bars=1",
    )
    fields.update(overrides)
    return RuntimeState(**fields)


def test_publish_with_no_clients_is_a_silent_no_op() -> None:
    async def body() -> None:
        publisher = RuntimeStatePublisher("127.0.0.1", 0)
        await publisher.start()
        try:
            assert publisher.client_count == 0
            await publisher.publish(_state())  # must not raise
        finally:
            await publisher.stop()

    asyncio.run(_run(body()))


def test_a_connected_client_receives_one_ndjson_line_per_publish() -> None:
    async def body() -> None:
        publisher = RuntimeStatePublisher("127.0.0.1", 0)
        await publisher.start()
        host, port = publisher._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]

        reader, writer = await asyncio.open_connection(host, port)
        try:
            await asyncio.sleep(0.05)  # let the server register the connection
            assert publisher.client_count == 1

            await publisher.publish(_state(detail="bars=42"))
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            payload = json.loads(line.decode("utf-8"))

            assert payload["schema_version"] == 0
            assert payload["ea_phase"] == "10"
            assert payload["detail"] == "bars=42"
            assert "connection" not in payload
            assert "emitted_at" not in payload
        finally:
            writer.close()
            await publisher.stop()

    asyncio.run(_run(body()))


def test_multiple_clients_each_receive_the_same_publish() -> None:
    async def body() -> None:
        publisher = RuntimeStatePublisher("127.0.0.1", 0)
        await publisher.start()
        host, port = publisher._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]

        reader1, writer1 = await asyncio.open_connection(host, port)
        reader2, writer2 = await asyncio.open_connection(host, port)
        try:
            await asyncio.sleep(0.05)
            assert publisher.client_count == 2

            await publisher.publish(_state(detail="both"))

            line1 = await asyncio.wait_for(reader1.readline(), timeout=2.0)
            line2 = await asyncio.wait_for(reader2.readline(), timeout=2.0)
            assert json.loads(line1)["detail"] == "both"
            assert json.loads(line2)["detail"] == "both"
        finally:
            writer1.close()
            writer2.close()
            await publisher.stop()

    asyncio.run(_run(body()))


def test_a_disconnected_client_is_dropped_and_does_not_break_future_publishes() -> None:
    async def body() -> None:
        publisher = RuntimeStatePublisher("127.0.0.1", 0)
        await publisher.start()
        host, port = publisher._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]

        _reader, writer = await asyncio.open_connection(host, port)
        await asyncio.sleep(0.05)
        assert publisher.client_count == 1

        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)

        # Must not raise even though the client is gone.
        await publisher.publish(_state(detail="after disconnect"))
        await asyncio.sleep(0.05)
        assert publisher.client_count == 0

        await publisher.stop()

    asyncio.run(_run(body()))
