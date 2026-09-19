"""vo.execution.preflight -- the commissioning sequence that must pass
before a strategy may arm a setup.

The failure paths matter more than the happy one here: a preflight that
works when everything works proves very little. What has to hold is that
it always cleans up, and that it says so loudly when it cannot."""

from __future__ import annotations

from datetime import UTC, datetime

from vo.core.mt5 import OrderAction, OrderFailureReason, OrderRequest, OrderResult
from vo.execution.execution_config import ExecutionConfig
from vo.execution.preflight import PreflightSequence, PreflightStep
from vo.market.account import Position, PositionSide
from vo.market.identity import InstrumentId

_NOW = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
_MAGIC = 20260914
_SYMBOL = "US100.n"
_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol=_SYMBOL
)


def _config() -> ExecutionConfig:
    return ExecutionConfig(
        version=1, magic_number=_MAGIC, comment_prefix="VO:", deviation_points=20
    )


def _position(
    ticket: int = 900001,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    magic: int = _MAGIC,
) -> Position:
    return Position(
        ticket=ticket,
        instrument_id=_INSTRUMENT,
        broker_symbol=_SYMBOL,
        side=PositionSide.LONG,
        volume=0.01,
        price_open=20_000.0,
        price_current=20_000.0,
        stop_loss=stop_loss,
        take_profit=take_profit,
        profit=0.0,
        swap=0.0,
        magic=magic,
        comment="VO:preflight",
        opened_at_broker_epoch_s=1_789_000_000,
    )


def _ok(**overrides: object) -> OrderResult:
    base: dict[str, object] = dict(
        generated_at_utc=_NOW,
        approved=True,
        retcode=10009,
        failure_reason=None,
        broker_comment="Done",
        deal_ticket=1,
        order_ticket=1,
        filled_volume=0.01,
        filled_price=20_000.0,
    )
    base.update(overrides)
    return OrderResult(**base)  # type: ignore[arg-type]


def _rejected(reason: OrderFailureReason = OrderFailureReason.BAD_STOPS) -> OrderResult:
    return _ok(approved=False, retcode=10016, failure_reason=reason, broker_comment="Invalid stops")


class FakeTerminal:
    """Models a broker that actually applies what it is told, so the
    verification steps have something real to check against."""

    def __init__(self, *, fail_on: OrderAction | None = None, apply_modifies: bool = True):
        self.positions: list[Position] = []
        self.sent: list[OrderRequest] = []
        self._fail_on = fail_on
        self._apply = apply_modifies

    def send_order(self, request: OrderRequest) -> OrderResult:
        self.sent.append(request)
        if self._fail_on is not None and request.action is self._fail_on:
            return _rejected()

        if request.action is OrderAction.OPEN:
            self.positions.append(_position())
        elif request.action is OrderAction.MODIFY and self._apply:
            self.positions = [
                _position(
                    ticket=p.ticket,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                )
                if p.ticket == request.position_ticket
                else p
                for p in self.positions
            ]
        elif request.action is OrderAction.CLOSE:
            self.positions = [
                p for p in self.positions if p.ticket != request.position_ticket
            ]
        return _ok()

    def now(self) -> datetime:
        return _NOW


def _sequence(terminal: FakeTerminal) -> PreflightSequence:
    return PreflightSequence(
        client=terminal,
        config=_config(),
        positions_now=lambda: list(terminal.positions),
        now=terminal.now,
    )


def _run(sequence: PreflightSequence):
    return sequence.run(
        broker_symbol=_SYMBOL,
        volume=0.01,
        stop_distance=50.0,
        target_distance=100.0,
        trail_improvement=20.0,
    )


def test_a_healthy_terminal_passes_and_leaves_nothing_open() -> None:
    terminal = FakeTerminal()

    report = _run(_sequence(terminal))

    assert report.passed is True
    assert report.cleanup_verified is True
    assert terminal.positions == []
    assert "PASSED" in report.summary()


