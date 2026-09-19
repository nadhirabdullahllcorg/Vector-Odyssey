"""
VOEaRuntime -- the layer-7 glue that turns vo.core's ObservationPipeline
into a stream of RuntimeState updates and publishes them.

Layering note: vo.telemetry is layer 11, the top of the stack, and its own
module docstring (state.py) says it is "allowed to import all of
them [the other layers]" -- vo.core (layer 9) may never import
vo.telemetry back (tests/unit/test_architecture.py's
test_modules_do_not_import_higher_layers forbids it), so the wiring that
needs both a running pipeline and a RuntimeState has to live up here, not
in vo.core. vo.core.pipeline.PipelineSnapshot itself carries no
telemetry dependency; only this module turns one into a RuntimeState.

Phase 14/16 wiring note: VOEaRuntime optionally accepts a
TradeEvaluationHooks (vo.telemetry.trade_pipeline) via the `trade_hooks`
constructor argument. When unset (the default, and the only mode any
production config exercises today), poll_once() behaves exactly as it
did before this note was written -- no pipeline evaluation, no signal,
no trade. When a caller does attach hooks (a test, or Phase 18's real
strategy), poll_once() runs the Signal/AllocationSignal/RiskCheck/
TradeSignal pipeline after every snapshot that produced new data and
records the result on self.last_pipeline_result. This module does NOT
send anything to a broker: no vo.execution call is made here, by
design. A produced TradeSignal is logged and kept, never dispatched --
that wiring is Phase 18's, and is deliberately absent so "no live
orders" remains true by omission, not by a flag someone could miss.

Calendar cache (2026-09-19). VOEaRuntime also keeps
self.latest_calendar_events fresh by re-reading
config.calendar_wire_path() (VO_CalendarBridge.mq5's snapshot file) on
its own cadence (config.wire.calendar_refresh_seconds), independent of
whether any bar/tick/meta line arrived this poll -- the calendar file is
rewritten on a timer, not driven by price ticks. This is the SAME kind
of disposable, always-safe scaffolding as trade_hooks: nothing in this
module reads self.latest_calendar_events (ComplianceEngine.on_snapshot's
`upcoming_events` parameter is not called anywhere here), so its only
present effect is keeping a fresh EconomicEvent tuple available for
whenever Phase 18's real strategy attaches a compliance evaluation step
-- deciding what to DO with a blackout is explicitly not this module's
job, exactly as trade_hooks never dispatches a TradeSignal. A missing or
not-yet-written calendar file is not an error (see
read_calendar_snapshot's own docstring): the cache just stays empty
until the bridge produces one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from vo.core.config import EAConfig
from vo.core.pipeline import ObservationPipeline, PipelineSnapshot
from vo.interfaces.economic_events import EconomicEvent
from vo.interfaces.signals import TradeSignal
from vo.market.economic_calendar_ingestion import (
    QuarantinedCalendarLine,
    read_calendar_snapshot,
)
from vo.market.ingestion import JsonlTailer
from vo.telemetry.live_dispatch import DispatchResult, LiveDispatcher
from vo.telemetry.publisher import RuntimeStatePublisher
from vo.telemetry.state import CURRENT_SCHEMA_VERSION, ConnectionStatus, RuntimeState
from vo.telemetry.trade_pipeline import (
    TradeEvaluationHooks,
    TradePipelineResult,
    evaluate_trade_pipeline,
)
from vo.time.brokers import BrokerProfile, load_broker_profiles
from vo.time.sessions import SessionConfig, load_session_configs

logger = logging.getLogger("vo_ea.runtime")


def _detail_for(snapshot: PipelineSnapshot, quarantine_count: int) -> str:
    if snapshot.latest_bar is None:
        if snapshot.latest_tick is not None:
            tick = snapshot.latest_tick
            tick_part = f"latest tick bid={tick.bid} ask={tick.ask}"
        else:
            tick_part = "no bars or ticks observed yet"
        return f"{tick_part} (quarantined={quarantine_count})"

    bar = snapshot.latest_bar
    context = snapshot.latest_context
    session = context.session if context is not None else None
    is_rth = context.is_rth if context is not None else None

    return (
        f"{bar.instrument_id.broker_symbol} {bar.timeframe.canonical} "
        f"bars={snapshot.bar_count} last_close={bar.close} "
        f"session={session} rth={is_rth} quarantined={quarantine_count}"
    )


def snapshot_to_runtime_state(
    snapshot: PipelineSnapshot, ea_phase: str, quarantine_count: int
) -> RuntimeState:
    return RuntimeState(
        schema_version=CURRENT_SCHEMA_VERSION,
        connection=ConnectionStatus.CONNECTED,
        emitted_at=datetime.now(UTC),
        ea_phase=ea_phase,
        detail=_detail_for(snapshot, quarantine_count),
    )


class VOEaRuntime:
    """
    One process's whole observation loop: tail the wire files, feed the
    pipeline, publish a RuntimeState after any poll that produced new
    data. Owns nothing about MT5 -- it only reads files VO_Bridge.mq5
    (or anything else honoring the same wire format) has written, per
    SS7's resolution: Python brain, thin MQL5 bridge.
    """

    def __init__(
        self,
        config: EAConfig,
        broker_profiles: dict[str, BrokerProfile],
        session_configs: dict[str, SessionConfig],
        publisher: RuntimeStatePublisher | None = None,
        trade_hooks: TradeEvaluationHooks | None = None,
        dispatcher: LiveDispatcher | None = None,
    ) -> None:
        self.config = config
        self.pipeline = ObservationPipeline(broker_profiles, session_configs)
        self.publisher = publisher or RuntimeStatePublisher(
            config.telemetry.host, config.telemetry.port
        )
        self._bar_tailer = JsonlTailer(config.bar_wire_path())
        self._tick_tailer = JsonlTailer(config.tick_wire_path())
        self._meta_tailer = JsonlTailer(config.meta_wire_path())
        self._running = False
        # Optional, unset by default. See the module docstring's "Phase
        # 14/16 wiring note" -- attaching hooks is what a test, or Phase
        # 18's real strategy, does; nothing in this module attaches them
        # on its own.
        self.trade_hooks = trade_hooks
        self.last_pipeline_result: TradePipelineResult | None = None

        # The live-order seam. None (the default) keeps this runtime's
        # original posture exactly: a TradeSignal is produced, logged,
        # and never dispatched. Attaching a dispatcher is the single,
        # deliberate act that lets this process reach a broker.
        self.dispatcher = dispatcher
        self.last_dispatch: DispatchResult | None = None

        # Calendar cache -- see this module's own docstring, "Calendar
        # cache" section. Always on, unlike trade_hooks: this changes no
        # decision-path behavior, it is a read-and-cache side channel
        # nothing consumes yet.
        self._calendar_path = config.calendar_wire_path()
        self._calendar_refresh_seconds = config.wire.calendar_refresh_seconds
        self._last_calendar_refresh: datetime | None = None
        self.latest_calendar_events: tuple[EconomicEvent, ...] = ()
        self.calendar_quarantine: tuple[QuarantinedCalendarLine, ...] = ()

    def _poll_tailer(self, tailer: JsonlTailer) -> int:
        result = tailer.poll()
        for record in result.records:
            self.pipeline.ingest(record)
        for quarantined_line in result.quarantined:
            logger.warning(
                "quarantined wire line from %s: %s", tailer.path, quarantined_line.reason
            )
        return len(result.records) + len(result.quarantined)

    def _refresh_calendar_if_due(self, now: datetime) -> None:
        """Re-read the calendar snapshot at most once per
        calendar_refresh_seconds -- cheap enough to check on every
        poll_once call, but the actual file read (and any quarantine
        logging) only happens once the interval has elapsed. The first
        call always refreshes (self._last_calendar_refresh starts None).
        """
        if self._last_calendar_refresh is not None:
            elapsed = (now - self._last_calendar_refresh).total_seconds()
            if elapsed < self._calendar_refresh_seconds:
                return

        result = read_calendar_snapshot(self._calendar_path)
        self.latest_calendar_events = result.events
        self.calendar_quarantine = result.quarantined
        self._last_calendar_refresh = now

        for quarantined_line in result.quarantined:
            logger.warning(
                "quarantined calendar line from %s: %s",
                self._calendar_path,
                quarantined_line.reason,
            )

    async def poll_once(self) -> int:
        """One pass over all three wire files. Returns how many new
        lines (parsed or not) were seen -- a RuntimeState is only
        published when this is nonzero, so an idle EA does not flood the
        dashboard with identical heartbeats."""
        new_count = sum(
            self._poll_tailer(tailer)
            for tailer in (self._bar_tailer, self._tick_tailer, self._meta_tailer)
        )

        self._refresh_calendar_if_due(datetime.now(UTC))

        if new_count:
            snapshot = self.pipeline.snapshot()
            state = snapshot_to_runtime_state(
                snapshot, self.config.ea_phase, len(self.pipeline.quarantined)
            )
            await self.publisher.publish(state)
            logger.info("published RuntimeState: %s", state.detail)

            if self.trade_hooks is not None:
                result = evaluate_trade_pipeline(
                    snapshot, self.trade_hooks, symbol=self.pipeline.latest_symbol
                )
                self.last_pipeline_result = result
                if result is not None and result.trade_signal is not None:
                    self._handle_trade_signal(result.trade_signal)
                elif result is not None and result.signal is not None:
                    logger.info(
                        "trade pipeline evaluated, no TradeSignal (signal=%s "
                        "decision=%s)",
                        result.signal.object_id,
                        result.signal.decision,
                    )

        return new_count

    def _handle_trade_signal(self, trade_signal: TradeSignal) -> None:
        """Dispatch the signal if a dispatcher is attached; otherwise log
        it and stop, exactly as this runtime always did."""
        if self.dispatcher is None:
            logger.info(
                "TradeSignal produced (%s): no dispatcher attached, so it is "
                "recorded and not sent",
                trade_signal.object_id,
            )
            return

        symbol = self.pipeline.latest_symbol
        if symbol is None:
            # Position sizing already needed a Symbol to produce this
            # signal, so this should not happen -- but dispatching
            # without one would mean guessing the tick economics the
            # headroom gate depends on, and a guess there either blocks
            # good trades or admits one that eats past the halt point.
            logger.warning(
                "TradeSignal %s not dispatched: no Symbol observed on the wire yet",
                trade_signal.object_id,
            )
            return

        result = self.dispatcher.dispatch(
            trade_signal,
            symbol=symbol,
            upcoming_events=self.latest_calendar_events,
        )
        self.last_dispatch = result

        if result.sent:
            logger.info("TradeSignal %s SENT", trade_signal.object_id)
        else:
            logger.warning(
                "TradeSignal %s not sent (%s): %s",
                trade_signal.object_id,
                result.outcome,
                result.reason,
            )

    async def run_forever(self) -> None:
        await self.publisher.start()
        self._running = True

        if self.dispatcher is not None:
            # Commissioning happens BEFORE the first poll, so no signal
            # can ever be evaluated against an unproven execution path.
            report = self.dispatcher.commission()
            if report is None:
                logger.warning(
                    "a dispatcher is attached but no preflight was configured -- "
                    "nothing will be dispatched until one has passed"
                )
            else:
                logger.info("execution preflight: %s", report.summary())
                if not report.passed:
                    logger.error(
                        "execution preflight FAILED -- this session will observe "
                        "and evaluate, but will dispatch nothing"
                    )
        logger.info(
            "VO_EA v%s watching %s for broker_symbol=%s",
            self.config.ea_phase,
            self.config.wire.dir,
            self.config.broker_symbol,
        )
        try:
            while self._running:
                await self.poll_once()
                await asyncio.sleep(self.config.wire.poll_interval_seconds)
        finally:
            await self.publisher.stop()

    def stop(self) -> None:
        self._running = False


def build_runtime(config: EAConfig) -> VOEaRuntime:
    broker_profiles = load_broker_profiles(config.brokers_path)
    session_configs = load_session_configs(config.sessions_path)
    return VOEaRuntime(config, broker_profiles, session_configs)


async def run_vo_ea(config: EAConfig) -> None:
    runtime = build_runtime(config)
    await runtime.run_forever()
