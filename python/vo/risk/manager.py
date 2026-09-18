"""
Risk Engine -- Phase 14's centerpiece (architecture/vo-phase-plan.md's
Block 4 Phase 14 row: "Risk manager - Signal / AllocationSignal /
RiskCheck / TradeSignal | Every rejection carries a reason; risk is the
sole TradeSignal producer").

evaluate_risk() takes a Signal, its AllocationSignal, the account's
current state and the instrument's tick economics, and returns an
always-produced RiskCheck: an explicit rejection with a reason, or an
approval carrying a concrete position size. build_trade_signal() is the
ONLY place in this codebase allowed to construct a TradeSignal
(tests/unit/test_architecture.py::test_risk_is_the_sole_trade_signal_producer
enforces this mechanically, scanning for TradeSignal( call sites outside
vo/risk/, the same AST-scan style gate G7 already uses for the
MetaTrader5 import).

SIZING, deliberately mechanical (Phase 32 scope, not this phase's):
position size is set so that a fill at `reference_price`, stopped out at
`stop_price`, loses exactly `risk_fraction` of the account's current
equity -- no ATR conditioning, no regime-conditioned multiplier, no
inverse-volatility scaling across instruments. Those are architecture/
vo-trade-logic-and-brain-plan.md's own confirmed Phase 32 scope ("ATR-
inverse position sizing across instruments with different volatility
profiles"), explicitly deferred, not guessed at here.

MARGIN, honestly incomplete: vo.market.account.AccountState carries
margin_free, but computing how much margin ONE new position would
consume needs per-symbol margin-requirement fields (initial margin rate,
leverage interaction) that vo.market.symbol.Symbol does not yet expose --
Phase 12 built Symbol from what copy_symbols_get/symbol_info actually
needed at the time, not this phase's need. evaluate_risk() only checks
the coarse account.trade_allowed / margin_free > 0 signal, and says so in
its own rejection reason when margin_free is exhausted -- it does not
claim to verify a specific new position fits within available margin.
Flagged here rather than silently assumed, the same way Bar's
tick-provenance fields stayed honestly None until a real source existed
to populate them.
"""

from __future__ import annotations

import math
from datetime import datetime

from vo.interfaces.signals import AllocationSignal, RiskCheck, RiskCheckError, Signal, TradeSignal
from vo.market.account import AccountState
from vo.market.symbol import Symbol
from vo.risk.risk_config import RiskConfig


def _rejected(
    *, object_id: str, signal_id: str, generated_at_utc: datetime, reason: str
) -> RiskCheck:
    return RiskCheck(
        object_id=object_id,
        signal_id=signal_id,
        generated_at_utc=generated_at_utc,
        approved=False,
        reason=reason,
        direction=None,
        volume=None,
        entry_reference_price=None,
        stop_price=None,
        take_profit_price=None,
    )


def _round_down_to_step(volume: float, *, step: float) -> float:
    """Never rounds UP -- a stop-loss-sized position rounded up risks more
    than the configured fraction, exactly the kind of optimistic rounding
    this project's None/refusal conventions exist to refuse elsewhere.

    The tiny epsilon compensates for float representation error (e.g.
    5.0 / 0.01 landing a hair under 500.0) without meaningfully changing
    what "round down" means -- it is many orders of magnitude smaller
    than any real volume_step this project configures."""
    steps = math.floor(volume / step + 1e-9)
    return round(steps * step, 8)


def evaluate_risk(
    signal: Signal,
    allocation: AllocationSignal,
    *,
    object_id: str,
    generated_at_utc: datetime,
    account: AccountState,
    symbol: Symbol,
    config: RiskConfig,
    open_position_count: int,
    take_profit_price: float | None = None,
) -> RiskCheck:
    if not allocation.approved:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=f"allocation declined: {allocation.reason}",
        )

    if not signal.is_proposal:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=f"signal decision is {signal.decision}, nothing to risk-check",
        )

    if signal.reference_price is None or signal.stop_price is None:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason="signal is missing reference_price/stop_price required for sizing",
        )

    if open_position_count >= config.max_open_positions:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=(
                f"already at max_open_positions ({config.max_open_positions}); "
                f"{open_position_count} open"
            ),
        )

    if not account.trade_allowed:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=f"account {account.login} does not currently allow trading",
        )

    if account.margin_free <= 0:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=(
                f"account {account.login} has no free margin "
                f"(margin_free={account.margin_free})"
            ),
        )

    stop_distance = abs(signal.reference_price - signal.stop_price)
    if stop_distance <= 0:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason="stop_distance is non-positive; stop_price must differ from reference_price",
        )

    if symbol.tick_size <= 0 or symbol.tick_value <= 0:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=(
                f"symbol {symbol.broker_symbol} has non-positive tick_size/tick_value; "
                "cannot size a position"
            ),
        )

    value_per_price_unit = symbol.tick_value / symbol.tick_size
    risk_per_lot = stop_distance * value_per_price_unit
    if risk_per_lot <= 0:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason="computed risk_per_lot is non-positive",
        )

    assert allocation.risk_fraction is not None  # approved AllocationSignal guarantees this
    risk_amount = account.equity * allocation.risk_fraction
    raw_volume = risk_amount / risk_per_lot
    volume = _round_down_to_step(min(raw_volume, config.max_volume), step=config.volume_step)

    if volume < config.min_volume:
        return _rejected(
            object_id=object_id,
            signal_id=signal.object_id,
            generated_at_utc=generated_at_utc,
            reason=(
                f"sized position ({volume}) is below the broker minimum volume "
                f"({config.min_volume}) for this stop distance and risk fraction; "
                "refusing rather than rounding up past the configured risk"
            ),
        )

    return RiskCheck(
        object_id=object_id,
        signal_id=signal.object_id,
        generated_at_utc=generated_at_utc,
        approved=True,
        reason=None,
        direction=signal.direction,
        volume=volume,
        entry_reference_price=signal.reference_price,
        stop_price=signal.stop_price,
        take_profit_price=take_profit_price,
    )


def build_trade_signal(
    risk_check: RiskCheck,
    *,
    object_id: str,
    instrument_id: str,
    generated_at_utc: datetime,
) -> TradeSignal:
    """
    The sole entry point to a TradeSignal in this codebase. Raises
    RiskCheckError for anything but an approved RiskCheck -- there is no
    silent fallback shape, matching every other "refuse rather than
    guess" convention in this project.
    """
    if not risk_check.approved:
        raise RiskCheckError(
            "cannot build a TradeSignal from a rejected RiskCheck "
            f"(reason: {risk_check.reason})"
        )

    assert risk_check.direction is not None
    assert risk_check.volume is not None
    assert risk_check.entry_reference_price is not None
    assert risk_check.stop_price is not None

    return TradeSignal(
        object_id=object_id,
        risk_check_id=risk_check.object_id,
        instrument_id=instrument_id,
        generated_at_utc=generated_at_utc,
        direction=risk_check.direction,
        volume=risk_check.volume,
        entry_reference_price=risk_check.entry_reference_price,
        stop_price=risk_check.stop_price,
        take_profit_price=risk_check.take_profit_price,
    )
