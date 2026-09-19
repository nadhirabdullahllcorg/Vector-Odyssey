"""scripts/publish_regime.py -- the live-feed publisher, driven off the
real golden bridge capture (tests/fixtures/golden), never a terminal.

Loaded by file path (it is a script, not a package module) the same way
a user runs it. Covers the two 2026-09-19 additions: the header's
last_bar_epoch and the --tier exploration override, which must write to
its OWN feed name and never touch the configured-tier feed."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from vo.core.config import EAConfig, LoggingConfig, TelemetryConfig, WireConfig
from vo.observation.swings import SwingLevel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _REPO_ROOT / "tests" / "fixtures" / "golden"


def _load_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "publish_regime", _REPO_ROOT / "scripts" / "publish_regime.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def wire_dir(tmp_path: Path) -> Path:
    """The golden capture laid out the way VO_Bridge.mq5 names wire files."""
    shutil.copy(_GOLDEN / "us100n_m1_bars_20260915.jsonl", tmp_path / "US100.n_bars.jsonl")
    shutil.copy(_GOLDEN / "us100n_meta_20260915.jsonl", tmp_path / "US100.n_meta.jsonl")
    return tmp_path


def _config(wire_dir: Path) -> EAConfig:
    return EAConfig(
        broker_symbol="US100.n",
        wire=WireConfig(dir=wire_dir, poll_interval_seconds=1.0),
        brokers_path=_REPO_ROOT / "config" / "settings" / "brokers.yaml",
        sessions_path=_REPO_ROOT / "config" / "settings" / "sessions.yaml",
        telemetry=TelemetryConfig(host="127.0.0.1", port=8765),
        logging=LoggingConfig(path=wire_dir / "x.log", level="INFO"),
        ea_phase="13",
    )


def test_header_carries_last_bar_epoch_and_warmup_note(wire_dir: Path) -> None:
    pr = _load_script()
    lines = pr.build_feed_lines(_config(wire_dir))
    assert lines[0].startswith("# VO_REGIME_FEED")
    assert " last_bar_epoch=" in lines[0]
    assert any(line.startswith("# warmup_bars=") for line in lines[1:3])


def test_tier_override_writes_its_own_feed_and_leaves_the_configured_feed_alone(
    wire_dir: Path,
) -> None:
    pr = _load_script()
    config = _config(wire_dir)
    pr.publish_once(config)
    configured = wire_dir / "US100.n_regime.feed"
    before = configured.read_text(encoding="utf-8")

    pr.publish_once(config, tier_override=SwingLevel.INTERNAL)
    exploration = wire_dir / "US100.n_regime_internal.feed"
    assert exploration.exists()
    assert configured.read_text(encoding="utf-8") == before  # untouched
    text = exploration.read_text(encoding="utf-8")
    assert "# tier_override=INTERNAL" in text
    # INTERNAL (K=2, 1xATR) resolves far more structure than SWING (K=5, 3xATR).
    assert text.count("\nBAND|") > before.count("\nBAND|")


def test_parse_tier_rejects_unknown_values() -> None:
    pr = _load_script()
    assert pr._parse_tier(["--tier", "internal"]) == ([], SwingLevel.INTERNAL)
    assert pr._parse_tier(["cfg.yaml"]) == (["cfg.yaml"], None)
    with pytest.raises(SystemExit):
        pr._parse_tier(["--tier", "bogus"])
    with pytest.raises(SystemExit):
        pr._parse_tier(["--tier"])
