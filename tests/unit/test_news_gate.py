"""Unit tests for vo.compliance.news_gate and its
vo.interfaces.economic_events contract types."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vo.compliance.news_gate import (
    NewsBlackoutVerdict,
    NewsGateConfig,
    NewsGateConfigError,
    evaluate_news_blackout,
)
from vo.interfaces.economic_events import EconomicEvent, EconomicEventError, EventImportance

_NOON = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _config(**overrides: object) -> NewsGateConfig:
    base: dict[str, object] = dict(
        version=1,
        buffer_before_minutes=5.0,
        buffer_after_minutes=5.0,
        min_importance=EventImportance.HIGH,
        currencies=("USD",),
    )
    base.update(overrides)
    return NewsGateConfig(**base)  # type: ignore[arg-type]


def _event(**overrides: object) -> EconomicEvent:
    base: dict[str, object] = dict(
        event_id="NFP-2026-09",
        name="Non-Farm Payrolls",
        currency="USD",
        scheduled_at_utc=_NOON,
        importance=EventImportance.HIGH,
    )
    base.update(overrides)
    return EconomicEvent(**base)  # type: ignore[arg-type]


# ── EconomicEvent invariants ────────────────────────────────────────────


def test_event_rejects_blank_event_id() -> None:
    with pytest.raises(EconomicEventError, match="event_id"):
        _event(event_id="  ")


def test_event_rejects_naive_timestamp() -> None:
    with pytest.raises(EconomicEventError, match="tz-aware"):
        _event(scheduled_at_utc=datetime(2026, 9, 19, 12, 0))


def test_event_rejects_negative_duration() -> None:
    with pytest.raises(EconomicEventError, match="duration_minutes"):
        _event(duration_minutes=-1.0)


# ── NewsGateConfig invariants ────────────────────────────────────────────


def test_config_rejects_negative_buffer_before() -> None:
    with pytest.raises(NewsGateConfigError, match="buffer_before_minutes"):
        _config(buffer_before_minutes=-1.0)


def test_config_rejects_negative_buffer_after() -> None:
    with pytest.raises(NewsGateConfigError, match="buffer_after_minutes"):
        _config(buffer_after_minutes=-1.0)


# ── NewsBlackoutVerdict invariants ───────────────────────────────────────


def test_blocked_verdict_requires_active_event() -> None:
    with pytest.raises(NewsGateConfigError, match="active_event"):
        NewsBlackoutVerdict(blocked=True, reason="because", active_event=None)


def test_blocked_verdict_requires_a_reason() -> None:
    with pytest.raises(NewsGateConfigError, match="reason"):
        NewsBlackoutVerdict(blocked=True, reason=None, active_event=_event())


def test_clear_verdict_carries_no_event_or_reason() -> None:
    with pytest.raises(NewsGateConfigError):
        NewsBlackoutVerdict(blocked=False, reason=None, active_event=_event())


# ── evaluate_news_blackout ───────────────────────────────────────────────


def test_no_events_means_no_blackout() -> None:
    verdict = evaluate_news_blackout([], _NOON, _config())
    assert verdict.blocked is False


def test_before_the_event_inside_the_buffer_is_blocked() -> None:
    now = datetime(2026, 9, 19, 11, 57, tzinfo=UTC)  # 3 min before noon
    verdict = evaluate_news_blackout([_event()], now, _config())
    assert verdict.blocked is True
    assert verdict.active_event is not None and verdict.active_event.event_id == "NFP-2026-09"
    assert "Non-Farm Payrolls" in (verdict.reason or "")


def test_after_the_event_inside_the_buffer_is_blocked() -> None:
    now = datetime(2026, 9, 19, 12, 3, tzinfo=UTC)  # 3 min after noon
    verdict = evaluate_news_blackout([_event()], now, _config())
    assert verdict.blocked is True


def test_outside_the_buffer_is_not_blocked() -> None:
    before = datetime(2026, 9, 19, 11, 54, tzinfo=UTC)  # 6 min before
    after = datetime(2026, 9, 19, 12, 6, tzinfo=UTC)  # 6 min after
    assert evaluate_news_blackout([_event()], before, _config()).blocked is False
    assert evaluate_news_blackout([_event()], after, _config()).blocked is False


def test_an_event_with_duration_extends_the_during_window() -> None:
    # FOMC statement at noon, press conference runs 45 minutes.
    event = _event(event_id="FOMC-2026-09", name="FOMC Press Conference", duration_minutes=45.0)
    now = datetime(2026, 9, 19, 12, 40, tzinfo=UTC)  # inside the presser, past a bare 5min buffer
    verdict = evaluate_news_blackout([event], now, _config())
    assert verdict.blocked is True


def test_importance_below_threshold_is_ignored() -> None:
    low = _event(importance=EventImportance.LOW)
    now = datetime(2026, 9, 19, 12, 1, tzinfo=UTC)
    verdict = evaluate_news_blackout([low], now, _config(min_importance=EventImportance.HIGH))
    assert verdict.blocked is False


def test_currency_outside_scope_is_ignored() -> None:
    eur_event = _event(event_id="ECB-1", currency="EUR")
    now = datetime(2026, 9, 19, 12, 1, tzinfo=UTC)
    verdict = evaluate_news_blackout([eur_event], now, _config(currencies=("USD",)))
    assert verdict.blocked is False


def test_currencies_none_means_every_currency_counts() -> None:
    eur_event = _event(event_id="ECB-1", currency="EUR")
    now = datetime(2026, 9, 19, 12, 1, tzinfo=UTC)
    verdict = evaluate_news_blackout([eur_event], now, _config(currencies=None))
    assert verdict.blocked is True


def test_earliest_overlapping_event_is_reported() -> None:
    earlier = _event(event_id="A", scheduled_at_utc=datetime(2026, 9, 19, 11, 58, tzinfo=UTC))
    later = _event(event_id="B", scheduled_at_utc=datetime(2026, 9, 19, 12, 2, tzinfo=UTC))
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)  # inside both windows
    verdict = evaluate_news_blackout([later, earlier], now, _config())
    assert verdict.blocked is True
    assert verdict.active_event is not None and verdict.active_event.event_id == "A"


def test_now_must_be_tz_aware() -> None:
    with pytest.raises(NewsGateConfigError, match="tz-aware"):
        evaluate_news_blackout([_event()], datetime(2026, 9, 19, 12, 0), _config())
