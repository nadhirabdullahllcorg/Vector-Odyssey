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
from vo.market.account import Order, Position, PositionSide

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
    CANCEL = "CANCEL"
    """Added 2026-09-19 for vo.execution.compliance_closeout: cancels a
    still-pending order (never sent to market) via MT5's
    TRADE_ACTION_REMOVE -- distinct from CLOSE, which closes an already-
    open, already-filled position via TRADE_ACTION_DEAL."""
    MODIFY = "MODIFY"
    """Added 2026-09-19 for live stop adjustment: changes an OPEN
    position's stop-loss/take-profit in place via MT5's
    TRADE_ACTION_SLTP. Sends no deal and moves no volume -- the position
    stays exactly as it is, only its protective levels move. This is the
    one primitive a trailing stop needs, and the codebase had none until
    now (OPEN/CLOSE/CANCEL could create and destroy, never adjust)."""

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
    order_ticket: int | None = None
    """Set only for CANCEL -- the pending order's own ticket (orders_get(),
    not positions_get()). Added after every other field, defaulted to
    None, so every pre-existing OPEN/CLOSE construction site (which never
    passed it) keeps working unchanged."""

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
            if self.order_ticket is not None:
                raise ValueError("an OPEN OrderRequest carries no order_ticket")
        elif self.action is OrderAction.CLOSE:
            if self.position_ticket is None:
                raise ValueError("a CLOSE OrderRequest needs a position_ticket")
            if self.order_ticket is not None:
                raise ValueError("a CLOSE OrderRequest carries no order_ticket")
        elif self.action is OrderAction.CANCEL:
            if self.order_ticket is None:
                raise ValueError("a CANCEL OrderRequest needs an order_ticket")
            if self.position_ticket is not None:
                raise ValueError("a CANCEL OrderRequest carries no position_ticket")
        else:  # MODIFY
            if self.position_ticket is None:
                raise ValueError("a MODIFY OrderRequest needs a position_ticket")
            if self.order_ticket is not None:
                raise ValueError("a MODIFY OrderRequest carries no order_ticket")
            if self.stop_loss is None and self.take_profit is None:
                # MT5's SLTP action writes BOTH levels from this one
                # request; sending it with neither set would clear both
                # protective levels off a live position. Never a thing
                # this codebase wants to express by accident.
                raise ValueError(
                    "a MODIFY OrderRequest needs at least one of stop_loss/take_profit -- "
                    "sending neither would strip both levels off an open position"
                )


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


def build_cancel_request(
    order: Order,
    *,
    magic: int,
    comment: str,
) -> OrderRequest:
    """A still-pending order (never filled) is cancelled outright, not
    closed -- MT5's TRADE_ACTION_REMOVE needs only the order's own
    ticket, no direction/price/deviation. `order.volume_current` is
    carried through only to satisfy OrderRequest's own volume>0 invariant
    uniformly across all three actions -- MT5 ignores it for REMOVE."""
    return OrderRequest(
        action=OrderAction.CANCEL,
        broker_symbol=order.broker_symbol,
        direction=None,
        volume=order.volume_current,
        price=None,
        stop_loss=None,
        take_profit=None,
        deviation_points=0,
        magic=magic,
        comment=comment,
        position_ticket=None,
        order_ticket=order.ticket,
    )


def build_modify_request(
    position: Position,
    *,
    stop_loss: float | None,
    take_profit: float | None,
    magic: int,
    comment: str,
) -> OrderRequest:
    """Move an open position's protective levels, without touching its
    volume or direction (MT5 TRADE_ACTION_SLTP).

    BOTH LEVELS ARE ABSOLUTE AND BOTH ARE WRITTEN. MT5's SLTP action
    takes the whole pair in one request, so passing None for one of them
    REMOVES that level from the live position. A caller that means to
    move only the stop must pass `take_profit=position.take_profit`
    explicitly to preserve it -- this signature deliberately has no
    defaults, so that choice cannot be made by omission. (This is the
    classic trailing-stop bug: move the stop, silently delete the
    target.)

    REFUSES TO LOOSEN A STOP. For a LONG, a stop may only move up; for a
    SHORT, only down. Every caller in this codebase wants trailing
    semantics, and widening risk on a live position is the one mistake
    here with no honest use case -- so it fails loud at the lowest level
    rather than depending on each caller's own care. Adding a stop where
    the position had none is always allowed. If a real loosening case
    ever appears, it gets its own explicit path, not a silent one.
    """
    if stop_loss is not None and position.stop_loss is not None:
        if position.side is PositionSide.LONG and stop_loss < position.stop_loss:
            raise ValueError(
                f"refusing to loosen a LONG stop on position {position.ticket}: "
                f"{position.stop_loss} -> {stop_loss} widens risk"
            )
        if position.side is PositionSide.SHORT and stop_loss > position.stop_loss:
            raise ValueError(
                f"refusing to loosen a SHORT stop on position {position.ticket}: "
                f"{position.stop_loss} -> {stop_loss} widens risk"
            )

    return OrderRequest(
        action=OrderAction.MODIFY,
        broker_symbol=position.broker_symbol,
        direction=None,
        volume=position.volume,
        price=None,
        stop_loss=stop_loss,
        take_profit=take_profit,
        deviation_points=0,
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
        opposite direction, which MT5 nets against the open position.
        CANCEL and MODIFY are different MT5 actions entirely
        (TRADE_ACTION_REMOVE for a still-pending order that was never
        filled; TRADE_ACTION_SLTP for moving an open position's
        protective levels without dealing any volume) and are each
        dispatched separately, before any of the DEAL-specific fields
        below are built."""
        self._require_connected()
        mt5 = _mt5()

        if request.action is OrderAction.CANCEL:
            assert request.order_ticket is not None
            raw = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": request.order_ticket})
            if raw is None:
                raise MT5ExecutionError(f"order_send() returned None: {mt5.last_error()}")
            from datetime import UTC as _UTC

            return map_order_result(raw, now=datetime.now(_UTC))

        if request.action is OrderAction.MODIFY:
            assert request.position_ticket is not None
            # TRADE_ACTION_SLTP: no deal, no volume, no price, no
            # deviation -- only the protective levels move. 0.0 is MT5's
            # own "no level" value, which is why build_modify_request
            # forces the caller to state both sides explicitly.
            sltp_request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": request.broker_symbol,
                "position": request.position_ticket,
                "sl": request.stop_loss if request.stop_loss is not None else 0.0,
                "tp": request.take_profit if request.take_profit is not None else 0.0,
                "magic": request.magic,
                "comment": request.comment,
            }
            raw = mt5.order_send(sltp_request)
            if raw is None:
                raise MT5ExecutionError(f"order_send() returned None: {mt5.last_error()}")
            from datetime import UTC as _UTC2

            return map_order_result(raw, now=datetime.now(_UTC2))

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
