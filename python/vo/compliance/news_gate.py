"""
vo.compliance.news_gate -- v1 (2026-09-19), built at the user's explicit
request: "I want a news gate where trading is not allowed 5 mins before
during and after news," applying to both live and prop-firm accounts.

Blocks new trade approval for a configurable window around any
EconomicEvent at or above a configured importance threshold:

    [event.scheduled_at_utc - buffer_before_minutes,
     event.scheduled_at_utc + event.duration_minutes + buffer_after_minutes]

`evaluate_news_blackout` is deliberately source-agnostic and has no
internal wall-clock read (same discipline as vo.risk.evaluate_risk and
vo.compliance.engine.ComplianceEngine.on_snapshot): it takes the caller's
own list of EconomicEvent and a caller-supplied `now_utc`, and knows
nothing about where the events came from. See
vo.interfaces.economic_events's own module docstring for why populating
that list in production (a third-party calendar API, an MQL5-side bridge
file, or a manually maintained FOMC/CPI/NFP list) is a decision this
module does not make.

CONFIG DEFAULTS (config/settings/news_gate.yaml) ARE A FLAGGED STARTING
POINT, same posture as compliance.yaml/risk.yaml/swings.yaml before it:
5-minute before/after buffers per the user's own instruction,
`min_importance: HIGH` and `currencies: [USD]` chosen because this
project trades USD-denominated US indices (US100/US500) -- not yet
confirmed as the account's actual required scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from vo.interfaces.economic_events import EconomicEvent, EventImportance


class NewsGateConfigError(ValueError):
    pass


_IMPORTANCE_ORDER: dict[EventImportance, int] = {
    EventImportance.NONE: 0,
    EventImportance.LOW: 1,
    EventImportance.MODERATE: 2,
    EventImportance.HIGH: 3,
}


@dataclass(frozen=True)
class NewsGateConfig:
    version: int
    buffer_before_minutes: float
    buffer_after_minutes: float
    min_importance: EventImportance
    currencies: tuple[str, ...] | None
    """None means every currency is in scope; otherwise only events whose
    `currency` appears here (case-sensitive, e.g. "USD") are considered."""

    def __post_init__(self) -> None:
        if self.buffer_before_minutes < 0:
            raise NewsGateConfigError("buffer_before_minutes must be >= 0")
        if self.buffer_after_minutes < 0:
            raise NewsGateConfigError("buffer_after_minutes must be >= 0")


@dataclass(frozen=True, slots=True)
class NewsBlackoutVerdict:
    """Always produced -- same "every rejection carries a reason"
    discipline as ComplianceVerdict/RiskCheck."""

    blocked: bool
    reason: str | None
    active_event: EconomicEvent | None

    def __post_init__(self) -> None:
        if self.blocked and self.active_event is None:
            raise NewsGateConfigError("a blocked NewsBlackoutVerdict must name active_event")
        if self.blocked and not (self.reason and self.reason.strip()):
            raise NewsGateConfigError("a blocked NewsBlackoutVerdict must carry a reason")
        if not self.blocked and self.active_event is not None:
            raise NewsGateConfigError("a clear NewsBlackoutVerdict carries no active_event")
        if not self.blocked and self.reason is not None:
            raise NewsGateConfigError("a clear NewsBlackoutVerdict carries no reason")


def evaluate_news_blackout(
    events: Sequence[EconomicEvent], now_utc: datetime, config: NewsGateConfig
) -> NewsBlackoutVerdict:
    """Is `now_utc` inside any qualifying event's blackout window? No
    wall-clock read, no I/O -- `events`/`now_utc` are both caller-supplied.
    When several qualifying windows overlap, the earliest-scheduled event
    is reported (a stable, deterministic choice for the verdict's reason
    string; every overlapping window still blocks regardless of which one
    is named)."""
    if now_utc.tzinfo is None:
        raise NewsGateConfigError("now_utc must be tz-aware")

    threshold = _IMPORTANCE_ORDER[config.min_importance]
    candidates = [e for e in events if _IMPORTANCE_ORDER[e.importance] >= threshold]
    if config.currencies is not None:
        candidates = [e for e in candidates if e.currency in config.currencies]

    for event in sorted(candidates, key=lambda e: e.scheduled_at_utc):
        window_start = event.scheduled_at_utc - timedelta(minutes=config.buffer_before_minutes)
        window_end = event.scheduled_at_utc + timedelta(
            minutes=event.duration_minutes + config.buffer_after_minutes
        )
        if window_start <= now_utc <= window_end:
            return NewsBlackoutVerdict(
                blocked=True,
                reason=(
                    f"{event.name} ({event.currency}, {event.importance}) scheduled "
                    f"{event.scheduled_at_utc.isoformat()} -- inside the "
                    f"{config.buffer_before_minutes:.0f}m-before/"
                    f"{config.buffer_after_minutes:.0f}m-after blackout window"
                ),
                active_event=event,
            )
    return NewsBlackoutVerdict(blocked=False, reason=None, active_event=None)


def _require_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NewsGateConfigError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def load_news_gate_config(path: str | Path) -> NewsGateConfig:
    """Load and validate config/settings/news_gate.yaml. Raises
    NewsGateConfigError for anything malformed rather than silently
    substituting a default -- same discipline as load_compliance_config/
    load_risk_config."""
    raw = yaml.safe_load(Path(path).read_text())
    top = _require_mapping(raw, what="news gate config")

    required = (
        "version",
        "buffer_before_minutes",
        "buffer_after_minutes",
        "min_importance",
        "currencies",
    )
    for key in required:
        if key not in top:
            raise NewsGateConfigError(f"news gate config requires '{key}'")

    raw_currencies = top["currencies"]
    currencies: tuple[str, ...] | None
    if raw_currencies is None:
        currencies = None
    elif isinstance(raw_currencies, list):
        currencies = tuple(str(c) for c in raw_currencies)
    else:
        raise NewsGateConfigError("news gate config 'currencies' must be a list or null")

    try:
        min_importance = EventImportance(str(top["min_importance"]))
    except ValueError as exc:
        raise NewsGateConfigError(
            f"news gate config 'min_importance' must be one of "
            f"{[i.value for i in EventImportance]}, got {top['min_importance']!r}"
        ) from exc

    return NewsGateConfig(
        version=int(top["version"]),
        buffer_before_minutes=float(top["buffer_before_minutes"]),
        buffer_after_minutes=float(top["buffer_after_minutes"]),
        min_importance=min_importance,
        currencies=currencies,
    )
