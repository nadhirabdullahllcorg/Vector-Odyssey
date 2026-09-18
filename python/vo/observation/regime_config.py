"""
RegimeConfig -- versioned Phase 13 parameters (config/settings/regime.yaml),
never hardcoded. Mirrors swing_config.py's reasoning: the swing tier, the
ER/Hurst windows and the [VO-H] anticipation thresholds are research inputs
expected to be revised against historical data, so they live in a versioned
YAML file, not in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vo.observation.regime import AnticipationConfig, RegimeEngine
from vo.observation.swing_config import SwingConfig
from vo.observation.swings import SwingEngine, SwingLevel


class RegimeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RegimeConfig:
    version: int
    tier: SwingLevel
    efficiency_ratio_period: int
    hurst_period: int
    anticipation: AnticipationConfig


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegimeConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def load_regime_config(path: str | Path) -> RegimeConfig:
    """Load and validate config/settings/regime.yaml. Raises
    RegimeConfigError for anything malformed rather than silently
    substituting a default -- the same discipline as load_swing_config."""
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="regime config")

    for key in ("version", "tier", "efficiency_ratio_period", "hurst_period"):
        if key not in top:
            raise RegimeConfigError(f"regime config requires '{key}'")

    tier_name = str(top["tier"]).upper()
    try:
        tier = SwingLevel[tier_name]
    except KeyError as exc:
        raise RegimeConfigError(
            f"tier must be one of {[level.name for level in SwingLevel]}, got {top['tier']!r}"
        ) from exc

    anticipation_raw = top.get("anticipation") or {}
    _require_mapping(anticipation_raw, what="anticipation")
    defaults = AnticipationConfig()
    anticipation = AnticipationConfig(
        er_trend_threshold=float(
            anticipation_raw.get("er_trend_threshold", defaults.er_trend_threshold)
        ),
        er_chop_threshold=float(
            anticipation_raw.get("er_chop_threshold", defaults.er_chop_threshold)
        ),
        shallow_pullback_ratio=float(
            anticipation_raw.get("shallow_pullback_ratio", defaults.shallow_pullback_ratio)
        ),
        deep_pullback_ratio=float(
            anticipation_raw.get("deep_pullback_ratio", defaults.deep_pullback_ratio)
        ),
    )

    return RegimeConfig(
        version=int(top["version"]),
        tier=tier,
        efficiency_ratio_period=int(top["efficiency_ratio_period"]),
        hurst_period=int(top["hurst_period"]),
        anticipation=anticipation,
    )


def build_regime_engine(
    regime_config: RegimeConfig,
    swing_config: SwingConfig,
    *,
    tick_size: float,
    methodology_version: int | None = None,
) -> RegimeEngine:
    """Wire a RegimeEngine from the two configs: a SwingEngine of the
    configured tier drives the structure, ER/Hurst windows and the
    anticipation thresholds come from regime.yaml. Lives here (not on
    RegimeEngine) so vo.observation.regime need not import its own config
    loader -- avoids an import cycle.

    `methodology_version` defaults to `regime_config.version` (fixed
    2026-09-18: this parameter used to silently default to a bare `1`
    regardless of regime.yaml's own `version` field, so bumping that
    field -- as the 2026-09-18 bodies-not-wicks boundary change does,
    1 -> 2 -- never actually reached a stamped RegimeState. An explicit
    override is still honored, for a caller that genuinely wants to pin
    a different version than the config file's own.)"""
    resolved_version = (
        methodology_version if methodology_version is not None else regime_config.version
    )
    swing_engine = SwingEngine.for_level(
        swing_config,
        regime_config.tier,
        tick_size=tick_size,
        methodology_version=resolved_version,
    )
    return RegimeEngine(
        swing_engine=swing_engine,
        efficiency_ratio_period=regime_config.efficiency_ratio_period,
        hurst_period=regime_config.hurst_period,
        anticipation=regime_config.anticipation,
        methodology_version=resolved_version,
    )
