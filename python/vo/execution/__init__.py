"""
vo.execution -- Phase 16, layer 10.

Owns what happens to an approved TradeSignal once it exists: turning it
into an MT5 order (vo.core.mt5, the write adapter this package calls
into), tracking which open positions are this EA's own, and reconciling
what MT5 actually reports open against that on every restart -- the two
responsibilities architecture/vo-architecture-audit.md's own diagram
names for this layer ("vo.execution EXEC ADAPTER ... + order/position
state + reconciliation").

Layer 10, same as vo.telemetry was before this phase -- vo.telemetry
shifts to 11 to make room, since a future RuntimeState will want to
report execution/reconciliation status (see vo.telemetry.trade_pipeline).
vo.execution may import vo.core (9) and everything below it, including
vo.risk's TradeSignal-producing layer (8) via vo.interfaces.signals (0).

SCOPE, stated plainly: everything in this package is real, tested,
live-capable code -- not a stub. What keeps it from sending a live order
today is that nothing in this codebase's own runtime paths (VOEaRuntime,
scripts/run_vo_ea.py) calls ExecutionRouter.place() or
execute_reconciliation_actions() yet. Wiring either into a live,
unattended process is Phase 18's own decision (the first live trade), per
architecture/vo-phase-plan.md's gate for that phase.
"""

from __future__ import annotations

from vo.execution.execution_config import (
    ExecutionConfig,
    ExecutionConfigError,
    load_execution_config,
)
from vo.execution.reconciliation import execute_reconciliation_actions, reconcile
from vo.execution.router import ExecutionRouter
from vo.execution.types import (
    ExecutionEvent,
    ReconciliationAction,
    ReconciliationFinding,
    ReconciliationPolicy,
    ReconciliationReport,
)

__all__ = [
    "ExecutionConfig",
    "ExecutionConfigError",
    "ExecutionEvent",
    "ExecutionRouter",
    "ReconciliationAction",
    "ReconciliationFinding",
    "ReconciliationPolicy",
    "ReconciliationReport",
    "execute_reconciliation_actions",
    "load_execution_config",
    "reconcile",
]
