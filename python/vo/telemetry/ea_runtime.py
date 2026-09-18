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
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from vo.core.config import EAConfig
from vo.core.pipeline import ObservationPipeline, PipelineSnapshot
from vo.market.ingestion import JsonlTailer
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

    def _poll_tailer(self, tailer: JsonlTailer) -> int:
        result = tailer.poll()
        for record in result.records:
            self.pipeline.ingest(record)
        for quarantined_line in result.quarantined:
            logger.warning(
                "quarantined wire line from %s: %s", tailer.path, quarantined_line.reason
            )
        return len(result.records) + len(result.quarantined)

    async def poll_once(self) -> int:
        """One pass over all three wire files. Returns how many new
        lines (parsed or not) were seen -- a RuntimeState is only
        published when this is nonzero, so an idle EA does not flood the
        dashboard with identical heartbeats."""
        new_count = sum(
            self._poll_tailer(tailer)
            for tailer in (self._bar_tailer, self._tick_tailer, self._meta_tailer)
        )

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
                    logger.info(
                        "TradeSignal produced (%s): no execution adapter is "
                        "wired to consume it -- Phase 18's job, not this "
                        "poll loop's",
                        result.trade_signal.object_id,
                    )
                elif result is not None and result.signal is not None:
                    logger.info(
                        "trade pipeline evaluated, no TradeSignal (signal=%s "
                        "decision=%s)",
                        result.signal.object_id,
                        result.signal.decision,
                    )

        return new_count

    async def run_forever(self) -> None:
        await self.publisher.start()
        self._running = True
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
