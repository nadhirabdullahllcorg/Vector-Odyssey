"""vo.valco.lrx_calibration -- what CERR looks like in history.

Case list: empty and short datasets, missing ATR, zero denominators,
unconfigured thresholds, multiple and overlapping windows, failed and
partial and complete cycles, causal cutoff, deterministic replay, both
ER definitions preserved, expansion outside the boundary, retracement
after expansion, MSS after retracement, and reversal direction
consistency."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_calibration import (
    describe,
    funnel,
    observe_consolidations,
    observe_cycles,
    write_jsonl,
)
from vo.valco.lrx_cerr import CerrConfig
from vo.valco.lrx_consolidation import ConsolidationConfig
from vo.valco.lrx_displacement import (
    DisplacementConfig,
    ExpansionDirection,
    measure_displacement,
)
from vo.valco.lrx_levels import LevelKind, LevelSide, ReferenceLevel
from vo.valco.lrx_mss import (
    ConfirmationMethod,
    MssDirection,
    MssEvent,
    MssQualification,
    SwingSelection,
)
from vo.valco.lrx_sweep import SweepEvent

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01


def _at(minute: int) -> datetime:
    return _START + timedelta(minutes=minute)


def _bar(minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_at(minute),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _chop(count: int, centre: float = 20_000.0, half: float = 5.0, offset: int = 0):
    bars = []
    for i in range(count):
        up = i % 2 == 0
        bars.append(
            _bar(
                i + offset,
                open_=centre - half / 2 if up else centre + half / 2,
                high=centre + half,
                low=centre - half,
                close=centre + half / 2 if up else centre - half / 2,
            )
        )
    return bars


def _flat(count: int, price: float = 20_000.0):
    return [
        _bar(i, open_=price, high=price, low=price, close=price)
        for i in range(count)
    ]


def _loose() -> ConsolidationConfig:
    """Limits generous enough that the chop fixture qualifies -- chosen
    to exercise the runner, NOT proposed as production values."""
    return ConsolidationConfig(
        window_bars=10,
        minimum_bars=10,
        max_range_atr=5.0,
        max_net_move_atr=2.0,
        max_efficiency_ratio=0.9,
    )


def _sweep(returned_index: int, side: LevelSide) -> SweepEvent:
    buy_side = side is LevelSide.BUY_SIDE
    level = 20_050.0 if buy_side else 19_985.0
    return SweepEvent(
        sweep_id=f"SWEEP:{returned_index}:{side}",
        level=ReferenceLevel(
            kind=LevelKind.PREV_DAY_HIGH if buy_side else LevelKind.PREV_DAY_LOW,
            price=level,
            established_at=None,
            trading_day=_START.date(),
        ),
        side=side,
        penetration_index=returned_index - 1,
        penetration_price=level + 8.0 if buy_side else level - 8.0,
        penetration_distance=8.0,
        penetration_atr_multiple=0.8,
        closed_beyond=False,
        returned_index=returned_index,
        returned_at_utc=_at(returned_index),
        bars_beyond=1,
    )


def _mss(
    direction: MssDirection, *, break_index: int, sweep_id: str, displacement_id: str
) -> MssEvent:
    return MssEvent(
        mss_id=f"MSS:{break_index}",
        sweep_id=sweep_id,
        displacement_id=displacement_id,
        direction=direction,
        qualification=MssQualification.PASS,
        broken_swing_id="swing-a",
        broken_price=20_000.0,
        swing_event_time=_at(0),
        swing_confirmation_time=_at(1),
        selection_method=SwingSelection.MOST_RECENT,
        alternate_swing_id=None,
        distance_from_displacement_origin=20.0,
        distance_from_sweep=20.0,
        break_index=break_index,
        break_price=20_005.0,
        break_time=_at(break_index),
        break_distance_points=5.0,
        break_distance_atr=1.0,
        confirmation_method=ConfirmationMethod.CANDLE_CLOSE,
    )


def _history():
    """Chop, then a bullish leg clearing the range, then a pullback.
    Twenty consolidation bars so a 10-bar window qualifies well before
    the breakout."""
    bars = _chop(20)
    bars.append(_bar(20, open_=20_005.0, high=20_030.0, low=20_004.0, close=20_028.0))
    bars.append(_bar(21, open_=20_028.0, high=20_060.0, low=20_027.0, close=20_058.0))
    bars.append(_bar(22, open_=20_058.0, high=20_059.0, low=20_035.0, close=20_037.0))
    bars.append(_bar(23, open_=20_037.0, high=20_045.0, low=20_036.0, close=20_044.0))
    sweep = _sweep(20, LevelSide.SELL_SIDE)
    displacement = measure_displacement(
        bars, 21, sweep, DisplacementConfig(), tick_size=_TICK
    )
    assert displacement is not None
    assert displacement.direction is ExpansionDirection.UP
    return bars, sweep, displacement


# ── the distribution pass is threshold-free ───────────────────────────────


def test_measurements_are_collected_with_no_thresholds_configured() -> None:
    """The whole point: the distribution can be gathered before anyone
    has chosen a single limit."""
    bare = ConsolidationConfig()
    assert bare.configured is False

    rows = observe_consolidations(_chop(60), bare, tick_size=_TICK)

    assert len(rows) > 0
    assert all(r.qualification == "UNCONFIGURED" for r in rows)
    assert all(r.range_points > 0.0 for r in rows)
    assert all(r.kaufman_efficiency_ratio is not None for r in rows)


def test_both_efficiency_definitions_survive_into_the_dataset() -> None:
    """Collapsing them would make the resulting study unable to answer
    which one better separates compression from direction."""
    rows = observe_consolidations(_chop(60), ConsolidationConfig(), tick_size=_TICK)

    assert all(hasattr(r, "kaufman_efficiency_ratio") for r in rows)
    assert all(hasattr(r, "body_efficiency_ratio") for r in rows)
    assert not any(hasattr(r, "efficiency_ratio") for r in rows)


def test_an_empty_dataset_produces_no_rows() -> None:
    assert observe_consolidations([], ConsolidationConfig(), tick_size=_TICK) == ()
    assert (
        observe_cycles(
            [],
            consolidation_config=_loose(),
            cerr_config=CerrConfig(),
            tick_size=_TICK,
        )
        == ()
    )


def test_too_little_history_produces_no_rows() -> None:
    rows = observe_consolidations(_chop(3), _loose(), tick_size=_TICK)
    assert rows == ()


def test_missing_atr_leaves_relative_fields_none_and_is_counted() -> None:
    rows = observe_consolidations(
        _chop(60), ConsolidationConfig(atr_period=500), tick_size=_TICK
    )

    assert len(rows) > 0
    assert all(r.range_atr is None for r in rows)
    assert all(r.range_points > 0.0 for r in rows)

    stats = describe(rows, ["range_atr", "range_points"])
    by_field = {s.field: s for s in stats}
    assert by_field["range_atr"].count == 0
    assert by_field["range_atr"].missing == len(rows)
    assert by_field["range_points"].count == len(rows)


def test_a_zero_denominator_leaves_the_body_ratio_none() -> None:
    rows = observe_consolidations(_flat(40), ConsolidationConfig(), tick_size=_TICK)

    assert len(rows) > 0
    assert all(r.body_efficiency_ratio is None for r in rows)
    assert all(r.total_path_points == 0.0 for r in rows)


def test_overlapping_windows_are_kept_not_deduplicated() -> None:
    """The distribution of what the detector would SEE is the point, not
    a set of disjoint episodes."""
    rows = observe_consolidations(_chop(40), _loose(), tick_size=_TICK)

    assert len(rows) > 1
    spans = [(r.start_index, r.end_index) for r in rows]
    assert len(spans) == len(set(spans))
    assert any(
        a[1] >= b[0] for a, b in pairwise(spans)
    ), "windows should overlap"


def test_step_thins_the_sweep_without_changing_the_rows_it_keeps() -> None:
    every = observe_consolidations(_chop(60), _loose(), tick_size=_TICK)
    thinned = observe_consolidations(_chop(60), _loose(), tick_size=_TICK, step=5)

    assert len(thinned) < len(every)
    kept = {(r.start_index, r.end_index): r for r in every}
    for row in thinned:
        match = kept[(row.start_index, row.end_index)]
        assert row.range_points == match.range_points
        assert row.kaufman_efficiency_ratio == match.kaufman_efficiency_ratio

    with pytest.raises(ValueError, match="step"):
        observe_consolidations(_chop(20), _loose(), tick_size=_TICK, step=0)


# ── cycles: production semantics unchanged ────────────────────────────────


def test_unconfigured_consolidation_produces_no_cycles_and_that_is_the_finding() -> None:
    bars, _sweep_event, _leg = _history()

    rows = observe_cycles(
        bars,
        consolidation_config=ConsolidationConfig(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        tick_size=_TICK,
    )

    assert rows == ()


def test_unconfigured_retracement_leaves_cycles_stalled_in_expansion() -> None:
    """Nothing in the runner relaxes this. A cycle that cannot advance
    is recorded as a cycle that did not advance."""
    bars, sweep, displacement = _history()

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(),
        displacements=[displacement],
        sweeps=[sweep],
        tick_size=_TICK,
    )

    assert len(rows) == 1
    assert rows[0].reached_expansion is True
    assert rows[0].reached_retracement is False
    assert rows[0].final_state in {"EXPANSION", "INVALIDATED"}


def test_a_configured_candidate_advances_to_retracement() -> None:
    bars, sweep, displacement = _history()

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep],
        tick_size=_TICK,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.reached_expansion is True
    assert row.reached_retracement is True
    assert row.retracement_depth_points is not None
    assert row.retracement_depth_points > 0.0
    assert row.bars_to_expansion is not None
    assert row.bars_to_retracement is not None


def test_expansion_measurements_reference_the_breached_boundary() -> None:
    bars, sweep, displacement = _history()

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep],
        tick_size=_TICK,
    )

    row = rows[0]
    assert row.direction == "UP"
    assert row.boundary_breached is not None
    assert row.expansion_distance_points is not None
    assert row.expansion_distance_points > 0.0
    assert row.displacement_id == displacement.displacement_id


def test_a_full_cycle_reaches_reversal_and_records_its_lineage() -> None:
    bars, sweep, displacement = _history()
    reversal_sweep = _sweep(22, LevelSide.SELL_SIDE)
    mss = _mss(
        MssDirection.BULLISH,
        break_index=23,
        sweep_id=reversal_sweep.sweep_id,
        displacement_id=displacement.displacement_id,
    )

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep, reversal_sweep],
        mss_events=[mss],
        tick_size=_TICK,
    )

    row = rows[0]
    assert row.reached_reversal is True
    assert row.final_state == "REVERSAL_CONFIRMED"
    assert row.reversal_mss_id == mss.mss_id
    assert row.reversal_sweep_side == "SELL_SIDE"
    assert row.bars_to_reversal is not None
    assert row.reversal_confirmed_at is not None
    assert row.transition_path.endswith("REVERSAL_CONFIRMED")


def test_a_contradictory_reversal_is_recorded_as_a_failed_cycle() -> None:
    """Reversal direction consistency, observed rather than enforced by
    the runner -- the production machine decides, the runner records."""
    bars, sweep, displacement = _history()
    wrong_sweep = _sweep(22, LevelSide.BUY_SIDE)
    mss = _mss(
        MssDirection.BEARISH,
        break_index=23,
        sweep_id=wrong_sweep.sweep_id,
        displacement_id=displacement.displacement_id,
    )

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep, wrong_sweep],
        mss_events=[mss],
        tick_size=_TICK,
    )

    row = rows[0]
    assert row.reached_reversal is False
    assert row.final_state == "INVALIDATED"
    assert row.invalidation_reason == "CONTRADICTORY_DIRECTION"


def test_an_mss_before_the_retracement_does_not_confirm() -> None:
    """The ordering guard, exercised through the runner."""
    bars, sweep, displacement = _history()
    reversal_sweep = _sweep(22, LevelSide.SELL_SIDE)
    early = _mss(
        MssDirection.BULLISH,
        break_index=21,
        sweep_id=reversal_sweep.sweep_id,
        displacement_id=displacement.displacement_id,
    )

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep, reversal_sweep],
        mss_events=[early],
        tick_size=_TICK,
    )

    assert rows[0].reached_reversal is False


def test_failed_and_partial_cycles_stay_in_the_dataset() -> None:
    """Keeping only confirmed reversals would bias the sample toward
    setups that worked."""
    bars, sweep, displacement = _history()

    rows = observe_cycles(
        bars,
        consolidation_config=_loose(),
        cerr_config=CerrConfig(minimum_retracement_points=10.0),
        displacements=[displacement],
        sweeps=[sweep],
        tick_size=_TICK,
    )

    counts = funnel(rows)
    assert counts["cycles"] == len(rows)
    assert counts["cycles"] >= counts["reached_expansion"]
    assert counts["reached_expansion"] >= counts["reached_retracement"]
    assert counts["reached_retracement"] >= counts["reached_reversal"]
    assert any(key.startswith("final:") for key in counts)
    assert sum(v for k, v in counts.items() if k.startswith("final:")) == len(rows)


# ── causality and determinism ─────────────────────────────────────────────


def test_observations_ignore_bars_that_have_not_happened_yet() -> None:
    """Replaying a truncated series must produce identical rows for the
    part they share."""
    full = _chop(60)
    truncated = full[:40]

    long_run = observe_consolidations(full, _loose(), tick_size=_TICK)
    short_run = observe_consolidations(truncated, _loose(), tick_size=_TICK)

    overlapping = [r for r in long_run if r.end_index < len(truncated)]
    assert len(short_run) == len(overlapping)
    for later, earlier in zip(overlapping, short_run, strict=True):
        assert later == earlier


def test_replaying_the_same_history_twice_gives_identical_datasets() -> None:
    bars, sweep, displacement = _history()

    runs = [
        observe_cycles(
            bars,
            consolidation_config=_loose(),
            cerr_config=CerrConfig(minimum_retracement_points=10.0),
            displacements=[displacement],
            sweeps=[sweep],
            tick_size=_TICK,
        )
        for _ in range(5)
    ]

    assert all(run == runs[0] for run in runs)


# ── description, not optimisation ─────────────────────────────────────────


def test_describe_reports_quantiles_and_says_what_was_missing() -> None:
    rows = observe_consolidations(_chop(80), ConsolidationConfig(), tick_size=_TICK)

    stats = describe(rows, ["range_atr", "kaufman_efficiency_ratio"])

    assert len(stats) == 2
    for entry in stats:
        assert entry.count + entry.missing == len(rows)
        if entry.count:
            assert entry.minimum <= entry.p25 <= entry.median
            assert entry.median <= entry.p75 <= entry.maximum


def test_the_runner_suggests_no_thresholds_and_ranks_nothing() -> None:
    """Descriptive only. A module that picked whichever threshold
    produced the prettiest history would be an overfitting machine
    wearing a research coat."""
    import ast
    import inspect

    import vo.valco.lrx_calibration as module

    # An AST scan of what the module DEFINES, not a text scan: the
    # docstring says "profitability" precisely to state what this module
    # refuses to do, and a text scan would flag its own disclaimer. The
    # identical mistake was made once already, on a docstring naming
    # SwingEngine.
    tree = ast.parse(inspect.getsource(module))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    for name in defined:
        lowered = name.lower()
        for banned in ("profit", "pnl", "optimi", "maximi", "rank", "sharpe"):
            assert banned not in lowered, f"{name} looks like optimisation"

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for banned_module in ("vo.risk", "vo.execution.router", "vo.telemetry.live_dispatch"):
        assert banned_module not in imported, banned_module


# ── output ────────────────────────────────────────────────────────────────


def test_rows_round_trip_through_jsonl(tmp_path: Path) -> None:
    rows = observe_consolidations(_chop(40), _loose(), tick_size=_TICK)
    out = tmp_path / "nested" / "consolidations.jsonl"

    written = write_jsonl(rows, out)

    assert written == len(rows)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(rows)

    first = json.loads(lines[0])
    assert first["range_points"] == rows[0].range_points
    assert "kaufman_efficiency_ratio" in first
    assert "body_efficiency_ratio" in first
    assert not list(out.parent.glob("*.partial"))
