"""
EAConfig -- Phase 10's "one config" tying together the instrument, the
wire files, the broker/session config, and the telemetry/log settings a
single VO_EA process needs at startup.

Deliberately narrow: this is not the versioned VO<->EA settings schema
(SS1, still unbuilt -- see dashboard/backend/settings_store.py's own
honest placeholder and Phase 20/22 in architecture/vo-phase-plan.md).
This is the static configuration one process reads once, the same role
config/settings/brokers.yaml and sessions.yaml already play for their own
concerns, loaded the same way: yaml.safe_load, a required top-level
mapping, a typed error for anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class EAConfigError(ValueError):
    """Raised for a structurally invalid config/settings/vo_ea.yaml."""


@dataclass(frozen=True)
class WireConfig:
    dir: Path
    poll_interval_seconds: float


@dataclass(frozen=True)
class TelemetryConfig:
    host: str
    port: int


@dataclass(frozen=True)
class LoggingConfig:
    path: Path
    level: str


@dataclass(frozen=True)
class EAConfig:
    """One process's complete startup configuration."""

    broker_symbol: str
    wire: WireConfig
    brokers_path: Path
    sessions_path: Path
    telemetry: TelemetryConfig
    logging: LoggingConfig
    ea_phase: str

    def bar_wire_path(self) -> Path:
        """VO_Transport.mqh's naming convention: <broker_symbol>_bars.jsonl
        under wire.dir (see VO_Bridge.mq5's VO_OpenSink calls)."""
        return self.wire.dir / f"{self.broker_symbol}_bars.jsonl"

    def tick_wire_path(self) -> Path:
        return self.wire.dir / f"{self.broker_symbol}_ticks.jsonl"

    def meta_wire_path(self) -> Path:
        return self.wire.dir / f"{self.broker_symbol}_meta.jsonl"


def _require_mapping(raw: Any, path: str, field: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        where = f"{path}:{field}" if field else str(path)
        raise EAConfigError(f"{where}: expected a YAML mapping")
    return raw


def _require(data: dict[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise EAConfigError(f"{path}: missing required field {key!r}")
    return data[key]


def load_ea_config(path: str | Path) -> EAConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw = _require_mapping(raw, str(path))

    instrument = _require_mapping(_require(raw, "instrument", str(path)), str(path), "instrument")
    broker_symbol = str(_require(instrument, "broker_symbol", str(path)))

    wire = _require_mapping(_require(raw, "wire", str(path)), str(path), "wire")
    wire_dir = _require(wire, "dir", str(path))
    poll_interval = float(wire.get("poll_interval_seconds", 1.0))
    if poll_interval <= 0:
        raise EAConfigError(f"{path}: wire.poll_interval_seconds must be > 0")

    config_files = raw.get("config_files") or {}
    brokers_path = Path(config_files.get("brokers", "config/settings/brokers.yaml"))
    sessions_path = Path(config_files.get("sessions", "config/settings/sessions.yaml"))

    telemetry = raw.get("telemetry") or {}
    telemetry_host = str(telemetry.get("host", "127.0.0.1"))
    telemetry_port = int(telemetry.get("port", 8765))

    logging_cfg = raw.get("logging") or {}
    log_path = Path(logging_cfg.get("path", "logs/vo_ea.log"))
    log_level = str(logging_cfg.get("level", "INFO")).upper()

    ea_phase = str(raw.get("ea_phase", "10"))

    return EAConfig(
        broker_symbol=broker_symbol,
        wire=WireConfig(dir=Path(wire_dir), poll_interval_seconds=poll_interval),
        brokers_path=brokers_path,
        sessions_path=sessions_path,
        telemetry=TelemetryConfig(host=telemetry_host, port=telemetry_port),
        logging=LoggingConfig(path=log_path, level=log_level),
        ea_phase=ea_phase,
    )
