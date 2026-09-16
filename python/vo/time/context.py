"""
TimeContext — the Time Engine's data contract (architecture/vo-time-engine.md §2).

Says *when*, never *what it means* (§0): a session name, a trading day, a
transition instant are all facts about the clock. Nothing here is a
strategy conclusion.

`TemporalStatus` (§3) is how the engine admits what it does not know,
instead of quietly turning an unresolvable offset into a confident number.
`VALID` is the only status a consumer should build a trade decision on —
enforcing that refusal is a later phase's job (the replay harness, P9); this
module just makes the fact visible on every TimeContext it produces.

Bar (vo.market, layer 1) cannot hold a `TimeContext` field directly — that
would be an upward import (vo.market importing vo.time, layer 2), which the
architecture layering test forbids even for an Optional field. `TemporalBar`
is the composition point instead: a Bar and the TimeContext computed for it,
held side by side rather than merged into one dataclass. This mirrors
`Candle` wrapping a `Bar` for geometry (Phase 5) rather than extending Bar
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, auto

from vo.market.bar import Bar


class TemporalStatus(Enum):
    VALID = auto()
    """The conversion is established and unambiguous."""
    UNKNOWN = auto()
    """Broker timezone rules could not be established for this instant."""
    AMBIGUOUS = auto()
    """A DST fall-back repeat: this local time occurred twice and nothing
    says which occurrence this is."""
    INVALID = auto()
    """A DST spring-forward gap: this local time never occurred at all."""


@dataclass(frozen=True)
class TimeProvenance:
    """
    Which rules produced this TimeContext — not to be confused with
    vo.market.Provenance, which is about one bar's raw wire timestamp.
    This is about the Time Engine's own configuration: what it would mean
    for two TimeContext values to have been computed under different rules.
    """

    rule_source: str
    """e.g. "vo.time.brokers" / "provisional" — where the offset rule came from."""
    tz_database_version: str | None = None
    session_config_version: int | None = None


@dataclass(frozen=True)
class SessionTransition:
    """A session boundary crossing — a clock event, nothing more (§4)."""

    previous_session: str | None
    session: str | None


@dataclass(frozen=True)
class TimeContext:
    """Frozen, serializable. Computed once per (utc, instrument); never mutated."""

    # ── broker origin ────────────────────────────────────────────────
    broker_timestamp: str | None
    """As received, unconverted — kept as its original string form."""
    broker_tz_label: str | None
    broker_utc_offset_seconds: int | None

    # ── canonical ────────────────────────────────────────────────────
    utc_timestamp: datetime

    # ── New York ─────────────────────────────────────────────────────
    ny_timestamp: datetime
    ny_utc_offset_seconds: int

    # ── calendar ─────────────────────────────────────────────────────
    trading_day: date
    trading_week: tuple[int, int]
    """ISO (year, week)."""
    trading_month: int
    trading_year: int
    day_of_week: int
    """0=Monday .. 6=Sunday, from trading_day (not necessarily ny_timestamp's
    own calendar date — see calendars.trading_day_of)."""

    # ── session ──────────────────────────────────────────────────────
    #
    # architecture/vo-time-engine.md §2 also lists `session_period` /
    # `previous_session_period` alongside `session` / `previous_session`,
    # without ever defining how the two differ - the doc's own worked
    # examples (§4's YAML, §9's test list) only ever exercise one concept
    # per instant. Rather than invent a distinction the spec doesn't give,
    # this implementation has one: `session`. If a real need for a second,
    # narrower "period" concept shows up later, it can be added then.
    session: str | None
    """None outside every configured window."""
    is_rth: bool
    session_started_at: datetime | None
    session_ends_at: datetime | None
    previous_session: str | None
    session_transition: SessionTransition | None
    transition_timestamp: datetime | None

    # ── honesty ──────────────────────────────────────────────────────
    status: TemporalStatus
    provenance: TimeProvenance


@dataclass(frozen=True)
class TemporalBar:
    """A Bar and the TimeContext computed for its open_time_utc, held
    together rather than merged onto Bar itself. See the module docstring."""

    bar: Bar
    temporal: TimeContext