def test_it_exercises_every_primitive_in_order() -> None:
    terminal = FakeTerminal()

    report = _run(_sequence(terminal))
    steps = [result.step for result in report.steps]

    assert steps == [
        PreflightStep.OPEN_POSITION,
        PreflightStep.VERIFY_OPEN,
        PreflightStep.SET_PROTECTIVE_LEVELS,
        PreflightStep.VERIFY_LEVELS,
        PreflightStep.TRAIL_STOP,
        PreflightStep.VERIFY_TRAIL,
        PreflightStep.CLOSE_POSITION,
        PreflightStep.VERIFY_CLOSED,
    ]


def test_the_trail_actually_tightens_the_stop() -> None:
    terminal = FakeTerminal()

    _run(_sequence(terminal))
    modifies = [r for r in terminal.sent if r.action is OrderAction.MODIFY]

    assert len(modifies) == 2
    assert modifies[1].stop_loss is not None and modifies[0].stop_loss is not None
    assert modifies[1].stop_loss > modifies[0].stop_loss


def test_the_trail_preserves_the_take_profit() -> None:
    """The classic trailing bug: move the stop, silently delete the
    target. If this ever regresses, preflight catches it before the
    strategy does."""
    terminal = FakeTerminal()

    _run(_sequence(terminal))
    modifies = [r for r in terminal.sent if r.action is OrderAction.MODIFY]

    assert modifies[1].take_profit == modifies[0].take_profit


def test_a_rejected_open_fails_immediately_and_opens_nothing() -> None:
    terminal = FakeTerminal(fail_on=OrderAction.OPEN)

    report = _run(_sequence(terminal))

    assert report.passed is False
    assert report.first_failure is not None
    assert report.first_failure.step is PreflightStep.OPEN_POSITION
    assert terminal.positions == []


def test_a_rejected_stop_placement_still_closes_the_position() -> None:
    """The case that matters: the self-test failed, but it must not leave
    an unmanaged live position behind."""
    terminal = FakeTerminal(fail_on=OrderAction.MODIFY)

    report = _run(_sequence(terminal))

    assert report.passed is False
    assert report.first_failure is not None
    assert report.first_failure.step is PreflightStep.SET_PROTECTIVE_LEVELS
    assert report.cleanup_verified is True
    assert terminal.positions == []


def test_a_terminal_that_accepts_a_modify_without_applying_it_is_caught() -> None:
    """A DONE retcode and a position actually carrying the stop are
    different claims. Verifying against the broker's own view is the
    whole point of the exercise."""
    terminal = FakeTerminal(apply_modifies=False)

    report = _run(_sequence(terminal))

    assert report.passed is False
    assert report.first_failure is not None
    assert report.first_failure.step is PreflightStep.VERIFY_LEVELS


def test_a_position_that_cannot_be_closed_is_reported_loudly() -> None:
    class StubbornTerminal(FakeTerminal):
        def send_order(self, request: OrderRequest) -> OrderResult:
            if request.action is OrderAction.CLOSE:
                self.sent.append(request)
                return _rejected(OrderFailureReason.MARKET_CLOSED)
            return super().send_order(request)

    terminal = StubbornTerminal()
    report = _run(_sequence(terminal))

    assert report.passed is False
    assert report.cleanup_verified is False
    assert "STILL OPEN" in report.summary() or "NOT confirmed closed" in report.summary()


def test_another_eas_position_is_not_mistaken_for_the_preflights_own() -> None:
    terminal = FakeTerminal()
    terminal.positions.append(_position(ticket=555, magic=99999))

    report = _run(_sequence(terminal))

    assert report.passed is True
    # The foreign position is untouched.
    assert any(p.ticket == 555 for p in terminal.positions)


def test_orders_are_tagged_so_they_are_identifiable_afterwards() -> None:
    terminal = FakeTerminal()

    _run(_sequence(terminal))

    assert terminal.sent
    for request in terminal.sent:
        assert request.magic == _MAGIC
        assert request.comment.startswith("VO:")


def test_an_unfinished_report_never_reads_as_passed() -> None:
    from vo.execution.preflight import PreflightReport

    assert PreflightReport(started_at_utc=_NOW).passed is False
