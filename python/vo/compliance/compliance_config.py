"""
ComplianceConfig -- versioned Account Compliance Engine parameters
(config/settings/compliance.yaml), never hardcoded. Mirrors
risk_config.py/swing_config.py/regime_config.py's own reasoning exactly:
these are [VO-D] starting points expected to be revised, not settled
constants.

THE DEFAULT VALUES ARE NOT THIS ACCOUNT'S REAL PROP-FIRM RULES -- they
are the exact defaults the open-source PropFirmGuard MQL5 reference
(InpDailyLossPct=5.0, InpTotalDdPct=10.0, InpBufferPct=0.5; verified
directly against mql5.com/en/code/76767 on 2026-09-19, not merely
assumed from secondhand description) ships with, used here as a
plausible, verified starting point in the exact style risk.yaml's own
header already established for this project ("THE VALUES BELOW ARE NOT A
FINISHED ANSWER"). The actual prop firm/evaluation this account is
running under was not supplied as of this file's creation -- confirm the
real daily-loss/max-drawdown percentages, whether the firm's drawdown is
static or trailing (this v1 engine implements static peak-equity
drawdown only, matching PropFirmGuard -- see engine.py's own module
docstring), minimum trading days, and any consistency rule before
trusting this config on a real evaluation account.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ComplianceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ComplianceConfig:
    version: int
    daily_loss_limit_fraction: float
    total_drawdown_limit_fraction: float
    safety_buffer_fraction: float
    warning_threshold_fraction: float
    critical_threshold_fraction: float

    def __post_init__(self) -> None:
        if not (0.0 < self.daily_loss_limit_fraction <= 1.0):
            raise ComplianceConfigError(
                f"daily_loss_limit_fraction must be in (0, 1], got "
                f"{self.daily_loss_limit_fraction}"
            )
        if not (0.0 < self.total_drawdown_limit_fraction <= 1.0):
            raise ComplianceConfigError(
                f"total_drawdown_limit_fraction must be in (0, 1], got "
                f"{self.total_drawdown_limit_fraction}"
            )
        if not (0.0 <= self.safety_buffer_fraction < self.daily_loss_limit_fraction):
            raise ComplianceConfigError(
                "safety_buffer_fraction must be >= 0 and strictly below "
                "daily_loss_limit_fraction (it is subtracted from it)"
            )
        if not (0.0 <= self.safety_buffer_fraction < self.total_drawdown_limit_fraction):
            raise ComplianceConfigError(
                "safety_buffer_fraction must be >= 0 and strictly below "
                "total_drawdown_limit_fraction (it is subtracted from it)"
            )
        if not (0.0 < self.warning_threshold_fraction < self.critical_threshold_fraction < 1.0):
            raise ComplianceConfigError(
                "warning_threshold_fraction must be < critical_threshold_fraction, "
                "and both must be in (0, 1)"
            )

    @property
    def effective_daily_loss_limit_fraction(self) -> float:
        """The buffer-adjusted daily limit ComplianceEngine actually
        enforces -- e.g. 5.0% limit minus a 0.5% buffer = 4.5%, matching
        PropFirmGuard's own "intervene before the real threshold" design
        (its own listing's phrasing: "causing intervention before broker/
        prop firm thresholds are triggered")."""
        return self.daily_loss_limit_fraction - self.safety_buffer_fraction

    @property
    def effective_total_drawdown_limit_fraction(self) -> float:
        return self.total_drawdown_limit_fraction - self.safety_buffer_fraction


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComplianceConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def load_compliance_config(path: str | Path) -> ComplianceConfig:
    """Load and validate config/settings/compliance.yaml. Raises
    ComplianceConfigError for anything malformed rather than silently
    substituting a default -- the same discipline as
    load_risk_config/load_swing_config/load_regime_config."""
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="compliance config")

    required = (
        "version",
        "daily_loss_limit_fraction",
        "total_drawdown_limit_fraction",
        "safety_buffer_fraction",
        "warning_threshold_fraction",
        "critical_threshold_fraction",
    )
    for key in required:
        if key not in top:
            raise ComplianceConfigError(f"compliance config requires '{key}'")

    return ComplianceConfig(
        version=int(top["version"]),
        daily_loss_limit_fraction=float(top["daily_loss_limit_fraction"]),
        total_drawdown_limit_fraction=float(top["total_drawdown_limit_fraction"]),
        safety_buffer_fraction=float(top["safety_buffer_fraction"]),
        warning_threshold_fraction=float(top["warning_threshold_fraction"]),
        critical_threshold_fraction=float(top["critical_threshold_fraction"]),
    )
