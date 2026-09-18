"""
Basic Regime Engine (vo.observation.regime), Phase 13 / gate G6.

Covers the value-type invariants, config loading, and -- the substance --
the state machine over a synthetic rise-then-fall tape: the Month 1
transition structure (never CONSOLIDATION -> RETRACEMENT/REVERSAL
directly), the append-only PULLBACK_UNRESOLVED -> resolution discipline
(RETRACEMENT / REVERSAL / -- as of 2026-09-18, per Lesson 1's own words --
CONSOLIDATION when the pullback stalls into containment), the [VO-H]
anticipation lean on every unresolved state, and no lookahead
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
from vo.observation.regime_config import build_regime_engine, load_regime_config
from vo.observation.swing_config import load_swing_config
from vo.observation.swings import SwingEngine, SwingLevel, SwingPoint, SwingStatus, SwingType

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


def test_build_regime_engine_defaults_methodology_version_from_config():
    """Fixed 2026-09-18, alongside the bodies-not-wicks boundary change:
    build_regime_engine used to silently ignore regime.yaml's own
    `version` field and always stamp a bare methodology_version=1,
    regardless of what the config file said -- so bumping regime.yaml's
    version (1 -> 2, the same day, for this exact fix) would never have
    actually reached a stamped RegimeState. Pinned directly: the default
    must come from the loaded config, and an explicit override must
    still win."""
    regime_cfg = load_regime_config(_REPO_ROOT / "config" / "settings" / "regime.yaml")
    swing_cfg = load_swing_config(_REPO_ROOT / "config" / "settings" / "swings.yaml")

    engine = build_regime_engine(regime_cfg, swing_cfg, tick_size=1.0)
    assert engine._methodology_version == regime_cfg.version

    overridden = build_regime_engine(
        regime_cfg, swing_cfg, tick_size=1.0, methodology_version=99
    )
    assert overridden._methodology_version == 99


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

    # A resolution is an append that supersedes a real PULLBACK_UNRESOLVED --
    # RETRACEMENT/REVERSAL, and (2026-09-18) CONSOLIDATION when a pullback
    # stalls into containment rather than resuming or breaking structure.
    for s in states:
        if s.regime in (RegimeType.RETRACEMENT, RegimeType.REVERSAL, RegimeType.CONSOLIDATION):
            # The engine's own initial CONSOLIDATION (set in __init__) is never
            # emitted as a record -- every CONSOLIDATION that IS emitted comes
            # from _resolve_consolidation, which always supersedes a real
            # PULLBACK_UNRESOLVED, so there is no first-record exception here.
            assert s.supersedes is not None
            superseded = engine.states.get(s.supersedes)
            assert superseded is not None
            assert superseded.regime is RegimeType.PULLBACK_UNRESOLVED

    # Evidence discipline: no bare labels -- every state carries evidence,
    # and once ER/Hurst have enough history, supporting features appear.
    assert all(s.evidence for s in states)
    assert any(s.supporting_features for s in states)


def test_unresolved_pullback_resolves_to_consolidation_on_failed_resumption():
    """Direct check of the exact boundary this fix adds -- same style as
    test_defining_broken_requires_a_close_not_a_bare_wick below: engine
    state is set directly rather than driven through the real SwingEngine,
    since test_machine_runs_and_respects_the_month1_invariants already
    covers the integration path. Grounded in Lesson 1's own words: "either
    it goes back to a consolidation again or it goes to a retracement" --
    a same-direction swing that tries to resume the trend but fails to
    reach a new extreme, with the defining swing still holding, is
    Lesson 1's third pullback outcome, not an indefinite wait."""
    engine = _make_engine()
    engine._regime = RegimeType.PULLBACK_UNRESOLVED
    engine._direction = RegimeDirection.UP
    engine._extreme_price = 110.0
    engine._defining_price = 100.0
    # A RETRACEMENT/REVERSAL has already resolved earlier in this
    # departure cycle -- required per the 2026-09-18 first-cycle
    # restriction (Lesson 2: "does not do consolidation expansion
    # consolidation" on the FIRST leg) for this stall to be allowed
    # to resolve to CONSOLIDATION at all. See
    # test_first_leg_pullback_does_not_stall_into_consolidation below
    # for the case where this is False.
    engine._resolved_since_consolidation = True
    unresolved = _state(
        object_id="unresolved-1",
        regime=RegimeType.PULLBACK_UNRESOLVED,
        direction=RegimeDirection.UP,
        anticipated_resolution=AnticipatedResolution.UNCLEAR,
    )
    engine.states.append(unresolved)
    engine._unresolved = unresolved

    current = _bar(5, 106.0)
    failed_retest = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-failed-retest",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=106.0,  # short of the 110.0 extreme -- resumption failed
        body_price=106.0,
        pivot_bar_id="bar-5",
        confirmed_at_bar_id="bar-6",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-5",
    )

    emitted = engine._unresolved_swing(failed_retest, current, er=0.4)

    assert len(emitted) == 1
    resolved = emitted[0]
    assert resolved.regime is RegimeType.CONSOLIDATION
    assert resolved.direction is None
    assert resolved.supersedes == "unresolved-1"
    assert engine.current_regime() is RegimeType.CONSOLIDATION
    assert engine._direction is None
    assert engine._extreme_price is None
    assert engine._defining_price is None

    transitions = list(engine.transitions.all())
    assert transitions[-1].from_state is RegimeType.PULLBACK_UNRESOLVED
    assert transitions[-1].to_state is RegimeType.CONSOLIDATION


