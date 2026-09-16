"""
The MT5 read adapter's mappers (Phase 12, vo.market.mt5).

The terminal-facing calls (initialize/account_info/positions_get/...) need
a live Windows terminal and the MetaTrader5 package, so they are not
exercised here. The mapping logic -- MT5 struct -> canonical value type --
is pure and is tested with fabricated, duck-typed inputs (SimpleNamespace),
exactly the way vo.market.deserialization is tested with fabricated wire
dicts. vo.market.mt5 imports cleanly here because its MetaTrader5 import is
lazy (inside _mt5()), never at module load.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vo.market.account import OrderKind, OrderState, PositionSide
from vo.market.identity import InstrumentId
from vo.market.mt5 import map_account, map_order, map_position, map_symbol

_SERVER = "1xTrade-Server"


def _raw_account(**over):
    base = dict(
        login=51234567,
        name="J. Nazir",
        server=_SERVER,
        currency="USD",
        balance=10000.0,
        equity=10120.5,
        profit=120.5,
        margin=500.0,
        margin_free=9620.5,
        margin_level=2024.1,
        leverage=100,
        trade_allowed=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_account_carries_the_fields_through():
    acct = map_account(_raw_account())
    assert acct.login == 51234567
    assert acct.server == _SERVER
    assert acct.currency == "USD"
    assert acct.equity == 10120.5
    assert acct.leverage == 100
    assert acct.trade_allowed is True
    assert acct.margin_level == 2024.1


def test_map_account_margin_level_is_none_when_no_margin_in_use():
    """MT5 reports margin_level 0.0 with no open positions; that is not a
    meaningful level, so it maps to None, not a misleading zero."""
    acct = map_account(_raw_account(margin=0.0, margin_level=0.0))
    assert acct.margin_level is None


def _raw_position(**over):
    base = dict(
        ticket=778001,
        symbol="US100.n",
        type=0,  # POSITION_TYPE_BUY
        volume=1.0,
        price_open=28950.0,
        price_current=28975.0,
        sl=28900.0,
        tp=29050.0,
        profit=25.0,
        swap=-1.2,
        magic=0,
        comment="",
        time=1_789_000_000,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_position_long_and_identity():
    pos = map_position(_raw_position(), broker_server=_SERVER)
    assert pos.side is PositionSide.LONG
    assert pos.instrument_id == InstrumentId(
        platform="MT5", broker_server=_SERVER, broker_symbol="US100.n"
    )
    assert pos.volume == 1.0
    assert pos.stop_loss == 28900.0
    assert pos.take_profit == 29050.0
    assert pos.opened_at_broker_epoch_s == 1_789_000_000


def test_map_position_short():
    pos = map_position(_raw_position(type=1), broker_server=_SERVER)
    assert pos.side is PositionSide.SHORT


def test_map_position_zero_sl_tp_becomes_none():
    pos = map_position(_raw_position(sl=0.0, tp=0.0), broker_server=_SERVER)
    assert pos.stop_loss is None
    assert pos.take_profit is None


def test_map_position_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unrecognized MT5 position type"):
        map_position(_raw_position(type=99), broker_server=_SERVER)


def _raw_order(**over):
    base = dict(
        ticket=990123,
        symbol="US100.n",
        type=2,  # ORDER_TYPE_BUY_LIMIT
        state=1,  # ORDER_STATE_PLACED
        volume_current=2.0,
        price_open=28800.0,
        sl=28750.0,
        tp=29000.0,
        magic=42,
        comment="vo",
        time_setup=1_789_000_500,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_order_kind_and_state():
    order = map_order(_raw_order(), broker_server=_SERVER)
    assert order.kind is OrderKind.BUY_LIMIT
    assert order.state is OrderState.PLACED
    assert order.volume_current == 2.0
    assert order.stop_loss == 28750.0
    assert order.broker_symbol == "US100.n"
    assert order.setup_at_broker_epoch_s == 1_789_000_500


def test_map_order_rejects_market_type_which_is_never_pending():
    """ORDER_TYPE_BUY (0) is a market order -- it becomes a position, never
    a pending order, so it must not appear here."""
    with pytest.raises(ValueError, match="non-pending MT5 order type"):
        map_order(_raw_order(type=0), broker_server=_SERVER)


def test_map_order_rejects_unknown_state():
    with pytest.raises(ValueError, match="Unrecognized MT5 order state"):
        map_order(_raw_order(state=99), broker_server=_SERVER)


def _raw_symbol(**over):
    base = dict(
        name="US100.n",
        description="E-mini Nasdaq 100/spot",
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_tick_value=0.01,
        trade_contract_size=1.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_symbol_matches_the_canonical_symbol_shape():
    sym = map_symbol(_raw_symbol(), broker_server=_SERVER)
    assert sym.broker_symbol == "US100.n"
    assert sym.tick_size == 0.01
    assert sym.contract_size == 1.0
    assert sym.source == "MT5"
    assert sym.instrument_id == InstrumentId(
        platform="MT5", broker_server=_SERVER, broker_symbol="US100.n"
    )
