"""
Restart reconciliation -- Phase 16's own gate: "unexpected pre-existing
position detected, not ignored" (architecture/vo-phase-plan.md's Block 4
Phase 16 row).

reconcile() is a pure function (given, not fetched, the same discipline
as vo.observation.atr): hand it whatever positions MT5 currently reports
open plus the configured magic number, and it classifies every one --
never silently skips a position, never treats "I don't recognize this"
as "ignore this".

DEFAULT POLICY, mechanical and honestly limited: a position is
`recognized` only if its `magic` field matches ExecutionConfig's
configured magic_number -- the one durable-across-restart signal Phase
16 has. Per the user's own explicit direction for this gate ("detect,
evaluate conditions, and eliminate any trade which doesn't follow
strategy rules and VO trade signal confirmations"), an unrecognized
position's default recommended action is CLOSE, not merely a warning --
but see this module's own "NOT AUTOMATIC" note below before assuming
that closes anything by itself.

What this default policy CANNOT do, honestly: check whether an open
position still matches a currently-live VO trade signal or the active
strategy's rules -- that needs Phase 18's real strategy and signal
history to exist, which Phase 16 does not have (see vo.execution.types'
ReconciliationPolicy docstring). Magic-number matching is a necessary,
not sufficient, proxy for "VO placed this and it is still sanctioned";
a deeper, strategy-aware ReconciliationPolicy is exactly the seam Phase
18 is expected to supply once it can actually answer that question.

NOT AUTOMATIC: reconcile() only classifies and recommends. Nothing in
this codebase's own runtime paths calls execute_reconciliation_actions()
on startup yet -- actually sending a close order is real, live-capable
code (see vo.core.mt5.MT5ExecutionClient.send_order), and wiring it into
an unattended startup path is deliberately left to whichever phase turns
on live order-sending in the first place (Phase 18), not assumed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from vo.core.mt5 import (
    MT5ExecutionError,
    OrderRequest,
    OrderResult,
    TerminalExecutionApi,
    build_close_request,
)
from vo.execution.types import (
    ReconciliationAction,
    ReconciliationFinding,
    ReconciliationPolicy,
    ReconciliationReport,
)
from vo.market.account import Position


def _default_policy(position: Position, magic_number: int) -> ReconciliationFinding:
    if position.magic == magic_number:
        return ReconciliationFinding(
            position=position,
            recognized=True,
            action=ReconciliationAction.KEEP,
            reason=f"magic {position.magic} matches this EA's configured magic_number",
        )

    return ReconciliationFinding(
        position=position,
        recognized=False,
        action=ReconciliationAction.CLOSE,
        reason=(
            f"magic {position.magic} does not match this EA's configured "
            f"magic_number ({magic_number}) -- not opened by this EA, and does not "
            "follow VO's strategy rules or carry a VO trade signal confirmation"
        ),
    )


DEFAULT_RECONCILIATION_POLICY: ReconciliationPolicy = _default_policy


def reconcile(
    positions: Sequence[Position],
    *,
    magic_number: int,
    now: datetime,
    policy: ReconciliationPolicy = DEFAULT_RECONCILIATION_POLICY,
) -> ReconciliationReport:
    """Classify every currently-open position. `now` is given, not
    fetched -- the caller (whichever process runs this at startup) owns
    the clock."""
    findings = tuple(policy(position, magic_number) for position in positions)
    return ReconciliationReport(generated_at_utc=now, findings=findings)


def execute_reconciliation_actions(
    report: ReconciliationReport,
    client: TerminalExecutionApi,
    *,
    magic: int,
    comment: str,
    deviation_points: int,
) -> tuple[OrderResult, ...]:
    """Actually sends a close order for every CLOSE-recommended finding
    in `report`, via `client` (real MT5ExecutionClient, or a fake in
    tests). Deliberately does nothing for KEEP or FLAG_FOR_REVIEW
    findings -- only CLOSE is ever acted on, and only when this function
    is called explicitly (see this module's own NOT AUTOMATIC note; the
    live VO_EA runtime does not call this today)."""
    results: list[OrderResult] = []
    for finding in report.to_close:
        request: OrderRequest = build_close_request(
            finding.position,
            magic=magic,
            comment=comment,
            deviation_points=deviation_points,
        )
        try:
            results.append(client.send_order(request))
        except MT5ExecutionError:
            raise
    return tuple(results)
