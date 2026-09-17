"""
ObservationPipeline -- Phase 10's "bridge -> canonical -> candle -> time
-> levels" assembly, in one place, for one instrument.

This module deliberately touches no network, no config file, and no MT5:
it consumes AnyRecord values -- however they arrived (a live tail, a
batch read_jsonl, a golden fixture, a unit test's hand-built record) --
and turns them into the same sequence of canonical objects every stage
after this one relies on. That is Stage 3's own acceptance condition
(architecture/vo-phase-plan.md SS3): "the same source observation should
produce the same canonical representation regardless of whether it came
from live MT5, historical, replay, or fixture." Routing every record
through vo.time.mapping.resolve_record (rather than branching on schema
version here) is what keeps that promise mechanical rather than aspirational.

No trading interpretation lives here -- this is the observation half of
D20 ("functioning MT5 EA"); everything that reads a PipelineSnapshot and
decides something is Phase 11 and later.
"""

from __future__ import annotations

from dataclasses import dataclass

from vo.market.bar import Bar
from vo.market.deserialization import AnyRecord
from vo.market.identity import InstrumentId
from vo.market.levels import PeriodOHLC, SessionOpens
from vo.market.records import SourceCapabilitiesRecord
from vo.market.sequence import BarSequence, CandleWindow
from vo.market.symbol import Symbol
from vo.market.tick import Tick
from vo.time.brokers import BrokerProfile
from vo.time.context import TimeContext
from vo.time.engine import VOTimeEngine
from vo.time.levels import ReferenceLevelEngine
from vo.time.mapping import resolve_record
from vo.time.sessions import SessionConfig


@dataclass(frozen=True)
class QuarantinedInboundRecord:
    """One record ObservationPipeline could not resolve or append, and
    why -- the same quarantine-not-abort discipline
    vo.market.mapping.map_records and vo.market.sequence.build_bar_sequence
    already use (E.7)."""

    record: AnyRecord
    reason: str


@dataclass(frozen=True)
class PipelineSnapshot:
    """
    What the pipeline currently knows about one instrument, after
    processing whatever has arrived so far. Consumed by vo.telemetry to
    build a RuntimeState -- this type carries no telemetry dependency of
    its own (vo.core may not import vo.telemetry; see
    tests/unit/test_architecture.py's layering test).
    """

    instrument: InstrumentId | None
    bar_count: int
    latest_bar: Bar | None
    latest_window: CandleWindow | None
    latest_context: TimeContext | None
    latest_tick: Tick | None
    session_opens: SessionOpens | None
    previous_day_ohlc: PeriodOHLC | None


_EMPTY_SNAPSHOT = PipelineSnapshot(
    instrument=None,
    bar_count=0,
    latest_bar=None,
    latest_window=None,
    latest_context=None,
    latest_tick=None,
    session_opens=None,
    previous_day_ohlc=None,
)


class ObservationPipeline:
    """
    Built once per broker_symbol for the lifetime of a VO_EA process.

    ingest() accepts one AnyRecord at a time. A bar record grows the
    running BarSequence and becomes the new "current" for Time Engine and
    reference-level purposes; a tick record only updates the latest
    observed Tick (Time/Levels are bar-driven -- see
    architecture/vo-time-engine.md and Phase 7's own scope); a symbol
    record updates the latest observed Symbol; anything resolve_record
    cannot honestly turn into a domain object (an unresolved v2
    timestamp, a duplicate/out-of-order bar, a malformed record) is
    quarantined with its reason rather than raised past this call.
    """

    def __init__(
        self,
        broker_profiles: dict[str, BrokerProfile],
        session_configs: dict[str, SessionConfig],
    ) -> None:
        self._broker_profiles = broker_profiles
        self._time_engine = VOTimeEngine(session_configs)
        self._sequence = BarSequence()
        self._instrument: InstrumentId | None = None
        self._latest_tick: Tick | None = None
        self._latest_symbol: Symbol | None = None
        self.quarantined: list[QuarantinedInboundRecord] = []

    def ingest(self, record: AnyRecord) -> None:
        try:
            self._ingest(record)
        except (ValueError, TypeError) as exc:
            self.quarantined.append(QuarantinedInboundRecord(record=record, reason=str(exc)))

    def _ingest(self, record: AnyRecord) -> None:
        if isinstance(record, SourceCapabilitiesRecord):
            # Source-capability metadata (what the feed can provide), not an
            # observation -- nothing to resolve or store. Ignore it rather
            # than quarantine it, so it does not inflate the quarantine count.
            return
        resolved = resolve_record(record, self._broker_profiles)

        if isinstance(resolved, Symbol):
            self._latest_symbol = resolved
        elif isinstance(resolved, Tick):
            self._latest_tick = resolved
        elif isinstance(resolved, Bar):
            self._sequence = self._sequence.append(resolved)
            self._instrument = resolved.instrument_id
        else:
            raise TypeError(f"Unexpected domain object type: {type(resolved).__name__}")

    @property
    def sequence(self) -> BarSequence:
        return self._sequence

    @property
    def latest_symbol(self) -> Symbol | None:
        return self._latest_symbol

    def snapshot(self) -> PipelineSnapshot:
        if self._instrument is None or not self._sequence.bars:
            if self._latest_tick is None:
                return _EMPTY_SNAPSHOT
            return PipelineSnapshot(
                instrument=self._instrument,
                bar_count=0,
                latest_bar=None,
                latest_window=None,
                latest_context=None,
                latest_tick=self._latest_tick,
                session_opens=None,
                previous_day_ohlc=None,
            )

        latest_bar = self._sequence.bars[-1]
        window = self._sequence.window_at(len(self._sequence) - 1)
        context = self._time_engine.context_for(latest_bar.open_time_utc, self._instrument)

        levels = ReferenceLevelEngine(self._time_engine, self._sequence)
        trading_day = context.trading_day

        return PipelineSnapshot(
            instrument=self._instrument,
            bar_count=len(self._sequence),
            latest_bar=latest_bar,
            latest_window=window,
            latest_context=context,
            latest_tick=self._latest_tick,
            session_opens=levels.session_opens(trading_day),
            previous_day_ohlc=levels.previous_day_ohlc(trading_day),
        )
