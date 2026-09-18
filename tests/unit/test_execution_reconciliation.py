"""vo.execution.reconciliation -- Phase 16's restart-time scan of
pre-existing open positions against this EA's configured magic number.

Per the user's own explicit direction for this gate ("detect, evaluate
conditions, and eliminate any trade which doesn't follow strategy rules
and VO trade signal confirmations"): the default policy recommends CLOSE
for any position it does not recognize, not merely a warning -- but
execute_reconciliation_actions() only ever touches CLOSE-recommended
findings, and is exercised here against a fake client, never a real one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from vo.core.mt5 import OrderRequest, OrderResult
from vo.execution.reconciliation import execute_reconciliation_actions, reconcile
from vo.execution.types import ReconciliationAction
from vo.market.account import Position, PositionSide
from vo.market.identity import InstrumentId

_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)
_SERVER = "1xTrade-Server"
_MAGIC = 20260914


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
        magic=_MAGIC,
        comment="VO:disposable_v0",
        opened_at_broker_epoch_s=1_789_000_000,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_a_position_carrying_our_magic_number_is_recognized_and_kept():
    report = reconcile([_position(magic=_MAGIC)], magic_number=_MAGIC, now=_NOW)
    assert report.recognized_count == 1
    assert report.unrecognized_count == 0
    finding = report.findings[0]
    assert finding.recognized is True
    assert finding.action is ReconciliationAction.KEEP


def test_a_position_with_a_different_magic_is_unrecognized_and_recommended_close():
    report = reconcile([_position(magic=999999)], magic_number=_MAGIC, now=_NOW)
    assert report.recognized_count == 0
    assert report.unrecognized_count == 1
    finding = report.findings[0]
    assert finding.recognized is False
    assert finding.action is ReconciliationAction.CLOSE
    assert "does not" in finding.reason
    assert len(report.to_close) == 1


def test_a_manually_opened_position_with_zero_magic_is_also_unrecognized():
    report = reconcile([_position(magic=0)], magic_number=_MAGIC, now=_NOW)
    assert report.findings[0].recognized is False
    assert report.findings[0].action is ReconciliationAction.CLOSE


def test_reconcile_handles_a_mixed_batch_and_classifies_every_position():
    positions = [_position(ticket=1, magic=_MAGIC), _position(ticket=2, magic=1)]
    report = reconcile(positions, magic_number=_MAGIC, now=_NOW)
    assert len(report.findings) == 2
    assert report.recognized_count == 1
    assert report.unrecognized_count == 1


def test_reconcile_with_no_open_positions_is_an_empty_but_valid_report():
    report = reconcile([], magic_number=_MAGIC, now=_NOW)
    assert report.findings == ()
    assert report.recognized_count == 0
    assert report.to_close == ()


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


def test_execute_reconciliation_actions_only_touches_close_findings():
    positions = [_position(ticket=1, magic=_MAGIC), _position(ticket=2, magic=1)]
    report = reconcile(positions, magic_number=_MAGIC, now=_NOW)
    client = _RecordingClient()

    results = execute_reconciliation_actions(
        report, client, magic=_MAGIC, comment="VO", deviation_points=20
    )

    assert len(results) == 1
    assert len(client.requests) == 1
    assert client.requests[0].position_ticket == 2


def test_execute_reconciliation_actions_sends_nothing_when_all_recognized():
    report = reconcile([_position(magic=_MAGIC)], magic_number=_MAGIC, now=_NOW)
    client = _RecordingClient()

    results = execute_reconciliation_actions(
        report, client, magic=_MAGIC, comment="VO", deviation_points=20
    )

    assert results == ()
    assert client.requests == []
