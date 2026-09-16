"""
Vector Odyssey dashboard -- backend (scaffold).

Phase 22 in architecture/vo-phase-plan.md reads "Dashboard -- separate
process. Reads RuntimeState, writes the versioned settings schema." and is
sequenced well after the runtime/research blocks. This module is that
process pulled forward and built as its own parallel track (see the phase
plan's SS10a) rather than a violation of build order: it is architecturally
inert on its own (it only ever *reads*, matching R11's "RuntimeState
publishing is fire-and-forget" and Phase 22's own gate, "EA fully
functional with dashboard closed"), so starting it early cannot put
strategy logic in the EA or pull a phase out of sequence.

It runs today, and shows a genuinely live, genuinely honest "no EA
connected yet" state -- never a mocked number standing in for one. Real
data starts flowing the moment a real `VO_EA` process (Phase 10+) opens a
TCP connection to it and writes newline-delimited RuntimeState JSON.

Run it with:  python -m uvicorn dashboard.backend.app:app --reload
(or `python scripts/run_dashboard.py`, which wraps the same call).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.backend.ea_link import run_ea_link
from dashboard.backend.settings_store import SettingsNotYetDefinedError, SettingsStore
from dashboard.backend.state_hub import StateHub, to_json_dict

logger = logging.getLogger("vo.dashboard.app")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

EA_TELEMETRY_HOST = os.environ.get("VO_EA_TELEMETRY_HOST", "127.0.0.1")
EA_TELEMETRY_PORT = int(os.environ.get("VO_EA_TELEMETRY_PORT", "8765"))

hub = StateHub()
settings_store = SettingsStore()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(run_ea_link(hub, EA_TELEMETRY_HOST, EA_TELEMETRY_PORT))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Vector Odyssey Dashboard", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "dashboard_scope": "scaffold, see vo-phase-plan.md SS10a"}


@app.get("/api/runtime")
def runtime_state() -> dict[str, object]:
    return to_json_dict(hub.current)


@app.get("/api/settings")
def read_settings() -> dict[str, object]:
    snapshot = settings_store.read()
    return {
        "schema_version": snapshot.schema_version,
        "settings": snapshot.settings,
        "note": snapshot.note,
    }


@app.put("/api/settings")
def write_settings(payload: dict[str, object]) -> dict[str, str]:
    try:
        settings_store.write(payload)
    except SettingsNotYetDefinedError as exc:
        return {"error": str(exc)}
    return {"status": "ok"}


@app.websocket("/ws/runtime")
async def ws_runtime(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = hub.subscribe()
    try:
        await websocket.send_text(hub.current_json())
        while True:
            message = await queue.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))
