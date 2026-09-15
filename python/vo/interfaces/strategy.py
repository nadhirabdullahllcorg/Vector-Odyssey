"""
The injection seam.

The EA is the execution engine. VO is the strategy injected into it. This module
holds the boundary between them, and nothing crosses that is not declared here.

    EA  →  VO     StrategyContext
    VO  →  EA     Decision, and later Signal / AllocationSignal / TradeSignal

A strategy receives a StrategyContext and cannot reach outward for anything
else. It therefore cannot tell whether its data came from a live terminal, a
replay file, a backtest or a stress harness — which is what keeps live and
backtest from drifting apart.

SCOPE NOTE — Phase 1.
StrategyContext is deliberately near-empty. Its parts do not exist yet: candle
geometry arrives at Phase 5, TimeContext at Phase 6, reference levels at Phase 7.
Declaring those fields now would mean inventing types that have to change, so
the Protocol grows as each part lands. What is fixed now is the shape of the
boundary, not its contents.

This module contains no market interpretation and no trading logic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vo.interfaces.decisions import Decision


@runtime_checkable
class StrategyContext(Protocol):
    """
    Everything a strategy is allowed to see, assembled by the runtime.

    Grows through Phases 5-7. A strategy must never import MT5, read a file,
    or reach for a clock; if it needs something, it belongs here.
    """

    @property
    def instrument_id(self) -> str:
        """Canonical instrument identity — never the raw broker symbol."""
        ...


@runtime_checkable
class IVOStrategy(Protocol):
    """
    What the runtime requires of any strategy, including the eventual full VO.

    Swapping BasicICTStrategy for FullVOStrategy is a change of one binding in
    the runtime wiring. Nothing below this interface — risk, execution,
    logging, monitoring, the backtester — is aware which is loaded.
    """

    strategy_id: str
    strategy_version: str

    def initialize(self) -> None:
        """Called once before any market data is delivered."""
        ...

    def on_bar(self, context: StrategyContext) -> Decision:
        """
        Called once per completed bar. Returns where the evaluation ended.

        NO_TRADE and NO_SIGNAL are correct, expected answers.
        """
        ...

    def explain(self) -> str:
        """
        Why the last decision came out as it did, in auditable terms.

        Required, not optional: a decision that cannot be explained cannot be
        reviewed, and an unreviewable decision has no place in the provenance
        chain.
        """
        ...

    def shutdown(self) -> None:
        """Called once on clean termination."""
        ...
