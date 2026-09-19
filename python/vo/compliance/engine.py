"""
ComplianceEngine -- the Account Compliance Engine's centerpiece (see
vo.interfaces.compliance's own module docstring for the full pipeline
position and the G15 gate). Tracks two account-survival limits against
the SAME AccountState.equity the risk manager already reads, and stages
a ComplianceVerdict every time it is asked -- SAFE/WARNING/CRITICAL/
BREACHED_DAILY/BREACHED_TOTAL -- with approve_trade() the sole place a
ComplianceApproval may be constructed.

RESEARCH BASIS (2026-09-19): the two limits and the safety-buffer idea
are drawn directly from the open-source PropFirmGuard MQL5 reference
(mql5.com/en/code/76767, fetched and read in full, not merely quoted
secondhand) -- InpDailyLossPct/InpTotalDdPct/InpBufferPct, current
equity (not balance) against a day-start reference and a since-activation
peak. The SAFE/WARNING/CRITICAL staging is drawn from a second,
independently verified reference, the MQL5 "Building a Prop-Firm
Compliance Monitor" article series (mql5.com/en/articles/24396) --
ENUM_COMPLIANCE_STATUS's own SAFE/WARNING/CRITICAL/BREACHED shape and its
70%/90% warning/critical thresholds.

NOTE ON TERMINOLOGY (corrected 2026-09-19): this engine's total-
drawdown reference IS a trailing drawdown in the sense most prop firms
use the term -- `_peak_equity` is a running maximum that rises with the
account, so the effective limit trails upward as equity grows, exactly
matching PropFirmGuard's own behavior. What v1 does NOT implement is a
LOCKED/frozen trailing stop some firms use once profit crosses a
threshold (e.g. the trailing reference stops moving and freezes at
breakeven once equity first reaches initial-balance-plus-X%) -- that is
a real, firm-specific variant, not yet needed until a specific firm's
rules are confirmed. Separately: this engine trails off ACCOUNT EQUITY
(intraday floating P&L included), matching PropFirmGuard's own choice --
some firms instead trail off BALANCE only (closed trades), which is a
looser standard; confirm which convention the real account uses before
trusting this on it.

WHAT THIS v1 DELIBERATELY DOES NOT DO, flagged rather than guessed at,
matching this project's own "ship the mechanical piece first" discipline
(risk.yaml/swings.yaml/regime.yaml's own headers all say the same thing
about their own first versions):

  - The locked/frozen trailing-stop variant and the balance-vs-equity
    trailing distinction described above.
  - Session restrictions (e.g. no trading outside RTH) -- would duplicate
    the already-real vo.time Time Engine rather than reinventing it; a
    natural v2 addition once there is a live path to wire it into.
    (News blackouts ARE now implemented -- see vo.compliance.news_gate,
    added 2026-09-19 at the user's explicit request, and wired into
    on_snapshot below via the optional `news_gate_config`/`upcoming_events`
    parameters.)
  - Consistency rules / minimum trading days / profit targets -- these
    need a persisted trade-history view this engine does not have
    (ComplianceEngine only ever sees the current AccountState snapshot);
    a v2 concern, not this file's job.
  - Persistence across a terminal restart -- PropFirmGuard's own design
    deliberately re-anchors day-start equity to whatever equity is
    current at restart (its own words: "prevents cumulative loss beyond
    a fresh trading day"), which this engine matches structurally (the
    day boundary is recomputed from `now_ny` every call, never loaded
    from a file) -- but the PEAK-equity reference is in-memory only here
    and does NOT survive a restart. A real prop account needs the peak
    persisted (matching the second reference's own SQLite-backed
    settings table, keyed by account login/server) before this can be
    trusted unattended across restarts -- flagged, not built, v2.

THE DAY BOUNDARY reuses vo.time.calendars.trading_day_of (the CME
trade-date convention this project already adopted for every other
day-keyed concept) rather than PropFirmGuard's own crude "server-time
reset hour" input -- a genuine improvement grounded in VO's own more
rigorous Time Engine, not a reference-implementation detail copied
uncritically.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

from vo.compliance.compliance_config import ComplianceConfig
from vo.compliance.news_gate import NewsGateConfig, evaluate_news_blackout
from vo.interfaces.compliance import (
    ComplianceApproval,
    ComplianceApprovalError,
    ComplianceStatus,
    ComplianceVerdict,
)
from vo.interfaces.economic_events import EconomicEvent
from vo.interfaces.signals import TradeSignal
from vo.market.account import AccountState
from vo.time.calendars import trading_day_of


class ComplianceEngine:
    """One account's whole compliance state. Call on_snapshot() once per
    tick/bar with the current AccountState (mirroring
    vo.telemetry.trade_pipeline's own per-snapshot evaluation shape,
    though this engine is not wired into it yet -- see this module's own
    docstring)."""

    def __init__(
        self,
        *,
        config: ComplianceConfig,
        trading_day_opens: time,
        methodology_version: int = 1,
        news_gate_config: NewsGateConfig | None = None,
    ) -> None:
        self._config = config
        self._trading_day_opens = trading_day_opens
        self._methodology_version = methodology_version
        self._news_gate_config = news_gate_config
        """None (the default) means the news gate is disabled -- on_snapshot
        never evaluates a blackout regardless of what `upcoming_events` a
        caller passes. Set to actually enable it (see
        vo.compliance.news_gate.load_news_gate_config)."""

        self._current_trading_day: date | None = None
        self._day_start_equity: float | None = None
        self._peak_equity: float | None = None
        # Once True, stays True regardless of later equity recovery --
        # matching PropFirmGuard's own "breaches [total drawdown] block
        # trading permanently" behavior. A daily breach, by contrast,
        # clears naturally the next trading day (a fresh _day_start_equity).
        self._total_breached = False

        self.verdicts: list[ComplianceVerdict] = []

    def current_status(self) -> ComplianceStatus:
        """The most recent verdict's status, or SAFE if on_snapshot() has
        never been called (mirrors StructureRangeEngine.current_state()'s
        own "nothing observed yet" convention)."""
        if not self.verdicts:
            return ComplianceStatus.SAFE
        return self.verdicts[-1].status

    def on_snapshot(
        self,
        *,
        object_id: str,
        generated_at_utc: datetime,
        now_ny: datetime,
        account: AccountState,
        upcoming_events: Sequence[EconomicEvent] = (),
    ) -> ComplianceVerdict:
        """Evaluate one account snapshot, update the day/peak references,
        and return the resulting ComplianceVerdict (always produced, same
        "every rejection carries a reason" discipline as RiskCheck).
        `generated_at_utc`/`now_ny` are both caller-supplied, never read
        from the wall clock here, matching evaluate_risk's own no-internal-
        clock-read convention (G3-adjacent testability, even though this
        engine is not itself a ReplayProbe). `upcoming_events` is likewise
        caller-supplied and ignored entirely unless `news_gate_config` was
        set at construction -- see vo.compliance.news_gate."""
        day = trading_day_of(now_ny, self._trading_day_opens)
        if self._current_trading_day is None or day != self._current_trading_day:
            self._current_trading_day = day
            self._day_start_equity = account.equity

        if self._peak_equity is None or account.equity > self._peak_equity:
            self._peak_equity = account.equity

        assert self._day_start_equity is not None  # set immediately above on first call
        assert self._peak_equity is not None  # set immediately above on first call

        daily_limit = self._config.effective_daily_loss_limit_fraction
        total_limit = self._config.effective_total_drawdown_limit_fraction

        daily_loss_fraction = max(
            0.0, (self._day_start_equity - account.equity) / self._day_start_equity
        )
        total_dd_fraction = max(0.0, (self._peak_equity - account.equity) / self._peak_equity)

        daily_used = daily_loss_fraction / daily_limit if daily_limit > 0 else 0.0
        total_used = total_dd_fraction / total_limit if total_limit > 0 else 0.0

        if total_dd_fraction >= total_limit:
            self._total_breached = True

        status: ComplianceStatus
        allowed: bool
        reason: str | None
        active_news_event: EconomicEvent | None

        if self._total_breached:
            status = ComplianceStatus.BREACHED_TOTAL
            allowed = False
            reason = (
                f"total drawdown {total_dd_fraction:.2%} has reached/exceeded the "
                f"buffer-adjusted limit {total_limit:.2%} (configured "
                f"{self._config.total_drawdown_limit_fraction:.2%} minus "
                f"{self._config.safety_buffer_fraction:.2%} buffer) -- permanently blocked"
            )
            active_news_event = None
        elif daily_loss_fraction >= daily_limit:
            status = ComplianceStatus.BREACHED_DAILY
            allowed = False
            reason = (
                f"daily loss {daily_loss_fraction:.2%} has reached/exceeded the "
                f"buffer-adjusted limit {daily_limit:.2%} (configured "
                f"{self._config.daily_loss_limit_fraction:.2%} minus "
                f"{self._config.safety_buffer_fraction:.2%} buffer) -- blocked until "
                f"the next trading day"
            )
            active_news_event = None
        else:
            news_verdict = (
                evaluate_news_blackout(upcoming_events, generated_at_utc, self._news_gate_config)
                if self._news_gate_config is not None
                else None
            )
            if news_verdict is not None and news_verdict.blocked:
                status = ComplianceStatus.NEWS_BLACKOUT
                allowed = False
                reason = news_verdict.reason
                active_news_event = news_verdict.active_event
            else:
                usage = max(daily_used, total_used)
                allowed = True
                reason = None
                active_news_event = None
                if usage >= self._config.critical_threshold_fraction:
                    status = ComplianceStatus.CRITICAL
                elif usage >= self._config.warning_threshold_fraction:
                    status = ComplianceStatus.WARNING
                else:
                    status = ComplianceStatus.SAFE

        verdict = ComplianceVerdict(
            object_id=object_id,
            generated_at_utc=generated_at_utc,
            status=status,
            allowed=allowed,
            reason=reason,
            day_start_equity=self._day_start_equity,
            peak_equity=self._peak_equity,
            current_equity=account.equity,
            daily_loss_used_fraction=daily_used,
            total_drawdown_used_fraction=total_used,
            active_news_event=active_news_event,
        )
        self.verdicts.append(verdict)
        return verdict


def approve_trade(
    verdict: ComplianceVerdict, trade_signal: TradeSignal, *, object_id: str
) -> ComplianceApproval:
    """The ONLY place in this codebase allowed to construct a
    ComplianceApproval -- tests/unit/test_architecture.py::
    test_compliance_is_the_sole_approval_producer enforces this
    mechanically (gate G15), the same AST-scan style G7/G14 already use.
    Raises ComplianceApprovalError rather than silently approving when
    the verdict does not allow it -- there is no code path that lets a
    blocked verdict reach ExecutionRouter.place() with a valid
    approval."""
    if not verdict.allowed:
        raise ComplianceApprovalError(
            f"cannot approve trade_signal={trade_signal.object_id}: "
            f"compliance verdict {verdict.object_id} is {verdict.status} ({verdict.reason})"
        )
    return ComplianceApproval(
        object_id=object_id,
        trade_signal_id=trade_signal.object_id,
        verdict_id=verdict.object_id,
        generated_at_utc=verdict.generated_at_utc,
    )