def test_first_leg_pullback_does_not_stall_into_consolidation():
    """The 2026-09-18 first-cycle restriction, pinned directly: Lesson 2
    states flatly, and repeatedly, "it does not do consolidation
    expansion consolidation, that does not happen" -- the FIRST expansion
    leaving a consolidation must resolve through RETRACEMENT or REVERSAL,
    not stall straight back into CONSOLIDATION. Same setup as
    test_unresolved_pullback_resolves_to_consolidation_on_failed_resumption
    above, EXCEPT _resolved_since_consolidation is left False (the
    default -- no RETRACEMENT/REVERSAL has resolved yet in this cycle).
    The failed retest must leave the pullback unresolved, not resolve it
    to CONSOLIDATION."""
    engine = _make_engine()
    engine._regime = RegimeType.PULLBACK_UNRESOLVED
    engine._direction = RegimeDirection.UP
    engine._extreme_price = 110.0
    engine._defining_price = 100.0
    assert engine._resolved_since_consolidation is False  # the default
    unresolved = _state(
        object_id="unresolved-first-leg",
        regime=RegimeType.PULLBACK_UNRESOLVED,
        direction=RegimeDirection.UP,
        anticipated_resolution=AnticipatedResolution.UNCLEAR,
    )
    engine.states.append(unresolved)
    engine._unresolved = unresolved

    current = _bar(5, 106.0)
    failed_retest = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-failed-retest-first-leg",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=106.0,  # short of the 110.0 extreme, same as the sibling test
        body_price=106.0,
        pivot_bar_id="bar-5",
        confirmed_at_bar_id="bar-6",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-5",
    )

    emitted = engine._unresolved_swing(failed_retest, current, er=0.4)

    assert emitted == []
    assert engine.current_regime() is RegimeType.PULLBACK_UNRESOLVED
    assert engine._unresolved is unresolved  # unchanged, still the same record


