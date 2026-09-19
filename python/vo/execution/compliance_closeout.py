"""
vo.execution.compliance_closeout -- closes every open position and
cancels every pending order the moment the Account Compliance Engine
(vo.compliance) reports a breach (BREACHED_DAILY or BREACHED_TOTAL),
mirroring PropFirmGuard's own documented behavior on breach: "closes all
positions by ticket one by one, deletes all pending orders, blocks
trading." Built 2026-09-19 at the user's own explicit correction: VO's
compliance engine previously only vetoed NEW trades (gate G15 /
ComplianceApproval) and left already-open positions running untouched --
a real gap from the reference behavior, not a deliberate design choice.

DELIBERATELY ITS OWN REPORT TYPE, not a reuse of
vo.execution.reconciliation's ReconciliationFinding/Report. Those encode
"recognized (VO's own, keep) vs unrecognized (not VO's, close)" --
provenance by magic number. A breach closeout closes EVERY open position
and pending order regardless of provenance (a stray, unrecognized
position is exactly as dangerous to the account during a breach as one
VO itself opened) and for a completely different reason (the ACCOUNT
breached a survival limit, not "this position's origin is unknown") --
reusing ReconciliationFinding here would either corrupt its own
invariant ("a recognized position should never default to CLOSE") or
require mislabeling every VO-owned position as unrecognized. Two
different questions, two different types.

NOT AUTOMATIC, same posture as every other execution primitive in this
project (ExecutionRouter.place(), execute_reconciliation_actions()):
plan_breach_closeout() only classifies; nothing in this codebase's own
runtime paths calls execute_breach_closeout() yet. Wiring it into a live
loop -- almost certainly right alongside ComplianceEngine.on_snapshot()
itself, since a closeout plan is meaningless without a fresh verdict --
is Phase 18's own decision, not assumed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from vo.core.mt5 import (
    MT5ExecutionError,
    OrderRequest,
    OrderResult,
    TerminalExecutionApi,
    build_cancel_request,
    build_close_request,
)
from vo.interfaces.compliance import ComplianceStatus, ComplianceVerdict
from vo.market.account import Order, Position

_BREACH_STATUSES = frozenset({ComplianceStatus.BREACHED_DAILY, ComplianceStatus.BREACHED_TOTAL})


@dataclass(frozen=True, slots=True)
class BreachCloseoutPlan:
    """What plan_breach_closeout() recommends, given one ComplianceVerdict
    and the account's current positions/pending orders.
    `triggered=False` (the verdict was not a breach) always carries empty
    position/order tuples -- never a partial plan a caller could misread
    as "close some of them"."""

    generated_at_utc: datetime
    triggered: bool
    status: ComplianceStatus
    reason: str | None
    positions_to_close: tuple[Position, ...]
    orders_to_cancel: tuple[Order, ...]

    def __post_init__(self) -> None:
        if self.generated_at_utc.tzinfo is None:
            raise ValueError("BreachCloseoutPlan.generated_at_utc must be tz-aware")
        if not self.triggered and (self.positions_to_close or self.orders_to_cancel):
            raise ValueError("an untriggered BreachCloseoutPlan must recommend closing nothing")
        if self.triggered and not (self.reason and self.reason.strip()):
            raise ValueError("a triggered BreachCloseoutPlan must carry a reason")


def plan_breach_closeout(
    verdict: ComplianceVerdict,
    *,
    positions: Sequence[Position],
    orders: Sequence[Order],
) -> BreachCloseoutPlan:
    """Pure function -- given, not fetched, the same discipline as
    vo.execution.reconciliation.reconcile()/vo.risk.evaluate_risk. Only
    BREACHED_DAILY/BREACHED_TOTAL trigger a closeout; NEWS_BLACKOUT/
    WARNING/CRITICAL/SAFE never do -- those are temporary or purely
    informational, not an account-survival emergency PropFirmGuard's own
    close-everything response is calibrated for."""
    if verdict.status not in _BREACH_STATUSES:
        return BreachCloseoutPlan(
            generated_at_utc=verdict.generated_at_utc,
            triggered=False,
            status=verdict.status,
            reason=None,
            positions_to_close=(),
            orders_to_cancel=(),
        )
    return BreachCloseoutPlan(
        generated_at_utc=verdict.generated_at_utc,
        triggered=True,
        status=verdict.status,
        reason=verdict.reason,
        positions_to_close=tuple(positions),
        orders_to_cancel=tuple(orders),
    )


def execute_breach_closeout(
    plan: BreachCloseoutPlan,
    client: TerminalExecutionApi,
    *,
    magic: int,
    comment: str,
    deviation_points: int,
) -> tuple[OrderResult, ...]:
    """Actually sends a close order for every position and a cancel order
    for every pending order in the plan, via `client` (real
    MT5ExecutionClient, or a fake in tests). Returns nothing and sends
    nothing for an untriggered plan. Positions are closed before pending
    orders are cancelled -- an open position is the account's actual live
    risk; a pending order that never fills is not, so if only one kind
    can be reached before something else goes wrong, closing positions
    first is the more defensible order."""
    if not plan.triggered:
        return ()

    results: list[OrderResult] = []

    for position in plan.positions_to_close:
        close_request: OrderRequest = build_close_request(
            position, magic=magic, comment=comment, deviation_points=deviation_points
        )
        try:
            results.append(client.send_order(close_request))
        except MT5ExecutionError:
            raise

    for order in plan.orders_to_cancel:
        cancel_request: OrderRequest = build_cancel_request(order, magic=magic, comment=comment)
        try:
            results.append(client.send_order(cancel_request))
        except MT5ExecutionError:
            raise

    return tuple(results)
