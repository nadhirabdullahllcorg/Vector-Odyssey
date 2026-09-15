"""
Contracts shared between the EA and VO.

This package depends on nothing else in ``vo``. Everything else may depend on
it. That is what makes it the boundary.
"""

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
from vo.interfaces.strategy import IVOStrategy, StrategyContext

__all__ = [
    "REGISTRY",
    "ConceptError",
    "ConceptRecord",
    "ConceptRegistry",
    "ConceptTag",
    "Decision",
    "Direction",
    "IVOStrategy",
    "StrategyContext",
    "concept",
    "decision_path",
]
