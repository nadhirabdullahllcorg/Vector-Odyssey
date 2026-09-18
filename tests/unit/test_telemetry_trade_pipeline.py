"""
vo.telemetry.trade_pipeline -- Phase 14's Signal/AllocationSignal/
RiskCheck/TradeSignal pipeline wired into a PipelineSnapshot, plus
VOEaRuntime's optional trade_hooks seam (vo.telemetry.ea_runtime).

evaluate_trade_pipeline() is exercised against a real ObservationPipeline
fed real wire records (same pattern as test_core_pipeline.py, using the
real committed brokers.yaml/sessions.yaml), never a fabricated
PipelineSnapshot -- InstrumentId.key's exact shape matters here (the
object_id-generation convention this module follows), so it should come
from the real pipeline, not be hand-typed.

VOEaRuntime's own tests confirm the "plumbing only" promise directly:
trade_hooks defaults to None, and poll_once() with no new wire data never
touches the pipeline evaluation at all -- the one behavior this whole
segment's wiring must not change for the only mode any production config
exercises today.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from vo.core.config import EAConfig, LoggingConfig, TelemetryConfig, WireConfig
from vo.core.pipeline import ObservationPipeline
from vo.interfaces.decisions import Decision, Direction
from vo.interfaces.signals import StrategyOutput
from vo.market.account import AccountState
from vo.market.records import BarRecordV2
from vo.market.symbol import Symbol
from vo.risk.risk_config import RiskConfig
from vo.telemetry.ea_runtime import VOEaRuntime
from vo.telemetry.trade_pipeline import TradeEvaluationHooks, evaluate_trade_pipeline
from vo.time.brokers import load_broker_profiles
from vo.time.sessions import load_session_configs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 9, 18, 14, 30, tzinfo=UTC)


def _broker_profiles():
    return load_broker_profiles(_REPO_ROOT / "config" / "settings" / "brokers.yaml")


def _session_configs():
    return load_session_configs(_REPO_ROOT / "config" / "settings" / "sessions.yaml")


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


def _snapshot_with_instrument():
    pipeline = ObservationPipeline(_broker_profiles(), _session_configs())
    pipeline.ingest(_bar())
    return pipeline.snapshot()


def _empty_snapshot():
    return ObservationPipeline(_broker_profiles(), _session_configs()).snapshot()


def _account(**overrides):
    defaults = dict(
        login=130695,
        name="Test",
        server="1xTrade-Server",
        currency="USD",
        balance=10_000.0,
        equity=10_000.0,
        profit=0.0,
        margin=0.0,
        margin_free=10_000.0,
        margin_level=None,
        leverage=100,
        trade_allowed=True,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def _symbol(**overrides):
    defaults = dict(
        broker_symbol="US100.n",
        description="NASDAQ 100",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=0.01,
        contract_size=10.0,
        source="MT5",
    )
    defaults.update(overrides)
    return Symbol(**defaults)


def _risk_config(**overrides):
    defaults = dict(
        version=1,
        risk_per_trade_fraction=0.01,
        min_volume=0.01,
        max_volume=5.0,
        volume_step=0.01,
        max_open_positions=1,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def _proposal_output(**overrides):
    defaults = dict(
        decision=Decision.SIGNAL_PROPOSED,
        direction=Direction.LONG,
        reference_price=29140.0,
        stop_price=29100.0,
        rationale="test proposal",
    )
    defaults.update(overrides)
    return StrategyOutput(**defaults)


def _hooks(propose, **overrides):
    defaults = dict(
        strategy_id="disposable_v0",
        strategy_version="0.1.0",
        propose=propose,
        account_state=lambda: _account(),
        open_position_count=lambda: 0,
        risk_config=_risk_config(),
    )
    defaults.update(overrides)
    return TradeEvaluationHooks(**defaults)


def test_no_instrument_returns_none_and_never_calls_propose():
    calls: list[object] = []

    def propose(snapshot):
        calls.append(snapshot)
        return _proposal_output()

    result = evaluate_trade_pipeline(_empty_snapshot(), _hooks(propose), symbol=_symbol())

    assert result is None
    assert calls == []


def test_propose_returning_none_means_nothing_to_evaluate_this_bar():
    result = evaluate_trade_pipeline(
        _snapshot_with_instrument(), _hooks(lambda snapshot: None), symbol=_symbol()
    )
    assert result is None


def test_full_happy_path_produces_a_trade_signal():
    result = evaluate_trade_pipeline(
        _snapshot_with_instrument(),
        _hooks(lambda snapshot: _proposal_output()),
        symbol=_symbol(),
    )

    assert result is not None
    assert result.signal.decision is Decision.SIGNAL_PROPOSED
    assert result.allocation.approved is True
    assert result.risk_check is not None
    assert result.risk_check.approved is True
    assert result.trade_signal is not None
    assert result.trade_signal.direction is Direction.LONG
    assert result.trade_signal.entry_reference_price == 29140.0
    assert result.trade_signal.stop_price == 29100.0
    # object_ids trace back to the real InstrumentId.key, not a stray
    # str(InstrumentId) default repr.
    assert result.signal.object_id.startswith("MT5:1xTrade-Server:US100.n:SIGNAL:")


def test_no_symbol_gap_still_produces_signal_and_allocation_but_no_risk_check():
    result = evaluate_trade_pipeline(
        _snapshot_with_instrument(),
        _hooks(lambda snapshot: _proposal_output()),
        symbol=None,
    )

    assert result is not None
    assert result.signal is not None
    assert result.allocation.approved is True
    assert result.risk_check is None
    assert result.trade_signal is None


def test_a_no_trade_output_produces_a_signal_but_never_a_trade_signal():
    output = _proposal_output(
        decision=Decision.NO_TRADE,
        direction=Direction.NEUTRAL,
        reference_price=None,
        stop_price=None,
    )
    result = evaluate_trade_pipeline(
        _snapshot_with_instrument(), _hooks(lambda snapshot: output), symbol=_symbol()
    )

    assert result is not None
    assert result.allocation.approved is False
    assert result.risk_check is not None
    assert result.risk_check.approved is False
    assert result.trade_signal is None


def test_account_not_trade_allowed_is_rejected_before_risk():
    result = evaluate_trade_pipeline(
        _snapshot_with_instrument(),
        _hooks(
            lambda snapshot: _proposal_output(),
            account_state=lambda: _account(trade_allowed=False),
        ),
        symbol=_symbol(),
    )

    assert result is not None
    assert result.allocation.approved is False
    assert result.risk_check is not None
    assert result.risk_check.approved is False
    assert result.trade_signal is None


# ── VOEaRuntime's optional trade_hooks seam ─────────────────────────────


def _ea_config(tmp_path: Path) -> EAConfig:
    return EAConfig(
        broker_symbol="US100.n",
        wire=WireConfig(dir=tmp_path, poll_interval_seconds=1.0),
        brokers_path=_REPO_ROOT / "config" / "settings" / "brokers.yaml",
        sessions_path=_REPO_ROOT / "config" / "settings" / "sessions.yaml",
        telemetry=TelemetryConfig(host="127.0.0.1", port=8765),
        logging=LoggingConfig(path=tmp_path / "vo_ea.log", level="INFO"),
        ea_phase="16",
    )


def _runtime(tmp_path: Path, **kwargs) -> VOEaRuntime:
    config = _ea_config(tmp_path)
    return VOEaRuntime(
        config,
        load_broker_profiles(config.brokers_path),
        load_session_configs(config.sessions_path),
        **kwargs,
    )


def test_trade_hooks_defaults_to_none_and_so_does_last_pipeline_result(tmp_path: Path):
    runtime = _runtime(tmp_path)
    assert runtime.trade_hooks is None
    assert runtime.last_pipeline_result is None


def test_runtime_accepts_and_stores_explicit_trade_hooks(tmp_path: Path):
    hooks = _hooks(lambda snapshot: None)
    runtime = _runtime(tmp_path, trade_hooks=hooks)
    assert runtime.trade_hooks is hooks


def test_poll_once_with_no_new_wire_data_never_touches_the_pipeline(tmp_path: Path):
    """No wire files exist under tmp_path, so JsonlTailer.poll() reports
    zero new records for all three tailers -- new_count stays 0, and per
    poll_once's own guard, trade-pipeline evaluation (and the publisher)
    are never reached, hooks or no hooks."""
    calls: list[object] = []
    hooks = _hooks(lambda snapshot: calls.append(snapshot) or None)
    runtime = _runtime(tmp_path, trade_hooks=hooks)

    async def body() -> int:
        return await runtime.poll_once()

    new_count = asyncio.run(asyncio.wait_for(body(), timeout=5.0))

    assert new_count == 0
    assert calls == []
    assert runtime.last_pipeline_result is None
