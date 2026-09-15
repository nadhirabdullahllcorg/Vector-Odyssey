"""
Decision outcomes.

NO_TRADE is a first-class result. The system must never be forced into a
position because a strategy happens to be enabled.

This module contains no market interpretation and no trading logic.
"""

from __future__ import annotations

from enum import Enum


class Direction(Enum):
    """Intended direction of exposure. Not a market opinion."""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"

    def __str__(self) -> str:
        return self.value


class Decision(Enum):
    """
    Where an evaluation ended.

    The six outcomes are deliberately distinct, so that "nothing happened"
    can always be distinguished from "something was refused, and here is
    which layer refused it".
    """

    NO_SIGNAL = "NO_SIGNAL"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    RISK_REJECTED = "RISK_REJECTED"
    NO_TRADE = "NO_TRADE"
    TRADE_APPROVED = "TRADE_APPROVED"
    TRADE_EXECUTED = "TRADE_EXECUTED"

    def __str__(self) -> str:
        return self.value

    @property
    def is_refusal(self) -> bool:
        """True when some layer declined. Every refusal carries a reason."""
        return self in _REFUSALS

    @property
    def reached_broker(self) -> bool:
        return self is Decision.TRADE_EXECUTED


_REFUSALS = frozenset(
    {
        Decision.NO_SIGNAL,
        Decision.SIGNAL_REJECTED,
        Decision.RISK_REJECTED,
        Decision.NO_TRADE,
    }
)
