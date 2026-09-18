"""
Trade-pipeline plumbing -- wiring Phase 14 (vo.signals -> vo.allocation ->
vo.risk) into a running VOEaRuntime, WITHOUT answering the question Phase
14 itself deferred to Phase 18: how IVOStrategy.on_bar's bare Decision
becomes a real StrategyOutput (see vo.interfaces.signals' own WIRING
NOTE, and architecture/vo-phase-plan.md SS14-notes' "two honest gaps").

This module does not answer that question either. TradeEvaluationHooks
is deliberately disposable scaffolding: an optional strategy/signal hook
that VOEaRuntime can be given, unset by default. With no hooks attached
(the only state that exists in production today), nothing here runs --
poll_once() behaves exactly as it did before this module existed. Phase
18 becomes "implement IVOStrategy for real, and attach a TradeEvaluation-
Hooks that bridges it to this pipeline" -- or something else entirely,
once a real strategy exists to shape that bridge against -- not "also
wire the pipeline", since this module already proves the pipeline wiring
correct and tested ahead of that decision.

Deliberately NOT part of vo.interfaces.strategy: IVOStrategy.on_bar still
returns a bare Decision, unchanged. TradeEvaluationHooks.propose is a
plain callable, not a Protocol method on IVOStrategy, so attaching it
never implies IVOStrategy's shape has changed.

vo.telemetry is layer 11 (bumped from 10 to make room for vo.execution,
layer 10, this same phase) -- this module may import vo.signals (6),
vo.allocation (7), vo.risk (8), and vo.market.account (1) freely.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from vo.allocation import allocate
from vo.core.pipeline import PipelineSnapshot
from vo.interfaces.signals import AllocationSignal, RiskCheck, Signal, StrategyOutput, TradeSignal
from vo.market.account import AccountState
from vo.market.symbol import Symbol
from vo.risk import RiskConfig, build_trade_signal, evaluate_risk
from vo.signals import generate_signal


@dataclass(frozen=True)
class TradeEvaluationHooks:
    """Optional, disposable scaffolding -- see this module's own
    docstring. Every field is a callable/value the caller supplies; none
    of them are fetched by this module itself, matching the "given, not
    fetched" discipline vo.observation.atr and vo.risk.manager already
    follow."""

    strategy_id: str
    strategy_version: str
    propose: Callable[[PipelineSnapshot], StrategyOutput | None]
    """Returns None when there is nothing to evaluate yet (e.g. no
    completed bar) -- distinct from a StrategyOutput carrying NO_SIGNAL/
    NO_TRADE, which IS a real evaluation outcome and still flows through
    the full pipeline below."""
    account_state: Callable[[], AccountState]
    open_position_count: Callable[[], int]
    risk_config: RiskConfig


@dataclass(frozen=True)
class TradePipelineResult:
    """Everything Phase 14's pipeline produced for one bar -- always a
    Signal and an AllocationSignal (Phase 14's own "every stage is always
    produced" discipline); risk_check/trade_signal are None only when
    account/symbol context was missing (see evaluate_trade_pipeline's own
    docstring), not on an ordinary rejection, which still produces a
    RiskCheck with approved=False."""

    signal: Signal
    allocation: AllocationSignal
    risk_check: RiskCheck | None
    trade_signal: TradeSignal | None


def evaluate_trade_pipeline(
    snapshot: PipelineSnapshot,
    hooks: TradeEvaluationHooks,
    *,
    symbol: Symbol | None,
) -> TradePipelineResult | None:
    """Runs one bar through generate_signal -> allocate -> evaluate_risk
    -> build_trade_signal. Returns None only when `hooks.propose` itself
    returned None (nothing to evaluate this bar) -- once a StrategyOutput
    exists, a Signal and an AllocationSignal are always produced, exactly
    matching Phase 14's own "every rejection carries a reason" discipline.

    `symbol` being None (no Symbol record observed on the wire yet) is
    the one honest gap this function itself introduces: evaluate_risk
    needs tick_size/tick_value to size a position, and there is nothing
    to substitute for a real Symbol -- so risk_check/trade_signal come
    back None rather than guessing, and the Signal/AllocationSignal
    stages (which do not need a Symbol) still run and are returned."""
    if snapshot.instrument is None:
        return None

    output = hooks.propose(snapshot)
    if output is None:
        return None

    now = datetime.now(UTC)
    instrument_key = snapshot.instrument.key

    signal = generate_signal(
        output,
        object_id=f"{instrument_key}:SIGNAL:{now.isoformat()}",
        instrument_id=instrument_key,
        generated_at_utc=now,
        strategy_id=hooks.strategy_id,
        strategy_version=hooks.strategy_version,
    )

    allocation = allocate(
        signal,
        object_id=f"{instrument_key}:ALLOC:{now.isoformat()}",
        generated_at_utc=now,
        account=hooks.account_state(),
        risk_fraction=hooks.risk_config.risk_per_trade_fraction,
    )

    if symbol is None:
        return TradePipelineResult(
            signal=signal, allocation=allocation, risk_check=None, trade_signal=None
        )

    risk_check = evaluate_risk(
        signal,
        allocation,
        object_id=f"{instrument_key}:RISK:{now.isoformat()}",
        generated_at_utc=now,
        account=hooks.account_state(),
        symbol=symbol,
        config=hooks.risk_config,
        open_position_count=hooks.open_position_count(),
    )

    trade_signal = None
    if risk_check.approved:
        trade_signal = build_trade_signal(
            risk_check,
            object_id=f"{instrument_key}:TRADE:{now.isoformat()}",
            instrument_id=instrument_key,
            generated_at_utc=now,
        )

    return TradePipelineResult(
        signal=signal, allocation=allocation, risk_check=risk_check, trade_signal=trade_signal
    )
