"""
vo.interfaces.compliance -- the Account Compliance Engine's contract
types (a new, non-numbered aside, alongside Phase 14/16's own
Signal/AllocationSignal/RiskCheck/TradeSignal and ExecutionEvent
families). Confirmed with the user 2026-09-19: research into commercial
and open-source prop-firm "guardian" EAs (PropFirmGuard, the MQL5
Compliance Monitor article series) surfaced a real, missing piece of
this project's own architecture-audit pipeline diagram
(architecture/vo-architecture-audit.md F.2/F.3) -- a veto gate sitting
between vo.risk's TradeSignal and vo.execution, enforcing the ACCOUNT's
own survival rules (daily loss limit, total drawdown limit) rather than
per-trade risk (already vo.risk's job) or strategy correctness (vo.signals'
job).

WHERE THIS SITS, exactly:

    StrategyOutput -> Signal -> AllocationSignal -> RiskCheck -> TradeSignal
                                                                      |
                                                                      v
                                                          COMPLIANCE GATE (this)
                                                                      |
                                                                      v
                                                          ExecutionRouter.place()

Same shape as G14 (risk is the sole TradeSignal producer): a new gate,
G15, makes vo.compliance.engine the ONLY place a ComplianceApproval may
be constructed (tests/unit/test_architecture.py::
test_compliance_is_the_sole_approval_producer, the identical AST-scan
style G7/G14 already use), and ExecutionRouter.place() now REQUIRES one
alongside the TradeSignal it is sending -- so the gate is structurally
unavoidable, not merely a convention a caller could skip. This mirrors
this project's own stated worry about commercial "Guardian" EAs (their
own MQL5 documentation admits an EA cannot guarantee stopping another
EA's order before it reaches the broker -- it can only react afterward):
VO's compliance gate is IN the path, not racing something already in it.

DELIBERATELY NOT BUILT YET, same discipline as Phase 14/16's own
"nothing sends a live order automatically" posture: nothing in
vo.telemetry.trade_pipeline or scripts/run_vo_ea.py constructs a
ComplianceEngine or calls it automatically. Wiring it into an unattended
runtime path is Phase 18's own decision (the first live trade), exactly
like ExecutionRouter.place()/execute_reconciliation_actions() already
are. What changes today is structural: ExecutionRouter.place() cannot be
called at all without a ComplianceApproval in hand, so whenever Phase 18
does wire a live path, it cannot forget this gate -- the type system
enforces it, not a reminder in a docstring.

WHY THESE ARE PLAIN DATACLASSES, NOT CanonicalRecord: mirrors Phase 14's
own Signal/AllocationSignal/RiskCheck/TradeSignal exactly (see
vo.interfaces.signals' own module docstring) rather than Phase 11/13's
heavier CanonicalRecord/AppendOnlyLog style -- this is a per-tick
evaluation with a running verdict, not an append-only structural-event
log like SwingPoint/RegimeState.

Layer 0 (vo.interfaces) -- depends on nothing else in vo, imported
freely by vo.compliance (9) and vo.execution (11).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from vo.interfaces.economic_events import EconomicEvent


class ComplianceApprovalError(ValueError):
    """Raised when a ComplianceVerdict or ComplianceApproval is
    constructed with an invalid shape, or when approve_trade() is asked
    to approve a TradeSignal against a verdict that does not allow it."""


class ComplianceStatus(Enum):
    """Where the account sits relative to its configured daily/total
    drawdown limits, staged rather than a bare pass/fail -- mirroring the
    SAFE/WARNING/CRITICAL/BREACHED staging the MQL5 Compliance Monitor
    reference uses (70%/90%-of-limit thresholds), which PropFirmGuard's
    simpler bare-boolean Allowed()/DailyBreached()/TotalBreached() does
    not have. WARNING/CRITICAL are informational only -- `allowed` is
    True for both; only a BREACHED_* status blocks a trade."""

    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    BREACHED_DAILY = "BREACHED_DAILY"
    BREACHED_TOTAL = "BREACHED_TOTAL"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"
    """Added v1.1 (2026-09-19, additive -- same convention as
    Decision.SIGNAL_PROPOSED): a qualifying economic event is inside its
    configured before/during/after blackout window (vo.compliance.
    news_gate). Blocking, but distinct from BREACHED_DAILY/BREACHED_TOTAL
    -- it is temporary by construction (the window always ends) and says
    nothing about the account's own loss/drawdown state."""

    def __str__(self) -> str:
        return self.value


