"""
SwingConfig -- versioned, per-tier swing-detection parameters
(config/settings/swings.yaml), never hardcoded constants.

Mirrors vo.time.sessions/vo.time.brokers's own reasoning for why this kind
of thing lives in YAML rather than in code: K (fractal half-window) and the
ATR multiplier are explicitly NOT settled numbers. The user's own framing
when this design was agreed (Phase 11, gate G6): "don't assume those
thresholds are correct -- your historical data should determine whether
the distinction is useful." A constant in code would have to be changed
and re-reviewed to test a different value; a config value is changed and
re-run. Keeping K and the ATR multiplier in the same versioned file (with
one `version` field bumped on any change) also means a swing computed
under one parameter set is never silently compared against one computed
under another -- SwingPoint does not currently carry which config version
produced it because Phase 11 has no consumer needing that yet, but the
config's own `version` field exists for exactly that trace the moment one
does.

Two tiers, matching vo.month01.ontology's already-registered ICT concept
(`internal_swing_nesting`, Month01/L08: "Large impulse swings contain
smaller internal swings"): INTERNAL (the smaller, more frequent pivot) and
SWING (the larger one -- the same "swing" that `equilibrium`/`discount`/
`premium` compute their 50% split against). Both share one `atr_period`
(computing two different ATR series for two tiers of the same instrument
was never asked for and isn't implied by anything the user specified); the
K and ATR multiplier are independent per tier, exactly as specified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SwingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SwingLevelConfig:
    """One tier's two independent filters -- neither substitutes for the
    other (this is the corrected, hybrid design: K-bar structural
    confirmation AND an ATR-scaled minimum reversal distance, both
    required)."""

    k: int
    """Bars required strictly on each side of a candidate pivot for the
    fractal/structural confirmation rule."""
    atr_multiplier: float
    """Minimum reversal distance, in multiples of ATR, measured across the
    same K-bar confirming window -- see vo.observation.swings."""

    def __post_init__(self) -> None:
        if self.k < 1:
            raise SwingConfigError(f"k must be >= 1, got {self.k}")
        if self.atr_multiplier <= 0:
            raise SwingConfigError(
                f"atr_multiplier must be positive, got {self.atr_multiplier}"
            )


@dataclass(frozen=True)
class SwingConfig:
    version: int
    atr_period: int
    internal: SwingLevelConfig
    swing: SwingLevelConfig

    def __post_init__(self) -> None:
        if self.atr_period < 1:
            raise SwingConfigError(f"atr_period must be >= 1, got {self.atr_period}")


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SwingConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _level_config(raw: Any, *, what: str) -> SwingLevelConfig:
    mapping = _require_mapping(raw, what=what)

    if "k" not in mapping:
        raise SwingConfigError(f"{what}.k is required")
    if "atr_multiplier" not in mapping:
        raise SwingConfigError(f"{what}.atr_multiplier is required")

    return SwingLevelConfig(k=int(mapping["k"]), atr_multiplier=float(mapping["atr_multiplier"]))


def load_swing_config(path: str | Path) -> SwingConfig:
    """
    Load and validate config/settings/swings.yaml. Raises SwingConfigError
    for anything malformed rather than silently substituting a default --
    a swing detector running on an unintended parameter set is exactly the
    kind of silent drift this project exists to prevent.
    """
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="swings config")

    if "version" not in top:
        raise SwingConfigError("swings config requires a top-level 'version'")
    if "atr_period" not in top:
        raise SwingConfigError("swings config requires a top-level 'atr_period'")

    levels = _require_mapping(top.get("levels"), what="levels")

    if "internal" not in levels:
        raise SwingConfigError("levels.internal is required")
    if "swing" not in levels:
        raise SwingConfigError("levels.swing is required")

    return SwingConfig(
        version=int(top["version"]),
        atr_period=int(top["atr_period"]),
        internal=_level_config(levels["internal"], what="levels.internal"),
        swing=_level_config(levels["swing"], what="levels.swing"),
    )
