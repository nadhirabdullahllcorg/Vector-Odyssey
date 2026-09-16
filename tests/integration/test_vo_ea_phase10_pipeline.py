"""
End-to-end Phase 10 check: a real VOEaRuntime, watching a real (temp)
wire directory, publishing over a real TCP socket to the dashboard's own,
unmodified ea_link.py client and StateHub.

architecture/vo-phase-plan.md v17's own open items list this exact gap:
"the local-TCP hop in dashboard/backend/ea_link.py has only ever seen
ConnectionRefusedError ... not the same as having proven the happy path.
Re-verify once Phase 10 exists and can actually open that socket." This
test is that re-verification -- it deliberately reuses the dashboard's
real consumer code (run_ea_link, StateHub) rather than a hand-rolled
socket client, so a wire-format mismatch between the two sides would
fail here, not just in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from dashboard.backend.ea_link import run_ea_link
from dashboard.backend.state_hub import StateHub

from vo.core.config import EAConfig, LoggingConfig, TelemetryConfig, WireConfig
from vo.telemetry.ea_runtime import VOEaRuntime
from vo.telemetry.state import ConnectionStatus
from vo.time.brokers import load_broker_profiles
from vo.time.sessions import load_session_configs

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bar_line(seq: int, timestamp: str, close: float) -> str:
    return json.dumps(
        {
            "schema_version": 2,
            "record_type": "bar",
            "timestamp": timestamp,
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "tick_volume": 100,
            "real_volume": 0,
            "spread": 10,
            "timeframe": "PERIOD_M1",
            "seq": seq,
            "source_feed": "live",
            "platform": "MT5",
            "broker_server": "1xTrade-Server",
            "broker_symbol": "US100.n",
        }
    )


def test_a_live_ea_runtime_makes_the_dashboards_ea_link_go_connected(tmp_path: Path) -> None:
    async def body() -> None:
        wire_dir = tmp_path / "wire"
        wire_dir.mkdir()

        config = EAConfig(
            broker_symbol="US100.n",
            wire=WireConfig(dir=wire_dir, poll_interval_seconds=0.05),
            brokers_path=_REPO_ROOT / "config" / "settings" / "brokers.yaml",
            sessions_path=_REPO_ROOT / "config" / "settings" / "sessions.yaml",
            telemetry=TelemetryConfig(host="127.0.0.1", port=0),
            logging=LoggingConfig(path=tmp_path / "vo_ea.log", level="INFO"),
            ea_phase="10",
        )

        runtime = VOEaRuntime(
            config,
            load_broker_profiles(config.brokers_path),
            load_session_configs(config.sessions_path),
        )

        await runtime.publisher.start()
        host, port = runtime.publisher._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]

        hub = StateHub()
        assert hub.current.connection is ConnectionStatus.DISCONNECTED

        link_task = asyncio.create_task(run_ea_link(hub, host, port))
        try:
            for _ in range(100):
                if runtime.publisher.client_count >= 1:
                    break
                await asyncio.sleep(0.02)
            assert runtime.publisher.client_count == 1, "dashboard client never connected"

            (wire_dir / "US100.n_bars.jsonl").write_text(
                _bar_line(1, "2026-09-10T18:49:00", 29141.15) + "\r\n", encoding="utf-8"
            )

            for _ in range(100):
                await runtime.poll_once()
                if hub.current.connection is ConnectionStatus.CONNECTED:
                    break
                await asyncio.sleep(0.02)

            assert hub.current.connection is ConnectionStatus.CONNECTED
            assert hub.current.ea_phase == "10"
            assert hub.current.detail is not None
            assert "bars=1" in hub.current.detail
            assert "US100.n" in hub.current.detail
        finally:
            link_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await link_task
            await runtime.publisher.stop()

    asyncio.run(asyncio.wait_for(body(), timeout=10.0))