# A BREACHED_*/NEWS_BLACKOUT status blocks new trades; SAFE/WARNING/
# CRITICAL do not.
_BLOCKING_STATUSES = frozenset(
    {
        ComplianceStatus.BREACHED_DAILY,
        ComplianceStatus.BREACHED_TOTAL,
        ComplianceStatus.NEWS_BLACKOUT,
    }
)


@dataclass(frozen=True, slots=True)
class ComplianceVerdict:
    """One account-level compliance evaluation, always produced --
    matching Phase 14's "every rejection carries a reason" discipline one
    stage further down the pipeline. `allowed=False` on any BREACHED_*
    status; `reason` is required exactly then, matching RiskCheck's own
    approved/reason relationship. The four usage/reference fields exist
    so a caller can render "62% of today's buffer-adjusted daily limit
    used" without re-deriving it from raw equity numbers -- matching
    ReconciliationReport's own surfaced-derived-counts convention."""

    object_id: str
    generated_at_utc: datetime
    status: ComplianceStatus
    allowed: bool
    reason: str | None
    day_start_equity: float
    peak_equity: float
    current_equity: float
    daily_loss_used_fraction: float
    """Today's loss so far, as a fraction of the buffer-adjusted daily
    limit (1.0 = at the limit, >1.0 = past it -- BREACHED_DAILY)."""
    total_drawdown_used_fraction: float
    """Drawdown from peak equity, as a fraction of the buffer-adjusted
    total-drawdown limit (same 1.0 convention as above)."""
    active_news_event: EconomicEvent | None = None
    """Set exactly when status is NEWS_BLACKOUT (vo.compliance.news_gate's
    NewsBlackoutVerdict.active_event, carried through) -- None otherwise.
    Added after the other fields, with a default, so this stays additive
    for anything already constructing a ComplianceVerdict without it."""

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ComplianceApprovalError("ComplianceVerdict.object_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise ComplianceApprovalError("ComplianceVerdict.generated_at_utc must be tz-aware")
        if self.allowed and self.status in _BLOCKING_STATUSES:
            raise ComplianceApprovalError(
                f"a ComplianceVerdict cannot be allowed=True with status={self.status}"
            )
        if not self.allowed and self.status not in _BLOCKING_STATUSES:
            raise ComplianceApprovalError(
                f"a ComplianceVerdict cannot be allowed=False with status={self.status}"
            )
        if not self.allowed and not (self.reason and self.reason.strip()):
            raise ComplianceApprovalError("a blocked ComplianceVerdict must carry a reason")
        if self.allowed and self.reason is not None:
            raise ComplianceApprovalError("an allowed ComplianceVerdict carries no reason")
        if self.status is ComplianceStatus.NEWS_BLACKOUT and self.active_news_event is None:
            raise ComplianceApprovalError(
                "a NEWS_BLACKOUT ComplianceVerdict must carry active_news_event"
            )
        if self.status is not ComplianceStatus.NEWS_BLACKOUT and self.active_news_event is not None:
            raise ComplianceApprovalError(
                "active_news_event is only set when status is NEWS_BLACKOUT"
            )
        if self.day_start_equity <= 0:
            raise ComplianceApprovalError("ComplianceVerdict.day_start_equity must be positive")
        if self.peak_equity <= 0:
            raise ComplianceApprovalError("ComplianceVerdict.peak_equity must be positive")


@dataclass(frozen=True, slots=True)
class ComplianceApproval:
    """The ONLY object ExecutionRouter.place() will accept alongside a
    TradeSignal (see this module's own docstring for the full gate). Not
    constructible anywhere except vo.compliance.engine.approve_trade --
    tests/unit/test_architecture.py::
    test_compliance_is_the_sole_approval_producer enforces this
    mechanically, the same AST-scan style G7/G14 already use."""

    object_id: str
    trade_signal_id: str
    verdict_id: str
    generated_at_utc: datetime

    def __post_init__(self) -> None:
        if not self.object_id.strip():
            raise ComplianceApprovalError("ComplianceApproval.object_id cannot be blank")
        if not self.trade_signal_id.strip():
            raise ComplianceApprovalError("ComplianceApproval.trade_signal_id cannot be blank")
        if not self.verdict_id.strip():
            raise ComplianceApprovalError("ComplianceApproval.verdict_id cannot be blank")
        if self.generated_at_utc.tzinfo is None:
            raise ComplianceApprovalError("ComplianceApproval.generated_at_utc must be tz-aware")
