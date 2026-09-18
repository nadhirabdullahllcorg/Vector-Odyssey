"""
Contracts shared between the EA and VO.

This package depends on nothing else in ``vo``. Everything else may depend on
it. That is what makes it the boundary.
"""

from vo.interfaces.canonical import (
    AppendOnlyLog,
    CanonicalRecord,
    CanonicalRecordError,
)
from vo.interfaces.concepts import (
    REGISTRY,
    ConceptError,
    ConceptRecord,
    ConceptRegistry,
    ConceptTag,
    concept,
    decision_path,
)
from vo.interfaces.decisions import Decision, Direction
from vo.interfaces.signals import (
    AllocationError,
    AllocationSignal,
    RiskCheck,
    RiskCheckError,
    Signal,
    SignalError,
    StrategyOutput,
    TradeSignal,
)
from vo.interfaces.strategy import IVOStrategy, StrategyContext

__all__ = [
    "REGISTRY",
    "AllocationError",
    "AllocationSignal",
    "AppendOnlyLog",
    "CanonicalRecord",
    "CanonicalRecordError",
    "ConceptError",
    "ConceptRecord",
    "ConceptRegistry",
    "ConceptTag",
    "Decision",
    "Direction",
    "IVOStrategy",
    "RiskCheck",
    "RiskCheckError",
    "Signal",
    "SignalError",
    "StrategyContext",
    "StrategyOutput",
    "TradeSignal",
    "concept",
    "decision_path",
]
