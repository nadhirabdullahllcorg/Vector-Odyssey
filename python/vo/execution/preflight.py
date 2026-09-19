"""
Execution preflight -- prove the plumbing moves a real order before any
strategy is allowed to arm a setup.

WHY THIS EXISTS. Every execution primitive in this codebase has been
tested against fakes and none of them has ever reached a broker. Open,
modify, trail, close, the compliance gate, the retcode mapping: all
verified in unit tests, all unproven against the only thing that
actually matters. First contact with a live order is first contact, and
no amount of offline testing changes that.

So the first live session does not begin by looking for a setup. It
begins by deliberately exercising every primitive, at minimum size,
against the real terminal, and refusing to let setup selection start
until each one has demonstrably worked. A broker that rejects a stop
modification, fills at a surprising price, or returns an unmapped
retcode is something to discover on one micro-lot placed on purpose --
not on a position taken because the strategy finally saw something.

THIS PLACES REAL ORDERS AND COSTS REAL MONEY. That is the point, and it
is also why it never runs by accident: the caller must construct the
sequence explicitly and pass a volume. Cost is one spread on the
smallest lot the broker allows, which is the cheapest insurance
available against a silently broken execution path.

CLEANUP IS NOT OPTIONAL. A preflight that fails halfway having left a
position open would be far worse than never running: an unmanaged
position, opened by a self-test, sitting in a live account. So the
runner always attempts to close what it opened, and a failure to clean
up is reported as its own loud result rather than folded into the step
that caused it. `cleanup_verified` is the field to read before walking
away from a failed run.

WHAT IS NOT COMMISSIONED, and why that is honest rather than a gap:
placing a PENDING order. VO has no pending-placement primitive -- only
build_cancel_request, which exists so compliance closeout can cancel
orders it did not place (a manually-placed order, say). There is
nothing of VO's own to commission there, so the sequence does not
pretend to test it. If pending entries are ever added, this sequence
gains two steps.

GATE. PreflightReport.passed is what a strategy checks before arming.
The check is the strategy's to make; this module does not reach forward
into vo.valco (it could not -- vo.execution is layer 12 and vo.valco is
6, so the dependency can only run the other way).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from vo.core.mt5 import (
    OrderAction,
    OrderRequest,
    OrderResult,
    TerminalExecutionApi,
    build_close_request,
    build_modify_request,
)
from vo.execution.execution_config import ExecutionConfig
from vo.interfaces.decisions import Direction
from vo.market.account import Position, PositionSide


class PreflightStep(Enum):
    """Each primitive the live path depends on, in the order a real trade
    would exercise them."""

    OPEN_POSITION = "OPEN_POSITION"
    VERIFY_OPEN = "VERIFY_OPEN"
    SET_PROTECTIVE_LEVELS = "SET_PROTECTIVE_LEVELS"
    VERIFY_LEVELS = "VERIFY_LEVELS"
    TRAIL_STOP = "TRAIL_STOP"
    VERIFY_TRAIL = "VERIFY_TRAIL"
    CLOSE_POSITION = "CLOSE_POSITION"
    VERIFY_CLOSED = "VERIFY_CLOSED"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step's outcome. `detail` always carries something readable --
    a passing step says what it observed, not just that it passed, so a
    log of a successful preflight is still evidence rather than a row of
    ticks."""

    step: PreflightStep
    ok: bool
    detail: str
    retcode: int | None = None


@dataclass
class PreflightReport:
    """The whole run. `passed` is what gates setup selection."""

    started_at_utc: datetime
    steps: list[StepResult] = field(default_factory=list)
    finished_at_utc: datetime | None = None
    cleanup_verified: bool = True
    """False when the runner opened a position it could not confirm
    closed. Read this before walking away from a failed preflight: it is
    the difference between a failed self-test and an orphaned live
    position."""

    @property
    def passed(self) -> bool:
        return (
            self.finished_at_utc is not None
            and self.cleanup_verified
            and bool(self.steps)
            and all(result.ok for result in self.steps)
        )

    @property
    def first_failure(self) -> StepResult | None:
        for result in self.steps:
            if not result.ok:
                return result
        return None

    def summary(self) -> str:
        if self.passed:
            return f"preflight PASSED ({len(self.steps)} steps)"
        failure = self.first_failure
        if failure is not None:
            head = f"preflight FAILED at {failure.step}: {failure.detail}"
        else:
            head = "preflight INCOMPLETE"
        if not self.cleanup_verified:
            head += "  -- WARNING: a position opened by preflight was NOT confirmed closed"
        return head


