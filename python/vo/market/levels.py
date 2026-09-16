"""
Reference-level value types — measured facts, never named after a
strategy concept (architecture/vo-time-engine.md §6, §9 "Levels").

These are pure data: OHLC aggregates, anchor prices, and their pairwise
comparisons. The *computation* that produces them needs trading-day/
week/month/RTH/settlement boundaries — all vo.time (layer 2) concepts —
so it cannot live here: vo.market (layer 1) may not import vo.time (the
architecture layering test forbids it, the same rule that kept
`Bar.temporal` out of vo.market in Phase 6). See vo.time.levels.
ReferenceLevelEngine for the engine that builds these. This module holds
only the shapes — the same value-type/engine split already used for
Provenance (vo.market) vs BrokerProfile/resolve_broker_utc (vo.time).

Naming discipline, per the doc's own instruction: "ORG" never appears as
a field or type name here. vo-time-engine.md §6/§10 asks, and deliberately
leaves open, whether ORG means an opening-range gap or an opening-range
band — two different shapes. `AnchorComparison` sidesteps the question
instead of guessing: it records which of two same-day anchor prices was
higher and which was lower, as a plain measurement. Whatever strategy
label that measurement deserves — ORG or otherwise — is VO's decision,
made later, once VO exists to make it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PeriodOHLC:
    """OHLC for one completed calendar period — a trading day, an ISO
    week, or a month — keyed to the trading_day the period starts on.
    `open`/`close` come from the period's chronologically first/last bar;
    `high`/`low` are the extremes across every bar in the period."""

    period_start: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class AnchorPrice:
    """One dated, timestamped price observation anchored to a named
    boundary — e.g. "the opening price observed at/after RTH open" or
    "the closing price observed at/before settlement". `label` is a
    plain descriptive string ("rth_open", "settlement", ...), never a
    strategy term. `utc_timestamp` is the backing bar's own
    `open_time_utc` — the same key every other object in this codebase
    uses to identify a bar — not a synthesized "exact boundary instant";
    where the two differ (a data gap sat across the boundary), this is
    the honest one."""

    label: str
    trading_day: date
    utc_timestamp: datetime
    price: float


@dataclass(frozen=True)
class AnchorComparison:
    """Which of two same-day anchor prices was higher, which was lower —
    recorded as a measurement, not a strategy conclusion (see the module
    docstring's note on ORG). `higher`/`lower` are None, never a guessed
    pick, when the two prices are exactly equal."""

    trading_day: date
    first: AnchorPrice
    second: AnchorPrice

    @property
    def higher(self) -> AnchorPrice | None:
        if self.first.price == self.second.price:
            return None
        return self.first if self.first.price > self.second.price else self.second

    @property
    def lower(self) -> AnchorPrice | None:
        if self.first.price == self.second.price:
            return None
        return self.second if self.first.price > self.second.price else self.first


@dataclass(frozen=True)
class SessionOpens:
    """Session-anchored opening prices for one trading day. Any field is
    None when no bar was observed at or after that boundary yet (an
    honest "not yet known", never a guessed carry-forward)."""

    trading_day: date
    trading_day_open: AnchorPrice | None
    week_open: AnchorPrice | None
    month_open: AnchorPrice | None
    rth_open: AnchorPrice | None


@dataclass(frozen=True)
class BoundaryPair:
    """A session boundary crossing exactly as observed in the data — both
    raw prices preserved unchanged, the same raw-preservation discipline
    architecture/vo-candle-layer.md applies to adjacent bars, applied
    here to adjacent sessions instead. Covers the normal intraday
    transition and the weekend/holiday gap alike: both are just "the
    session recorded on the next bar differs from the session recorded
    on the previous one" as observed in the actual bar stream, nothing
    calendar-specific assumed. `previous_session`/`session` are plain
    strings — the Time Engine's own session names — never a VO concept."""

    previous_session: str | None
    session: str | None
    prior_close: float
    prior_close_at_utc: datetime
    next_open: float
    next_open_at_utc: datetime
