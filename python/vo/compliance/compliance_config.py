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
real daily-loss/max-drawdown percentages; whether its drawdown convention
matches what this engine implements -- a trailing (peak-equity, not
fixed-initial-balance) drawdown that never locks/freezes once profit
crosses a threshold, and trails EQUITY (intraday floating P&L included)
rather than BALANCE (closed trades only) -- see engine.py's own module
docstring for the corrected terminology and exactly which variant is NOT
built; minimum trading days; and any consistency rule before trusting
this config on a real evaluation account.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class ComplianceConfigError(ValueError):
    pass


class DailyLossMode(Enum):
    """How the DAILY gate decides it has been breached.

    FIXED is the prop-firm shape and the default: the day is over once
    losses from the trading day's start equity reach their own limit,
    independent of how much total-drawdown headroom remains. A firm
    imposes this as a rule of its own, so it is enforced as one.

    DRAWDOWN_HEADROOM is for an account with no external daily rule --
    a personal live account, where the only real constraint is the max
    drawdown. The day is halted not at a fixed daily loss, but once the
    TOTAL drawdown has consumed `daily_halt_at_total_usage` of its
    buffer-adjusted allowance: stop trading while there is still room
    left, rather than at an arbitrary daily figure. `daily_loss_
    limit_fraction` stays configured and stays REPORTED on every verdict
    in this mode (it is useful telemetry), it simply does not gate.
    """

    FIXED = "FIXED"
    DRAWDOWN_HEADROOM = "DRAWDOWN_HEADROOM"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ComplianceConfig:
    version: int
    daily_loss_limit_fraction: float
    total_drawdown_limit_fraction: float
    safety_buffer_fraction: float
    warning_threshold_fraction: float
    critical_threshold_fraction: float
    daily_loss_mode: DailyLossMode = DailyLossMode.FIXED
    # Only read in DRAWDOWN_HEADROOM mode: the fraction of the
    # buffer-adjusted TOTAL drawdown allowance that, once consumed, halts
    # the day. Additive with a default so every existing ComplianceConfig
    # construction site (prop defaults, every test) is unchanged.
    daily_halt_at_total_usage: float = 0.75
    # The account's real high-water mark in account currency, if it is
    # known and sits ABOVE whatever equity the engine first observes.
    #
    # None (the default) keeps the original behavior: the drawdown
    # reference is anchored to first-seen equity, which quietly forgives
    # everything that happened before the process started. Setting it
    # anchors the peak to real account history instead.
    #
    # This is a RISK anchor and nothing else. It makes the drawdown
    # limits STRICTER, never looser, because drawdown is measured from a
    # higher peak. It is not a recovery target, and nothing downstream is
    # permitted to read it as one -- see vo.telemetry.benchmark and gate
    # G16 for where "distance back to the peak" is allowed to live.
    high_water_mark_currency: float | None = None
    # The same anchor expressed the way an account holder actually knows
    # it -- "I am down X from my peak" -- rather than as an absolute
    # equity figure they would have to look up. When set, the peak is
    # anchored to (first observed equity + this), once, on the first
    # snapshot.
    #
    # Mutually exclusive with high_water_mark_currency: two ways to say
    # the same thing, and letting both be set invites them to disagree.
    high_water_mark_drawdown_currency: float | None = None

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
        if (
            self.high_water_mark_currency is not None
            and self.high_water_mark_drawdown_currency is not None
        ):
            raise ComplianceConfigError(
                "set high_water_mark_currency OR high_water_mark_drawdown_currency, "
                "not both -- they are two spellings of one anchor and could disagree"
            )
        if (
            self.high_water_mark_drawdown_currency is not None
            and self.high_water_mark_drawdown_currency <= 0
        ):
            raise ComplianceConfigError(
                f"high_water_mark_drawdown_currency must be > 0 when set, got "
                f"{self.high_water_mark_drawdown_currency}"
            )
        if self.high_water_mark_currency is not None and self.high_water_mark_currency <= 0:
            raise ComplianceConfigError(
                f"high_water_mark_currency must be > 0 when set, got "
                f"{self.high_water_mark_currency}"
            )
        if not (0.0 < self.daily_halt_at_total_usage <= 1.0):
            raise ComplianceConfigError(
                f"daily_halt_at_total_usage must be in (0, 1], got "
                f"{self.daily_halt_at_total_usage}"
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

    raw_mode = str(top.get("daily_loss_mode", DailyLossMode.FIXED.value)).upper()
    try:
        daily_loss_mode = DailyLossMode(raw_mode)
    except ValueError as exc:
        raise ComplianceConfigError(
            f"daily_loss_mode must be one of "
            f"{sorted(m.value for m in DailyLossMode)}, got {raw_mode!r}"
        ) from exc

    return ComplianceConfig(
        version=int(top["version"]),
        daily_loss_limit_fraction=float(top["daily_loss_limit_fraction"]),
        total_drawdown_limit_fraction=float(top["total_drawdown_limit_fraction"]),
        safety_buffer_fraction=float(top["safety_buffer_fraction"]),
        warning_threshold_fraction=float(top["warning_threshold_fraction"]),
        critical_threshold_fraction=float(top["critical_threshold_fraction"]),
        daily_loss_mode=daily_loss_mode,
        daily_halt_at_total_usage=float(top.get("daily_halt_at_total_usage", 0.75)),
        high_water_mark_currency=(
            float(top["high_water_mark_currency"])
            if top.get("high_water_mark_currency") is not None
            else None
        ),
        high_water_mark_drawdown_currency=(
            float(top["high_water_mark_drawdown_currency"])
            if top.get("high_water_mark_drawdown_currency") is not None
            else None
        ),
    )
