"""
vo.risk -- layer 8.

Phase 14's Risk Engine, and this codebase's sole producer of TradeSignal
(architecture/vo-phase-plan.md's Block 4 Phase 14 gate: "risk is the sole
TradeSignal producer" -- enforced mechanically by
tests/unit/test_architecture.py::test_risk_is_the_sole_trade_signal_producer).
May import vo.interfaces (0), vo.market (1), vo.time (2), vo.observation
(3), vo.month01 (4), vo.research (5), vo.signals (6) and vo.allocation
(7); may not import vo.core (9) or vo.telemetry (10) -- see
tests/unit/test_architecture.py's LAYERS dict.
"""

from __future__ import annotations

from vo.risk.manager import build_trade_signal, evaluate_risk
from vo.risk.risk_config import RiskConfig, RiskConfigError, load_risk_config

__all__ = [
    "RiskConfig",
    "RiskConfigError",
    "build_trade_signal",
    "evaluate_risk",
    "load_risk_config",
]
