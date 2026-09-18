"""
ExecutionRouter -- Phase 16's second responsibility, order/position
state (the first, reconciliation, lives in vo.execution.reconciliation).
Takes an approved TradeSignal (Phase 14's sole output), tags it with this
EA's magic number/comment (ExecutionConfig), sends it via a
TerminalExecutionApi (real MT5ExecutionClient, or a fake in tests), and
keeps an in-memory record of what it has placed -- the "order/position
state" half of Phase 16's gate.

NOT wired to anything automatic: nothing in vo.telemetry or
scripts/run_vo_ea.py constructs an ExecutionRouter or calls place() from
a live, unattended path yet -- see vo.execution's own module docstring
and architecture/vo-phase-plan.md's Phase 18 gate (the first live trade).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from vo.core.mt5 import TerminalExecutionApi, build_open_request
from vo.execution.execution_config import ExecutionConfig
from vo.execution.types import ExecutionEvent
from vo.interfaces.signals import TradeSignal


@dataclass
class ExecutionRouter:
    """One process's whole "TradeSignal -> MT5 order" seam. `client` is
    whatever implements TerminalExecutionApi.send_order -- real or fake.
    `known_tickets` grows as place() succeeds; it is in-memory only and
    intentionally does not survive a restart (see vo.execution.
    reconciliation for how a restart re-establishes which positions are
    this EA's own, via the magic number rather than this local memory)."""

    client: TerminalExecutionApi
    config: ExecutionConfig
    events: list[ExecutionEvent] = field(default_factory=list)
    known_tickets: dict[int, str] = field(default_factory=dict)
    """order_ticket -> the TradeSignal.object_id that opened it."""

    def place(self, trade_signal: TradeSignal, *, broker_symbol: str) -> ExecutionEvent:
        """Builds the OrderRequest, sends it, records the resulting
        ExecutionEvent (win or lose -- a rejected send is recorded just
        as faithfully as a filled one, matching Phase 14's "every
        rejection carries a reason" discipline one stage further down
        the pipeline)."""
        request = build_open_request(
            trade_signal,
            broker_symbol=broker_symbol,
            magic=self.config.magic_number,
            comment=self.config.comment_prefix,
            deviation_points=self.config.deviation_points,
        )
        result = self.client.send_order(request)

        event = ExecutionEvent(
            object_id=f"{trade_signal.object_id}:EXEC",
            trade_signal_id=trade_signal.object_id,
            generated_at_utc=datetime.now(UTC),
            order_result=result,
        )
        self.events.append(event)

        if result.approved and result.order_ticket is not None:
            self.known_tickets[result.order_ticket] = trade_signal.object_id

        return event
