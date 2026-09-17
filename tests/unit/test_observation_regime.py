"""
Basic Regime Engine (vo.observation.regime), Phase 13 / gate G6.

Covers the value-type invariants, config loading, and -- the substance --
the state machine over a synthetic rise-then-fall tape: the Month 1
transition structure (never CONSOLIDATION -> RETRACEMENT/REVERSAL
directly), the append-only PULLBACK_UNRESOLVED -> resolution discipline,
the [VO-H] anticipation lean on every unresolved state, and no lookahead
(assert_no_lookahead -- the second real engine, after SwingEngine, to be
driven by Phase 9's harness).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vo.core.replay import ReplayHarness, assert_no_lookahead
from vo.interfaces import CanonicalRecordError
from vo.market import Bar, BarSequence, InstrumentId, Timeframe
from vo.observation.regime import (
    OBJECT_TYPE_REGIME_STATE,
    AnticipatedResolution,
    RegimeDirection,
    RegimeEngine,
    RegimeState,
    RegimeType,
)
from vo.observation.regime_config import load_regime_config
from vo.observation.swings import SwingEngine, SwingLevel

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bar(minute: int, center: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0 + timedelta(minutes=minute),
        open=center,
        high=center + 2,
        low=center - 2,
        close=center,
        tick_volume=10,
        real_volume=0,
    )


def _rise_then_fall_sequence() -> BarSequence:
    """Alternating local highs/lows on an up-drift then a steeper down-drift
    -- guarantees confirmable swings that trend up (expansion up, pullbacks)
    then break down (reversal), a rich regime tape."""
    seq = BarSequence()
    for i in range(48):
        trend = i * 4 if i < 24 else (24 * 4 - (i - 24) * 5)
        osc = 8 if i % 2 == 0 else -8
        seq = seq.append(_bar(i, 100 + trend + osc))
    return seq


def _make_engine() -> RegimeEngine:
    swing = SwingEngine(
        level=SwingLevel.INTERNAL, k=1, atr_period=1, atr_multiplier=0.5, tick_size=1.0
    )
    return RegimeEngine(swing_engine=swing, efficiency_ratio_period=10, hurst_period=20)


# ── value type invariants ────────────────────────────────────────────


def _state(**over) -> RegimeState:
    base = dict(
        object_type=OBJECT_TYPE_REGIME_STATE,
        object_id="id-1",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        regime=RegimeType.EXPANSION,
        direction=RegimeDirection.UP,
        confidence=0.7,
        evidence="x",
    )
    base.update(over)
    return RegimeState(**base)


def test_confidence_must_be_a_probability():
    with pytest.raises(CanonicalRecordError, match="confidence must be in"):
        _state(confidence=1.5)


def test_anticipation_only_valid_on_an_unresolved_state():
    with pytest.raises(CanonicalRecordError, match="anticipated_resolution is only valid"):
        _state(
            regime=RegimeType.EXPANSION,
            anticipated_resolution=AnticipatedResolution.RETRACEMENT,
        )


def test_a_pullback_may_carry_a_lean():
    state = _state(
        regime=RegimeType.PULLBACK_UNRESOLVED,
        anticipated_resolution=AnticipatedResolution.REVERSAL,
    )
    assert state.anticipated_resolution is AnticipatedResolution.REVERSAL


# ── config ────────────────────────────────────────────────────────────


def test_regime_config_loads_from_yaml():
    cfg = load_regime_config(_REPO_ROOT / "config" / "settings" / "regime.yaml")
    assert cfg.tier is SwingLevel.SWING
    assert cfg.efficiency_ratio_period >= 1
    assert cfg.hurst_period >= 4
    assert 0.0 <= cfg.anticipation.er_chop_threshold <= cfg.anticipation.er_trend_threshold <= 1.0


# ── the state machine ─────────────────────────────────────────────────


def test_engine_starts_in_consolidation():
    assert _make_engine().current_regime() is RegimeType.CONSOLIDATION


def test_machine_runs_and_respects_the_month1_invariants():
    seq = _rise_then_fall_sequence()
    engine = _make_engine()
    ReplayHarness(seq).run(engine)

    states = list(engine.states.all())
    transitions = list(engine.transitions.all())

    # It actually classified something, including at least one expansion.
    assert states, "engine emitted no regime states"
    assert any(s.regime is RegimeType.EXPANSION for s in states), "no expansion ever detected"

    # Month 1 structural constraint: you only leave CONSOLIDATION through
    # EXPANSION -- never straight into RETRACEMENT/REVERSAL.
    for tr in transitions:
        if tr.from_state is RegimeType.CONSOLIDATION:
            assert tr.to_state is RegimeType.EXPANSION, (
                f"illegal transition CONSOLIDATION -> {tr.to_state}"
            )

    # Every unresolved pullback carries a [VO-H] lean.
    for s in states:
        if s.regime is RegimeType.PULLBACK_UNRESOLVED:
            assert s.anticipated_resolution is not None

    # A resolution is an append that supersedes a real PULLBACK_UNRESOLVED.
    for s in states:
        if s.regime in (RegimeType.RETRACEMENT, RegimeType.REVERSAL):
            assert s.supersedes is not None
            superseded = engine.states.get(s.supersedes)
            assert superseded is not None
            assert superseded.regime is RegimeType.PULLBACK_UNRESOLVED

    # Evidence discipline: no bare labels -- every state carries evidence,
    # and once ER/Hurst have enough history, supporting features appear.
    assert all(s.evidence for s in states)
    assert any(s.supporting_features for s in states)


def test_no_lookahead():
    seq = _rise_then_fall_sequence()
    assert_no_lookahead(seq, _make_engine, cutoff=30)


# ── _defining_broken: close-required, not a bare wick (the fix this pins) ──


def test_defining_broken_requires_a_close_not_a_bare_wick():
    """Direct check of the exact boundary this fix changes -- a full-tape
    scenario would only prove *a* reversal happened somewhere, not pin
    the wick-vs-close distinction itself. Every OTHER transition in this
    machine already requires a confirmed SwingPoint (K-bar + ATR-filtered)
    before it fires; this one used to fire on any single wick with zero
    confirmation. Direction/defining_price are set directly since driving
    the real SwingEngine to exactly this boundary is what
    test_machine_runs_and_respects_the_month1_invariants already covers
    at the integration level."""
    engine = _make_engine()
    engine._direction = RegimeDirection.UP
    engine._defining_price = 100.0

    wick_only = Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0,
        open=102.0,
        high=103.0,
        low=98.0,  # wick pokes below the defining price...
        close=101.0,  # ...but the close does not
        tick_volume=10,
        real_volume=0,
    )
    assert engine._defining_broken(wick_only) is False

    real_break = Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0,
        open=102.0,
        high=103.0,
        low=98.0,
        close=99.0,  # the close itself is past the defining price
        tick_volume=10,
        real_volume=0,
    )
    assert engine._defining_broken(real_break) is True

    # Symmetric for the DOWN direction (defining_price broken from below).
    engine_down = _make_engine()
    engine_down._direction = RegimeDirection.DOWN
    engine_down._defining_price = 100.0

    wick_only_down = Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0,
        open=98.0,
        high=102.0,  # wick pokes above the defining price...
        low=97.0,
        close=99.0,  # ...but the close does not
        tick_volume=10,
        real_volume=0,
    )
    assert engine_down._defining_broken(wick_only_down) is False

    real_break_down = Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_T0,
        open=98.0,
        high=102.0,
        low=97.0,
        close=101.0,
        tick_volume=10,
        real_volume=0,
    )
    assert engine_down._defining_broken(real_break_down) is True
