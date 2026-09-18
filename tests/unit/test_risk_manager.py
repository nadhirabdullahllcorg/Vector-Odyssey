"""vo.risk.manager -- Phase 14's Risk Engine, stage 3, and TradeSignal."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.interfaces.decisions import Decision, Direction
from vo.interfaces.signals import (
    AllocationSignal,
    RiskCheck,
    RiskCheckError,
    Signal,
    TradeSignal,
)
from vo.market.account import AccountState
from vo.market.symbol import Symbol
from vo.risk.manager import build_trade_signal, evaluate_risk
from vo.risk.risk_config import RiskConfig

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


def _symbol(**overrides):
    defaults = dict(
        broker_symbol="US100",
        description="NASDAQ 100",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=10.0,
        source="MT5",
    )
    defaults.update(overrides)
    return Symbol(**defaults)


def _config(**overrides):
    defaults = dict(
        version=1,
        risk_per_trade_fraction=0.01,
        min_volume=0.01,
        max_volume=5.0,
        volume_step=0.01,
        max_open_positions=1,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


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


def _allocation(approved=True, **overrides):
    defaults = dict(
        object_id="ALLOC-1",
        signal_id="SIG-1",
        generated_at_utc=_NOW,
        approved=approved,
        risk_fraction=0.01 if approved else None,
        reason=None if approved else "declined",
    )
    defaults.update(overrides)
    return AllocationSignal(**defaults)


def _evaluate(**overrides):
    kwargs = dict(
        signal=_signal(),
        allocation=_allocation(),
        object_id="RISK-1",
        generated_at_utc=_NOW,
        account=_account(),
        symbol=_symbol(),
        config=_config(),
        open_position_count=0,
    )
    kwargs.update(overrides)
    return evaluate_risk(**kwargs)


def test_sizes_a_position_from_equity_and_stop_distance():
    check = _evaluate()
    assert check.approved
    # equity 10_000 * risk_fraction 0.01 = 100 risked; stop_distance 50 *
    # value_per_price_unit (tick_value/tick_size = 1.0) = 50 risk/lot ->
    # 100 / 50 = 2.0 lots, already aligned to the 0.01 volume_step.
    assert check.volume == pytest.approx(2.0)
    assert check.direction is Direction.LONG
    assert check.entry_reference_price == 25000.0
    assert check.stop_price == 24950.0
    assert check.reason is None


def test_rejects_when_allocation_declined():
    check = _evaluate(allocation=_allocation(approved=False, reason="account disabled"))
    assert not check.approved
    assert "account disabled" in check.reason


def test_rejects_a_non_proposal_signal():
    check = _evaluate(signal=_signal(decision=Decision.NO_TRADE))
    assert not check.approved
    assert "NO_TRADE" in check.reason


def test_rejects_at_max_open_positions():
    check = _evaluate(open_position_count=1, config=_config(max_open_positions=1))
    assert not check.approved
    assert "max_open_positions" in check.reason


def test_rejects_when_trading_not_allowed():
    check = _evaluate(account=_account(trade_allowed=False))
    assert not check.approved


def test_rejects_when_no_free_margin():
    check = _evaluate(account=_account(margin_free=0.0))
    assert not check.approved
    assert "margin_free" in check.reason


def test_rejects_when_risk_amount_too_small_for_min_volume():
    check = _evaluate(account=_account(equity=10.0))
    assert not check.approved
    assert "minimum volume" in check.reason


def test_caps_volume_at_max_volume():
    check = _evaluate(
        account=_account(equity=10_000_000.0),
        config=_config(max_volume=5.0),
    )
    assert check.approved
    assert check.volume == pytest.approx(5.0)


def test_build_trade_signal_from_an_approved_check():
    check = _evaluate()
    trade = build_trade_signal(
        check,
        object_id="TRADE-1",
        instrument_id="US100",
        generated_at_utc=_NOW,
    )
    assert isinstance(trade, TradeSignal)
    assert trade.volume == check.volume
    assert trade.direction is Direction.LONG
    assert trade.entry_reference_price == 25000.0
    assert trade.stop_price == 24950.0


def test_build_trade_signal_refuses_a_rejected_check():
    check = _evaluate(signal=_signal(decision=Decision.NO_TRADE))
    with pytest.raises(RiskCheckError):
        build_trade_signal(
            check,
            object_id="TRADE-2",
            instrument_id="US100",
            generated_at_utc=_NOW,
        )


def test_risk_check_approved_requires_direction():
    with pytest.raises(RiskCheckError):
        RiskCheck(
            object_id="R",
            signal_id="S",
            generated_at_utc=_NOW,
            approved=True,
            reason=None,
            direction=None,
            volume=1.0,
            entry_reference_price=25000.0,
            stop_price=24950.0,
            take_profit_price=None,
        )


def test_risk_check_declined_requires_reason():
    with pytest.raises(RiskCheckError):
        RiskCheck(
            object_id="R",
            signal_id="S",
            generated_at_utc=_NOW,
            approved=False,
            reason=None,
            direction=None,
            volume=None,
            entry_reference_price=None,
            stop_price=None,
            take_profit_price=None,
        )


def test_trade_signal_rejects_neutral_direction():
    with pytest.raises(RiskCheckError):
        TradeSignal(
            object_id="T",
            risk_check_id="R",
            instrument_id="US100",
            generated_at_utc=_NOW,
            direction=Direction.NEUTRAL,
            volume=1.0,
            entry_reference_price=25000.0,
            stop_price=24950.0,
            take_profit_price=None,
        )


def test_trade_signal_rejects_non_positive_volume():
    with pytest.raises(RiskCheckError):
        TradeSignal(
            object_id="T",
            risk_check_id="R",
            instrument_id="US100",
            generated_at_utc=_NOW,
            direction=Direction.LONG,
            volume=0.0,
            entry_reference_price=25000.0,
            stop_price=24950.0,
            take_profit_price=None,
        )
