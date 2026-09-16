"""
ITimeEngine / VOTimeEngine (architecture/vo-time-engine.md §5).

Live, replay, backtest, walk-forward and stress all receive the same
implementation behind this Protocol; only the source of `utc` differs.
That is what stops live and backtest from drifting into different session
definitions.

`context_for` takes an *already-UTC* instant, deliberately: broker ->
UTC conversion (brokers.py's resolve_broker_utc) is a separate, earlier
step that needs broker-specific inputs (the raw wall-clock reading, which
broker) this Protocol's methods do not take. `build_time_context` at the
bottom wires the two steps together for the common case of "I have a raw
broker record and want a full TimeContext" without folding broker-origin
concerns into the five core methods themselves.

Session transitions are computed as an exact-boundary fact, not by
comparing against a previous bar: `transition_at(utc, instrument)` reports
a transition when `utc` equals the session's start instant. Detecting
"the first bar after a boundary" from a bar stream is a higher-layer
concern (built on this primitive), not something a stateless, single-
instant Protocol method can know.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol

from vo.market.identity import InstrumentId
from vo.time.calendars import trading_day_of
from vo.time.context import SessionTransition, TemporalStatus, TimeContext, TimeProvenance
from vo.time.sessions import SessionConfig, is_rth, session_at, session_bounds_utc


class ITimeEngine(Protocol):
    def context_for(self, utc: datetime, instrument: InstrumentId) -> TimeContext: ...
    def session_at(self, utc: datetime, instrument: InstrumentId) -> str | None: ...
    def transition_at(
        self, utc: datetime, instrument: InstrumentId
    ) -> SessionTransition | None: ...
    def trading_day_of(self, utc: datetime, instrument: InstrumentId) -> date: ...
    def is_rth(self, utc: datetime, instrument: InstrumentId) -> bool: ...


class UnknownInstrumentError(KeyError):
    """Raised when an instrument has no configured session model - never
    silently falls back to another instrument's sessions."""


class VOTimeEngine:
    """
    The real implementation: config-driven session lookups keyed by
    `InstrumentId.broker_symbol` (config/settings/sessions.yaml is keyed by
    broker symbol, matching how brokers.yaml is keyed by broker server —
    both configs describe what is actually observable from the wire).
    """

    def __init__(self, session_configs: dict[str, SessionConfig]) -> None:
        self._session_configs = session_configs

    def _config_for(self, instrument: InstrumentId) -> SessionConfig:
        config = self._session_configs.get(instrument.broker_symbol)
        if config is None:
            raise UnknownInstrumentError(
                f"No session config for {instrument.broker_symbol!r} - "
                f"add it to config/settings/sessions.yaml"
            )
        return config

    def config_for(self, instrument: InstrumentId) -> SessionConfig:
        """Public accessor for the resolved SessionConfig - vo.time.levels
        (Phase 7) needs the raw RTH/settlement time-of-day and timezone
        directly, beyond what the five ITimeEngine methods expose."""
        return self._config_for(instrument)

    def context_for(self, utc: datetime, instrument: InstrumentId) -> TimeContext:
        if utc.tzinfo is None:
            raise ValueError("utc must be timezone-aware")

        config = self._config_for(instrument)
        zone = config.zone
        ny_timestamp = utc.astimezone(zone)
        ny_offset = ny_timestamp.utcoffset()
        assert ny_offset is not None  # ny_timestamp is aware

        trading_day = trading_day_of(ny_timestamp, config.trading_day_opens)
        iso_year, iso_week, _ = trading_day.isocalendar()

        window = session_at(ny_timestamp, config)
        session_name = window.name if window else None
        session_started_at: datetime | None = None
        session_ends_at: datetime | None = None
        previous_session: str | None = None
        transition: SessionTransition | None = None
        transition_timestamp: datetime | None = None

        if window is not None:
            session_started_at, session_ends_at = session_bounds_utc(ny_timestamp, window, zone)

            just_before = session_at(ny_timestamp - timedelta(minutes=1), config)
            previous_session = just_before.name if just_before else None

            if utc == session_started_at:
                transition = SessionTransition(
                    previous_session=previous_session, session=session_name
                )
                transition_timestamp = utc

        return TimeContext(
            broker_timestamp=None,
            broker_tz_label=None,
            broker_utc_offset_seconds=None,
            utc_timestamp=utc,
            ny_timestamp=ny_timestamp,
            ny_utc_offset_seconds=int(ny_offset.total_seconds()),
            trading_day=trading_day,
            trading_week=(iso_year, iso_week),
            trading_month=trading_day.month,
            trading_year=trading_day.year,
            day_of_week=trading_day.weekday(),
            session=session_name,
            is_rth=is_rth(ny_timestamp, config),
            session_started_at=session_started_at,
            session_ends_at=session_ends_at,
            previous_session=previous_session,
            session_transition=transition,
            transition_timestamp=transition_timestamp,
            status=TemporalStatus.VALID,
            provenance=TimeProvenance(rule_source="vo.time.engine.VOTimeEngine"),
        )

    def session_at(self, utc: datetime, instrument: InstrumentId) -> str | None:
        return self.context_for(utc, instrument).session

    def transition_at(self, utc: datetime, instrument: InstrumentId) -> SessionTransition | None:
        return self.context_for(utc, instrument).session_transition

    def trading_day_of(self, utc: datetime, instrument: InstrumentId) -> date:
        return self.context_for(utc, instrument).trading_day

    def is_rth(self, utc: datetime, instrument: InstrumentId) -> bool:
        return self.context_for(utc, instrument).is_rth
