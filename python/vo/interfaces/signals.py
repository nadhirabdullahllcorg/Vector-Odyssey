"""
Signal / AllocationSignal / RiskCheck / TradeSignal -- Phase 14's contract
types (architecture/vo-phase-plan.md's Block 4 row: "Risk manager -
Signal / AllocationSignal / RiskCheck / TradeSignal | Every rejection
carries a reason; risk is the sole TradeSignal producer").

The pipeline a strategy's evaluation passes through before anything can
reach a broker (architecture/vo-architecture-audit.md F.2):

    StrategyOutput  (what a strategy proposes, if anything)
        -> Signal            vo.signals     SIGNAL GENERATOR
        -> AllocationSignal  vo.allocation  CAPITAL ALLOCATOR
        -> RiskCheck         vo.risk        RISK ENGINE
        -> TradeSignal       vo.risk ONLY -- see vo.risk.manager's module
                             docstring and this gate's own test,
                             tests/unit/test_architecture.py::
                             test_risk_is_the_sole_trade_signal_producer

Every stage is always produced, even a rejection -- a Signal exists for a
NO_TRADE bar exactly as it does for a proposed one, an AllocationSignal
exists even when nothing was allocated, and so on. That is what "every
rejection carries a reason" means mechanically: there is always an object
to carry it, at the exact stage that produced it, so the full
observation -> ... -> outcome chain Phase 19 will need stays
reconstructible from Phase 14 onward rather than retrofitted later.

These are pure contract types, not logic -- the same split
StrategyContext/Decision already established against the engines that
produce and consume them. Everything here carries its upstream context BY
REFERENCE (ids, enum values, a flat named-feature snapshot) rather than by
embedding whole domain objects such as a RegimeState -- matching G8's own
chart-object-identity discipline (embed identity, not the object) -- and
specifically so this module can stay in vo.interfaces (layer 0), which
vo.interfaces.strategy's own module docstring requires ("This package
depends on nothing else in vo") and tests/unit/test_architecture.py's
test_interfaces_depends_on_nothing_outside_itself enforces mechanically.

WIRING NOTE, flagged rather than guessed: IVOStrategy.on_bar
(vo.interfaces.strategy) still returns a bare Decision today, exactly as
Phase 1 left it -- its own scope note says the protocol "grows as each
part lands." StrategyOutput below is a new, Phase-14-owned shape that
vo.signals.generator consumes; whether on_bar eventually returns this
shape directly, or a real strategy exposes it through some other call, is
Phase 18's decision once a real disposable strategy exists to shape it
against (architecture/vo-trade-logic-and-brain-plan.md SS1) -- inventing
that answer now, with zero real callers, is exactly the "build-order jump"
vo.interfaces.canonical's own docstring warns against. This module makes
Phase 14 buildable, testable and usable ahead of that wiring, not a
substitute for deciding it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from vo.interfaces.decisions import Decision, Direction

# Decisions a strategy may legitimately report to the signal generator.
# Every other Decision value belongs to a later stage (SIGNAL_REJECTED,
# RISK_REJECTED: vo.signals/vo.risk; TRADE_APPROVED, TRADE_EXECUTED:
# risk/execution) and it is a signal-generation error, not a real
# strategy outcome, for one to arrive already carrying them.
STRATEGY_DECISIONS = frozenset({Decision.NO_SIGNAL, Decision.NO_TRADE, Decision.SIGNAL_PROPOSED})


class SignalError(ValueError):
    """Raised when a StrategyOutput or Signal is constructed with an
    invalid shape."""


class AllocationError(ValueError):
    """Raised when an AllocationSignal is constructed with an invalid
    shape."""


class RiskCheckError(ValueError):
    """Raised when a RiskCheck (or the TradeSignal built from one) is
    constructed with an invalid shape."""


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    """
    What one strategy evaluation proposes, before vo.signals wraps it into
    an audited Signal. See the module WIRING NOTE above for why this is
    not (yet) IVOStrategy.on_bar's own return type.
    """

    decision: Decision
    direction: Direction
    reference_price: float | None
    stop_price: float | None
    rationale: str

    def __post_init__(self) -> None:
        if self.decision not in STRATEGY_DECISIONS:
            raise SignalError(
                "StrategyOutput.decision must be one of "
                f"{sorted(d.value for d in STRATEGY_DECISIONS)}, got {self.decision}"
            )
        if not self.rationale.strip():
            raise SignalError(
                "StrategyOutput.rationale cannot be blank -- IVOStrategy.explain() "
                "is required, not optional (see vo.interfaces.strategy)"
            )
        if self.decision is Decision.SIGNAL_PROPOSED:
            if self.direction is Direction.NEUTRAL:
                raise SignalError("a SIGNAL_PROPOSED output needs a real direction, not NEUTRAL")
            if self.reference_price is None:
                raise SignalError("a SIGNAL_PROPOSED output needs a reference_price")
            if self.stop_price is None:
                raise SignalError("a SIGNAL_PROPOSED output needs a stop_price")
            if self.reference_price <= 0:
                raise SignalError("StrategyOutput.reference_price must be positive")
            if self.stop_price <= 0:
                raise SignalError("StrategyOutput.stop_price must be positive")
            if self.stop_price == self.reference_price:
                raise SignalError("StrategyOutput.stop_price cannot equal reference_price")


@dataclass(frozen=True, slots=True)
class Signal:
    """
    A strategy evaluation, made auditable and portable across the runtime
    (vo.signals.generator.generate_signal). Always produced -- for a
    NO_SIGNAL/NO_TRADE bar exactly as for a proposed setup -- so the audit
    trail never has a gap at this stage.
    """

    object_id: str
    instrument_id: str
    generated_at_utc: datetime
    strategy_id: str
    strategy_version: str
    decision: Decision
    direction: Direction
    reference_price: float | None
    stop_price: float | None
    regime_state_id: str | None
    regime_state: str | None
    features: Mapping[str, float]
    rationale: str
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise SignalError("Signal.object_id cannot be blank")
        if not self.instrument_id.strip():
            raise SignalError("Signal.instrument_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise SignalError("Signal.generated_at_utc must be timezone-aware")
        if not self.strategy_id.strip():
            raise SignalError("Signal.strategy_id cannot be blank")
        if not self.strategy_version.strip():
            raise SignalError("Signal.strategy_version cannot be blank")
        if not self.rationale.strip():
            raise SignalError("Signal.rationale cannot be blank")
        if self.decision is Decision.SIGNAL_REJECTED and not (
            self.rejection_reason and self.rejection_reason.strip()
        ):
            raise SignalError("a SIGNAL_REJECTED Signal must carry a rejection_reason")
        if self.decision is not Decision.SIGNAL_REJECTED and self.rejection_reason is not None:
            raise SignalError("rejection_reason is only meaningful on a SIGNAL_REJECTED Signal")
        if self.decision in (
            Decision.RISK_REJECTED,
            Decision.TRADE_APPROVED,
            Decision.TRADE_EXECUTED,
        ):
            raise SignalError(
                f"Signal.decision cannot be {self.decision} -- that outcome belongs to a "
                "later stage (vo.risk or execution), not the signal generator"
            )
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def is_proposal(self) -> bool:
        """True only when this Signal carries a real, still-live proposal
        for the allocator/risk engine to evaluate."""
        return self.decision is Decision.SIGNAL_PROPOSED


@dataclass(frozen=True, slots=True)
class AllocationSignal:
    """
    vo.allocation's output -- deliberately simple (architecture/
    vo-architecture-audit.md's own words for this stage: "contract
    correct, behaviour deliberately simple"). Confirms capital access is
    not withheld and hands the configured per-trade risk fraction
    forward; it does not itself size a position -- vo.risk does that.
    """

    object_id: str
    signal_id: str
    generated_at_utc: datetime
    approved: bool
    risk_fraction: float | None
    reason: str | None

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise AllocationError("AllocationSignal.object_id cannot be blank")
        if not self.signal_id.strip():
            raise AllocationError("AllocationSignal.signal_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise AllocationError("AllocationSignal.generated_at_utc must be timezone-aware")
        if self.approved:
            if self.risk_fraction is None:
                raise AllocationError("an approved AllocationSignal needs a risk_fraction")
            if not (0.0 < self.risk_fraction <= 1.0):
                raise AllocationError(f"risk_fraction must be in (0, 1], got {self.risk_fraction}")
            if self.reason is not None:
                raise AllocationError("an approved AllocationSignal carries no reason")
        else:
            if not (self.reason and self.reason.strip()):
                raise AllocationError("a declined AllocationSignal must carry a reason")
            if self.risk_fraction is not None:
                raise AllocationError("a declined AllocationSignal carries no risk_fraction")


@dataclass(frozen=True, slots=True)
class RiskCheck:
    """
    vo.risk's evaluation of a Signal + AllocationSignal against account
    state -- Phase 14's own gate: "every rejection carries a reason."
    Always produced, approved or not; only an approved RiskCheck may ever
    back a TradeSignal (see vo.risk.manager.build_trade_signal and the
    G14 architecture test).
    """

    object_id: str
    signal_id: str
    generated_at_utc: datetime
    approved: bool
    reason: str | None
    direction: Direction | None
    volume: float | None
    entry_reference_price: float | None
    stop_price: float | None
    take_profit_price: float | None

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise RiskCheckError("RiskCheck.object_id cannot be blank")
        if not self.signal_id.strip():
            raise RiskCheckError("RiskCheck.signal_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise RiskCheckError("RiskCheck.generated_at_utc must be timezone-aware")
        if self.approved:
            if self.reason is not None:
                raise RiskCheckError("an approved RiskCheck carries no reason")
            if self.direction is None or self.direction is Direction.NEUTRAL:
                raise RiskCheckError("an approved RiskCheck needs a real direction")
            if self.volume is None or self.volume <= 0:
                raise RiskCheckError("an approved RiskCheck needs a positive volume")
            if self.entry_reference_price is None or self.entry_reference_price <= 0:
                raise RiskCheckError(
                    "an approved RiskCheck needs a positive entry_reference_price"
                )
            if self.stop_price is None or self.stop_price <= 0:
                raise RiskCheckError("an approved RiskCheck needs a positive stop_price")
        else:
            if not (self.reason and self.reason.strip()):
                raise RiskCheckError("a declined RiskCheck must carry a reason")
            if self.volume is not None:
                raise RiskCheckError("a declined RiskCheck carries no volume")


@dataclass(frozen=True, slots=True)
class TradeSignal:
    """
    The one object allowed to reach vo.execution (Phase 16, not yet
    built). Constructible ONLY from an approved RiskCheck -- see
    vo.risk.manager.build_trade_signal, the sole place in this codebase
    that may call this constructor
    (tests/unit/test_architecture.py::test_risk_is_the_sole_trade_signal_producer
    enforces this mechanically, the same AST-scan style G7 already uses
    for the MetaTrader5 import).
    """

    object_id: str
    risk_check_id: str
    instrument_id: str
    generated_at_utc: datetime
    direction: Direction
    volume: float
    entry_reference_price: float
    stop_price: float
    take_profit_price: float | None

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise RiskCheckError("TradeSignal.object_id cannot be blank")
        if not self.risk_check_id.strip():
            raise RiskCheckError("TradeSignal.risk_check_id cannot be blank")
        if not self.instrument_id.strip():
            raise RiskCheckError("TradeSignal.instrument_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise RiskCheckError("TradeSignal.generated_at_utc must be timezone-aware")
        if self.direction is Direction.NEUTRAL:
            raise RiskCheckError("TradeSignal.direction cannot be NEUTRAL")
        if self.volume <= 0:
            raise RiskCheckError("TradeSignal.volume must be positive")
        if self.entry_reference_price <= 0:
            raise RiskCheckError("TradeSignal.entry_reference_price must be positive")
        if self.stop_price <= 0:
            raise RiskCheckError("TradeSignal.stop_price must be positive")
