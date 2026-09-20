"""vo.valco.lrx_replay -- generating the upstream event stream.

Case list: event generation from real config, ordering within a bar,
causal truncation, determinism, provenance preservation, and the
dependency failures that must be reported rather than guessed past."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.swing_config import load_swing_config
from vo.observation.swings import SwingEngine, SwingLevel
from vo.time.engine import VOTimeEngine
from vo.time.sessions import load_session_configs
from vo.valco.lrx_config import load_lrx_config
from vo.valco.lrx_displacement import DisplacementConfig, QualificationMode
from vo.valco.lrx_mss import MssConfig
from vo.valco.lrx_replay import (
    ReplayDependencyError,
    replay_config_from_lrx,
    replay_events,
)

_REPO = Path(__file__).resolve().parents[2]
_SESSIONS = _REPO / "config" / "settings" / "sessions.yaml"
_SWINGS = _REPO / "config" / "settings" / "swings.yaml"
_LRX = _REPO / "config" / "settings" / "lrx.yaml"

_START = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
_STEP_MINUTES = 15
_TICK = 0.01


def _instrument(symbol: str = "US100.n") -> InstrumentId:
    return InstrumentId(
        platform="MT5", broker_server="1xTrade-Server", broker_symbol=symbol
    )


def _bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    symbol: str = "US100.n",
) -> Bar:
    return Bar(
        instrument_id=_instrument(symbol),
        timeframe=Timeframe.M15,
        open_time_utc=_START + timedelta(minutes=_STEP_MINUTES * index),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _time_engine() -> VOTimeEngine:
    return VOTimeEngine(load_session_configs(_SESSIONS))


def _swing_factory():
    config = load_swing_config(_SWINGS)

    def factory(level: SwingLevel) -> SwingEngine:
        return SwingEngine.for_level(config, level, tick_size=_TICK)

    return factory


def _config():
    """Detector parameters from the SHIPPED lrx.yaml, with the
    displacement and MSS definitions stated explicitly -- each is a
    research choice and defaulting one silently would hide which
    definition produced a dataset."""
    return replay_config_from_lrx(
        load_lrx_config(_LRX),
        displacement=DisplacementConfig(mode=QualificationMode.ATR_RANGE),
        mss=MssConfig(),
    )


def _market(count: int = 288) -> list[Bar]:
    """A zig-zag over three days of M15 bars.

    Three days, not one, because PREV_DAY_HIGH/LOW/CLOSE and the opening
    range only exist once there IS a previous day -- a single session
    yields RTH_SETTLEMENT alone, and with nothing to raid the funnel is
    empty for reasons that have nothing to do with the code.

    Each leg's final bar spikes so its extreme is unique. Without that,
    a turn bar opening at the previous close shares that bar's high
    exactly, no strictly-greater pivot exists, and the swing engine
    confirms nothing at any amplitude.
    """
    bars: list[Bar] = []
    price = 20_000.0
    rising = True
    legs = (12, 7, 9, 6, 14, 8)
    index = 0
    leg_number = 0
    while index < count:
        length = legs[leg_number % len(legs)]
        leg_number += 1
        step = 30.0 if rising else -39.0
        for position in range(length):
            if index >= count:
                break
            open_ = price
            close = price + step
            spike = 3.0 if position == length - 1 else 1.0
            bars.append(
                _bar(
                    index,
                    open_=open_,
                    high=max(open_, close) + (spike if rising else 1.0),
                    low=min(open_, close) - (1.0 if rising else spike),
                    close=close,
                )
            )
            price = close
            index += 1
        rising = not rising
    return bars


def _replay(bars, config=None):
    return replay_events(
        bars,
        time_engine=_time_engine(),
        swing_engine_factory=_swing_factory(),
        config=config if config is not None else _config(),
        tick_size=_TICK,
    )


# ── it actually runs on the shipped configuration ─────────────────────────


def test_the_shipped_configuration_drives_a_replay() -> None:
    """The whole point of this stage: real session, swing and LRX config
    produce an event stream from bars, with no value invented here."""
    events = _replay(_market())

    assert events.bars_replayed == 288
    assert events.levels_seen > 0
    assert len(events.swings) > 0
    assert len(events.sweeps) > 0
    assert len(events.displacements) > 0
    assert len(events.mss_events) > 0


def test_the_event_stream_is_counted_and_reportable() -> None:
    counts = _replay(_market()).counts

    assert set(counts) == {
        "bars",
        "swings",
        "levels_seen",
        "sweeps",
        "displacements_measured",
        "displacements",
        "mss",
    }
    assert counts["bars"] == 288
    # The funnel can only narrow.
    assert counts["displacements_measured"] >= counts["displacements"]
    assert counts["sweeps"] >= counts["displacements"] >= counts["mss"]


def test_an_empty_series_produces_an_empty_stream() -> None:
    events = _replay([])

    assert events.bars_replayed == 0
    assert events.swings == ()
    assert events.sweeps == ()
    assert events.displacements == ()
    assert events.mss_events == ()


# ── provenance ────────────────────────────────────────────────────────────


def test_an_unqualified_leg_is_recorded_but_never_seeks_a_shift() -> None:
    """measure_displacement returns a FAIL carrying its numbers rather
    than a bare None, which is right -- near misses are the population a
    threshold study needs. But counting them as displacements makes the
    stage look as though it filters nothing, and a 1:1 sweep-to-
    displacement ratio on real data is what that looks like.

    So they are kept in `displacements` and excluded from the funnel and
    from the MSS search."""
    events = _replay(_market())
    by_id = {d.displacement_id: d for d in events.displacements}

    assert len(events.displacements) >= len(events.qualified_displacements)
    for mss in events.mss_events:
        assert by_id[mss.displacement_id].qualified, (
            "an unqualified leg produced an MSS"
        )


def test_every_displacement_names_the_raid_it_followed() -> None:
    events = _replay(_market())
    sweep_ids = {s.sweep_id for s in events.sweeps}

    for displacement in events.displacements:
        assert displacement.sweep_id in sweep_ids


def test_every_mss_names_its_displacement_its_sweep_and_its_swing() -> None:
    """The chain is followed by id, never reconstructed by proximity."""
    events = _replay(_market())
    sweep_ids = {s.sweep_id for s in events.sweeps}
    displacement_ids = {d.displacement_id for d in events.displacements}
    swing_ids = {s.swing_id for s in events.swings}

    for mss in events.mss_events:
        assert mss.sweep_id in sweep_ids
        assert mss.displacement_id in displacement_ids
        assert mss.broken_swing_id in swing_ids


def test_events_carry_the_bar_indices_they_came_from() -> None:
    bars = _market()
    events = _replay(bars)

    for sweep in events.sweeps:
        assert 0 <= sweep.penetration_index < len(bars)
        assert 0 <= sweep.returned_index < len(bars)
    for displacement in events.displacements:
        assert 0 <= displacement.start_index <= displacement.end_index < len(bars)
    for mss in events.mss_events:
        assert 0 <= mss.break_index < len(bars)


# ── causality ─────────────────────────────────────────────────────────────


def test_truncating_history_reproduces_the_same_events() -> None:
    """The load-bearing guarantee. Nothing at bar N may depend on a bar
    after N, so replaying only the first N bars must produce exactly the
    events the full replay produced up to N."""
    bars = _market()
    cut = 180

    full = _replay(bars)
    partial = _replay(bars[:cut])

    def before_cut(events, attr, index_of):
        return tuple(e for e in getattr(events, attr) if index_of(e) < cut)

    assert before_cut(full, "sweeps", lambda s: s.returned_index) == partial.sweeps
    assert (
        before_cut(full, "displacements", lambda d: d.end_index)
        == partial.displacements
    )
    assert before_cut(full, "mss_events", lambda m: m.break_index) == partial.mss_events


def test_expansion_is_searched_after_the_raid_not_on_its_own_bar() -> None:
    """measure_displacement refuses index <= returned_index, because the
    leg comes AFTER the raid. Asking only on the sweep's own bar
    therefore produced zero displacements forever -- silently, since
    "no displacement" looks exactly like "no qualifying expansion"."""
    events = _replay(_market())
    by_id = {s.sweep_id: s for s in events.sweeps}

    assert events.displacements
    for displacement in events.displacements:
        sweep = by_id[displacement.sweep_id]
        assert displacement.end_index > sweep.returned_index


def test_the_shift_is_searched_after_the_leg_not_on_its_own_bar() -> None:
    """The same reasoning one stage later: a break of a prior swing
    happens on some bar after the expansion, not on the bar that
    confirmed it."""
    events = _replay(_market())
    by_id = {d.displacement_id: d for d in events.displacements}

    assert events.mss_events
    for mss in events.mss_events:
        assert mss.break_index > by_id[mss.displacement_id].end_index


def test_an_mss_never_precedes_the_displacement_it_confirms() -> None:
    events = _replay(_market())
    by_id = {d.displacement_id: d for d in events.displacements}

    for mss in events.mss_events:
        displacement = by_id[mss.displacement_id]
        assert mss.break_index > displacement.start_index
        assert mss.break_time >= displacement.start_at_utc


def test_a_displacement_never_precedes_the_sweep_it_followed() -> None:
    events = _replay(_market())
    by_id = {s.sweep_id: s for s in events.sweeps}

    for displacement in events.displacements:
        sweep = by_id[displacement.sweep_id]
        assert displacement.end_index >= sweep.returned_index


def test_a_swing_is_never_available_before_it_occurred() -> None:
    for swing in _replay(_market()).swings:
        assert swing.available_at >= swing.occurred_at


def test_replaying_the_same_history_twice_is_identical() -> None:
    bars = _market()

    runs = [_replay(bars) for _ in range(3)]

    assert all(run.counts == runs[0].counts for run in runs)
    assert all(run.sweeps == runs[0].sweeps for run in runs)
    assert all(run.displacements == runs[0].displacements for run in runs)
    assert all(run.mss_events == runs[0].mss_events for run in runs)
    assert all(run.swings == runs[0].swings for run in runs)


# ── dependencies are reported, never guessed past ─────────────────────────


def test_an_unknown_instrument_is_reported_as_a_missing_dependency() -> None:
    """A fabricated session boundary would silently relocate every level
    in the dataset, so this must fail loudly."""
    bars = [
        _bar(
            i,
            open_=20_000.0,
            high=20_005.0,
            low=19_995.0,
            close=20_001.0,
            symbol="NOT_CONFIGURED",
        )
        for i in range(30)
    ]

    with pytest.raises(ReplayDependencyError, match="no session config"):
        _replay(bars)


def test_an_unsequenceable_series_is_reported_not_silently_dropped() -> None:
    """Replaying bars the pipeline itself would quarantine would
    describe history the engines never see."""
    bars = _market(40)
    duplicated = [*bars, bars[-1]]

    with pytest.raises(ReplayDependencyError, match="quarantined"):
        _replay(duplicated)


def test_the_replay_reimplements_no_detector() -> None:
    """Every event comes from the existing engines. A replay computing
    its own version of any of them would make historical results
    describe something other than what trades."""
    import ast
    import inspect

    import vo.valco.lrx_replay as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for required in (
        "vo.valco.lrx_sweep",
        "vo.valco.lrx_displacement",
        "vo.valco.lrx_mss",
        "vo.valco.lrx_swings",
        "vo.valco.lrx_levels",
        "vo.observation.swings",
        "vo.time.levels",
    ):
        assert required in imported, required

    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for banned in ("detect_sweep", "measure_displacement", "detect_mss", "three_bar"):
        assert banned not in defined, banned
