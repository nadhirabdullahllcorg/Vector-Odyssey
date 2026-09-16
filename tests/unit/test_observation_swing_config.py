"""vo.observation.swing_config -- versioned per-tier swing parameters."""

from __future__ import annotations

from pathlib import Path

import pytest

from vo.observation.swing_config import SwingConfigError, load_swing_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = _REPO_ROOT / "config" / "settings" / "swings.yaml"


def test_loads_the_real_committed_config():
    config = load_swing_config(_REAL_CONFIG)
    assert config.version == 1
    assert config.atr_period == 20
    assert config.internal.k == 2
    assert config.swing.k == 5
    assert config.internal.atr_multiplier > 0
    assert config.swing.atr_multiplier > 0


def test_rejects_non_mapping_top_level(tmp_path: Path):
    path = tmp_path / "swings.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(SwingConfigError):
        load_swing_config(path)


def test_requires_version(tmp_path: Path):
    path = tmp_path / "swings.yaml"
    path.write_text(
        "atr_period: 20\nlevels:\n"
        "  internal: {k: 2, atr_multiplier: 1.0}\n"
        "  swing: {k: 5, atr_multiplier: 3.0}\n"
    )
    with pytest.raises(SwingConfigError):
        load_swing_config(path)


def test_requires_both_levels(tmp_path: Path):
    path = tmp_path / "swings.yaml"
    path.write_text(
        "version: 1\natr_period: 20\nlevels:\n"
        "  internal: {k: 2, atr_multiplier: 1.0}\n"
    )
    with pytest.raises(SwingConfigError):
        load_swing_config(path)


def test_rejects_non_positive_k():
    with pytest.raises(SwingConfigError):
        from vo.observation.swing_config import SwingLevelConfig

        SwingLevelConfig(k=0, atr_multiplier=1.0)


def test_rejects_non_positive_atr_multiplier():
    with pytest.raises(SwingConfigError):
        from vo.observation.swing_config import SwingLevelConfig

        SwingLevelConfig(k=2, atr_multiplier=0.0)


def test_rejects_non_positive_atr_period(tmp_path: Path):
    path = tmp_path / "swings.yaml"
    path.write_text(
        "version: 1\natr_period: 0\nlevels:\n"
        "  internal: {k: 2, atr_multiplier: 1.0}\n"
        "  swing: {k: 5, atr_multiplier: 3.0}\n"
    )
    with pytest.raises(SwingConfigError):
        load_swing_config(path)
