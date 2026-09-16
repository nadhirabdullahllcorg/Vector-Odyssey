"""
ProvisionalTimeEngine — lets the pipeline run before the real rules are
settled, without being mistaken for the finished thing (architecture/
vo-time-engine.md §5).

Every TimeContext it produces is stamped `TimeProvenance(rule_source=
"provisional")`, and construction logs a warning once. A dashboard (a
later phase) renders that provenance as a banner; nothing here decides
what the banner looks like, only that the fact is never hidden.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime

from vo.market.identity import InstrumentId
from vo.time.context import SessionTransition, TimeContext, TimeProvenance
from vo.time.engine import VOTimeEngine
from vo.time.sessions import SessionConfig

logger = logging.getLogger(__name__)


class ProvisionalTimeEngine:
    def __init__(self, session_configs: dict[str, SessionConfig]) -> None:
        logger.warning(
            "ProvisionalTimeEngine constructed: every TimeContext it produces is "
            "provisional and must not be treated as the finished Time Engine."
        )
        self._delegate = VOTimeEngine(session_configs)

    def _stamp(self, context: TimeContext) -> TimeContext:
        return replace(context, provenance=TimeProvenance(rule_source="provisional"))

    def context_for(self, utc: datetime, instrument: InstrumentId) -> TimeContext:
        return self._stamp(self._delegate.context_for(utc, instrument))

    def session_at(self, utc: datetime, instrument: InstrumentId) -> str | None:
        return self._delegate.session_at(utc, instrument)

    def transition_at(self, utc: datetime, instrument: InstrumentId) -> SessionTransition | None:
        return self._delegate.transition_at(utc, instrument)

    def trading_day_of(self, utc: datetime, instrument: InstrumentId) -> date:
        return self._delegate.trading_day_of(utc, instrument)

    def is_rth(self, utc: datetime, instrument: InstrumentId) -> bool:
        return self._delegate.is_rth(utc, instrument)
