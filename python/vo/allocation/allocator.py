"""
Capital Allocator -- Phase 14, second stage of the Signal / AllocationSignal
/ RiskCheck / TradeSignal pipeline. Deliberately simple (architecture/
vo-architecture-audit.md's own words: "AllocationSignal -- contract
correct, behaviour deliberately simple"): this stage confirms capital
access is not withheld and hands the configured per-trade risk fraction
forward. It does not size a position -- that is vo.risk's job, once a
concrete stop distance and instrument tick economics are on hand.
"""

from __future__ import annotations

from datetime import datetime

from vo.interfaces.signals import AllocationSignal, Signal
from vo.market.account import AccountState


def allocate(
    signal: Signal,
    *,
    object_id: str,
    generated_at_utc: datetime,
    account: AccountState,
    risk_fraction: float,
) -> AllocationSignal:
    """
    Every rejection carries a reason (Phase 14's gate) -- checked in this
    order so the reason always names the first real blocker:

      1. the Signal itself is not a live proposal (NO_SIGNAL/NO_TRADE --
         nothing to allocate to)
      2. the account will not accept trades at all
         (AccountState.trade_allowed is False)
      3. otherwise approved, carrying `risk_fraction` forward for vo.risk
    """
    if not signal.is_proposal:
        return AllocationSignal(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            approved=False,
            risk_fraction=None,
            reason=f"signal decision is {signal.decision}, nothing to allocate",
        )

    if not account.trade_allowed:
        return AllocationSignal(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            approved=False,
            risk_fraction=None,
            reason=f"account {account.login} does not currently allow trading",
        )

    return AllocationSignal(
        object_id=object_id,
        signal_id=signal.object_id,
        generated_at_utc=generated_at_utc,
        approved=True,
        risk_fraction=risk_fraction,
        reason=None,
    )