@dataclass
class PreflightSequence:
    """
    Runs the commissioning sequence against a live terminal.

    `positions_now` is supplied by the caller rather than fetched here,
    matching this codebase's "given, not fetched" discipline -- it
    returns the account's current positions each time it is called, so
    the runner can verify what the broker actually did rather than
    trusting its own send results. That distinction is the whole value
    of the exercise: a retcode saying DONE and a position actually
    carrying the stop you asked for are different claims.
    """

    client: TerminalExecutionApi
    config: ExecutionConfig
    positions_now: Callable[[], Sequence[Position]]
    now: Callable[[], datetime]

    def _comment(self, suffix: str) -> str:
        return f"{self.config.comment_prefix}{suffix}"

    def _send(self, request: OrderRequest) -> OrderResult:
        return self.client.send_order(request)

    def _own_positions(self) -> list[Position]:
        return [
            position
            for position in self.positions_now()
            if position.magic == self.config.magic_number
        ]

    def run(
        self,
        *,
        broker_symbol: str,
        volume: float,
        stop_distance: float,
        target_distance: float,
        trail_improvement: float,
    ) -> PreflightReport:
        """Open, protect, trail, close -- verifying against the broker's
        own view of the account at every stage.

        Distances are in price units and should be comfortably outside
        the broker's stop level; a rejection for "stops too close" is a
        real thing to discover here, but it should be distinguishable
        from a broken primitive, which is why the detail text carries the
        retcode.
        """
        report = PreflightReport(started_at_utc=self.now())
        opened: Position | None = None

        try:
            # 1. Open, at the smallest size that is allowed to exist.
            open_request = OrderRequest(
                action=OrderAction.OPEN,
                broker_symbol=broker_symbol,
                direction=Direction.LONG,
                volume=volume,
                price=None,
                stop_loss=None,
                take_profit=None,
                deviation_points=self.config.deviation_points,
                magic=self.config.magic_number,
                comment=self._comment("preflight"),
                position_ticket=None,
            )
            result = self._send(open_request)
            report.steps.append(
                StepResult(
                    step=PreflightStep.OPEN_POSITION,
                    ok=result.approved,
                    detail=(
                        f"filled {result.filled_volume} @ {result.filled_price}"
                        if result.approved
                        else f"rejected: {result.failure_reason} ({result.broker_comment})"
                    ),
                    retcode=result.retcode,
                )
            )
            if not result.approved:
                return self._finish(report, opened)

            # 2. Verify the broker agrees a position exists.
            opened = self._find_opened()
            report.steps.append(
                StepResult(
                    step=PreflightStep.VERIFY_OPEN,
                    ok=opened is not None,
                    detail=(
                        f"ticket {opened.ticket} @ {opened.price_open}"
                        if opened
                        else "no position with this EA's magic number appeared"
                    ),
                )
            )
            if opened is None:
                return self._finish(report, opened)

            # 3. Attach a stop and a target.
            stop = opened.price_open - stop_distance
            target = opened.price_open + target_distance
            result = self._send(
                build_modify_request(
                    opened,
                    stop_loss=stop,
                    take_profit=target,
                    magic=self.config.magic_number,
                    comment=self._comment("pf-sltp"),
                )
            )
            report.steps.append(
                StepResult(
                    step=PreflightStep.SET_PROTECTIVE_LEVELS,
                    ok=result.approved,
                    detail=(
                        f"requested sl={stop:.2f} tp={target:.2f}"
                        if result.approved
                        else f"rejected: {result.failure_reason} ({result.broker_comment})"
                    ),
                    retcode=result.retcode,
                )
            )
            if not result.approved:
                return self._finish(report, opened)

            # 4. Verify they are actually ON the position. A DONE retcode
            #    and a position carrying the level are different claims.
            refreshed = self._refresh(opened.ticket)
            levels_ok = (
                refreshed is not None
                and refreshed.stop_loss is not None
                and refreshed.take_profit is not None
            )
            report.steps.append(
                StepResult(
                    step=PreflightStep.VERIFY_LEVELS,
                    ok=levels_ok,
                    detail=(
                        f"broker reports sl={refreshed.stop_loss} tp={refreshed.take_profit}"
                        if refreshed is not None
                        else "position vanished before levels could be verified"
                    ),
                )
            )
            if not levels_ok or refreshed is None:
                return self._finish(report, opened)

            # 5. Trail the stop -- the primitive that did not exist until
            #    2026-09-19 and has never run against a broker.
            trailed_stop = stop + trail_improvement
            result = self._send(
                build_modify_request(
                    refreshed,
                    stop_loss=trailed_stop,
                    take_profit=refreshed.take_profit,
                    magic=self.config.magic_number,
                    comment=self._comment("pf-trail"),
                )
            )
            report.steps.append(
                StepResult(
                    step=PreflightStep.TRAIL_STOP,
                    ok=result.approved,
                    detail=(
                        f"tightened sl {stop:.2f} -> {trailed_stop:.2f}"
                        if result.approved
                        else f"rejected: {result.failure_reason} ({result.broker_comment})"
                    ),
                    retcode=result.retcode,
                )
            )
            if not result.approved:
                return self._finish(report, opened)

            after_trail = self._refresh(opened.ticket)
            moved = (
                after_trail is not None
                and after_trail.stop_loss is not None
                and after_trail.stop_loss > (refreshed.stop_loss or float("-inf"))
            )
            report.steps.append(
                StepResult(
                    step=PreflightStep.VERIFY_TRAIL,
                    ok=moved,
                    detail=(
                        f"broker reports sl={after_trail.stop_loss}"
                        if after_trail is not None
                        else "position vanished before the trail could be verified"
                    ),
                )
            )

            return self._finish(report, opened)
        except Exception as exc:
            # Deliberately broad: whatever went wrong, the cleanup in
            # _finish still has to run. An unhandled exception here
            # would be the one path that leaves a live position behind.
            report.steps.append(
                StepResult(
                    step=PreflightStep.CLOSE_POSITION,
                    ok=False,
                    detail=f"preflight raised before completing: {exc!r}",
                )
            )
            return self._finish(report, opened)

    def _finish(self, report: PreflightReport, opened: Position | None) -> PreflightReport:
        """Always close what was opened, whatever went wrong on the way."""
        if opened is not None:
            current = self._refresh(opened.ticket)
            if current is not None:
                result = self._send(
                    build_close_request(
                        current,
                        magic=self.config.magic_number,
                        comment=self._comment("pf-close"),
                        deviation_points=self.config.deviation_points,
                    )
                )
                report.steps.append(
                    StepResult(
                        step=PreflightStep.CLOSE_POSITION,
                        ok=result.approved,
                        detail=(
                            f"closed {result.filled_volume} @ {result.filled_price}"
                            if result.approved
                            else f"rejected: {result.failure_reason} ({result.broker_comment})"
                        ),
                        retcode=result.retcode,
                    )
                )

            still_open = self._refresh(opened.ticket) is not None
            report.cleanup_verified = not still_open
            report.steps.append(
                StepResult(
                    step=PreflightStep.VERIFY_CLOSED,
                    ok=not still_open,
                    detail=(
                        "no preflight position remains"
                        if not still_open
                        else f"position {opened.ticket} IS STILL OPEN -- close it manually"
                    ),
                )
            )

        report.finished_at_utc = self.now()
        return report

    def _find_opened(self) -> Position | None:
        own = self._own_positions()
        return own[-1] if own else None

    def _refresh(self, ticket: int) -> Position | None:
        for position in self.positions_now():
            if position.ticket == ticket:
                return position
        return None


def preflight_long_position(position: Position) -> bool:
    """A preflight only ever opens LONG, so its own cleanup logic never
    has to branch on side. Exposed so a caller can assert it."""
    return position.side is PositionSide.LONG
