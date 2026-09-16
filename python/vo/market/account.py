"""
Account / position / order state -- the read side of the MT5 API (Phase 12).

These are plain value types: honest snapshots of what the terminal reports
about the account and its open positions and pending orders, mapped off
MetaTrader5's own structures by vo.market.mt5 (the one module allowed to
import that package, gate G7). Nothing here imports MetaTrader5 or reaches
a terminal -- like Bar/Tick/Symbol, these are the shapes, not the source.

Deliberately read-only and interpretation-free: no P&L targets, no "should
I close this", no risk rules. That is the risk manager's job (Phase 14)
and the execution adapter's (Phase 16); a snapshot of broker state carries
none of it.

Two honesty conventions, matching the rest of vo.market:
  - MT5 uses 0.0 for "no stop loss / no take profit set". That is not a
    real price of zero, so it maps to None here -- the same UNKNOWN-vs-zero
    discipline TickCoverage and Candle geometry already use.
  - MT5's position/order timestamps are broker-server epoch seconds, not
    true UTC (the same fact schema v2 and vo.time.mapping exist to handle).
    They are kept raw as `*_broker_epoch_s`, never silently relabelled UTC;
    resolving them to real instants needs the Time Engine + a broker
    profile, a wiring step left to whichever later phase consumes these.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from vo.market.identity import InstrumentId


class PositionSide(Enum):
    """Direction of an open position. Maps MT5 POSITION_TYPE_BUY (0) ->
    LONG, POSITION_TYPE_SELL (1) -> SHORT."""

    LONG = "LONG"
    SHORT = "SHORT"

    def __str__(self) -> str:
        return self.value


class OrderKind(Enum):
    """A pending order's type. Maps MT5 ORDER_TYPE_* (2..7); market
    ORDER_TYPE_BUY/SELL (0/1) are not pending orders -- once filled they
    are positions, so they never appear in orders_get()."""

    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"
    BUY_STOP_LIMIT = "BUY_STOP_LIMIT"
    SELL_STOP_LIMIT = "SELL_STOP_LIMIT"

    def __str__(self) -> str:
        return self.value


class OrderState(Enum):
    """A pending order's lifecycle state. Maps MT5 ORDER_STATE_* (0..9)."""

    STARTED = "STARTED"
    PLACED = "PLACED"
    CANCELED = "CANCELED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REQUEST_ADD = "REQUEST_ADD"
    REQUEST_MODIFY = "REQUEST_MODIFY"
    REQUEST_CANCEL = "REQUEST_CANCEL"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class AccountState:
    """A snapshot of the trading account, off MT5 account_info(). Money
    fields are in the account currency; `margin_level` is a percentage and
    is None when there is no margin in use (MT5 reports 0.0 for that, which
    is not a meaningful level)."""

    login: int
    name: str
    server: str
    currency: str
    balance: float
    equity: float
    profit: float
    margin: float
    margin_free: float
    margin_level: float | None
    leverage: int
    trade_allowed: bool


@dataclass(frozen=True)
class Position:
    """One open position, off MT5 positions_get()."""

    ticket: int
    instrument_id: InstrumentId
    broker_symbol: str
    side: PositionSide
    volume: float
    price_open: float
    price_current: float
    stop_loss: float | None
    take_profit: float | None
    profit: float
    swap: float
    magic: int
    comment: str
    opened_at_broker_epoch_s: int
    """Broker-server epoch seconds, NOT UTC -- see the module docstring."""


@dataclass(frozen=True)
class Order:
    """One pending order, off MT5 orders_get()."""

    ticket: int
    instrument_id: InstrumentId
    broker_symbol: str
    kind: OrderKind
    state: OrderState
    volume_current: float
    price_open: float
    stop_loss: float | None
    take_profit: float | None
    magic: int
    comment: str
    setup_at_broker_epoch_s: int
    """Broker-server epoch seconds, NOT UTC -- see the module docstring."""
