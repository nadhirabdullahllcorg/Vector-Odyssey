"""
vo.execution's own contract types -- ExecutionEvent (what happened when
ExecutionRouter placed one TradeSignal) and the Reconciliation* family
(what a restart-time scan of open positions found, and what it recommends
doing about each one). Deliberately plain value types, matching Phase
14's own style in vo.interfaces.signals: every outcome is an object,
never an exception used for ordinary control flow.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.core.mt5 import OrderResult
from vo.market.account import Position


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Ties one TradeSignal to the OrderResult MT5 actually returned for
    it -- the audit trail Phase 19 (feature/state correlation) and beyond
    will need: "did VO identify it" (Phase 14) versus "did MT5 execute
    it" (this), kept as two separately inspectable facts rather than
    collapsed into one."""

    object_id: str
    trade_signal_id: str
    generated_at_utc: datetime
    order_result: OrderResult

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ValueError("ExecutionEvent.object_id cannot be blank")
        if not self.trade_signal_id.strip():
            raise ValueError("ExecutionEvent.trade_signal_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise ValueError("ExecutionEvent.generated_at_utc must be timezone-aware")


class ReconciliationAction(Enum):
    """What reconcile() recommends doing about one open position. Never
    executed automatically by reconcile() itself -- see
    execute_reconciliation_actions and this package's own module
    docstring on why nothing calls it from a live path yet."""

    KEEP = "KEEP"
    CLOSE = "CLOSE"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    """One open position's reconciliation verdict. `recognized=True`
    means its `magic` field matches this EA's configured magic number
    (see ExecutionConfig.magic_number) -- the durable-across-restart tag
    Phase 16's own gate needed a scheme for (architecture/
    vo-phase-plan.md SS16-notes) and had none defined until now."""

    position: Position
    recognized: bool
    action: ReconciliationAction
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("ReconciliationFinding.reason cannot be blank")
        if self.recognized and self.action is ReconciliationAction.CLOSE:
            raise ValueError(
                "a recognized (VO-tagged) position should never default to CLOSE -- "
                "override via a custom ReconciliationPolicy if that is genuinely intended"
            )


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """The full restart-time scan. `unrecognized_count` and
    `flagged_count` are surfaced directly (not just derivable from
    `findings`) so a caller can log "N unrecognized positions found" in
    one line without re-deriving it -- matching RuntimeState.detail's own
    one-line-summary convention (vo.telemetry.ea_runtime)."""

    generated_at_utc: datetime
    findings: tuple[ReconciliationFinding, ...]

    def __post_init__(self) -> None:
        if self.generated_at_utc.tzinfo is None:
            raise ValueError("ReconciliationReport.generated_at_utc must be timezone-aware")

    @property
    def recognized_count(self) -> int:
        return sum(1 for f in self.findings if f.recognized)

    @property
    def unrecognized_count(self) -> int:
        return sum(1 for f in self.findings if not f.recognized)

    @property
    def to_close(self) -> tuple[ReconciliationFinding, ...]:
        return tuple(f for f in self.findings if f.action is ReconciliationAction.CLOSE)

    @property
    def to_review(self) -> tuple[ReconciliationFinding, ...]:
        return tuple(f for f in self.findings if f.action is ReconciliationAction.FLAG_FOR_REVIEW)


# A pluggable policy hook: given one Position and the configured magic
# number, decide its ReconciliationFinding. reconcile()'s own default
# (DEFAULT_RECONCILIATION_POLICY in reconciliation.py) is purely
# mechanical -- magic-number match or not -- because Phase 16 has no
# strategy/signal context to check a position against yet (IVOStrategy
# still returns a bare Decision; see vo.interfaces.signals' WIRING NOTE
# and architecture/vo-phase-plan.md SS14-notes). A richer policy that
# actually evaluates "does this position still match a live VO trade
# signal" needs Phase 18's strategy to exist first, and can be supplied
# here without changing this module once it does.
ReconciliationPolicy = Callable[[Position, int], ReconciliationFinding]
