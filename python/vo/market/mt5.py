"""
The MT5 read adapter -- Phase 12.

This is the ONE place in the whole codebase allowed to import MetaTrader5
(gate G7, enforced by tests/unit/test_architecture.py::
test_only_the_broker_adapter_imports_metatrader5). Everything else works
against the canonical value types (vo.market.account, vo.market.symbol),
never the terminal, so live / replay / backtest cannot drift apart -- the
whole reason the adapter is fenced off here.

Scope: READ only. account, symbol, open positions, pending orders. No
order is ever sent from this module -- placing/modifying/closing is the
execution adapter's job (Phase 16, the separately-authorized
vo.core.mt5), kept apart so a read path can never accidentally become a
write path.

Two structural choices make this testable and portable:

  1. `import MetaTrader5` is LAZY -- it happens inside _mt5(), never at
     module import. So this module imports cleanly on Linux/CI (where the
     Windows-only package and a terminal do not exist) for the
     architecture test and the mapper unit tests; the real package is only
     touched when you actually connect on the Windows terminal. The
     architecture test still sees the `import MetaTrader5` in the source
     AST and allows it here (and forbids it everywhere else).

  2. The mapping is split from the fetching. `map_account`/`map_position`/
     `map_order`/`map_symbol` are pure functions of a duck-typed MT5
     struct (anything with the right attributes), unit-tested with
     fabricated inputs exactly the way vo.market.deserialization is tested
     with fabricated wire dicts -- no terminal, no package. The
     MT5ReadClient methods do only the fetch, then delegate to the mappers.

MetaTrader5 is an optional, Windows-only dependency (pyproject.toml's
[project.optional-dependencies] mt5). A helpful error is raised if a
connect is attempted without it installed, rather than an opaque
ImportError.
"""

from __future__ import annotations

from typing import Any, Protocol

from vo.market.account import (
    AccountState,
    Order,
    OrderKind,
    OrderState,
    Position,
    PositionSide,
    Rate,
)
from vo.market.identity import InstrumentId
from vo.market.symbol import Symbol

_PLATFORM = "MT5"

# MT5 integer enums -> our value-type enums. Explicit, and raising on an
# unrecognized value rather than guessing (same discipline as
# Timeframe.from_mt5): an unknown position/order type is a fact worth
# surfacing, not silently coercing.
_POSITION_SIDE = {0: PositionSide.LONG, 1: PositionSide.SHORT}

_ORDER_KIND = {
    2: OrderKind.BUY_LIMIT,
    3: OrderKind.SELL_LIMIT,
    4: OrderKind.BUY_STOP,
    5: OrderKind.SELL_STOP,
    6: OrderKind.BUY_STOP_LIMIT,
    7: OrderKind.SELL_STOP_LIMIT,
}

_ORDER_STATE = {
    0: OrderState.STARTED,
    1: OrderState.PLACED,
    2: OrderState.CANCELED,
    3: OrderState.PARTIAL,
    4: OrderState.FILLED,
    5: OrderState.REJECTED,
    6: OrderState.EXPIRED,
    7: OrderState.REQUEST_ADD,
    8: OrderState.REQUEST_MODIFY,
    9: OrderState.REQUEST_CANCEL,
}


class MT5Error(RuntimeError):
    """A terminal call failed, or MetaTrader5 is not installed. Carries
    MT5's own last_error() text where there is one."""


def _price_or_none(value: float) -> float | None:
    """MT5 uses 0.0 for 'no SL/TP set'; that is UNKNOWN, not a real zero."""
    return None if value == 0.0 else float(value)


def _instrument_id(broker_symbol: str, broker_server: str) -> InstrumentId:
    return InstrumentId(
        platform=_PLATFORM, broker_server=broker_server, broker_symbol=broker_symbol
    )


# ── pure mappers (no terminal, no package) ──────────────────────────────────


