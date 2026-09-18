"""
vo.signals -- layer 6.

Phase 14's Signal Generator: wraps one strategy evaluation
(vo.interfaces.signals.StrategyOutput) into an always-produced, auditable
Signal. May import vo.interfaces (0), vo.market (1), vo.time (2),
vo.observation (3), vo.month01 (4) and vo.research (5); may not import
vo.allocation (7), vo.risk (8), vo.core (9) or vo.telemetry (10) -- see
tests/unit/test_architecture.py's LAYERS dict.

Nothing here decides whether a trade happens -- that is vo.allocation's
and vo.risk's job, one and two layers up. This layer only makes a
strategy's evaluation auditable.
"""

from __future__ import annotations

from vo.signals.generator import generate_signal

__all__ = ["generate_signal"]
