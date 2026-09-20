"""vo.valco.lrx_config -- the LRX strategy's versioned settings."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from vo.valco.lrx_config import (
    EntryModel,
    LrxConfigError,
    OriginModel,
    SessionWindow,
    TargetMode,
    load_lrx_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO_ROOT / "config" / "settings" / "lrx.yaml"


def test_the_shipped_config_loads() -> None:
    config = load_lrx_config(_SHIPPED)

    assert config.version == 1
    assert config.broker_symbol == "US100"
    assert config.execution_timeframe == "M1"
    assert config.context_timeframe == "M15"


def test_the_shipped_session_windows_match_what_the_user_specified() -> None:
    """Confirmed 2026-09-19: trade 07:00-14:00 NY, setups only 09:00-13:45."""
    session = load_lrx_config(_SHIPPED).session

    assert session.timezone == "America/New_York"
    assert session.trade_window.opens == time(7, 0)
    assert session.trade_window.closes == time(14, 0)
    assert session.setup_selection.opens == time(9, 0)
    assert session.setup_selection.closes == time(13, 45)


def test_the_shipped_limits_match_what_the_user_specified() -> None:
    limits = load_lrx_config(_SHIPPED).limits

    assert limits.max_trades_per_day == 1
    assert limits.max_concurrent_positions == 1


def test_a_setup_window_is_half_open() -> None:
    """A selection window closing at 13:45 does not admit 13:45 itself --
    otherwise a setup armed exactly at the boundary is ambiguous."""
    window = SessionWindow(opens=time(9, 0), closes=time(13, 45))

    assert window.contains(time(9, 0)) is True
    assert window.contains(time(13, 44)) is True
    assert window.contains(time(13, 45)) is False
    assert window.contains(time(8, 59)) is False


def test_a_backwards_window_is_rejected() -> None:
    with pytest.raises(LrxConfigError, match="must be before"):
        SessionWindow(opens=time(14, 0), closes=time(7, 0))


def test_the_shipped_score_does_not_filter_trades() -> None:
    """Spec section 27: the setup score is observational in v1. If this
    ever flips silently, the research value of the score is gone."""
    config = load_lrx_config(_SHIPPED)

    assert config.flag("score_filters_trades") is False
    assert config.flag("require_sweep") is True
    assert config.flag("a_flag_that_does_not_exist") is False


def test_the_shipped_target_mode_is_single_target() -> None:
    """T1_ONLY is the honest setting until a position manager exists."""
    assert load_lrx_config(_SHIPPED).objective.target_mode is TargetMode.T1_ONLY


def test_enums_parse_from_the_shipped_config() -> None:
    config = load_lrx_config(_SHIPPED)

    assert config.expansion.origin_model is OriginModel.SWING_ORIGIN
    assert config.entry_model is EntryModel.FVG_CE


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "lrx.yaml"
    path.write_text(text, encoding="utf-8")
    return path


_MINIMAL = """
version: 1
instrument: {broker_symbol: US100, execution_timeframe: M1, context_timeframe: M15}
session:
  timezone: America/New_York
  trade_window: {opens: "07:00", closes: "14:00"}
  setup_selection: {opens: "09:00", closes: "13:45"}
reference_levels: {minimum_class: B}
sweep: {min_penetration_atr: 0.1, return_max_bars: 12}
expansion: {min_atr_multiple: 1.5, max_bars: 5, atr_period: 14, origin_model: SWING_ORIGIN}
inefficiency: {types: [FVG], min_size_atr: 0.15, max_age_bars: 60}
entry: {model: FVG_CE, max_rebalance_depth: 1.0}
objective:
  cluster_tolerance_atr: 0.5
  min_distance_atr: 1.0
  min_reward_risk: 1.5
  target_mode: T1_ONLY