def map_account(raw: Any) -> AccountState:
    margin = float(raw.margin)
    return AccountState(
        login=int(raw.login),
        name=str(raw.name),
        server=str(raw.server),
        currency=str(raw.currency),
        balance=float(raw.balance),
        equity=float(raw.equity),
        profit=float(raw.profit),
        margin=margin,
        margin_free=float(raw.margin_free),
        # margin_level is a percentage; MT5 reports 0.0 when no margin is in
        # use, which is not a meaningful "level" -> None.
        margin_level=(float(raw.margin_level) if margin != 0.0 else None),
        leverage=int(raw.leverage),
        trade_allowed=bool(raw.trade_allowed),
    )


def map_position(raw: Any, *, broker_server: str) -> Position:
    side = _POSITION_SIDE.get(int(raw.type))
    if side is None:
        raise ValueError(f"Unrecognized MT5 position type: {raw.type!r}")
    return Position(
        ticket=int(raw.ticket),
        instrument_id=_instrument_id(str(raw.symbol), broker_server),
        broker_symbol=str(raw.symbol),
        side=side,
        volume=float(raw.volume),
        price_open=float(raw.price_open),
        price_current=float(raw.price_current),
        stop_loss=_price_or_none(float(raw.sl)),
        take_profit=_price_or_none(float(raw.tp)),
        profit=float(raw.profit),
        swap=float(raw.swap),
        magic=int(raw.magic),
        comment=str(raw.comment),
        opened_at_broker_epoch_s=int(raw.time),
    )


def map_order(raw: Any, *, broker_server: str) -> Order:
    kind = _ORDER_KIND.get(int(raw.type))
    if kind is None:
        raise ValueError(
            f"Unrecognized or non-pending MT5 order type: {raw.type!r} "
            f"(market BUY/SELL become positions, not orders)"
        )
    state = _ORDER_STATE.get(int(raw.state))
    if state is None:
        raise ValueError(f"Unrecognized MT5 order state: {raw.state!r}")
    return Order(
        ticket=int(raw.ticket),
        instrument_id=_instrument_id(str(raw.symbol), broker_server),
        broker_symbol=str(raw.symbol),
        kind=kind,
        state=state,
        volume_current=float(raw.volume_current),
        price_open=float(raw.price_open),
        stop_loss=_price_or_none(float(raw.sl)),
        take_profit=_price_or_none(float(raw.tp)),
        magic=int(raw.magic),
        comment=str(raw.comment),
        setup_at_broker_epoch_s=int(raw.time_setup),
    )


def map_symbol(raw: Any, *, broker_server: str) -> Symbol:
    broker_symbol = str(raw.name)
    return Symbol(
        broker_symbol=broker_symbol,
        description=str(raw.description),
        digits=int(raw.digits),
        point=float(raw.point),
        tick_size=float(raw.trade_tick_size),
        tick_value=float(raw.trade_tick_value),
        contract_size=float(raw.trade_contract_size),
        source=_PLATFORM,
        instrument_id=_instrument_id(broker_symbol, broker_server),
    )


