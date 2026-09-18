"""
The MT5 execution adapter -- Phase 16.

The second (and last reserved) place in the whole codebase allowed to
import MetaTrader5 (gate G7, tests/unit/test_architecture.py::
test_only_the_broker_adapter_imports_metatrader5's MT5_ADAPTER_MODULES
already reserves this exact name, set alongside vo.market.mt5 when
Phase 12 was built). Where vo.market.mt5 is the READ side, this is the
WRITE side: constructing an order request, sending it, and mapping
whatever MT5 hands back into an honest OrderResult. Nothing here decides
WHETHER to trade -- that is Phase 14's job (vo.risk, already built); this
module only knows how to ask MT5 to do what a TradeSignal already says.

Mirrors vo.market.mt5's own two structural choices, for the same reasons:

  1. `import MetaTrader5` is LAZY (inside _mt5()), so this module imports
     cleanly on Linux/CI without the Windows-only package installed.

  2. The mapping is split from the sending. `build_open_request`/
     `build_close_request`/`map_order_result` are pure functions --
     OrderRequest in, or a duck-typed MT5 OrderSendResult in, canonical
     value types out -- unit-tested with fabricated inputs exactly like
     vo.market.mt5's map_account/map_position/... Only MT5ExecutionClient
     itself touches a live terminal.

SCOPE, same discipline as vo.market.mt5's own docstring: this module can
build a request and send it, and it is fully unit-tested doing so against
fabricated inputs -- but nothing in this codebase calls send_order() from
any automatic, unattended path yet. Wiring a live send into a running
VO_EA process is explicitly Phase 18's decision (the first live trade),
per architecture/vo-phase-plan.md's own gate for that phase and the
"observation mode" toggle described there. Building and testing this
module now is what makes Phase 18 "wire it up," not "invent it."

RETCODE CLASSIFICATION, an honest caveat: MT5's TRADE_RETCODE_* integer
constants below are transcribed from MetaTrader5's public documentation,
not verified against a live terminal (this development environment has
no Windows machine or MT5 install) -- the same kind of gap Phase 14
flagged for Symbol's missing volume/margin fields. Every constant used
here is a widely-documented, stable MT5 platform constant, but anything
that does not appear in the mapping below classifies as UNKNOWN rather
than being silently guessed at, and the raw retcode is always preserved
on OrderResult for direct inspection regardless of classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from vo.interfaces.decisions import Direction
from vo.interfaces.signals import TradeSignal
from vo.market.account import Position

_PLATFORM = "MT5"


class MT5ExecutionError(RuntimeError):
    """A terminal call failed outright (not a normal trade-rejection
    retcode -- those map to OrderResult.approved=False instead), or
    MetaTrader5 is not installed."""


class OrderAction(Enum):
    """What kind of request this is. Distinct from MT5's own numeric
    TRADE_ACTION_* constants -- this is OUR classification of intent,
    translated to the right MT5 request shape by to_mt5_request()."""

    OPEN = "OPEN"
    CLOSE = "CLOSE"

    def __str__(self) -> str:
        return self.value


class OrderFailureReason(Enum):
    """Why a send failed, grouped into the named failure modes
    architecture/vo-architecture-audit.md's execution-adapter gate calls
    out by name: "reject, requote, bad volume, bad stops, margin, market
    closed, disconnect". UNKNOWN is honest overflow for any MT5 retcode
    not in the table below -- never silently folded into another
    category."""

    REJECTED = "REJECTED"
    REQUOTE = "REQUOTE"
    BAD_VOLUME = "BAD_VOLUME"
    BAD_STOPS = "BAD_STOPS"
    MARGIN = "MARGIN"
    MARKET_CLOSED = "MARKET_CLOSED"
    DISCONNECT = "DISCONNECT"
    TRADING_DISABLED = "TRADING_DISABLED"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


# MT5 TRADE_RETCODE_* -> OrderFailureReason. Only the codes that are a
# real rejection map here; TRADE_RETCODE_DONE (10009) and
# TRADE_RETCODE_DONE_PARTIAL (10010) are successes, handled separately in
# map_order_result, never entered into this table.
_RETCODE_FAILURE: dict[int, OrderFailureReason] = {
    10004: OrderFailureReason.REQUOTE,
    10006: OrderFailureReason.REJECTED,
    10007: OrderFailureReason.REJECTED,  # request canceled by trader
    10011: OrderFailureReason.REJECTED,  # generic request-processing error
    10013: OrderFailureReason.REJECTED,  # invalid request
    10014: OrderFailureReason.BAD_VOLUME,
    10015: OrderFailureReason.BAD_STOPS,  # invalid price
    10016: OrderFailureReason.BAD_STOPS,
    10017: OrderFailureReason.TRADING_DISABLED,
    10018: OrderFailureReason.MARKET_CLOSED,
    10019: OrderFailureReason.MARGIN,  # "no money" -- insufficient funds/margin
    10020: OrderFailureReason.REQUOTE,  # price changed
    10021: OrderFailureReason.REQUOTE,  # no quotes to process the request
    10026: OrderFailureReason.TRADING_DISABLED,  # autotrading disabled by server
    10027: OrderFailureReason.TRADING_DISABLED,  # autotrading disabled by terminal
    10028: OrderFailureReason.REJECTED,  # request locked for processing
    10029: OrderFailureReason.REJECTED,  # order/position frozen
    10031: OrderFailureReason.DISCONNECT,  # no connection to the trade server
    10035: OrderFailureReason.REJECTED,  # invalid order
    10036: OrderFailureReason.REJECTED,  # position already closed
    10038: OrderFailureReason.REJECTED,  # a close order already exists
    10039: OrderFailureReason.REJECTED,  # reached the position limit
    10040: OrderFailureReason.REJECTED,
    10045: OrderFailureReason.REJECTED,  # hedging prohibited
}

_SUCCESS_RETCODES = frozenset({10008, 10009, 10010})  # PLACED, DONE, DONE_PARTIAL


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """What we want MT5 to do -- built by build_open_request/
    build_close_request, never by hand elsewhere, so every request this
    codebase can produce traces back to either an approved TradeSignal or
    a Position being closed. Carries no `magic`/`comment` typing beyond
    plain str/int -- vo.execution.execution_config.ExecutionConfig owns
    what those values actually are; this type just carries them."""

    action: OrderAction
    broker_symbol: str
    direction: Direction | None
    volume: float
    price: float | None
    stop_loss: float | None
    take_profit: float | None
    deviation_points: int
    magic: int
    comment: str
    position_ticket: int | None

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("OrderRequest.broker_symbol cannot be blank")
        if self.volume <= 0:
            raise ValueError("OrderRequest.volume must be positive")
        if self.deviation_points < 0:
            raise ValueError("OrderRequest.deviation_points cannot be negative")
        if self.action is OrderAction.OPEN:
            if self.direction is None or self.direction is Direction.NEUTRAL:
                raise ValueError("an OPEN OrderRequest needs a real direction")
            if self.position_ticket is not None:
                raise ValueError("an OPEN OrderRequest carries no position_ticket")
        else:  # CLOSE
            if self.position_ticket is None:
                raise ValueError("a CLOSE OrderRequest needs a position_ticket")


@dataclass(frozen=True, slots=True)
class OrderResult:
    """MT5's answer to one OrderRequest, mapped off its OrderSendResult --
    always produced, approved or not, the same "every outcome is an
    object, not an exception" discipline Phase 14's RiskCheck already
    established. approved=True means MT5 actually filled/placed it
    (retcode in _SUCCESS_RETCODES); anything else is a named
    OrderFailureReason with the raw retcode/comment preserved."""

    generated_at_utc: datetime
    approved: bool
    retcode: int
    failure_reason: OrderFailureReason | None
    broker_comment: str
    deal_ticket: int | None
    order_ticket: int | None
    filled_volume: float | None
    filled_price: float | None

    def __post_init__(self) -> None:
        if self.generated_at_utc.tzinfo is None:
            raise ValueError("OrderResult.generated_at_utc must be timezone-aware")
        if self.approved and self.failure_reason is not None:
            raise ValueError("an approved OrderResult carries no failure_reason")
        if not self.approved and self.failure_reason is None:
            raise ValueError("a rejected OrderResult must carry a failure_reason")


def build_open_request(
    trade_signal: TradeSignal,
    *,
    broker_symbol: str,
    magic: int,
    comment: str,
    deviation_points: int,
) -> OrderRequest:
    """The ONE honest translation of an approved TradeSignal (Phase 14's
    sole output -- see vo.risk.manager.build_trade_signal and gate G14)
    into something MT5 can be asked to do. Takes broker_symbol
    separately rather than parsing it out of TradeSignal.instrument_id,
    since InstrumentId's own string form is this codebase's identity key,
    not necessarily MT5's exact symbol spelling (see vo.market.identity)."""
    return OrderRequest(
        action=OrderAction.OPEN,
        broker_symbol=broker_symbol,
        direction=trade_signal.direction,
        volume=trade_signal.volume,
        price=trade_signal.entry_reference_price,
        stop_loss=trade_signal.stop_price,
        take_profit=trade_signal.take_profit_price,
        deviation_points=deviation_points,
        magic=magic,
        comment=comment,
        position_ticket=None,
    )


