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
from datetime import time
from pathlib import Path
from typing import Any

import yaml


class EAConfigError(ValueError):
    """Raised for a structurally invalid config/settings/vo_ea.yaml."""


@dataclass(frozen=True)
class WireConfig:
    dir: Path
    poll_interval_seconds: float
    # How often VOEaRuntime re-reads the calendar bridge's snapshot file
    # (vo.market.economic_calendar_ingestion.read_calendar_snapshot) --
    # independent of poll_interval_seconds, since the calendar file is
    # rewritten whole on its own cadence (VO_CalendarBridge.mq5's own
    # InpRefreshMinutes), not appended to on every tick/bar like the
    # price wire files. Additive field with a default so every existing
    # WireConfig(...) call site (tests included) keeps working unchanged.
    calendar_refresh_seconds: float = 60.0


@dataclass(frozen=True)
class LiveTradingConfig:
    """Everything a process needs to place real orders -- and the flag
    that says it may.

    `enabled` is never inferred. There is no "enabled if a terminal is
    reachable" and no enabled-by-presence-of-a-file: a process trades
    because someone wrote enabled: true and meant it. Every other field
    here is inert while it is false.
    """

    enabled: bool
    execution_config_path: Path
    compliance_config_path: Path
    trading_day_opens: time
    news_gate_config_path: Path | None = None
    compliance_state_path: Path | None = None
    preflight_volume: float = 0.01
    preflight_stop_distance: float = 50.0
    preflight_target_distance: float = 100.0
    preflight_trail_improvement: float = 20.0

    def __post_init__(self) -> None:
        if self.preflight_volume <= 0:
            raise EAConfigError(
                f"preflight_volume must be > 0, got {self.preflight_volume}"
            )
        if self.preflight_stop_distance <= 0 or self.preflight_target_distance <= 0:
            raise EAConfigError("preflight stop/target distances must be > 0")
        if self.preflight_trail_improvement <= 0:
            raise EAConfigError(
                "preflight_trail_improvement must be > 0 -- a trail that does not "
                "tighten the stop proves nothing"
            )


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
    live_trading: LiveTradingConfig | None = None

    def bar_wire_path(self) -> Path:
        """VO_Transport.mqh's naming convention: <broker_symbol>_bars.jsonl
        under wire.dir (see VO_Bridge.mq5's VO_OpenSink calls)."""
        return self.wire.dir / f"{self.broker_symbol}_bars.jsonl"

    def tick_wire_path(self) -> Path:
        return self.wire.dir / f"{self.broker_symbol}_ticks.jsonl"

    def meta_wire_path(self) -> Path:
        return self.wire.dir / f"{self.broker_symbol}_meta.jsonl"

    def calendar_wire_path(self) -> Path:
        """VO_CalendarBridge.mq5's default output path
        (InpOutputSubdir/InpOutputFilename). Unlike the other three wire
        files, this one is NOT broker_symbol-scoped -- MT5's economic
        calendar is account/terminal-wide, not per-instrument -- so the
        filename is fixed rather than built from self.broker_symbol."""
        return self.wire.dir / "calendar.jsonl"


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

    calendar_refresh = float(wire.get("calendar_refresh_seconds", 60.0))
    if calendar_refresh <= 0:
        raise EAConfigError(f"{path}: wire.calendar_refresh_seconds must be > 0")

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

    live_raw = raw.get("live_trading") or {}
    live_trading: LiveTradingConfig | None = None
    if live_raw:
        opens_text = str(live_raw.get("trading_day_opens", "18:00"))
        try:
            opens_hour, opens_minute = (int(part) for part in opens_text.split(":"))
        except ValueError as exc:
            raise EAConfigError(
                f"{path}: live_trading.trading_day_opens must be HH:MM, got {opens_text!r}"
            ) from exc

        live_trading = LiveTradingConfig(
            enabled=bool(live_raw.get("enabled", False)),
            execution_config_path=Path(
                live_raw.get("execution_config", "config/settings/execution.yaml")
            ),
            compliance_config_path=Path(
                live_raw.get("compliance_config", "config/settings/compliance_live.yaml")
            ),
            trading_day_opens=time(opens_hour, opens_minute),
            news_gate_config_path=(
                Path(live_raw["news_gate_config"])
                if live_raw.get("news_gate_config")
                else None
            ),
            compliance_state_path=(
                Path(live_raw["compliance_state"])
                if live_raw.get("compliance_state")
                else None
            ),
            preflight_volume=float(live_raw.get("preflight_volume", 0.01)),
            preflight_stop_distance=float(live_raw.get("preflight_stop_distance", 50.0)),
            preflight_target_distance=float(
                live_raw.get("preflight_target_distance", 100.0)
            ),
            preflight_trail_improvement=float(
                live_raw.get("preflight_trail_improvement", 20.0)
            ),
        )

    return EAConfig(
        broker_symbol=broker_symbol,
        wire=WireConfig(
            dir=Path(wire_dir),
            poll_interval_seconds=poll_interval,
            calendar_refresh_seconds=calendar_refresh,
        ),
        brokers_path=brokers_path,
        sessions_path=sessions_path,
        telemetry=TelemetryConfig(host=telemetry_host, port=telemetry_port),
        logging=LoggingConfig(path=log_path, level=log_level),
        ea_phase=ea_phase,
        live_trading=live_trading,
    )