def map_rate(raw: Any) -> Rate:
    """Map one MT5 rate row to a Rate. MT5's copy_rates returns a numpy
    structured array whose rows are accessed by field name (item access),
    so this uses raw["open"] rather than raw.open -- and is tested with a
    plain dict, which supports the same access."""
    return Rate(
        time_broker_epoch_s=int(raw["time"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        tick_volume=int(raw["tick_volume"]),
        spread=int(raw["spread"]),
        real_volume=int(raw["real_volume"]),
    )


# ── the terminal-facing client (the only real-package caller) ───────────────


class TerminalReadApi(Protocol):
    """What a consumer needs from the read side, so a fake can stand in for
    MT5ReadClient in tests without a terminal."""

    def account(self) -> AccountState: ...
    def symbol(self, broker_symbol: str) -> Symbol: ...
    def positions(self) -> tuple[Position, ...]: ...
    def orders(self) -> tuple[Order, ...]: ...
    def copy_rates(self, broker_symbol: str, count: int) -> tuple[Rate, ...]: ...


def _mt5() -> Any:
    """Lazily import MetaTrader5. Isolated here so the whole module imports
    without the Windows-only package present (CI, Linux, the mapper tests),
    and so a missing install produces a clear message, not a bare
    ImportError from deep in a call."""
    try:
        import MetaTrader5  # lazy on purpose -- see this function's docstring
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise MT5Error(
            "MetaTrader5 is not installed. It is an optional, Windows-only "
            "dependency: install with `pip install \"MetaTrader5\"` on the "
            "machine running the terminal (see pyproject.toml's [mt5] extra)."
        ) from exc
    return MetaTrader5


class MT5ReadClient:
    """
    Read-only client over a running MT5 terminal. Construct, `connect()`,
    read, `shutdown()`. Only ever runs where the terminal and the
    MetaTrader5 package both exist (a Windows machine); the mappers above
    carry all the logic that is testable without one.
    """

    def __init__(self) -> None:
        self._server: str | None = None

    def connect(self) -> None:
        mt5 = _mt5()
        if not mt5.initialize():  # pragma: no cover - needs a live terminal
            raise MT5Error(f"MT5 initialize() failed: {mt5.last_error()}")
        info = mt5.account_info()  # pragma: no cover - needs a live terminal
        if info is None:
            raise MT5Error(f"MT5 account_info() returned None: {mt5.last_error()}")
        self._server = str(info.server)

    def shutdown(self) -> None:  # pragma: no cover - needs a live terminal
        _mt5().shutdown()
        self._server = None

    def _require_server(self) -> str:
        if self._server is None:
            raise MT5Error("Not connected -- call connect() first.")
        return self._server

    def account(self) -> AccountState:  # pragma: no cover - needs a live terminal
        info = _mt5().account_info()
        if info is None:
            raise MT5Error(f"account_info() returned None: {_mt5().last_error()}")
        return map_account(info)

    def symbol(self, broker_symbol: str) -> Symbol:  # pragma: no cover
        info = _mt5().symbol_info(broker_symbol)
        if info is None:
            raise MT5Error(
                f"symbol_info({broker_symbol!r}) returned None: {_mt5().last_error()}"
            )
        return map_symbol(info, broker_server=self._require_server())

    def positions(self) -> tuple[Position, ...]:  # pragma: no cover
        server = self._require_server()
        raw = _mt5().positions_get()
        if raw is None:
            return ()
        return tuple(map_position(p, broker_server=server) for p in raw)

    def orders(self) -> tuple[Order, ...]:  # pragma: no cover
        server = self._require_server()
        raw = _mt5().orders_get()
        if raw is None:
            return ()
        return tuple(map_order(o, broker_server=server) for o in raw)

    def symbols(self, contains: str | None = None) -> tuple[str, ...]:  # pragma: no cover
        """The broker symbol NAMES this terminal offers, optionally filtered
        to those containing `contains` (case-insensitive). A read-only
        lookup helper -- names only, no mapping -- for finding the exact
        spelling of an instrument (broker symbol names vary: US100.n,
        USTEC, NAS100, ...)."""
        raw = _mt5().symbols_get()
        if raw is None:
            return ()
        names = tuple(str(s.name) for s in raw)
        if contains:
            needle = contains.lower()
            names = tuple(n for n in names if needle in n.lower())
        return names

    def copy_rates(self, broker_symbol: str, count: int) -> tuple[Rate, ...]:  # pragma: no cover
        """The most recent `count` M1 bars for `broker_symbol`, straight from
        the terminal's history (copy_rates_from_pos, position 0). Raw Rates
        (broker-server epoch time); the caller resolves them to canonical
        Bars via a broker profile. The terminal returns however much M1
        history it has downloaded -- ask for more than exists and you simply
        get what exists."""
        self._require_server()
        mt5 = _mt5()
        rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M1, 0, count)
        if rates is None or len(rates) == 0:
            raise MT5Error(
                f"copy_rates_from_pos({broker_symbol!r}, M1, 0, {count}) returned "
                f"no data: {mt5.last_error()}"
            )
        return tuple(map_rate(r) for r in rates)
