"""vo.interfaces.signals / vo.signals.generator -- Phase 14, stage 1."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.interfaces.decisions import Decision, Direction
from vo.interfaces.signals import Signal, SignalError, StrategyOutput
from vo.signals.generator import generate_signal

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)


def _proposal(**overrides):
    defaults = dict(
        decision=Decision.SIGNAL_PROPOSED,
        direction=Direction.LONG,
        reference_price=25000.0,
        stop_price=24950.0,
        rationale="test setup",
    )
    defaults.update(overrides)
    return StrategyOutput(**defaults)


def test_no_signal_output_needs_no_prices():
    output = StrategyOutput(
        decision=Decision.NO_SIGNAL,
        direction=Direction.NEUTRAL,
        reference_price=None,
        stop_price=None,
        rationale="nothing to evaluate this bar",
    )
    assert output.decision is Decision.NO_SIGNAL


def test_no_trade_output_needs_no_prices():
    output = StrategyOutput(
        decision=Decision.NO_TRADE,
        direction=Direction.NEUTRAL,
        reference_price=None,
        stop_price=None,
        rationale="setup present but declined",
    )
    assert output.decision is Decision.NO_TRADE


def test_signal_proposed_output_valid():
    output = _proposal()
    assert output.direction is Direction.LONG


def test_rejects_blank_rationale():
    with pytest.raises(SignalError):
        StrategyOutput(
            decision=Decision.NO_TRADE,
            direction=Direction.NEUTRAL,
            reference_price=None,
            stop_price=None,
            rationale="   ",
        )


def test_rejects_downstream_only_decisions():
    for decision in (
        Decision.SIGNAL_REJECTED,
        Decision.RISK_REJECTED,
        Decision.TRADE_APPROVED,
        Decision.TRADE_EXECUTED,
    ):
        with pytest.raises(SignalError):
            StrategyOutput(
                decision=decision,
                direction=Direction.NEUTRAL,
                reference_price=None,
                stop_price=None,
                rationale="not a strategy-owned outcome",
            )


def test_signal_proposed_requires_direction():
    with pytest.raises(SignalError):
        _proposal(direction=Direction.NEUTRAL)


def test_signal_proposed_requires_reference_price():
    with pytest.raises(SignalError):
        _proposal(reference_price=None)


def test_signal_proposed_requires_stop_price():
    with pytest.raises(SignalError):
        _proposal(stop_price=None)


def test_signal_proposed_rejects_non_positive_reference_price():
    with pytest.raises(SignalError):
        _proposal(reference_price=0.0)


def test_signal_proposed_rejects_equal_stop_and_reference():
    with pytest.raises(SignalError):
        _proposal(stop_price=25000.0, reference_price=25000.0)


def test_generate_signal_carries_the_proposal_through():
    output = _proposal()
    signal = generate_signal(
        output,
        object_id="SIG-000001",
        instrument_id="US100",
        generated_at_utc=_NOW,
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
        regime_state_id="REGIME-000184",
        regime_state="EXPANSION",
        features={"atr": 12.5, "efficiency_ratio": 0.61},
    )
    assert signal.decision is Decision.SIGNAL_PROPOSED
    assert signal.is_proposal
    assert signal.direction is Direction.LONG
    assert signal.reference_price == 25000.0
    assert signal.stop_price == 24950.0
    assert signal.regime_state == "EXPANSION"
    assert signal.features["atr"] == 12.5
    assert signal.rejection_reason is None


def test_generate_signal_is_not_a_proposal_for_no_trade():
    output = StrategyOutput(
        decision=Decision.NO_TRADE,
        direction=Direction.NEUTRAL,
        reference_price=None,
        stop_price=None,
        rationale="no setup this bar",
    )
    signal = generate_signal(
        output,
        object_id="SIG-000002",
        instrument_id="US100",
        generated_at_utc=_NOW,
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
    )
    assert not signal.is_proposal


def test_generate_signal_features_are_immutable():
    output = _proposal()
    signal = generate_signal(
        output,
        object_id="SIG-000003",
        instrument_id="US100",
        generated_at_utc=_NOW,
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
        features={"atr": 12.5},
    )
    with pytest.raises(TypeError):
        signal.features["atr"] = 99.0  # type: ignore[index]


def test_signal_rejects_naive_datetime():
    output = _proposal()
    with pytest.raises(SignalError):
        generate_signal(
            output,
            object_id="SIG-000004",
            instrument_id="US100",
            generated_at_utc=datetime(2026, 9, 18, 14, 30),  # naive
            strategy_id="disposable_v0",
            strategy_version="0.1.0",
        )


def test_signal_rejected_requires_reason():
    with pytest.raises(SignalError):
        Signal(
            object_id="SIG-000005",
            instrument_id="US100",
            generated_at_utc=_NOW,
            strategy_id="disposable_v0",
            strategy_version="0.1.0",
            decision=Decision.SIGNAL_REJECTED,
            direction=Direction.NEUTRAL,
            reference_price=None,
            stop_price=None,
            regime_state_id=None,
            regime_state=None,
            features={},
            rationale="setup looked plausible",
            rejection_reason=None,
        )


def test_signal_rejected_with_reason_is_valid():
    signal = Signal(
        object_id="SIG-000006",
        instrument_id="US100",
        generated_at_utc=_NOW,
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
        decision=Decision.SIGNAL_REJECTED,
        direction=Direction.NEUTRAL,
        reference_price=None,
        stop_price=None,
        regime_state_id=None,
        regime_state=None,
        features={},
        rationale="setup looked plausible",
        rejection_reason="strategy_id not registered",
    )
    assert not signal.is_proposal
