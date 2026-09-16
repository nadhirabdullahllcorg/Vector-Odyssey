"""
StateHub -- the dashboard backend's single source of "what is the current
RuntimeState", and the fan-out point to every connected browser.

Deliberately tiny: one current value, one set of WebSocket subscribers, one
broadcast. Nothing here interprets RuntimeState -- it is a pass-through, the
same "diagnostics/statistics live in the dashboard, never a second opinion
on what the EA observed" discipline the phase plan already applies to chart
visualization (SS13a).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
from datetime import datetime

from vo.telemetry.state import RuntimeState, disconnected_state


def to_json_dict(state: RuntimeState) -> dict[str, object]:
    payload = dataclasses.asdict(state)
    payload["connection"] = state.connection.name

    emitted_at = payload.get("emitted_at")
    if isinstance(emitted_at, datetime):
        payload["emitted_at"] = emitted_at.isoformat()

    return payload


class StateHub:
    """Holds the latest RuntimeState and pushes updates to subscribers."""

    def __init__(self) -> None:
        self._current: RuntimeState = disconnected_state(
            detail="dashboard backend just started; no EA connection attempted yet"
        )
        self._subscribers: set[asyncio.Queue[str]] = set()

    @property
    def current(self) -> RuntimeState:
        return self._current

    def current_json(self) -> str:
        return json.dumps(to_json_dict(self._current))

    async def publish(self, state: RuntimeState) -> None:
        """Record a new state and fan it out. Never blocks on a slow
        subscriber -- each subscriber has its own bounded queue, and a full
        queue drops the oldest pending message rather than stalling the
        publisher, so one stuck browser tab can never hold up telemetry for
        every other subscriber."""

        self._current = state
        encoded = self.current_json()

        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(encoded)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)
