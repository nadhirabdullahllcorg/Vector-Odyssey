"""
Session model — per instrument, configured, never hardcoded (architecture/
vo-time-engine.md §4).

RTH is a property of the instrument, not of the world: a CFD, the
underlying index and a futures contract on "the same" market can each have
a different session structure. So sessions live in versioned YAML keyed by
instrument (config/settings/sessions.yaml), never as constants in code.

Session transitions are temporal events, nothing more (§4): `ASIA ->
LONDON` is a clock fact. Whatever happens to price at the same moment is a
separate observation for a much later layer to correlate — nothing here
decides that they are related.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml


class SessionConfigError(ValueError):
    pass


def _parse_hhmm(value: str) -> time:
    hour_str, minute_str = value.split(":")
    return time(int(hour_str), int(minute_str))


@dataclass(frozen=True)
class SessionWindow:
    name: str
    start: time
    end: time
    """May be < start, meaning the window wraps past midnight."""

    def contains(self, wall_clock: time) -> bool:
        if self.start <= self.end:
            return self.start <= wall_clock < self.end
        return wall_clock >= self.start or wall_clock < self.end


@dataclass(frozen=True)
class SessionConfig:
    instrument_symbol: str
    timezone: str
    trading_day_opens: time
    sessions: tuple[SessionWindow, ...]
    rth: SessionWindow
    settlement: time | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_session_configs(path: str | Path) -> dict[str, SessionConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        raise SessionConfigError(f"{path}: expected a YAML mapping at the top level")

    instruments = raw.get("instruments")
    if not isinstance(instruments, dict):
        raise SessionConfigError(f"{path}: expected a top-level 'instruments' mapping")

    configs: dict[str, SessionConfig] = {}
    for symbol, entry in instruments.items():
        configs[str(symbol)] = _entry_to_config(str(symbol), entry)
    return configs


def _entry_to_config(symbol: str, entry: dict[str, Any]) -> SessionConfig:
    try:
        timezone = str(entry["timezone"])
        trading_day_opens = _parse_hhmm(str(entry["trading_day_opens"]))
        raw_sessions = entry["sessions"]
        rth_entry = entry["rth"]
    except KeyError as exc:
        raise SessionConfigError(f"{symbol}: missing required field {exc}") from exc

    windows = tuple(
        SessionWindow(
            name=str(name),
            start=_parse_hhmm(str(window["start"])),
            end=_parse_hhmm(str(window["end"])),
        )
        for name, window in raw_sessions.items()
    )
    rth = SessionWindow(
        name="RTH",
        start=_parse_hhmm(str(rth_entry["start"])),
        end=_parse_hhmm(str(rth_entry["end"])),
    )
    settlement = _parse_hhmm(str(entry["settlement"])) if "settlement" in entry else None

    return SessionConfig(
        instrument_symbol=symbol,
        timezone=timezone,
        trading_day_opens=trading_day_opens,
        sessions=windows,
        rth=rth,
        settlement=settlement,
    )


def session_at(ny_timestamp: datetime, config: SessionConfig) -> SessionWindow | None:
    wall_clock = ny_timestamp.timetz().replace(tzinfo=None)
    for window in config.sessions:
        if window.contains(wall_clock):
            return window
    return None


def is_rth(ny_timestamp: datetime, config: SessionConfig) -> bool:
    return config.rth.contains(ny_timestamp.timetz().replace(tzinfo=None))


def session_bounds_utc(
    ny_timestamp: datetime, window: SessionWindow, zone: ZoneInfo
) -> tuple[datetime, datetime]:
    """The UTC instants this window started and will end, for the
    occurrence of `window` that contains `ny_timestamp`."""
    current_date = ny_timestamp.date()
    wall_clock = ny_timestamp.timetz().replace(tzinfo=None)

    if window.start <= window.end:
        start_date = end_date = current_date
    elif wall_clock >= window.start:
        start_date, end_date = current_date, current_date + timedelta(days=1)
    else:
        start_date, end_date = current_date - timedelta(days=1), current_date

    start_ny = datetime.combine(start_date, window.start, tzinfo=zone)
    end_ny = datetime.combine(end_date, window.end, tzinfo=zone)
    return start_ny.astimezone(UTC), end_ny.astimezone(UTC)