def build_close_request(
    position: Position,
    *,
    magic: int,
    comment: str,
    deviation_points: int,
) -> OrderRequest:
    """Closing is an opposite-direction deal against the SAME position
    ticket -- MT5's netting/hedging semantics are handled by passing
    `position` in the request, not by us computing a net direction."""
    return OrderRequest(
        action=OrderAction.CLOSE,
        broker_symbol=position.broker_symbol,
        direction=None,
        volume=position.volume,
        price=None,
        stop_loss=None,
        take_profit=None,
        deviation_points=deviation_points,
        magic=magic,
        comment=comment,
        position_ticket=position.ticket,
    )


def map_order_result(raw: Any, *, now: datetime) -> OrderResult:
    """Map MT5's OrderSendResult (order_send()'s return value) to an
    OrderResult. `now` is given, not fetched (this project's "given, not
    fetched" discipline -- see vo.observation.atr) since OrderSendResult
    carries no timestamp of its own."""
    retcode = int(raw.retcode)
    approved = retcode in _SUCCESS_RETCODES
    failure_reason = None if approved else _RETCODE_FAILURE.get(retcode, OrderFailureReason.UNKNOWN)

    deal = getattr(raw, "deal", 0) or 0
    order = getattr(raw, "order", 0) or 0
    volume = getattr(raw, "volume", 0.0) or 0.0
    price = getattr(raw, "price", 0.0) or 0.0

    return OrderResult(
        generated_at_utc=now,
        approved=approved,
        retcode=retcode,
        failure_reason=failure_reason,
        broker_comment=str(getattr(raw, "comment", "")),
        deal_ticket=int(deal) if approved and deal else None,
        order_ticket=int(order) if approved and order else None,
        filled_volume=float(volume) if approved else None,
        filled_price=float(price) if approved else None,
    )


