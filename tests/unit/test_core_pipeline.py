"""
ObservationPipeline -- Phase 10's bridge -> canonical -> candle -> time ->
levels assembly, for one instrument, fed real wire records via
vo.time.mapping.resolve_record and the real, committed
config/settings/brokers.yaml / sessions.yaml (US100.n / 1xTrade-Server) --
same pattern test_time_mapping.py already uses.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from vo.core.pipeline import ObservationPipeline
from vo.market.records import (
    BarRecordV2,
    SourceCapabilitiesRecord,
    SymbolRecordV2,
    TickRecordV2,
)
from vo.time.brokers import load_broker_profiles
from vo.time.sessions import load_session_configs

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _broker_profiles():
    return load_broker_profiles(_REPO_ROOT / "config" / "settings" / "brokers.yaml")


def _session_configs():
    return load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")


def _pipeline() -> ObservationPipeline:
    return ObservationPipeline(_broker_profiles(), _session_configs())


def _bar(**overrides) -> BarRecordV2:
    fields = dict(
        timestamp=datetime(2026, 9, 10, 18, 49, 0),
        open=29132.30,
        high=29159.39,
        low=29131.81,
        close=29141.15,
        tick_volume=231,
        real_volume=0,
        spread=12,
        timeframe="PERIOD_M1",
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )
    fields.update(overrides)
    return BarRecordV2(**fields)


def _tick(**overrides) -> TickRecordV2:
    fields = dict(
        timestamp_server_ms=1_800_000_000_000,
        bid=29140.0,
        ask=29140.5,
        last=None,
        volume=1.0,
        volume_real=0.0,
        flags=6,
        seq=1,
        source_feed="live",
        platform="MT5",
        broker_server="1xTrade-Server",
        broker_symbol="US100.n",
    )
    fields.update(overrides)
    return TickRecordV2(**fields)


def test_empty_pipeline_snapshot_is_all_none() -> None:
    snapshot = _pipeline().snapshot()

    assert snapshot.instrument is None
    assert snapshot.bar_count == 0
    assert snapshot.latest_bar is None
    assert snapshot.latest_window is None


def test_ingesting_a_bar_grows_the_sequence_and_populates_time_context() -> None:
    pipeline = _pipeline()
    pipeline.ingest(_bar())

    snapshot = pipeline.snapshot()

    assert snapshot.bar_count == 1
    assert snapshot.latest_bar is not None
    assert snapshot.latest_bar.close == 29141.15
    assert snapshot.instrument is not None
    assert snapshot.instrument.broker_symbol == "US100.n"
    assert snapshot.latest_context is not None
    assert snapshot.latest_context.session is not None
    assert snapshot.latest_window is not None
    assert snapshot.latest_window.current == snapshot.latest_bar
    assert not pipeline.quarantined


def test_a_tick_updates_latest_tick_without_touching_the_bar_sequence() -> None:
    pipeline = _pipeline()
    pipeline.ingest(_tick())

    snapshot = pipeline.snapshot()

    assert snapshot.bar_count == 0
    assert snapshot.latest_bar is None
    assert snapshot.latest_tick is not None
    assert snapshot.latest_tick.bid == 29140.0


def test_a_symbol_record_is_tracked_but_does_not_appear_in_the_bar_snapshot() -> None:
    pipeline = _pipeline()
    pipeline.ingest(
        SymbolRecordV2(
            broker_symbol="US100.n",
            description="US Tech 100",
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            contract_size=1.0,
            platform="MT5",
            broker_server="1xTrade-Server",
        )
    )

    assert pipeline.latest_symbol is not None
    assert pipeline.latest_symbol.broker_symbol == "US100.n"
    assert pipeline.snapshot().bar_count == 0


def test_reference_levels_appear_once_a_second_trading_day_is_observed() -> None:
    pipeline = _pipeline()
    pipeline.ingest(_bar(timestamp=datetime(2026, 9, 10, 18, 49, 0), seq=1))

    snapshot = pipeline.snapshot()
    # Only one trading day observed so far -- no "previous day" yet.
    assert snapshot.previous_day_ohlc is None

    pipeline.ingest(
        _bar(
            timestamp=datetime(2026, 9, 11, 20, 0, 0),
            seq=2,
            open=29160.0,
            high=29210.0,
            low=29159.0,
            close=29200.0,
        )
    )

    snapshot = pipeline.snapshot()
    assert snapshot.bar_count == 2
    assert snapshot.previous_day_ohlc is not None
    assert snapshot.previous_day_ohlc.close == 29141.15
    assert snapshot.session_opens is not None


def test_an_unresolvable_broker_server_is_quarantined_not_raised() -> None:
    pipeline = _pipeline()
    pipeline.ingest(_bar(broker_server="Nobody-Has-Heard-Of-This-Server"))

    assert pipeline.snapshot().bar_count == 0
    assert len(pipeline.quarantined) == 1
    assert "No broker profile" in pipeline.quarantined[0].reason


def test_a_duplicate_bar_is_quarantined_not_raised() -> None:
    pipeline = _pipeline()
    pipeline.ingest(_bar())
    pipeline.ingest(_bar())  # identical bar -> BarSequenceViolation

    assert pipeline.snapshot().bar_count == 1
    assert len(pipeline.quarantined) == 1


def test_source_capabilities_record_is_ignored_not_quarantined() -> None:
    """A SourceCapabilitiesRecord describes what the feed can provide -- it is
    not an observation, so the pipeline skips it rather than quarantining it
    (otherwise it silently inflates VO_EA's quarantine count)."""
    pipeline = _pipeline()
    pipeline.ingest(
        SourceCapabilitiesRecord(
            platform="MT5",
            broker_server="1xTrade-Server",
            broker_symbol="US100.n",
            real_volume_available=False,
            tick_level_available=True,
        )
    )
    assert pipeline.quarantined == []
    assert len(pipeline.sequence) == 0
