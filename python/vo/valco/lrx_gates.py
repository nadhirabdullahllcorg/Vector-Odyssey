"""
Whether LRX may arm a setup at all -- evaluated before any market
structure is considered.

TWO KINDS OF QUESTION, DELIBERATELY SEPARATED. "Is there a setup?" is a
question about the market. "Am I allowed to take one right now?" is a
question about the account, the clock, and whether the plumbing has been
proven. Mixing them is how a strategy ends up with session logic
smeared through its pattern detection, and how "why didn't it trade
today?" becomes unanswerable. Everything here is the second kind.

EVERY BLOCKING REASON IS REPORTED, NOT JUST THE FIRST. A gate that
short-circuits tells you the market was closed; it does not tell you
that the daily cap was also reached and compliance was also blocking.
When the question at the end of a flat day is "why did nothing happen",
one reason is a worse answer than three. The cost is evaluating a few
cheap predicates that a short-circuit would have skipped, which is
nothing.

PREFLIGHT IS PASSED IN, NOT IMPORTED. vo.valco is layer 6 and
vo.execution is layer 12, so this module could not import the preflight
report even if it wanted to -- the dependency can only run the other
way, and the layering test enforces it. The caller (which sits above
both) runs the preflight and hands down a plain bool. That is the right
shape regardless of layering: a strategy should not be able to run its
own commissioning test and mark its own homework.

NOTHING HERE READS A CLOCK. `now_ny` is supplied, like every other
timestamp in this codebase, so a backtest and a live session evaluate
the identical function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.valco.lrx_config import LrxConfig


class ArmingBlock(Enum):
    """One reason LRX may not arm a new setup."""

    PREFLIGHT_NOT_PASSED = "PREFLIGHT_NOT_PASSED"
    """The execution plumbing has not been proven against the broker this
    session. Nothing may be armed until it has -- see
    vo.execution.preflight."""

    OUTSIDE_SETUP_WINDOW = "OUTSIDE_SETUP_WINDOW"
    """Outside 09:00-13:45 NY. A setup already armed may still trigger;
    this blocks arming a NEW one."""

    OUTSIDE_TRADE_WINDOW = "OUTSIDE_TRADE_WINDOW"
    """Outside 07:00-14:00 NY -- nothing may be held, let alone armed."""

    DAILY_TRADE_CAP_REACHED = "DAILY_TRADE_CAP_REACHED"
    CONSECUTIVE_LOSS_CAP_REACHED = "CONSECUTIVE_LOSS_CAP_REACHED"

    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    """One at a time. The risk manager enforces this too; arming a setup
    that risk would certainly reject just wastes a setup and muddies the
    log."""

    COMPLIANCE_BLOCKED = "COMPLIANCE_BLOCKED"
    """Daily halt, drawdown breach, or a news blackout -- whatever the
    compliance engine said, reported here as one fact."""

    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ArmingDecision:
    """Whether a new setup may be armed, and every reason it may not."""

    allowed: bool
    blocks: tuple[ArmingBlock, ...]

    def __post_init__(self) -> None:
        if self.allowed and self.blocks:
            raise ValueError("an allowed ArmingDecision cannot carry blocking reasons")
        if not self.allowed and not self.blocks:
            raise ValueError(
                "a blocked ArmingDecision must say why -- every rejection carries a reason"
            )

    @property
    def reason(self) -> str | None:
        if self.allowed:
            return None
        return ", ".join(str(block) for block in self.blocks)


def evaluate_arming(
    config: LrxConfig,
    *,
    now_ny: datetime,
    preflight_passed: bool,
    compliance_allows: bool,
    open_position_count: int,
    trades_today: int,
    consecutive_losses: int,
    spread_points: int | None = None,
) -> ArmingDecision:
    """
    Every account/clock/plumbing condition, evaluated together.

    `spread_points` is optional because a backtest may not model spread
    per bar; None skips the spread check rather than inventing a value.
    The check is also skipped when the configured limit is 0, which is
    how lrx.yaml expresses "no spread filter" without a second flag.
    """
    blocks: list[ArmingBlock] = []
    moment = now_ny.time()

    if not preflight_passed:
        blocks.append(ArmingBlock.PREFLIGHT_NOT_PASSED)

    if config.flag("use_session_filter"):
        if not config.session.trade_window.contains(moment):
            blocks.append(ArmingBlock.OUTSIDE_TRADE_WINDOW)
        elif not config.session.setup_selection.contains(moment):
            # Only reported when the trade window IS open: outside it,
            # OUTSIDE_TRADE_WINDOW already says everything, and listing
            # both would be noise rather than detail.
            blocks.append(ArmingBlock.OUTSIDE_SETUP_WINDOW)

    if not compliance_allows:
        blocks.append(ArmingBlock.COMPLIANCE_BLOCKED)

    if open_position_count >= config.limits.max_concurrent_positions:
        blocks.append(ArmingBlock.POSITION_ALREADY_OPEN)

    if trades_today >= config.limits.max_trades_per_day:
        blocks.append(ArmingBlock.DAILY_TRADE_CAP_REACHED)

    if consecutive_losses >= config.limits.max_consecutive_losses:
        blocks.append(ArmingBlock.CONSECUTIVE_LOSS_CAP_REACHED)

    limit = config.limits.max_spread_points
    if limit > 0 and spread_points is not None and spread_points > limit:
        blocks.append(ArmingBlock.SPREAD_TOO_WIDE)

    return ArmingDecision(allowed=not blocks, blocks=tuple(blocks))


def must_flatten(config: LrxConfig, *, now_ny: datetime) -> bool:
    """Whether an OPEN position must be closed now because the trade
    window has ended.

    Separate from arming on purpose: a position already running is
    governed by the trade window, not the setup-selection window, so a
    trade entered at 13:44 may keep running until 14:00. Returns False
    when flat_at_window_close is off, in which case ending the window
    only stops new entries.
    """
    if not config.session.flat_at_window_close:
        return False
    if not config.flag("use_session_filter"):
        return False
    return not config.session.trade_window.contains(now_ny.time())
