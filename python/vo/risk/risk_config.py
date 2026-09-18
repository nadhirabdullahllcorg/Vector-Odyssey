"""
RiskConfig -- versioned Phase 14 parameters (config/settings/risk.yaml),
never hardcoded. Mirrors swing_config.py/regime_config.py's own reasoning:
the per-trade risk fraction and volume rounding rules are [VO-D] starting
points expected to be revised, not settled constants, so they live in a
versioned YAML file rather than in code.

Deliberately narrow, matching Phase 14's own scope: this configures
MECHANICAL position sizing (risk a fixed fraction of equity against a
strategy-stated stop distance, rounded to the broker's lot-step rules).
The regime-conditioned ATR-based stop/target sizing architecture/
vo-trade-logic-and-brain-plan.md describes is explicitly Phase 32 scope,
built on top of this once it exists -- not this file's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class RiskConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RiskConfig:
    version: int
    risk_per_trade_fraction: float
    min_volume: float
    max_volume: float
    volume_step: float
    max_open_positions: int

    def __post_init__(self) -> None:
        if not (0.0 < self.risk_per_trade_fraction <= 1.0):
            raise RiskConfigError(
                f"risk_per_trade_fraction must be in (0, 1], got {self.risk_per_trade_fraction}"
            )
        if self.min_volume <= 0:
            raise RiskConfigError(f"min_volume must be positive, got {self.min_volume}")
        if self.max_volume < self.min_volume:
            raise RiskConfigError("max_volume cannot be below min_volume")
        if self.volume_step <= 0:
            raise RiskConfigError(f"volume_step must be positive, got {self.volume_step}")
        if self.max_open_positions < 1:
            raise RiskConfigError(
                f"max_open_positions must be >= 1, got {self.max_open_positions}"
            )


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RiskConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def load_risk_config(path: str | Path) -> RiskConfig:
    """Load and validate config/settings/risk.yaml. Raises RiskConfigError
    for anything malformed rather than silently substituting a default --
    the same discipline as load_swing_config/load_regime_config."""
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="risk config")

    required = (
        "version",
        "risk_per_trade_fraction",
        "min_volume",
        "max_volume",
        "volume_step",
        "max_open_positions",
    )
    for key in required:
        if key not in top:
            raise RiskConfigError(f"risk config requires '{key}'")

    return RiskConfig(
        version=int(top["version"]),
        risk_per_trade_fraction=float(top["risk_per_trade_fraction"]),
        min_volume=float(top["min_volume"]),
        max_volume=float(top["max_volume"]),
        volume_step=float(top["volume_step"]),
        max_open_positions=int(top["max_open_positions"]),
    )
