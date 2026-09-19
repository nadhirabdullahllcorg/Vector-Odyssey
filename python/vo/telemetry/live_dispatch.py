"""
The last seam: an approved TradeSignal actually reaching the broker.

Until this module, "no live orders" was true in this codebase by
OMISSION -- ExecutionRouter.place() existed, was tested, and was called
from nowhere. That was the right posture while nothing had ever been
commissioned. This is the deliberate, reviewable place where that
changes, and it is built so the omission stays the default: a
VOEaRuntime with no LiveDispatcher attached behaves exactly as it did
before, producing a TradeSignal and logging it undispatched.

THE ORDER OF THE GATES IS THE POINT. A signal passes, in sequence:

  1. COMMISSIONED. Has the preflight proven this session's execution
     path against the real broker? Until it has, nothing is dispatched
     at all -- see vo.execution.preflight for why first contact with a
     live order should be a micro-lot placed on purpose.
  2. COMPLIANCE. A fresh account snapshot, evaluated now: daily halt,
     drawdown, news blackout. The verdict must allow.
  3. HEADROOM. Would THIS trade, losing in full, carry drawdown past the
     day's halt point? Refused beforehand rather than discovered after.
  4. APPROVAL. vo.compliance.engine.approve_trade is the only
     constructor of a ComplianceApproval (gate G15), and
     ExecutionRouter.place() requires one -- so there is no path from
     here to the broker that skips step 2.
  5. DISPATCH.

Every refusal is recorded with its reason rather than returning a bare
None, matching the "every rejection carries a reason" discipline that
runs from RiskCheck through ComplianceVerdict. A day with no trades
should be explicable afterwards, and "the dispatcher said no" is not an
explanation.

NOTHING HERE DECIDES WHAT TO TRADE. The signal arrives already sized and
already risk-checked; this module only decides whether it may go, and
sends it if so.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from vo.compliance.engine import ComplianceEngine, approve_trade
from vo.execution.preflight import PreflightReport, PreflightSequence
from vo.execution.router import ExecutionRouter
from vo.execution.types import ExecutionEvent
from vo.interfaces.compliance import ComplianceVerdict
from vo.interfaces.economic_events import EconomicEvent
from vo.interfaces.signals import TradeSignal
from vo.market.account import AccountState
from vo.market.symbol import Symbol


class DispatchOutcome(Enum):
    """What happened to one TradeSignal at this seam."""

    SENT = "SENT"
    NOT_COMMISSIONED = "NOT_COMMISSIONED"
    COMPLIANCE_BLOCKED = "COMPLIANCE_BLOCKED"
    INSUFFICIENT_HEADROOM = "INSUFFICIENT_HEADROOM"
    REJECTED_BY_BROKER = "REJECTED_BY_BROKER"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """One signal's fate, always with a reason when it did not go."""

    outcome: DispatchOutcome
    trade_signal_id: str
    generated_at_utc: datetime
    reason: str | None = None
    verdict: ComplianceVerdict | None = None
    event: ExecutionEvent | None = None

    def __post_init__(self) -> None:
        if self.outcome is not DispatchOutcome.SENT and not self.reason:
            raise ValueError(
                f"a {self.outcome} DispatchResult must carry a reason -- "
                f"a flat day has to be explicable afterwards"
            )

    @property
    def sent(self) -> bool:
        return self.outcome is DispatchOutcome.SENT


def projected_loss(trade_signal: TradeSignal, symbol: Symbol) -> float:
    """What this trade costs in account currency if it loses in full.

    The figure the headroom gate compares against. Computed from the
    signal's own entry/stop/volume and the symbol's tick economics --
    never estimated, since a wrong number here either blocks good trades
    or admits one that eats past the halt point.
    """
    if symbol.tick_size <= 0:
        raise ValueError(f"symbol {symbol.broker_symbol} has a non-positive tick_size")

    distance = abs(trade_signal.entry_reference_price - trade_signal.stop_price)
    ticks = distance / symbol.tick_size
    return ticks * symbol.tick_value * trade_signal.volume


