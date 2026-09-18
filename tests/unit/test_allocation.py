"""vo.allocation.allocator -- Phase 14, stage 2."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.allocation.allocator import allocate
from vo.interfaces.decisions import Decision, Direction
from vo.interfaces.signals import AllocationError, AllocationSignal, Signal
from vo.market.account import AccountState

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)


def _account(**overrides):
    defaults = dict(
        login=130695,
        name="Test",
        server="1xTrade-Server",
        currency="USD",
        balance=10_000.0,
        equity=10_000.0,
        profit=0.0,
        margin=0.0,
        margin_free=10_000.0,
        margin_level=None,
        leverage=100,
        trade_allowed=True,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _signal(decision=Decision.SIGNAL_PROPOSED, **overrides):
    is_proposal = decision is Decision.SIGNAL_PROPOSED
    defaults = dict(
        object_id="SIG-1",
        instrument_id="US100",
        generated_at_utc=_NOW,
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
        decision=decision,
        direction=Direction.LONG if is_proposal else Direction.NEUTRAL,
        reference_price=25000.0 if is_proposal else None,
        stop_price=24950.0 if is_proposal else None,
        regime_state_id=None,
        regime_state=None,
        features={},
        rationale="test",
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_declines_a_non_proposal_signal():
    result = allocate(
        _signal(decision=Decision.NO_TRADE),
        object_id="ALLOC-1",
        generated_at_utc=_NOW,
        account=_account(),
        risk_fraction=0.01,
    )
    assert not result.approved
    assert result.risk_fraction is None
    assert "NO_TRADE" in result.reason


def test_declines_when_trading_not_allowed():
    result = allocate(
        _signal(),
        object_id="ALLOC-2",
        generated_at_utc=_NOW,
        account=_account(trade_allowed=False),
        risk_fraction=0.01,
    )
    assert not result.approved
    assert "130695" in result.reason


def test_approves_a_live_proposal():
    result = allocate(
        _signal(),
        object_id="ALLOC-3",
        generated_at_utc=_NOW,
        account=_account(),
        risk_fraction=0.02,
    )
    assert result.approved
    assert result.risk_fraction == 0.02
    assert result.reason is None


def test_allocation_signal_approved_requires_risk_fraction():
    with pytest.raises(AllocationError):
        AllocationSignal(
            object_id="A",
            signal_id="S",
            generated_at_utc=_NOW,
            approved=True,
            risk_fraction=None,
            reason=None,
        )


def test_allocation_signal_declined_requires_reason():
    with pytest.raises(AllocationError):
        AllocationSignal(
            object_id="A",
            signal_id="S",
            generated_at_utc=_NOW,
            approved=False,
            risk_fraction=None,
            reason=None,
        )


def test_allocation_signal_rejects_out_of_range_fraction():
    with pytest.raises(AllocationError):
        AllocationSignal(
            object_id="A",
            signal_id="S",
            generated_at_utc=_NOW,
            approved=True,
            risk_fraction=1.5,
            reason=None,
        )
