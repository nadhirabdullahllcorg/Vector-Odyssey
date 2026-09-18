"""
The MT5 write adapter's pure functions (Phase 16, vo.core.mt5).

Mirrors tests/unit/test_mt5_read.py's own style exactly: the
terminal-facing calls (connect/send_order's real body) need a live
Windows terminal and the MetaTrader5 package, so they are not exercised
here. build_open_request/build_close_request/map_order_result are pure
and are tested with fabricated, duck-typed inputs (SimpleNamespace).
vo.core.mt5 imports cleanly here because its MetaTrader5 import is lazy
(inside _mt5()), never at module load.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from vo.core.mt5 import (
    OrderAction,
    OrderFailureReason,
    OrderRequest,
    build_close_request,
    build_open_request,
    map_order_result,
)
from vo.interfaces.decisions import Direction
from vo.interfaces.signals import TradeSignal
from vo.market.account import Position, PositionSide
from vo.market.identity import InstrumentId

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)
_SERVER = "1xTrade-Server"


def _trade_signal(**overrides):
    defaults = dict(
        object_id="TRADE-1",
        risk_check_id="RISK-1",
        instrument_id="US100",
        generated_at_utc=_NOW,
        direction=Direction.LONG,
        volume=2.0,
        entry_reference_price=25000.0,
        stop_price=24950.0,
        take_profit_price=25100.0,
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _position(**overrides):
    defaults = dict(
        ticket=778001,
        instrument_id=InstrumentId(platform="MT5", broker_server=_SERVER, broker_symbol="US100.n"),
        broker_symbol="US100.n",
        side=PositionSide.LONG,
        volume=1.0,
        price_open=28950.0,
        price_current=28975.0,
        stop_loss=28900.0,
        take_profit=29050.0,
        profit=25.0,
        swap=-1.2,
        magic=20260914,
        comment="VO:disposable_v0",
        opened_at_broker_epoch_s=1_789_000_000,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_build_open_request_translates_an_approved_trade_signal():
    request = build_open_request(
        _trade_signal(),
        broker_symbol="US100.n",
        magic=20260914,
        comment="VO:disposable_v0",
        deviation_points=20,
    )
    assert request.action is OrderAction.OPEN
    assert request.broker_symbol == "US100.n"
    assert request.direction is Direction.LONG
    assert request.volume == 2.0
    assert request.price == 25000.0
    assert request.stop_loss == 24950.0
    assert request.take_profit == 25100.0
    assert request.magic == 20260914
    assert request.comment == "VO:disposable_v0"
    assert request.position_ticket is None


def test_build_close_request_carries_the_position_ticket_not_a_direction():
    request = build_close_request(
        _position(), magic=20260914, comment="VO:disposable_v0", deviation_points=20
    )
    assert request.action is OrderAction.CLOSE
    assert request.broker_symbol == "US100.n"
    assert request.direction is None
    assert request.volume == 1.0
    assert request.position_ticket == 778001


def test_order_request_rejects_open_with_neutral_or_missing_direction():
    with pytest.raises(ValueError, match="real direction"):
        OrderRequest(
            action=OrderAction.OPEN,
            broker_symbol="US100.n",
            direction=None,
            volume=1.0,
            price=25000.0,
            stop_loss=None,
            take_profit=None,
            deviation_points=20,
            magic=1,
            comment="x",
            position_ticket=None,
        )


def test_order_request_rejects_close_without_position_ticket():
    with pytest.raises(ValueError, match="position_ticket"):
        OrderRequest(
            action=OrderAction.CLOSE,
            broker_symbol="US100.n",
            direction=None,
            volume=1.0,
            price=None,
            stop_loss=None,
            take_profit=None,
            deviation_points=20,
            magic=1,
            comment="x",
            position_ticket=None,
        )


def test_order_request_rejects_non_positive_volume():
    with pytest.raises(ValueError, match="volume must be positive"):
        OrderRequest(
            action=OrderAction.OPEN,
            broker_symbol="US100.n",
            direction=Direction.LONG,
            volume=0.0,
            price=25000.0,
            stop_loss=None,
            take_profit=None,
            deviation_points=20,
            magic=1,
            comment="x",
            position_ticket=None,
        )


def _raw_result(**over):
    base = dict(
        retcode=10009, deal=555, order=990, volume=2.0, price=25001.0,
        comment="Request executed",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_order_result_success():
    result = map_order_result(_raw_result(), now=_NOW)
    assert result.approved is True
    assert result.failure_reason is None
    assert result.retcode == 10009
    assert result.deal_ticket == 555
    assert result.order_ticket == 990
    assert result.filled_volume == 2.0
    assert result.filled_price == 25001.0
    assert result.generated_at_utc == _NOW


def test_map_order_result_partial_done_is_also_a_success():
    result = map_order_result(_raw_result(retcode=10010), now=_NOW)
    assert result.approved is True
    assert result.failure_reason is None


@pytest.mark.parametrize(
    "retcode,expected",
    [
        (10004, OrderFailureReason.REQUOTE),
        (10006, OrderFailureReason.REJECTED),
        (10014, OrderFailureReason.BAD_VOLUME),
        (10015, OrderFailureReason.BAD_STOPS),
        (10016, OrderFailureReason.BAD_STOPS),
        (10017, OrderFailureReason.TRADING_DISABLED),
        (10018, OrderFailureReason.MARKET_CLOSED),
        (10019, OrderFailureReason.MARGIN),
        (10020, OrderFailureReason.REQUOTE),
        (10021, OrderFailureReason.REQUOTE),
        (10026, OrderFailureReason.TRADING_DISABLED),
        (10027, OrderFailureReason.TRADING_DISABLED),
        (10031, OrderFailureReason.DISCONNECT),
        (10045, OrderFailureReason.REJECTED),
    ],
)
def test_map_order_result_classifies_every_named_failure_mode(retcode, expected):
    result = map_order_result(_raw_result(retcode=retcode, deal=0, order=0), now=_NOW)
    assert result.approved is False
    assert result.failure_reason is expected
    assert result.retcode == retcode
    assert result.deal_ticket is None
    assert result.order_ticket is None
    assert result.filled_volume is None
    assert result.filled_price is None


def test_map_order_result_unrecognized_retcode_is_honestly_unknown():
    result = map_order_result(_raw_result(retcode=99999, deal=0, order=0), now=_NOW)
    assert result.approved is False
    assert result.failure_reason is OrderFailureReason.UNKNOWN


def test_map_order_result_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        map_order_result(_raw_result(), now=datetime(2026, 9, 18, 14, 30))
