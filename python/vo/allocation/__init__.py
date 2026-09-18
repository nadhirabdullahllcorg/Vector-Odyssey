"""
vo.allocation -- layer 7.

Phase 14's Capital Allocator: deliberately simple (architecture/
vo-architecture-audit.md's own words: "AllocationSignal -- contract
correct, behaviour deliberately simple"). May import vo.interfaces (0),
vo.market (1), vo.time (2), vo.observation (3), vo.month01 (4),
vo.research (5) and vo.signals (6); may not import vo.risk (8), vo.core
(9) or vo.telemetry (10) -- see tests/unit/test_architecture.py's LAYERS
dict.
"""

from __future__ import annotations

from vo.allocation.allocator import allocate

__all__ = ["allocate"]