def test_unresolved_pullback_still_resolves_to_retracement_on_real_resumption():
    """Same setup, but the swing DOES clear the extreme -- must still
    resolve to RETRACEMENT (then re-enter EXPANSION), unchanged by this
    fix. Guards against the failed-resumption branch swallowing the
    genuine-resumption case."""
    engine = _make_engine()
    engine._regime = RegimeType.PULLBACK_UNRESOLVED
    engine._direction = RegimeDirection.UP
    engine._extreme_price = 110.0
    engine._defining_price = 100.0
    engine._last_low = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-low-anchor",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.LOW,
        status=SwingStatus.CONFIRMED,
        price=100.0,
        body_price=100.0,
        pivot_bar_id="bar-3",
        confirmed_at_bar_id="bar-4",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-3",
    )
    unresolved = _state(
        object_id="unresolved-2",
        regime=RegimeType.PULLBACK_UNRESOLVED,
        direction=RegimeDirection.UP,
        anticipated_resolution=AnticipatedResolution.UNCLEAR,
    )
    engine.states.append(unresolved)
    engine._unresolved = unresolved

    current = _bar(5, 112.0)
    real_resumption = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-real-resumption",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=112.0,  # clears the 110.0 extreme
        body_price=112.0,
        pivot_bar_id="bar-5",
        confirmed_at_bar_id="bar-6",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=108.0,
        reversal_extreme_bar_id="bar-5",
    )

    emitted = engine._unresolved_swing(real_resumption, current, er=0.6)

    assert [s.regime for s in emitted] == [RegimeType.RETRACEMENT, RegimeType.EXPANSION]
    assert emitted[0].supersedes == "unresolved-2"
    assert engine.current_regime() is RegimeType.EXPANSION


def test_higher_high_and_lower_low_use_body_not_wick():
    """The 2026-09-18 bodies-not-wicks fix, pinned directly: Lesson 1
    states the consolidation range is "defined specifically by the
    bodies of the candles not the wicks." A swing whose WICK clears the
    prior swing's wick but whose BODY does not must NOT count as a
    higher-high/lower-low -- and the reverse (wick falls short, body
    clears) must count. Constructed directly rather than through the
    real SwingEngine, same style as the wick-vs-close REVERSAL test
    below, to pin the exact boundary rather than rely on the full-tape
    integration test to happen to exercise it."""
    engine = _make_engine()
    engine._prev_high = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-prev-high",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=110.0,
        body_price=108.0,
        pivot_bar_id="bar-1",
        confirmed_at_bar_id="bar-2",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-1",
    )

    wick_only_break = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-wick-only",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=111.0,  # wick clears the prior 110.0 wick...
        body_price=107.0,  # ...but the body falls short of the prior 108.0 body
        pivot_bar_id="bar-5",
        confirmed_at_bar_id="bar-6",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-5",
    )
    assert engine._is_higher_high(wick_only_break) is False

    body_break = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-body-break",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.HIGH,
        status=SwingStatus.CONFIRMED,
        price=109.0,  # wick falls short of the prior 110.0 wick...
        body_price=109.0,  # ...but the body clears the prior 108.0 body
        pivot_bar_id="bar-7",
        confirmed_at_bar_id="bar-8",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=104.0,
        reversal_extreme_bar_id="bar-7",
    )
    assert engine._is_higher_high(body_break) is True

    # Symmetric for the DOWN side (_is_lower_low / _prev_low).
    engine._prev_low = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-prev-low",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.LOW,
        status=SwingStatus.CONFIRMED,
        price=90.0,
        body_price=92.0,
        pivot_bar_id="bar-9",
        confirmed_at_bar_id="bar-10",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=96.0,
        reversal_extreme_bar_id="bar-9",
    )
    wick_only_low = SwingPoint(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        object_type="SWING",
        object_id="swing-wick-only-low",
        observed_at=_T0,
        recorded_at=_T0,
        methodology_version=1,
        level=SwingLevel.INTERNAL,
        swing_type=SwingType.LOW,
        status=SwingStatus.CONFIRMED,
        price=89.0,  # wick clears the prior 90.0 wick...
        body_price=93.0,  # ...but the body does not clear the prior 92.0 body
        pivot_bar_id="bar-11",
        confirmed_at_bar_id="bar-12",
        reversal_ticks=4,
        atr_ticks_at_pivot=2,
        reversal_extreme_price=96.0,
        reversal_extreme_bar_id="bar-11",
    )
    assert engine._is_lower_low(wick_only_low) is False


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