class TerminalExecutionApi(Protocol):
    """What a consumer needs from the write side, so a fake can stand in
    for MT5ExecutionClient in tests without a terminal -- mirrors
    vo.market.mt5.TerminalReadApi."""

    def send_order(self, request: OrderRequest) -> OrderResult: ...


def _mt5() -> Any:
    """Lazily import MetaTrader5 -- see vo.market.mt5._mt5's own
    docstring; the reasoning is identical here."""
    try:
        import MetaTrader5  # lazy on purpose -- see this function's docstring
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise MT5ExecutionError(
            "MetaTrader5 is not installed. It is an optional, Windows-only "
            "dependency: install with `pip install \"MetaTrader5\"` on the "
            "machine running the terminal (see pyproject.toml's [mt5] extra)."
        ) from exc
    return MetaTrader5


class MT5ExecutionClient:
    """
    Write-side client over a running MT5 terminal. Construct, `connect()`
    (idempotent with vo.market.mt5.MT5ReadClient's own connect -- MT5's
    Python API is one process-global connection, not per-object, so
    either client may call it), `send_order()`, `shutdown()`.

    send_order() is a real, live-capable call -- nothing in this module
    refuses to send. What keeps this safe today is that nothing in this
    codebase's own runtime paths calls it yet (see this module's own
    docstring, and architecture/vo-phase-plan.md's Phase 18 gate).
    """

    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> None:
        mt5 = _mt5()
        if not mt5.initialize():  # pragma: no cover - needs a live terminal
            raise MT5ExecutionError(f"MT5 initialize() failed: {mt5.last_error()}")
        self._connected = True

    def shutdown(self) -> None:  # pragma: no cover - needs a live terminal
        _mt5().shutdown()
        self._connected = False

    def _require_connected(self) -> None:
        if not self._connected:
            raise MT5ExecutionError("Not connected -- call connect() first.")

    def send_order(self, request: OrderRequest) -> OrderResult:  # pragma: no cover
        """Sends `request` and maps the result. Uses TRADE_ACTION_DEAL for
        both OPEN and CLOSE -- MT5's standard "market execution" action;
        a CLOSE is a deal against `request.position_ticket` in the
        opposite direction, which MT5 nets against the open position."""
        self._require_connected()
        mt5 = _mt5()

        mt5_request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.broker_symbol,
            "volume": request.volume,
            "deviation": request.deviation_points,
            "magic": request.magic,
            "comment": request.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        if request.action is OrderAction.OPEN:
            assert request.direction is not None
            is_buy = request.direction is Direction.LONG
            mt5_request["type"] = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(request.broker_symbol)
            mt5_request["price"] = (tick.ask if is_buy else tick.bid) if tick else request.price
            if request.stop_loss is not None:
                mt5_request["sl"] = request.stop_loss
            if request.take_profit is not None:
                mt5_request["tp"] = request.take_profit
        else:
            assert request.position_ticket is not None
            mt5_request["position"] = request.position_ticket
            # Closing sends the OPPOSITE side of whatever is open; MT5
            # resolves the actual current side from the position ticket
            # itself via positions_get(), not guessed here.
            positions = mt5.positions_get(ticket=request.position_ticket)
            if not positions:
                raise MT5ExecutionError(
                    f"position {request.position_ticket} not found; cannot close it"
                )
            current_side = int(positions[0].type)
            mt5_request["type"] = mt5.ORDER_TYPE_SELL if current_side == 0 else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(request.broker_symbol)
            is_buy = mt5_request["type"] == mt5.ORDER_TYPE_BUY
            mt5_request["price"] = tick.ask if is_buy else tick.bid

        raw = mt5.order_send(mt5_request)
        if raw is None:
            raise MT5ExecutionError(f"order_send() returned None: {mt5.last_error()}")

        from datetime import UTC

        return map_order_result(raw, now=datetime.now(UTC))
