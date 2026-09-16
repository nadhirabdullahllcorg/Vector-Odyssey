"""
OpeningRangeGap (ORG) -- [VO-D].

The Opening Range Gap: the gap between the *previous* trading day's
settlement (its 16:14 closing print) and the *current* trading day's RTH
open (its 09:30 opening print). Today's open is the gap's HIGH when it
opened above the prior settlement (a gap up) and its LOW when it opened
below (a gap down); the prior settlement is the opposite boundary, and
equilibrium is the 50% midpoint between them.

Classification: [VO-D]. This is VO's operationalization of the question
vo-time-engine.md S6/S10 deliberately left open ("ORG -- opening range
gap, or opening range?"), resolved to the gap reading. It composes the
already-registered [ICT] `equilibrium` concept (vo.month01.ontology.
equilibrium, Month01/L04-Equilibrium-Vs-Discount): the 50% midpoint here
is that same equilibrium, applied to the settlement->open boundary rather
than to a swing range. ORG is being built ahead of its Phase-32 home at
the user's explicit direction; a formal vo.month01.ontology marker can be
added once its exact source lesson is settled (it is not one of Month 1's
eight core lessons).

Pure value type: like AnchorComparison, high/low/equilibrium/open_position
are computed properties, never stored fields -- the same "don't store what
is derivable" discipline (C2) that keeps Candle geometry out of stored
state. Building it (finding the prior settlement and today's RTH open)
needs the Time Engine and lives in vo.time.levels.ReferenceLevelEngine.
opening_range_gap; this module holds only the shape.

No trade meaning is encoded here: which side is premium/discount, whether
price is expected to revisit equilibrium, whether the gap "fills" -- none
of that. It records where the open sits relative to prior settlement and
the midpoint between them, and stops (G2: nothing here is reachable from a
trade decision -- there is no decision path yet).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum, auto

from vo.market.levels import AnchorPrice


class OpenPosition(Enum):
    """Which boundary of the gap today's RTH open itself is."""

    HIGH = auto()  # gap up: open above prior settlement -> open is the ORG high
    LOW = auto()  # gap down: open below prior settlement -> open is the ORG low
    FLAT = auto()  # exact tie: open == prior settlement, no gap


@dataclass(frozen=True)
class OpeningRangeGap:
    """One trading day's opening range gap: prior settlement <-> today's
    RTH open. high / low / equilibrium / open_position are derived from
    the two anchors, never stored."""

    trading_day: date
    session_open: AnchorPrice  # today's RTH 09:30 opening print
    prior_settlement: AnchorPrice  # previous trading day's 16:14 settlement close

    @property
    def high(self) -> float:
        return max(self.session_open.price, self.prior_settlement.price)

    @property
    def low(self) -> float:
        return min(self.session_open.price, self.prior_settlement.price)

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2

    @property
    def open_position(self) -> OpenPosition:
        if self.session_open.price > self.prior_settlement.price:
            return OpenPosition.HIGH
        if self.session_open.price < self.prior_settlement.price:
            return OpenPosition.LOW
        return OpenPosition.FLAT
