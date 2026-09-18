"""vo.risk.risk_config -- versioned Phase 14 sizing parameters."""

from __future__ import annotations

from pathlib import Path

import pytest

from vo.risk.risk_config import RiskConfig, RiskConfigError, load_risk_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = _REPO_ROOT / "config" / "settings" / "risk.yaml"


def test_loads_the_real_committed_config():
    config = load_risk_config(_REAL_CONFIG)
    assert config.version == 1
    assert config.risk_per_trade_fraction == 0.01
    assert config.min_volume == 0.01
    assert config.max_volume == 5.0
    assert config.volume_step == 0.01
    assert config.max_open_positions == 1


def test_rejects_non_mapping_top_level(tmp_path: Path):
    path = tmp_path / "risk.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(RiskConfigError):
        load_risk_config(path)


def test_requires_every_field(tmp_path: Path):
    path = tmp_path / "risk.yaml"
    path.write_text("version: 1\nrisk_per_trade_fraction: 0.01\n")
    with pytest.raises(RiskConfigError):
        load_risk_config(path)


def test_rejects_out_of_range_risk_fraction():
    with pytest.raises(RiskConfigError):
        RiskConfig(
            version=1,
            risk_per_trade_fraction=1.5,
            min_volume=0.01,
            max_volume=5.0,
            volume_step=0.01,
            max_open_positions=1,
        )


def test_rejects_max_volume_below_min_volume():
    with pytest.raises(RiskConfigError):
        RiskConfig(
            version=1,
            risk_per_trade_fraction=0.01,
            min_volume=1.0,
            max_volume=0.5,
            volume_step=0.01,
            max_open_positions=1,
        )


def test_rejects_non_positive_volume_step():
    with pytest.raises(RiskConfigError):
        RiskConfig(
            version=1,
            risk_per_trade_fraction=0.01,
            min_volume=0.01,
            max_volume=5.0,
            volume_step=0.0,
            max_open_positions=1,
        )


def test_rejects_zero_max_open_positions():
    with pytest.raises(RiskConfigError):
        RiskConfig(
            version=1,
            risk_per_trade_fraction=0.01,
            min_volume=0.01,
            max_volume=5.0,
            volume_step=0.01,
            max_open_positions=0,
        )