@dataclass
class LiveDispatcher:
    """
    Holds the live-order seam for one process.

    `preflight` is optional ONLY so a caller can supply an
    already-commissioned report (a session that ran it earlier, a test).
    With neither a sequence nor a passed report, nothing dispatches --
    "uncommissioned" is the safe default and is never assumed away.
    """

    router: ExecutionRouter
    compliance: ComplianceEngine
    account_state: Callable[[], AccountState]
    now: Callable[[], datetime]
    now_ny: Callable[[], datetime]
    broker_symbol: str
    preflight: PreflightSequence | None = None
    preflight_volume: float = 0.01
    preflight_stop_distance: float = 50.0
    preflight_target_distance: float = 100.0
    preflight_trail_improvement: float = 20.0
    report: PreflightReport | None = None
    results: list[DispatchResult] = field(default_factory=list)

    @property
    def commissioned(self) -> bool:
        return self.report is not None and self.report.passed

    def commission(self) -> PreflightReport | None:
        """Run the preflight once. Subsequent calls return the existing
        report rather than placing more orders -- commissioning is a
        per-session event, not a per-poll one."""
        if self.report is not None:
            return self.report
        if self.preflight is None:
            return None

        self.report = self.preflight.run(
            broker_symbol=self.broker_symbol,
            volume=self.preflight_volume,
            stop_distance=self.preflight_stop_distance,
            target_distance=self.preflight_target_distance,
            trail_improvement=self.preflight_trail_improvement,
        )
        return self.report

    def dispatch(
        self,
        trade_signal: TradeSignal,
        *,
        symbol: Symbol,
        upcoming_events: Sequence[EconomicEvent] = (),
    ) -> DispatchResult:
        """Run one signal through every gate, and send it if all pass."""
        at = self.now()

        if not self.commissioned:
            return self._record(
                DispatchResult(
                    outcome=DispatchOutcome.NOT_COMMISSIONED,
                    trade_signal_id=trade_signal.object_id,
                    generated_at_utc=at,
                    reason=(
                        "the execution path has not been proven against the broker this "
                        "session -- run the preflight before anything is dispatched"
                    ),
                )
            )

        account = self.account_state()
        verdict = self.compliance.on_snapshot(
            object_id=f"{self.broker_symbol}:COMPLIANCE:{at.isoformat()}",
            generated_at_utc=at,
            now_ny=self.now_ny(),
            account=account,
            upcoming_events=upcoming_events,
        )

        if not verdict.allowed:
            return self._record(
                DispatchResult(
                    outcome=DispatchOutcome.COMPLIANCE_BLOCKED,
                    trade_signal_id=trade_signal.object_id,
                    generated_at_utc=at,
                    reason=verdict.reason or str(verdict.status),
                    verdict=verdict,
                )
            )

        headroom_rejection = self.compliance.headroom_rejection(
            account=account, projected_loss=projected_loss(trade_signal, symbol)
        )
        if headroom_rejection is not None:
            return self._record(
                DispatchResult(
                    outcome=DispatchOutcome.INSUFFICIENT_HEADROOM,
                    trade_signal_id=trade_signal.object_id,
                    generated_at_utc=at,
                    reason=headroom_rejection,
                    verdict=verdict,
                )
            )

        approval = approve_trade(
            verdict,
            trade_signal,
            object_id=f"{self.broker_symbol}:APPROVAL:{at.isoformat()}",
        )
        event = self.router.place(
            trade_signal, approval, broker_symbol=self.broker_symbol
        )

        if not event.order_result.approved:
            return self._record(
                DispatchResult(
                    outcome=DispatchOutcome.REJECTED_BY_BROKER,
                    trade_signal_id=trade_signal.object_id,
                    generated_at_utc=at,
                    reason=(
                        f"{event.order_result.failure_reason} "
                        f"(retcode {event.order_result.retcode}: "
                        f"{event.order_result.broker_comment})"
                    ),
                    verdict=verdict,
                    event=event,
                )
            )

        return self._record(
            DispatchResult(
                outcome=DispatchOutcome.SENT,
                trade_signal_id=trade_signal.object_id,
                generated_at_utc=at,
                verdict=verdict,
                event=event,
            )
        )

    def _record(self, result: DispatchResult) -> DispatchResult:
        self.results.append(result)
        return result
