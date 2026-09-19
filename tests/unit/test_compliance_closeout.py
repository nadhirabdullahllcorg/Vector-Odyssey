"""vo.execution.compliance_closeout -- closes every open position and
cancels every pending order the moment a ComplianceVerdict reports a
breach, mirroring PropFirmGuard's own documented behavior. Exercised
against a fake client, never a real one -- same posture as
test_execution_reconciliation.py."""

from __future__ import annotations

from datetime import UTC, datetime

from vo.core.mt5 import OrderAction, OrderRequest, OrderResult
from vo.execution.compliance_closeout import execute_breach_closeout, plan_breach_closeout
from vo.interfaces.compliance import ComplianceStatus, ComplianceVerdict
from vo.interfaces.economic_events import EconomicEvent, EventImportance
from vo.market.account import Order, OrderKind, OrderState, Position, PositionSide
from vo.market.identity import InstrumentId

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)
_SERVER = "1xTrade-Server"
_MAGIC = 20260914


def _verdict(**overrides):
    defaults = dict(
        object_id="V1",
        generated_at_utc=_NOW,
        status=ComplianceStatus.SAFE,
        allowed=True,
        reason=None,
        day_start_equity=10_000.0,
        peak_equity=10_000.0,
        current_equity=10_000.0,
        daily_loss_used_fraction=0.0,
        total_drawdown_used_fraction=0.0,
    )
    defaults.update(overrides)
    return ComplianceVerdict(**defaults)


def _breached_verdict(status=ComplianceStatus.BREACHED_TOTAL, **overrides):
    base = dict(
        status=status,
        allowed=False,
        reason=f"{status} -- test fixture",
    )
    base.update(overrides)
    return _verdict(**base)


def _position(**overrides):
    defaults = dict(
        ticket=778001,
        instrument_id=InstrumentId(platform="MT5", broker_server=_SERVER, broker_symbol="US100.n"),
        broker_symbol="US100.n",
        side=PositionSide.LONG,
        volume=1.0,
        price_open=28950.0,
        price_current=28500.0,
        stop_loss=None,
        take_profit=None,
        profit=-450.0,
        swap=-1.2,
        magic=_MAGIC,
        comment="VO:disposable_v0",
        opened_at_broker_epoch_s=1_789_000_000,
    )
    defaults.update(overrides)
    return Position(**defaults)


def _order(**overrides):
    defaults = dict(
        ticket=990001,
        instrument_id=InstrumentId(platform="MT5", broker_server=_SERVER, broker_symbol="US100.n"),
        broker_symbol="US100.n",
        kind=OrderKind.BUY_LIMIT,
        state=OrderState.PLACED,
        volume_current=1.0,
        price_open=28400.0,
        stop_loss=None,
        take_profit=None,
        magic=_MAGIC,
        comment="VO:disposable_v0",
        setup_at_broker_epoch_s=1_789_000_000,
    )
    defaults.update(overrides)
    return Order(**defaults)


class _RecordingClient:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    def send_order(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        return OrderResult(
            generated_at_utc=_NOW,
            approved=True,
            retcode=10009,
            failure_reason=None,
            broker_comment="Request executed",
            deal_ticket=1,
            order_ticket=2,
            filled_volume=request.volume,
            filled_price=None,
        )


# ── plan_breach_closeout ─────────────────────────────────────────────────


def test_a_safe_verdict_plans_nothing():
    plan = plan_breach_closeout(_verdict(), positions=[_position()], orders=[_order()])
    assert plan.triggered is False
    assert plan.positions_to_close == ()
    assert plan.orders_to_cancel == ()


def test_warning_and_critical_also_plan_nothing():
    for status in (ComplianceStatus.WARNING, ComplianceStatus.CRITICAL):
        verdict = _verdict(status=status, allowed=True, reason=None)
        plan = plan_breach_closeout(verdict, positions=[_position()], orders=[])
        assert plan.triggered is False


def test_news_blackout_does_not_trigger_a_closeout():
    event = EconomicEvent(
        event_id="NFP-1",
        name="Non-Farm Payrolls",
        currency="USD",
        scheduled_at_utc=_NOW,
        importance=EventImportance.HIGH,
    )
    verdict = _verdict(
        status=ComplianceStatus.NEWS_BLACKOUT,
        allowed=False,
        reason="inside the blackout window -- test fixture",
        active_news_event=event,
    )
    plan = plan_breach_closeout(verdict, positions=[_position()], orders=[])
    assert plan.triggered is False


def test_a_total_breach_plans_to_close_every_position_and_cancel_every_order():
    verdict = _breached_verdict(ComplianceStatus.BREACHED_TOTAL)
    positions = [_position(ticket=1), _position(ticket=2)]
    orders = [_order(ticket=10)]
    plan = plan_breach_closeout(verdict, positions=positions, orders=orders)
    assert plan.triggered is True
    assert plan.status is ComplianceStatus.BREACHED_TOTAL
    assert plan.positions_to_close == tuple(positions)
    assert plan.orders_to_cancel == tuple(orders)
    assert plan.reason is not None


def test_a_daily_breach_also_triggers_a_closeout():
    verdict = _breached_verdict(ComplianceStatus.BREACHED_DAILY)
    plan = plan_breach_closeout(verdict, positions=[_position()], orders=[])
    assert plan.triggered is True


def test_an_unrecognized_stray_position_is_closed_too_regardless_of_magic():
    # A breach closeout is not a reconciliation scan -- it closes
    # everything open, whoever opened it, because the ACCOUNT breached.
    verdict = _breached_verdict()
    stray = _position(ticket=5, magic=0)
    plan = plan_breach_closeout(verdict, positions=[stray], orders=[])
    assert plan.positions_to_close == (stray,)


# ── execute_breach_closeout ───────────────────────────────────────────────


def test_execute_breach_closeout_does_nothing_for_an_untriggered_plan():
    plan = plan_breach_closeout(_verdict(), positions=[_position()], orders=[_order()])
    client = _RecordingClient()
    results = execute_breach_closeout(
        plan, client, magic=_MAGIC, comment="VO:breach_closeout", deviation_points=20
    )
    assert results == ()
    assert client.requests == []


def test_execute_breach_closeout_closes_positions_then_cancels_orders():
    verdict = _breached_verdict()
    positions = [_position(ticket=1), _position(ticket=2)]
    orders = [_order(ticket=10)]
    plan = plan_breach_closeout(verdict, positions=positions, orders=orders)
    client = _RecordingClient()

    results = execute_breach_closeout(
        plan, client, magic=_MAGIC, comment="VO:breach_closeout", deviation_points=20
    )

    assert len(results) == 3
    assert len(client.requests) == 3
    # positions closed first, in order, then the pending order cancelled
    assert client.requests[0].action is OrderAction.CLOSE
    assert client.requests[0].position_ticket == 1
    assert client.requests[1].action is OrderAction.CLOSE
    assert client.requests[1].position_ticket == 2
    assert client.requests[2].action is OrderAction.CANCEL
    assert client.requests[2].order_ticket == 10
