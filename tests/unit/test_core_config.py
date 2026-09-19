"""EAConfig -- Phase 10's "one config" loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from vo.core.config import EAConfigError, load_ea_config

_VALID = """
version: 1
instrument:
  broker_symbol: "US100.n"
wire:
  dir: "/tmp/mt5-files/VectorOdyssey"
  poll_interval_seconds: 2.5
config_files:
  brokers: "config/settings/brokers.yaml"
  sessions: "config/settings/sessions.yaml"
telemetry:
  host: "127.0.0.1"
  port: 9999
logging:
  path: "logs/vo_ea.log"
  level: "debug"
ea_phase: "10"
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "vo_ea.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_ea_config_reads_every_field(tmp_path: Path) -> None:
    config = load_ea_config(_write(tmp_path, _VALID))

    assert config.broker_symbol == "US100.n"
    assert config.wire.dir == Path("/tmp/mt5-files/VectorOdyssey")
    assert config.wire.poll_interval_seconds == 2.5
    assert config.brokers_path == Path("config/settings/brokers.yaml")
    assert config.sessions_path == Path("config/settings/sessions.yaml")
    assert config.telemetry.host == "127.0.0.1"
    assert config.telemetry.port == 9999
    assert config.logging.level == "DEBUG"
    assert config.ea_phase == "10"


def test_bar_tick_meta_wire_paths_follow_vo_transport_naming(tmp_path: Path) -> None:
    config = load_ea_config(_write(tmp_path, _VALID))

    base = Path("/tmp/mt5-files/VectorOdyssey")
    assert config.bar_wire_path() == base / "US100.n_bars.jsonl"
    assert config.tick_wire_path() == base / "US100.n_ticks.jsonl"
    assert config.meta_wire_path() == base / "US100.n_meta.jsonl"


def test_calendar_wire_path_is_not_broker_symbol_scoped(tmp_path: Path) -> None:
    """MT5's economic calendar is account/terminal-wide, not per-symbol --
    VO_CalendarBridge.mq5 writes one fixed filename, unlike the three
    price wire files."""
    config = load_ea_config(_write(tmp_path, _VALID))

    assert config.calendar_wire_path() == Path("/tmp/mt5-files/VectorOdyssey/calendar.jsonl")
    assert "US100.n" not in config.calendar_wire_path().name


def test_calendar_refresh_seconds_defaults_and_is_configurable(tmp_path: Path) -> None:
    default_config = load_ea_config(_write(tmp_path, _VALID))
    assert default_config.wire.calendar_refresh_seconds == 60.0

    text = """
    instrument:
      broker_symbol: "US100.n"
    wire:
      dir: "/tmp/mt5-files/VectorOdyssey"
      calendar_refresh_seconds: 300
    """
    configured = load_ea_config(_write(tmp_path, text))
    assert configured.wire.calendar_refresh_seconds == 300.0


def test_non_positive_calendar_refresh_seconds_raises(tmp_path: Path) -> None:
    text = """
    instrument:
      broker_symbol: "US100.n"
    wire:
      dir: "/tmp/mt5-files/VectorOdyssey"
      calendar_refresh_seconds: 0
    """
    with pytest.raises(EAConfigError):
        load_ea_config(_write(tmp_path, text))


def test_defaults_apply_when_optional_sections_are_omitted(tmp_path: Path) -> None:
    minimal = """
    instrument:
      broker_symbol: "US100.n"
    wire:
      dir: "/tmp/mt5-files/VectorOdyssey"
    """
    config = load_ea_config(_write(tmp_path, minimal))

    assert config.wire.poll_interval_seconds == 1.0
    assert config.telemetry.host == "127.0.0.1"
    assert config.telemetry.port == 8765
    assert config.logging.path == Path("logs/vo_ea.log")
    assert config.logging.level == "INFO"
    assert config.ea_phase == "10"


def test_non_mapping_top_level_raises(tmp_path: Path) -> None:
    with pytest.raises(EAConfigError):
        load_ea_config(_write(tmp_path, "- just\n- a\n- list\n"))


def test_missing_instrument_section_raises(tmp_path: Path) -> None:
    text = """
    wire:
      dir: "/tmp/mt5-files/VectorOdyssey"
    """
    with pytest.raises(EAConfigError):
        load_ea_config(_write(tmp_path, text))


def test_missing_wire_dir_raises(tmp_path: Path) -> None:
    text = """
    instrument:
      broker_symbol: "US100.n"
    wire:
      poll_interval_seconds: 1.0
    """
    with pytest.raises(EAConfigError):
        load_ea_config(_write(tmp_path, text))


def test_non_positive_poll_interval_raises(tmp_path: Path) -> None:
    text = """
    instrument:
      broker_symbol: "US100.n"
    wire:
      dir: "/tmp/mt5-files/VectorOdyssey"
      poll_interval_seconds: 0
    """
    with pytest.raises(EAConfigError):
        load_ea_config(_write(tmp_path, text))