stop: {buffer_atr: 0.25}
setup_life: {max_age_minutes: 45, max_bars_after_mss: 30}
limits: {max_trades_per_day: 1, max_concurrent_positions: 1, max_consecutive_losses: 2}
"""


def test_a_selection_window_outside_the_trade_window_is_rejected(tmp_path: Path) -> None:
    """Arming a setup the trade window cannot hold is incoherent."""
    text = _MINIMAL.replace('setup_selection: {opens: "09:00", closes: "13:45"}',
                            'setup_selection: {opens: "06:00", closes: "13:45"}')

    with pytest.raises(LrxConfigError, match="before the trade window opens"):
        load_lrx_config(_write(tmp_path, text))


def test_a_selection_window_closing_after_the_trade_window_is_rejected(tmp_path: Path) -> None:
    text = _MINIMAL.replace('setup_selection: {opens: "09:00", closes: "13:45"}',
                            'setup_selection: {opens: "09:00", closes: "15:00"}')

    with pytest.raises(LrxConfigError, match="after the trade window closes"):
        load_lrx_config(_write(tmp_path, text))


def test_a_missing_required_section_raises(tmp_path: Path) -> None:
    text = _MINIMAL.replace("stop: {buffer_atr: 0.25}\n", "")

    with pytest.raises(LrxConfigError, match="missing required key 'stop'"):
        load_lrx_config(_write(tmp_path, text))


def test_an_unknown_enum_value_raises_and_names_the_alternatives(tmp_path: Path) -> None:
    text = _MINIMAL.replace("origin_model: SWING_ORIGIN", "origin_model: VIBES")

    with pytest.raises(LrxConfigError, match="must be one of"):
        load_lrx_config(_write(tmp_path, text))


def test_a_malformed_time_raises(tmp_path: Path) -> None:
    text = _MINIMAL.replace('opens: "07:00"', 'opens: "7am"')

    with pytest.raises(LrxConfigError, match="is not a HH:MM time"):
        load_lrx_config(_write(tmp_path, text))


def test_a_nonsense_numeric_bound_raises(tmp_path: Path) -> None:
    text = _MINIMAL.replace("min_penetration_atr: 0.1", "min_penetration_atr: 0")

    with pytest.raises(LrxConfigError, match="min_penetration_atr"):
        load_lrx_config(_write(tmp_path, text))


# ── baseline frequency lock (2026-09-20) ──────────────────────────────────


def test_the_shipped_baseline_allows_one_trade_per_day() -> None:
    """Pinned because three different figures for this exist across the
    written specs -- 6 in the Liquidity Reversal Model's section 33, 3 in
    the section-38 draft schema, 5 in an earlier instruction. All are
    superseded. One is current intent, and a backtest generated under any
    other number is not the baseline.

    This asserts the SHIPPED config, not a loader default, because the
    number that matters is the one a run actually uses.
    """
    assert load_lrx_config(_SHIPPED).limits.max_trades_per_day == 1


def test_the_baseline_holds_one_position_at_a_time() -> None:
    """Strategy-layer arming only. The ENFORCED cap is risk.yaml's
    max_open_positions; this value exists so LRX does not arm a setup the
    risk manager would reject anyway. Two layers, stated twice on
    purpose, and a test on each."""
    config = load_lrx_config(_SHIPPED)

    assert config.limits.max_concurrent_positions == 1
    assert config.limits.max_consecutive_losses == 2


def test_the_per_level_repeat_limit_is_declared_but_not_yet_parsed() -> None:
    """max_trades_per_level_per_day: 1 sits in lrx.yaml and NOTHING reads
    it -- LrxConfig has no `repeat` section and no code references the
    key. It is not enforced anywhere today.

    Pinned as a known gap rather than left to be assumed active. The
    repeat/blacklist rules belong to the Setup State Machine, which is
    not built; when it is, this test should be replaced by one asserting
    the limit actually binds. Until then a reader skimming the YAML would
    reasonably believe a level cannot be traded twice in a day, and it
    can.
    """
    import yaml

    raw = yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))
    assert raw["repeat"]["max_trades_per_level_per_day"] == 1

    config = load_lrx_config(_SHIPPED)
    assert not hasattr(config, "repeat")


def test_risk_enforces_the_single_open_position_independently() -> None:
    """The strategy declining to arm and the risk layer refusing to size
    are different guarantees. If the strategy config were edited, this
    must still hold."""
    from vo.risk.risk_config import load_risk_config

    assert load_risk_config(Path("config/settings/risk.yaml")).max_open_positions == 1
