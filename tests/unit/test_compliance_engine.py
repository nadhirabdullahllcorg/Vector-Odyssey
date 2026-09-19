"""Unit tests for vo.compliance (the Account Compliance Engine) and its
vo.interfaces.compliance contract types. Config values mirror
config/settings/compliance.yaml's own PropFirmGuard-derived defaults
(5% daily / 10% total / 0.5% buffer / 70%-90% warning-critical staging)
unless a test needs otherwise."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from vo.compliance.compliance_config import (
    ComplianceConfig,
    ComplianceConfigError,
    DailyLossMode,
    load_compliance_config,
)
from vo.compliance.engine import ComplianceEngine, approve_trade
from vo.compliance.news_gate import NewsGateConfig
from vo.interfaces.compliance import (
    ComplianceApproval,
    ComplianceApprovalError,
    ComplianceStatus,
    ComplianceVerdict,
)
from vo.interfaces.decisions import Direction
from vo.interfaces.economic_events import EconomicEvent, EventImportance
from vo.interfaces.signals import TradeSignal
from vo.market.account import AccountState

_TRADING_DAY_OPENS = time(18, 0)
_GENERATED_AT = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def _config(**overrides: float) -> ComplianceConfig:
    base = dict(
        version=1,
        daily_loss_limit_fraction=0.05,
        total_drawdown_limit_fraction=0.10,
        safety_buffer_fraction=0.005,
        warning_threshold_fraction=0.70,
        critical_threshold_fraction=0.90,
    )
    base.update(overrides)
    return ComplianceConfig(**base)  # type: ignore[arg-type]


def _engine(**overrides: float) -> ComplianceEngine:
    return ComplianceEngine(config=_config(**overrides), trading_day_opens=_TRADING_DAY_OPENS)


def _news_gate_config(**overrides: object) -> NewsGateConfig:
    base: dict[str, object] = dict(
        version=1,
        buffer_before_minutes=5.0,
        buffer_after_minutes=5.0,
        min_importance=EventImportance.HIGH,
        currencies=("USD",),
    )
    base.update(overrides)
    return NewsGateConfig(**base)  # type: ignore[arg-type]


def _news_event(**overrides: object) -> EconomicEvent:
    base: dict[str, object] = dict(
        event_id="NFP-2026-09",
        name="Non-Farm Payrolls",
        currency="USD",
        scheduled_at_utc=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
        importance=EventImportance.HIGH,
    )
    base.update(overrides)
    return EconomicEvent(**base)  # type: ignore[arg-type]


def _account(equity: float) -> AccountState:
    return AccountState(
        login=12345,
        name="Test Account",
        server="TestServer",
        currency="USD",
        balance=equity,
        equity=equity,
        profit=0.0,
        margin=0.0,
        margin_free=100_000.0,
        margin_level=None,
        leverage=100,
        trade_allowed=True,
    )


def _ny(hour: int, *, day: int = 10) -> datetime:
    """A US/Eastern-naive-but-tz-aware stand-in: trading_day_of only
    inspects wall-clock time-of-day and date, so a fixed UTC offset is
    fine for these tests (they never cross a real DST transition)."""
    from datetime import timezone

    ny = timezone(timedelta(hours=-4))
    return datetime(2026, 9, day, hour, 0, tzinfo=ny)


def _trade_signal(object_id: str = "TS1") -> TradeSignal:
    return TradeSignal(
        object_id=object_id,
        risk_check_id="RC1",
        instrument_id="US100",
        generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        direction=Direction.LONG,
        volume=0.1,
        entry_reference_price=20000.0,
        stop_price=19950.0,
        take_profit_price=None,
    )


# ── ComplianceConfig ──────────────────────────────────────────────────


def test_config_rejects_buffer_at_or_above_daily_limit() -> None:
    with pytest.raises(ComplianceConfigError, match="safety_buffer_fraction"):
        _config(daily_loss_limit_fraction=0.005, safety_buffer_fraction=0.005)


def test_config_rejects_warning_at_or_above_critical() -> None:
    with pytest.raises(ComplianceConfigError, match="warning_threshold_fraction"):
        _config(warning_threshold_fraction=0.90, critical_threshold_fraction=0.70)


def test_config_effective_limits_subtract_the_buffer() -> None:
    cfg = _config()
    assert cfg.effective_daily_loss_limit_fraction == pytest.approx(0.045)
    assert cfg.effective_total_drawdown_limit_fraction == pytest.approx(0.095)


# ── ComplianceVerdict / ComplianceApproval invariants ──────────────────


def test_verdict_allowed_true_cannot_carry_a_breached_status() -> None:
    with pytest.raises(ComplianceApprovalError):
        ComplianceVerdict(
            object_id="V1",
            generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
            status=ComplianceStatus.BREACHED_DAILY,
            allowed=True,
            reason=None,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
            current_equity=9_500.0,
            daily_loss_used_fraction=1.0,
            total_drawdown_used_fraction=0.5,
        )


def test_verdict_blocked_must_carry_a_reason() -> None:
    with pytest.raises(ComplianceApprovalError, match="reason"):
        ComplianceVerdict(
            object_id="V1",
            generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
            status=ComplianceStatus.BREACHED_DAILY,
            allowed=False,
            reason=None,
            day_start_equity=10_000.0,
            peak_equity=10_000.0,
            current_equity=9_500.0,
            daily_loss_used_fraction=1.0,
            total_drawdown_used_fraction=0.5,
        )


def test_approval_requires_tz_aware_timestamp() -> None:
    with pytest.raises(ComplianceApprovalError, match="tz-aware"):
        ComplianceApproval(
            object_id="A1",
            trade_signal_id="TS1",
            verdict_id="V1",
            generated_at_utc=datetime(2026, 9, 10),  # naive
        )


# ── ComplianceEngine.on_snapshot ────────────────────────────────────────


def test_first_snapshot_is_safe_and_anchors_day_start_and_peak() -> None:
    engine = _engine()
    verdict = engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    assert verdict.status is ComplianceStatus.SAFE
    assert verdict.allowed is True
    assert verdict.day_start_equity == 10_000.0
    assert verdict.peak_equity == 10_000.0


def test_peak_equity_tracks_the_running_maximum() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    v2 = engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(10_500.0),
    )
    assert v2.peak_equity == 10_500.0
    v3 = engine.on_snapshot(
        object_id="V3", generated_at_utc=datetime(2026, 9, 10, 2, tzinfo=UTC),
        now_ny=_ny(12), account=_account(10_200.0),
    )
    # peak does not fall back down when equity dips
    assert v3.peak_equity == 10_500.0


def test_daily_loss_breach_blocks_and_is_flagged() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    # effective daily limit is 4.5% of 10,000 = 450 -> equity <= 9,550 breaches
    verdict = engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_500.0),
    )
    assert verdict.status is ComplianceStatus.BREACHED_DAILY
    assert verdict.allowed is False
    assert verdict.reason is not None and "daily loss" in verdict.reason


def test_daily_breach_clears_on_the_next_trading_day() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_500.0),
    )
    assert engine.current_status() is ComplianceStatus.BREACHED_DAILY

    # trading_day_opens is 18:00 NY -- crossing it starts a new trading day
    verdict = engine.on_snapshot(
        object_id="V3", generated_at_utc=datetime(2026, 9, 10, 23, tzinfo=UTC),
        now_ny=_ny(19, day=10), account=_account(9_500.0),
    )
    assert verdict.status is ComplianceStatus.SAFE
    assert verdict.day_start_equity == 9_500.0  # re-anchored to current equity


def test_total_drawdown_breach_blocks_permanently_even_after_recovery() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    # effective total limit is 9.5% of 10,000 = 950 -> equity <= 9,050 breaches
    breach = engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_000.0),
    )
    assert breach.status is ComplianceStatus.BREACHED_TOTAL
    assert breach.allowed is False

    # equity fully recovers back to the original peak on a NEW trading day
    recovered = engine.on_snapshot(
        object_id="V3", generated_at_utc=datetime(2026, 9, 10, 23, tzinfo=UTC),
        now_ny=_ny(19, day=10), account=_account(10_000.0),
    )
    assert recovered.status is ComplianceStatus.BREACHED_TOTAL
    assert recovered.allowed is False


def test_warning_and_critical_staging_below_the_hard_limit() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    # daily effective limit 4.5% = 450. equity 9,680 -> loss 320, used ~0.711
    # (comfortably past the 70% threshold -- not placed exactly on the
    # boundary, since 0.70 itself is not exactly representable in binary
    # floating point and a boundary value can land a hair under or over it)
    warn = engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_680.0),
    )
    assert warn.status is ComplianceStatus.WARNING
    assert warn.allowed is True

    # equity 9,590 -> loss 410, used ~0.911 (comfortably past the 90%
    # threshold, same floating-point-boundary reasoning as above)
    critical = engine.on_snapshot(
        object_id="V3", generated_at_utc=datetime(2026, 9, 10, 2, tzinfo=UTC),
        now_ny=_ny(12), account=_account(9_590.0),
    )
    assert critical.status is ComplianceStatus.CRITICAL
    assert critical.allowed is True


# ── news gate integration ────────────────────────────────────────────────


def test_news_blackout_blocks_an_otherwise_safe_snapshot() -> None:
    engine = ComplianceEngine(
        config=_config(),
        trading_day_opens=_TRADING_DAY_OPENS,
        news_gate_config=_news_gate_config(),
    )
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    verdict = engine.on_snapshot(
        object_id="V2",
        generated_at_utc=datetime(2026, 9, 10, 12, 2, tzinfo=UTC),  # 2 min after the event
        now_ny=_ny(11), account=_account(10_000.0),  # otherwise perfectly SAFE
        upcoming_events=[_news_event()],
    )
    assert verdict.status is ComplianceStatus.NEWS_BLACKOUT
    assert verdict.allowed is False
    assert verdict.active_news_event is not None
    assert verdict.active_news_event.event_id == "NFP-2026-09"
    assert verdict.reason is not None and "Non-Farm Payrolls" in verdict.reason


def test_no_news_gate_config_means_events_are_ignored() -> None:
    engine = _engine()  # no news_gate_config passed
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    verdict = engine.on_snapshot(
        object_id="V2",
        generated_at_utc=datetime(2026, 9, 10, 12, 2, tzinfo=UTC),
        now_ny=_ny(11), account=_account(10_000.0),
        upcoming_events=[_news_event()],
    )
    assert verdict.status is ComplianceStatus.SAFE
    assert verdict.allowed is True


def test_daily_breach_takes_precedence_over_a_concurrent_news_blackout() -> None:
    engine = ComplianceEngine(
        config=_config(),
        trading_day_opens=_TRADING_DAY_OPENS,
        news_gate_config=_news_gate_config(),
    )
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    # daily loss AND inside the news window, at the same instant
    verdict = engine.on_snapshot(
        object_id="V2",
        generated_at_utc=datetime(2026, 9, 10, 12, 2, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_500.0),  # 5% loss -- breaches the 4.5% effective limit
        upcoming_events=[_news_event()],
    )
    assert verdict.status is ComplianceStatus.BREACHED_DAILY
    assert verdict.active_news_event is None


# ── approve_trade / G15 ─────────────────────────────────────────────────


def test_approve_trade_succeeds_on_an_allowed_verdict() -> None:
    engine = _engine()
    verdict = engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    signal = _trade_signal()
    approval = approve_trade(verdict, signal, object_id="A1")
    assert approval.trade_signal_id == signal.object_id
    assert approval.verdict_id == verdict.object_id


def test_approve_trade_raises_on_a_blocked_verdict() -> None:
    engine = _engine()
    engine.on_snapshot(
        object_id="V1", generated_at_utc=datetime(2026, 9, 10, tzinfo=UTC),
        now_ny=_ny(10), account=_account(10_000.0),
    )
    breached = engine.on_snapshot(
        object_id="V2", generated_at_utc=datetime(2026, 9, 10, 1, tzinfo=UTC),
        now_ny=_ny(11), account=_account(9_000.0),
    )
    with pytest.raises(ComplianceApprovalError):
        approve_trade(breached, _trade_signal(), object_id="A1")


# ── DailyLossMode.DRAWDOWN_HEADROOM: the live personal account ─────────────
#
# The user's own instruction (2026-09-19): "for live account only have
# daily risk breach when drawdown is reaching close to breach and max
# draw down 20%". There is no prop firm imposing a daily rule on that
# account, so the day halts on total-drawdown headroom instead.


def _live_config(**overrides: object) -> ComplianceConfig:
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
    return ComplianceConfig(**base)  # type: ignore[arg-type]


def test_headroom_mode_ignores_a_daily_loss_that_would_breach_a_prop_account() -> None:
    """A 6% day is past the 5% daily figure, but total drawdown has barely
    moved -- on this account that is not a reason to stop."""
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    now_ny = datetime(2026, 9, 21, 10, 0)

    engine.on_snapshot(
        object_id="v1", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(100_000.0)
    )
    verdict = engine.on_snapshot(
        object_id="v2", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(94_000.0)
    )

    assert verdict.status is not ComplianceStatus.BREACHED_DAILY
    assert verdict.allowed is True
    # The daily figure is still reported -- it just does not gate.
    assert verdict.daily_loss_used_fraction > 1.0


def test_headroom_mode_halts_the_day_once_drawdown_nears_the_breach() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    now_ny = datetime(2026, 9, 21, 10, 0)

    engine.on_snapshot(
        object_id="v1", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(100_000.0)
    )
    # Enforced allowance is 19.5%; 75% of that is ~14.6% drawdown.
    verdict = engine.on_snapshot(
        object_id="v2", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(85_000.0)
    )

    assert verdict.status is ComplianceStatus.BREACHED_DAILY
    assert verdict.allowed is False
    assert verdict.reason is not None
    assert "headroom left" in verdict.reason


def test_headroom_mode_still_breaches_total_at_the_real_limit() -> None:
    """The 20% max drawdown is still the hard stop, and it is still
    permanent."""
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    now_ny = datetime(2026, 9, 21, 10, 0)

    engine.on_snapshot(
        object_id="v1", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(100_000.0)
    )
    verdict = engine.on_snapshot(
        object_id="v2", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(80_000.0)
    )

    assert verdict.status is ComplianceStatus.BREACHED_TOTAL
    assert verdict.allowed is False


def test_fixed_mode_is_unchanged_and_is_the_default() -> None:
    """Every existing prop-shaped config keeps its exact behavior."""
    config = ComplianceConfig(
        version=1,
        daily_loss_limit_fraction=0.05,
        total_drawdown_limit_fraction=0.10,
        safety_buffer_fraction=0.005,
        warning_threshold_fraction=0.70,
        critical_threshold_fraction=0.90,
    )
    assert config.daily_loss_mode is DailyLossMode.FIXED

    engine = ComplianceEngine(config=config, trading_day_opens=_TRADING_DAY_OPENS)
    now_ny = datetime(2026, 9, 21, 10, 0)
    engine.on_snapshot(
        object_id="v1", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(100_000.0)
    )
    verdict = engine.on_snapshot(
        object_id="v2", generated_at_utc=_GENERATED_AT, now_ny=now_ny, account=_account(95_400.0)
    )

    assert verdict.status is ComplianceStatus.BREACHED_DAILY


# ── the per-position form of the same halt rule ───────────────────────────
#
# "that 65% is applied to per position also" (user, 2026-09-19). The daily
# gate asks whether drawdown has already reached the halt point; this asks
# whether the trade being proposed would carry it past.


def test_a_trade_that_fits_inside_the_remaining_headroom_is_allowed() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 10, 0),
        account=_account(100_000.0),
    )

    rejection = engine.headroom_rejection(account=_account(100_000.0), projected_loss=500.0)

    assert rejection is None


def test_a_trade_larger_than_the_remaining_headroom_is_refused() -> None:
    """Halt sits at 65% of a 19.5% allowance = 12.675% drawdown, i.e.
    equity 87_325 off a 100_000 peak. From 88_000 there is 675 of room --
    a trade risking 800 does not fit."""
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 10, 0),
        account=_account(100_000.0),
    )
    engine.on_snapshot(
        object_id="v2",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 11, 0),
        account=_account(88_000.0),
    )

    rejection = engine.headroom_rejection(account=_account(88_000.0), projected_loss=800.0)

    assert rejection is not None
    assert "not usable room" in rejection


def test_the_verdict_reports_the_remaining_headroom() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    verdict = engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 10, 0),
        account=_account(100_000.0),
    )

    # Halt equity = 100_000 * (1 - 0.195 * 0.65) = 87_325.
    assert verdict.headroom_to_halt_currency == pytest.approx(12_675.0)


def test_headroom_never_reports_negative_room() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)
    engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 10, 0),
        account=_account(100_000.0),
    )
    verdict = engine.on_snapshot(
        object_id="v2",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 11, 0),
        account=_account(80_000.0),
    )

    assert verdict.headroom_to_halt_currency == 0.0


def test_fixed_mode_reports_no_headroom_figure() -> None:
    """In FIXED mode the day ends on a daily figure, so there is no single
    headroom number -- None beats a misleading one."""
    engine = ComplianceEngine(config=_config(), trading_day_opens=_TRADING_DAY_OPENS)
    verdict = engine.on_snapshot(
        object_id="v1",
        generated_at_utc=_GENERATED_AT,
        now_ny=datetime(2026, 9, 21, 10, 0),
        account=_account(100_000.0),
    )

    assert verdict.headroom_to_halt_currency is None
    assert engine.headroom_rejection(account=_account(100_000.0), projected_loss=9_999.0) is None


def test_headroom_cannot_be_judged_before_any_snapshot() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)

    rejection = engine.headroom_rejection(account=_account(100_000.0), projected_loss=1.0)

    assert rejection is not None
    assert "no snapshot" in rejection


def test_a_negative_projected_loss_is_a_programming_error() -> None:
    engine = ComplianceEngine(config=_live_config(), trading_day_opens=_TRADING_DAY_OPENS)

    with pytest.raises(ValueError, match="cannot be negative"):
        engine.headroom_rejection(account=_account(100_000.0), projected_loss=-1.0)


def test_the_shipped_live_profile_matches_what_the_user_confirmed() -> None:
    """Pins config/settings/compliance_live.yaml to the numbers the user
    actually gave, so a later edit cannot drift them silently."""
    repo_root = Path(__file__).resolve().parents[2]
    config = load_compliance_config(repo_root / "config" / "settings" / "compliance_live.yaml")

    assert config.total_drawdown_limit_fraction == 0.20
    assert config.daily_loss_mode is DailyLossMode.DRAWDOWN_HEADROOM
    assert config.daily_halt_at_total_usage == 0.65
