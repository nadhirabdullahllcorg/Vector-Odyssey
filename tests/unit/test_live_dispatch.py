"""vo.telemetry.live_dispatch -- the seam where an approved TradeSignal
actually reaches a broker.

The refusals matter more than the send. Every one of these gates exists
because something it guards against would cost real money."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from vo.compliance.compliance_config import ComplianceConfig, DailyLossMode
from vo.compliance.engine import ComplianceEngine
from vo.core.mt5 import OrderRequest, OrderResult
from vo.execution.execution_config import ExecutionConfig
from vo.execution.router import ExecutionRouter
from vo.interfaces.decisions import Direction
from vo.interfaces.signals import TradeSignal
from vo.market.account import AccountState
from vo.market.identity import InstrumentId
from vo.market.symbol import Symbol
from vo.telemetry.live_dispatch import (
    DispatchOutcome,
    DispatchResult,
    LiveDispatcher,
    projected_loss,
)

_NOW = datetime(2026, 9, 21, 14, 30, tzinfo=UTC)
_NY = datetime(2026, 9, 21, 10, 30)
_SYMBOL_NAME = "US100.n"
_MAGIC = 20260914


def _symbol() -> Symbol:
    return Symbol(
        broker_symbol=_SYMBOL_NAME,
        description="Nasdaq",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=1.0,
        source="test",
        instrument_id=InstrumentId(
            platform="MT5", broker_server="1xTrade-Server", broker_symbol=_SYMBOL_NAME
        ),
    )


def _account(equity: float = 100_000.0) -> AccountState:
    return AccountState(
        login=5150234,
        name="J. Nazir",
        server="1xTrade-Server",
        currency="USD",
        balance=equity,
        equity=equity,
        profit=0.0,
        margin=0.0,
        margin_free=equity,
        margin_level=None,
        leverage=100,
        trade_allowed=True,
    )


def _trade_signal(volume: float = 1.0, stop: float = 19_950.0) -> TradeSignal:
    return TradeSignal(
        object_id="ts:1",
        risk_check_id="rc:1",
        instrument_id=_SYMBOL_NAME,
        generated_at_utc=_NOW,
        direction=Direction.LONG,
        volume=volume,
        entry_reference_price=20_000.0,
        stop_price=stop,
        take_profit_price=20_100.0,
    )


def _compliance(**overrides: object) -> ComplianceEngine:
    base: dict[str, object] = dict(
        version=1,
        daily_loss_limit_fraction=0.05,
        total_drawdown_limit_fraction=0.20,
        safety_buffer_fraction=0.005,
        warning_threshold_fraction=0.70,
        critical_threshold_fraction=0.90,
        daily_loss_mode=DailyLossMode.DRAWDOWN_HEADROOM,
        daily_halt_at_total_usage=0.65,
    )
    base.update(overrides)
    return ComplianceEngine(
        config=ComplianceConfig(**base),  # type: ignore[arg-type]
        trading_day_opens=time(18, 0),
    )


class FakeTerminal:
    def __init__(self, *, approve: bool = True) -> None:
        self.sent: list[OrderRequest] = []
        self._approve = approve

    def send_order(self, request: OrderRequest) -> OrderResult:
        self.sent.append(request)
        return OrderResult(
            generated_at_utc=_NOW,
            approved=self._approve,
            retcode=10009 if self._approve else 10019,
            failure_reason=None if self._approve else __import__(
                "vo.core.mt5", fromlist=["OrderFailureReason"]
            ).OrderFailureReason.MARGIN,
            broker_comment="Done" if self._approve else "No money",
            deal_ticket=1,
            order_ticket=1,
            filled_volume=1.0,
            filled_price=20_000.0,
        )


class PassedPreflight:
    """Stands in for a preflight that has already succeeded."""

    def __init__(self, passed: bool = True) -> None:
        self._passed = passed
        self.runs = 0

    def run(self, **_: object):
        from vo.execution.preflight import PreflightReport, PreflightStep, StepResult

        self.runs += 1
        report = PreflightReport(started_at_utc=_NOW)
        report.steps.append(
            StepResult(step=PreflightStep.OPEN_POSITION, ok=self._passed, detail="stub")
        )
        report.finished_at_utc = _NOW
        return report


def _dispatcher(
    *, terminal: FakeTerminal | None = None,
    preflight: PassedPreflight | None = None,
    compliance: ComplianceEngine | None = None,
    equity: float = 100_000.0,
) -> LiveDispatcher:
    terminal = terminal or FakeTerminal()
    return LiveDispatcher(
        router=ExecutionRouter(
            client=terminal,
            config=ExecutionConfig(
                version=1, magic_number=_MAGIC, comment_prefix="VO:", deviation_points=20
            ),
        ),
        compliance=compliance or _compliance(),
        account_state=lambda: _account(equity),
        now=lambda: _NOW,
        now_ny=lambda: _NY,
        broker_symbol=_SYMBOL_NAME,
        preflight=preflight,  # type: ignore[arg-type]
    )


# ── the loss figure the headroom gate depends on ──────────────────────────


def test_projected_loss_is_computed_from_the_signals_own_numbers() -> None:
    """50 points of stop distance, tick_size 0.01, tick_value 0.01,
    1.0 lot -> 5000 ticks * 0.01 = 50.00."""
    assert projected_loss(_trade_signal(), _symbol()) == pytest.approx(50.0)


# ── the gates, in order ───────────────────────────────────────────────────


def test_nothing_dispatches_before_the_execution_path_is_commissioned() -> None:
    dispatcher = _dispatcher(preflight=None)

    result = dispatcher.dispatch(_trade_signal(), symbol=_symbol())

    assert result.outcome is DispatchOutcome.NOT_COMMISSIONED
    assert result.reason is not None and "proven against the broker" in result.reason


def test_a_failed_preflight_leaves_the_session_uncommissioned() -> None:
    dispatcher = _dispatcher(preflight=PassedPreflight(passed=False))
    dispatcher.commission()

    assert dispatcher.commissioned is False
    assert dispatcher.dispatch(_trade_signal(), symbol=_symbol()).outcome is (
        DispatchOutcome.NOT_COMMISSIONED
    )


def test_commissioning_runs_once_not_once_per_poll() -> None:
    """Each run places real orders. Repeating it every poll would be a
    self-inflicted trading strategy."""
    preflight = PassedPreflight()
    dispatcher = _dispatcher(preflight=preflight)

    dispatcher.commission()
    dispatcher.commission()
    dispatcher.commission()

    assert preflight.runs == 1


def test_a_commissioned_session_sends_a_clean_signal() -> None:
    terminal = FakeTerminal()
    dispatcher = _dispatcher(terminal=terminal, preflight=PassedPreflight())
    dispatcher.commission()

    result = dispatcher.dispatch(_trade_signal(), symbol=_symbol())

    assert result.outcome is DispatchOutcome.SENT
    assert result.sent is True
    assert len(terminal.sent) == 1
    assert terminal.sent[0].magic == _MAGIC


def test_compliance_blocking_stops_the_signal_before_the_broker() -> None:
    compliance = _compliance()
    # Drive the account into a breach before dispatching.
    compliance.on_snapshot(
        object_id="v1", generated_at_utc=_NOW, now_ny=_NY, account=_account(100_000.0)
    )
    dispatcher = _dispatcher(compliance=compliance, preflight=PassedPreflight(), equity=70_000.0)
    dispatcher.commission()

    result = dispatcher.dispatch(_trade_signal(), symbol=_symbol())

    assert result.outcome is DispatchOutcome.COMPLIANCE_BLOCKED
    assert result.reason


def test_a_trade_too_big_for_the_remaining_headroom_is_refused() -> None:
    compliance = _compliance()
    compliance.on_snapshot(
        object_id="v1", generated_at_utc=_NOW, now_ny=_NY, account=_account(100_000.0)
    )
    # Equity near the halt point leaves only a sliver of room.
    dispatcher = _dispatcher(
        compliance=compliance, preflight=PassedPreflight(), equity=87_400.0
    )
    dispatcher.commission()

    result = dispatcher.dispatch(_trade_signal(volume=50.0), symbol=_symbol())

    assert result.outcome is DispatchOutcome.INSUFFICIENT_HEADROOM
    assert result.reason is not None and "not usable room" in result.reason


def test_a_broker_rejection_is_recorded_as_faithfully_as_a_fill() -> None:
    dispatcher = _dispatcher(terminal=FakeTerminal(approve=False), preflight=PassedPreflight())
    dispatcher.commission()

    result = dispatcher.dispatch(_trade_signal(), symbol=_symbol())

    assert result.outcome is DispatchOutcome.REJECTED_BY_BROKER
    assert result.event is not None
    assert result.reason is not None and "retcode" in result.reason


def test_every_outcome_is_kept_for_the_audit_trail() -> None:
    dispatcher = _dispatcher(preflight=PassedPreflight())

    dispatcher.dispatch(_trade_signal(), symbol=_symbol())   # uncommissioned
    dispatcher.commission()
    dispatcher.dispatch(_trade_signal(), symbol=_symbol())   # sent

    assert len(dispatcher.results) == 2
    assert [r.outcome for r in dispatcher.results] == [
        DispatchOutcome.NOT_COMMISSIONED,
        DispatchOutcome.SENT,
    ]


def test_a_refusal_without_a_reason_is_a_programming_error() -> None:
    """A flat day has to be explicable afterwards."""
    with pytest.raises(ValueError, match="must carry a reason"):
        DispatchResult(
            outcome=DispatchOutcome.COMPLIANCE_BLOCKED,
            trade_signal_id="ts:1",
            generated_at_utc=_NOW,
        )
