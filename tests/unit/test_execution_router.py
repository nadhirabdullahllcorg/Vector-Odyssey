"""vo.execution.router -- ExecutionRouter.place(), Phase 16's
"TradeSignal -> MT5 order" seam, exercised against a fake
TerminalExecutionApi (never a real terminal)."""

from __future__ import annotations

from datetime import UTC, datetime

from vo.core.mt5 import OrderRequest, OrderResult
from vo.execution.execution_config import ExecutionConfig
from vo.execution.router import ExecutionRouter
from vo.execution.types import ExecutionEvent
from vo.interfaces.compliance import ComplianceApproval
from vo.interfaces.decisions import Direction
from vo.interfaces.signals import TradeSignal

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)


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


def _approval(trade_signal: TradeSignal, **overrides) -> ComplianceApproval:
    """A valid ComplianceApproval for the given TradeSignal -- built
    directly here (not via vo.compliance.engine.approve_trade) since this
    file exercises ExecutionRouter in isolation; gate G15
    (test_compliance_is_the_sole_approval_producer) exempts test files."""
    defaults = dict(
        object_id="APPROVAL-1",
        trade_signal_id=trade_signal.object_id,
        verdict_id="VERDICT-1",
        generated_at_utc=_NOW,
    )
    defaults.update(overrides)
    return ComplianceApproval(**defaults)


def _config(**overrides):
    defaults = dict(version=1, magic_number=20260914, comment_prefix="VO", deviation_points=20)
    defaults.update(overrides)
    return ExecutionConfig(**defaults)


class _FakeApprovingClient:
    """Records every OrderRequest it was asked to send, and always
    reports a fill."""

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
            deal_ticket=555,
            order_ticket=990,
            filled_volume=request.volume,
            filled_price=request.price,
        )


class _FakeRejectingClient:
    def send_order(self, request: OrderRequest) -> OrderResult:
        from vo.core.mt5 import OrderFailureReason

        return OrderResult(
            generated_at_utc=_NOW,
            approved=False,
            retcode=10019,
            failure_reason=OrderFailureReason.MARGIN,
            broker_comment="No money",
            deal_ticket=None,
            order_ticket=None,
            filled_volume=None,
            filled_price=None,
        )


def test_place_tags_the_request_with_the_configured_magic_and_comment():
    client = _FakeApprovingClient()
    router = ExecutionRouter(client=client, config=_config())
    signal = _trade_signal()

    router.place(signal, _approval(signal), broker_symbol="US100.n")

    assert len(client.requests) == 1
    sent = client.requests[0]
    assert sent.magic == 20260914
    assert sent.comment == "VO"
    assert sent.broker_symbol == "US100.n"
    assert sent.volume == 2.0


def test_place_records_an_execution_event_on_success():
    client = _FakeApprovingClient()
    router = ExecutionRouter(client=client, config=_config())
    signal = _trade_signal()

    event = router.place(signal, _approval(signal), broker_symbol="US100.n")

    assert isinstance(event, ExecutionEvent)
    assert event.trade_signal_id == "TRADE-1"
    assert event.order_result.approved is True
    assert router.events == [event]
    assert router.known_tickets[990] == "TRADE-1"


def test_place_records_an_execution_event_even_when_rejected():
    client = _FakeRejectingClient()
    router = ExecutionRouter(client=client, config=_config())
    signal = _trade_signal()

    event = router.place(signal, _approval(signal), broker_symbol="US100.n")

    assert event.order_result.approved is False
    assert router.events == [event]
    assert router.known_tickets == {}


def test_place_refuses_an_approval_issued_for_a_different_trade_signal():
    client = _FakeApprovingClient()
    router = ExecutionRouter(client=client, config=_config())
    signal = _trade_signal()
    mismatched = _approval(_trade_signal(object_id="TRADE-OTHER"))

    try:
        router.place(signal, mismatched, broker_symbol="US100.n")
    except ValueError as exc:
        assert "mismatched" in str(exc)
    else:
        raise AssertionError("expected a ValueError for a mismatched ComplianceApproval")
    assert client.requests == []
