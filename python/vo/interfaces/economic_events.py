"""
vo.interfaces.economic_events -- EconomicEvent, the contract
architecture/vo-time-engine.md Section 7 named and explicitly left
unimplemented ("EconomicEvent and EconomicEventContext are defined as
contracts in this phase and left unimplemented... They record facts --
event, currency, scheduled time in UTC/NY/broker time, actual, forecast,
previous, revision, source -- and never conclusions"). Built now because
vo.compliance.news_gate (v1, 2026-09-19) is the first real consumer --
confirmed with the user, applying to both live and prop-firm accounts.

v1 SCOPE IS NARROWER than that original Section 7 field list: only what a
news-blackout gate needs to decide "is now inside a blackout window" --
event_id, name, currency, scheduled_at_utc, importance, duration_minutes,
source. actual_value/forecast_value/previous_value/revision_value are
included as optional fields so a later research consumer ("did the market
move because of a surprise vs. an in-line print") can populate the SAME
object rather than needing a second type -- v1's gate never reads them.

Plain frozen dataclass, not a CanonicalRecord subclass -- same reasoning
as vo.interfaces.signals/vo.interfaces.compliance: an EconomicEvent
describes an external fact with its own natural key (event_id, assigned
by whatever calendar source populates it), not something VO itself
observed and stamped with object_id/generated_at_utc/methodology_version.

WHERE THE DATA ACTUALLY COMES FROM IS DELIBERATELY NOT THIS MODULE'S
CONCERN, and is not yet decided -- researched directly against MQL5's own
docs, 2026-09-19: MQL5 has a native Economic Calendar API
(CalendarEventByCountry/CalendarValueHistory/etc., mql5.com/en/docs/
calendar) but the Python `MetaTrader5` package does NOT expose it (its
function list is market-data/trading only -- mql5.com/en/docs/
python_metatrader5); and MQL5's own calendar is not guaranteed populated
on every terminal/VPS (gated behind a Community-tab toggle, community-
reported gaps on some hosting setups). The realistic options -- an
official third-party calendar API (e.g. Finnhub, Financial Modeling Prep)
called directly from Python, an MQL5-side bridge script writing
CalendarValueHistory output to a file vo.market.ingestion-style, or a
manually maintained list of known events (FOMC/NFP/CPI dates are
published well in advance) -- are a wiring/config decision for whoever
populates `events`, not something this contract type or
vo.compliance.news_gate needs to know about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EconomicEventError(ValueError):
    """Raised when an EconomicEvent is constructed with an invalid shape."""


class EventImportance(Enum):
    """Mirrors MQL5's own ENUM_CALENDAR_EVENT_IMPORTANCE four-level scale
    (NONE/LOW/MODERATE/HIGH) rather than inventing a new one, so a value
    read from MQL5's native calendar (were a future bridge built) maps
    across with no translation table."""

    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    """One scheduled (or released) economic event. `duration_minutes` is
    0.0 for a point-in-time release (the overwhelming majority -- NFP,
    CPI, most central-bank rate decisions) and > 0.0 for an event with a
    real span (e.g. a press conference following a rate decision) --
    letting vo.compliance.news_gate's before/during/after window collapse
    correctly to a single instant when there is no "during" to speak of."""

    event_id: str
    name: str
    currency: str
    scheduled_at_utc: datetime
    importance: EventImportance
    duration_minutes: float = 0.0
    source: str = "unspecified"
    actual_value: float | None = None
    forecast_value: float | None = None
    previous_value: float | None = None
    revision_value: float | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise EconomicEventError("EconomicEvent.event_id cannot be blank")
        if not self.name.strip():
            raise EconomicEventError("EconomicEvent.name cannot be blank")
        if not self.currency.strip():
            raise EconomicEventError("EconomicEvent.currency cannot be blank")
        if self.scheduled_at_utc.tzinfo is None:
            raise EconomicEventError("EconomicEvent.scheduled_at_utc must be tz-aware")
        if self.duration_minutes < 0:
            raise EconomicEventError("EconomicEvent.duration_minutes must be >= 0")
        if not self.source.strip():
            raise EconomicEventError("EconomicEvent.source cannot be blank")
