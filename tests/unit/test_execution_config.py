"""vo.execution.execution_config -- versioned Phase 16 order-tagging params."""

from __future__ import annotations

from pathlib import Path

import pytest

from vo.execution.execution_config import (
    ExecutionConfig,
    ExecutionConfigError,
    load_execution_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = _REPO_ROOT / "config" / "settings" / "execution.yaml"


def test_loads_the_real_committed_config():
    config = load_execution_config(_REAL_CONFIG)
    assert config.version == 1
    assert config.magic_number == 20260914
    assert config.comment_prefix == "VO"
    assert config.deviation_points == 20


def test_rejects_non_mapping_top_level(tmp_path: Path):
    path = tmp_path / "execution.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(ExecutionConfigError):
        load_execution_config(path)


def test_requires_every_field(tmp_path: Path):
    path = tmp_path / "execution.yaml"
    path.write_text("version: 1\nmagic_number: 42\n")
    with pytest.raises(ExecutionConfigError):
        load_execution_config(path)


def test_rejects_non_positive_magic_number():
    with pytest.raises(ExecutionConfigError):
        ExecutionConfig(version=1, magic_number=0, comment_prefix="VO", deviation_points=20)


def test_rejects_blank_comment_prefix():
    with pytest.raises(ExecutionConfigError):
        ExecutionConfig(version=1, magic_number=1, comment_prefix="   ", deviation_points=20)


def test_rejects_comment_prefix_too_long():
    with pytest.raises(ExecutionConfigError):
        ExecutionConfig(
            version=1, magic_number=1, comment_prefix="x" * 27, deviation_points=20
        )


def test_rejects_negative_deviation_points():
    with pytest.raises(ExecutionConfigError):
        ExecutionConfig(version=1, magic_number=1, comment_prefix="VO", deviation_points=-1)


def test_comment_for_appends_strategy_id():
    config = ExecutionConfig(version=1, magic_number=1, comment_prefix="VO", deviation_points=20)
    assert config.comment_for("disposable_v0") == "VO:disposable_v0"


def test_comment_for_truncates_to_mt5s_comment_limit():
    config = ExecutionConfig(version=1, magic_number=1, comment_prefix="VO", deviation_points=20)
    comment = config.comment_for("a_very_long_strategy_identifier_that_overflows")
    assert len(comment) <= 31
    assert comment.startswith("VO:")
