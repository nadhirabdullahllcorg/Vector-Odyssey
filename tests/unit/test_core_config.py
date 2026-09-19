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


# ── live trading: the switch that lets a process place real orders ────────


def test_live_trading_is_off_when_the_section_is_absent(tmp_path: Path) -> None:
    """A config that never mentions live trading must never trade."""
    config = load_ea_config(_write(tmp_path, _VALID))

    assert config.live_trading is None


def test_the_shipped_config_ships_with_live_trading_disabled() -> None:
    """The repository default must be a research process, not a trading
    one. If this ever flips, it should be a deliberate commit."""
    repo_root = Path(__file__).resolve().parents[2]
    config = load_ea_config(repo_root / "config" / "settings" / "vo_ea.yaml")

    assert config.live_trading is not None
    assert config.live_trading.enabled is False


def test_live_trading_must_be_switched_on_explicitly(tmp_path: Path) -> None:
    """Present-but-unset is off. There is no enabled-by-presence."""
    text = _VALID + """
live_trading:
  execution_config: "config/settings/execution.yaml"
"""
    config = load_ea_config(_write(tmp_path, text))

    assert config.live_trading is not None
    assert config.live_trading.enabled is False


def test_live_trading_reads_its_paths_and_preflight_sizing(tmp_path: Path) -> None:
    text = _VALID + """
live_trading:
  enabled: true
  compliance_config: "config/settings/compliance_live.yaml"
  compliance_state: "logs/compliance_state.json"
  trading_day_opens: "18:00"
  preflight_volume: 0.02
"""
    live = load_ea_config(_write(tmp_path, text)).live_trading

    assert live is not None
    assert live.enabled is True
    assert live.compliance_config_path == Path("config/settings/compliance_live.yaml")
    assert live.compliance_state_path == Path("logs/compliance_state.json")
    assert live.trading_day_opens.hour == 18
    assert live.preflight_volume == 0.02


def test_a_preflight_trail_that_does_not_tighten_is_refused(tmp_path: Path) -> None:
    """A trail step of zero would 'pass' without proving the primitive
    works -- the exact commissioning theatre preflight exists to avoid."""
    text = _VALID + """
live_trading:
  enabled: true
  preflight_trail_improvement: 0
"""
    with pytest.raises(EAConfigError, match="does not tighten"):
        load_ea_config(_write(tmp_path, text))


def test_a_nonsense_preflight_volume_is_refused(tmp_path: Path) -> None:
    text = _VALID + """
live_trading:
  enabled: true
  preflight_volume: 0
"""
    with pytest.raises(EAConfigError, match="preflight_volume"):
        load_ea_config(_write(tmp_path, text))


def test_a_malformed_trading_day_open_is_refused(tmp_path: Path) -> None:
    text = _VALID + """
live_trading:
  enabled: true
  trading_day_opens: "six pm"
"""
    with pytest.raises(EAConfigError, match="trading_day_opens"):
        load_ea_config(_write(tmp_path, text))
