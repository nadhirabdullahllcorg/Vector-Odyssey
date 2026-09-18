"""
Signal Generator -- Phase 14, first stage of the Signal / AllocationSignal
/ RiskCheck / TradeSignal pipeline (architecture/vo-phase-plan.md's Block 4
Phase 14 row; architecture/vo-architecture-audit.md F.2: "Signal generator
-> Signal with full audit payload | Signal carries feature + market-state
snapshot; cannot reach execution").

generate_signal() is a pure function: given a StrategyOutput (what one
evaluation proposed) plus the context that produced it, it returns an
always-produced, always-auditable Signal. It never reaches outward for a
clock, a file, or MT5 -- everything it needs arrives as an argument, the
same "given, not fetched" discipline vo.observation.atr and
vo.observation.regime already follow.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from vo.interfaces.signals import Signal, StrategyOutput


def generate_signal(
    output: StrategyOutput,
    *,
    object_id: str,
    instrument_id: str,
    generated_at_utc: datetime,
    strategy_id: str,
    strategy_version: str,
    regime_state_id: str | None = None,
    regime_state: str | None = None,
    features: Mapping[str, float] | None = None,
) -> Signal:
    """
    Wrap one strategy evaluation into an auditable Signal.

    `output` was already validated at construction
    (vo.interfaces.signals.StrategyOutput.__post_init__) -- a
    SIGNAL_PROPOSED output is structurally sound (a real direction, a
    positive reference_price and stop_price distinct from it) by the time
    it reaches here. Nothing at signal-generation time today makes a
    structurally sound proposal untradeable -- a future check belongs
    here, producing a Decision.SIGNAL_REJECTED Signal with a
    rejection_reason, only if it is genuinely about the SIGNAL itself
    (e.g. an unregistered strategy_id) rather than capital or risk, which
    belong to the next two stages. None exist yet, so every structurally
    valid StrategyOutput passes through unchanged today.
    """
    return Signal(
        object_id=object_id,
        instrument_id=instrument_id,
        generated_at_utc=generated_at_utc,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        decision=output.decision,
        direction=output.direction,
        reference_price=output.reference_price,
        stop_price=output.stop_price,
        regime_state_id=regime_state_id,
        regime_state=regime_state,
        features=features or {},
        rationale=output.rationale,
        rejection_reason=None,
    )
