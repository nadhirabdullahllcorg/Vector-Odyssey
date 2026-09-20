"""vo.valco.lrx_sensitivity -- is the CERR definition stable?

Covers: screening against the production qualification function rather
than a copy, determinism, the population bands, window-length profiling,
the body-ratio-above-one diagnostic, and that funnel selection is blind
to outcome."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_consolidation import ConsolidationQualification, EfficiencyMeasure
from vo.valco.lrx_sensitivity import (
    CandidateConfig,
    Population,
    body_er_anomalies,
    build_grid,
    classify_population,
    measure_windows,
    profile_window_length,
    screen_candidate,
    select_for_funnel,
)

_INSTRUMENT = InstrumentId(
    platform="MT5", broker_server="1xTrade-Server", broker_symbol="US100.n"
)
_START = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
_TICK = 0.01


def _bar(i: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        instrument_id=_INSTRUMENT,
        timeframe=Timeframe.M1,
        open_time_utc=_START + timedelta(minutes=i),
        open=open_,
        high=high,
        low=low,
        close=close,
        tick_volume=100,
        real_volume=0,
        spread=80,
    )


def _chop(count: int, centre: float = 20_000.0, half: float = 5.0) -> list[Bar]:
    bars = []
    for i in range(count):
        up = i % 2 == 0
        bars.append(
            _bar(
                i,
                open_=centre - half / 2 if up else centre + half / 2,
                high=centre + half,
                low=centre - half,
                close=centre + half / 2 if up else centre - half / 2,
            )
        )
    return bars


def _gapped(count: int = 60) -> list[Bar]:
    """Bars that jump between one close and the next open -- the shape
    that makes the body ratio exceed one."""
    bars = []
    price = 20_000.0
    for i in range(count):
        open_ = price + (40.0 if i % 4 == 0 else 0.0)  # a gap every fourth bar
        close = open_ + 1.0
        bars.append(_bar(i, open_=open_, high=close + 0.5, low=open_ - 0.5, close=close))
        price = close
    return bars


def _measure(bars, window_bars=10):
    return measure_windows(
        bars,
        window_bars=window_bars,
        minimum_bars=min(10, window_bars),
        atr_period=14,
        tick_size=_TICK,
    )


def _candidate(**overrides) -> CandidateConfig:
    base = {
        "window_bars": 10,
        "max_range_atr": 5.0,
        "max_net_move_atr": 2.0,
        "max_efficiency_ratio": 0.4,
        "efficiency_measure": EfficiencyMeasure.KAUFMAN,
    }
    return CandidateConfig(**(base | overrides))


def _screen(measurements, candidate):
    return screen_candidate(
        measurements, candidate, minimum_bars=10, atr_period=14
    )


# ── screening uses the production rule, not a copy ────────────────────────


def test_screening_agrees_with_the_production_qualification_function() -> None:
    """A screening routine with its own copy of the rule would
    eventually disagree with the detector it claims to study, and the
    disagreement would be invisible."""
    from vo.valco.lrx_consolidation import qualify_consolidation

    measurements = _measure(_chop(60))
    candidate = _candidate()
    config = candidate.to_consolidation_config(minimum_bars=10, atr_period=14)

    expected = sum(
        1
        for m in measurements
        if qualify_consolidation(m, config).qualification
        is ConsolidationQualification.CONSOLIDATION
    )

    assert _screen(measurements, candidate).qualifying == expected


def test_a_screen_records_the_whole_candidate() -> None:
    candidate = _candidate(max_range_atr=3.0, efficiency_measure=EfficiencyMeasure.BODY)
    result = _screen(_measure(_chop(60)), candidate)

    assert result.candidate == candidate
    assert "BODY" in candidate.label
    assert "r3" in candidate.label
    assert result.windows_measured == len(_measure(_chop(60)))


def test_screening_is_deterministic() -> None:
    measurements = _measure(_chop(60))
    runs = [_screen(measurements, _candidate()) for _ in range(5)]

    assert all(run == runs[0] for run in runs)


def test_a_tighter_limit_never_admits_more_windows() -> None:
    """Monotonicity. If it ever fails, a threshold is not doing what its
    name says."""
    measurements = _measure(_chop(60))
    counts = [
        _screen(measurements, _candidate(max_range_atr=limit)).qualifying
        for limit in (1.0, 2.0, 3.0, 5.0, 10.0)
    ]

    assert counts == sorted(counts)


# ── population bands are about size, never outcome ────────────────────────


def test_the_population_bands_split_on_size_alone() -> None:
    assert classify_population(0.0) is Population.EMPTY
    assert classify_population(0.05) is Population.SPARSE
    assert classify_population(25.0) is Population.USABLE
    assert classify_population(75.0) is Population.PERMISSIVE
    assert classify_population(99.0) is Population.DEGENERATE


def test_a_definition_admitting_almost_everything_is_degenerate() -> None:
    """Not a definition at all -- a description of "bars existed"."""
    measurements = _measure(_chop(60))
    permissive = _candidate(
        max_range_atr=1000.0, max_net_move_atr=1000.0, max_efficiency_ratio=1.0
    )

    result = _screen(measurements, permissive)
    assert result.qualifying_pct > 90.0
    assert result.population is Population.DEGENERATE


def test_an_impossible_definition_is_empty_not_an_error() -> None:
    result = _screen(_measure(_chop(60)), _candidate(max_range_atr=0.001))

    assert result.qualifying == 0
    assert result.population is Population.EMPTY
    assert result.median_range_atr is None


def test_funnel_selection_is_blind_to_outcome() -> None:
    """Selecting which candidates to examine by how good their results
    looked would be optimisation arrived at more slowly."""
    import ast
    import inspect

    import vo.valco.lrx_sensitivity as module

    source = inspect.getsource(module.select_for_funnel)
    for banned in ("profit", "pnl", "reversal", "best", "sort(key=lambda s: s.q"):
        assert banned not in source.lower(), banned

    tree = ast.parse(inspect.getsource(module))
    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for banned in ("optimise", "optimize", "rank_by_profit"):
        assert banned not in defined


def test_funnel_selection_spreads_across_the_grid_and_is_capped() -> None:
    measurements = _measure(_chop(200))
    grid = build_grid(
        window_bars=[10],
        range_atr=[3.0, 4.0, 5.0, 6.0, 7.0],
        net_move_atr=[1.0, 2.0, 3.0],
        efficiency=[0.2, 0.3],
        measures=[EfficiencyMeasure.KAUFMAN],
    )
    screens = [_screen(measurements, c) for c in grid]

    chosen = select_for_funnel(screens, limit=4)

    assert len(chosen) <= 4
    assert all(s.population is Population.USABLE for s in chosen)
    assert len({s.candidate.label for s in chosen}) == len(chosen)


def test_the_grid_is_a_deterministic_cartesian_product() -> None:
    grid = build_grid(
        window_bars=[5, 10],
        range_atr=[3.0, 4.0],
        net_move_atr=[1.0],
        efficiency=[0.2, 0.3],
        measures=[EfficiencyMeasure.KAUFMAN, EfficiencyMeasure.BODY],
    )

    assert len(grid) == 2 * 2 * 1 * 2 * 2
    assert len(set(grid)) == len(grid)
    assert grid == build_grid(
        window_bars=[5, 10],
        range_atr=[3.0, 4.0],
        net_move_atr=[1.0],
        efficiency=[0.2, 0.3],
        measures=[EfficiencyMeasure.KAUFMAN, EfficiencyMeasure.BODY],
    )


# ── window length is a confound until measured ────────────────────────────


def test_window_length_changes_the_distribution() -> None:
    """The reason the profile is mandatory: a limit like range_atr < 5
    can encode DURATION, since longer windows mechanically span more
    range."""
    bars = _chop(300)
    short = profile_window_length(_measure(bars, 5), window_bars=5)
    long = profile_window_length(_measure(bars, 20), window_bars=20)

    assert short.n > 0 and long.n > 0
    assert short.window_bars == 5
    assert long.window_bars == 20
    assert short.range_atr[1] is not None and long.range_atr[1] is not None
    assert long.range_atr[1] >= short.range_atr[1]


def test_a_profile_reports_quartiles_for_every_measure() -> None:
    profile = profile_window_length(_measure(_chop(120)), window_bars=10)

    for trio in (
        profile.range_atr,
        profile.net_move_atr,
        profile.kaufman_efficiency_ratio,
        profile.body_efficiency_ratio,
        profile.mean_range_atr,
    ):
        assert len(trio) == 3
        present = [v for v in trio if v is not None]
        assert present == sorted(present)


# ── the body ratio diagnostic ─────────────────────────────────────────────


def test_gaps_push_the_body_ratio_above_one() -> None:
    """Not an arithmetic error. The numerator crosses inter-bar gaps the
    denominator never counted -- so it is body-only directional
    efficiency, not bounded path efficiency."""
    measurements = _measure(_gapped(120))
    anomalies = body_er_anomalies(measurements)

    assert anomalies, "the gapped fixture should produce ratios above 1"
    for row in anomalies:
        assert row.body_efficiency_ratio > 1.0
        assert abs(row.net_move_points) > row.total_path_points


def test_kaufman_stays_bounded_where_the_body_ratio_does_not() -> None:
    """Kaufman's denominator is the same path as its numerator, walked
    step by step, so it cannot exceed one. That is what makes it the
    cleaner research variable."""
    for measurement in _measure(_gapped(120)):
        if measurement.kaufman_efficiency_ratio is not None:
            assert 0.0 <= measurement.kaufman_efficiency_ratio <= 1.0


def test_anomalies_are_reported_never_clamped() -> None:
    """A clamp would hide exactly the windows where the two measures
    disagree most, which are the interesting ones."""
    anomalies = body_er_anomalies(_measure(_gapped(120)), limit=3)

    assert len(anomalies) <= 3
    values = [a.body_efficiency_ratio for a in anomalies]
    assert values == sorted(values, reverse=True)
    assert max(values) > 1.0


def test_a_clean_sample_reports_no_anomalies() -> None:
    assert body_er_anomalies(_measure(_chop(120))) == ()


def test_measuring_windows_never_reads_past_the_series() -> None:
    bars = _chop(120)
    full = _measure(bars)
    truncated = _measure(bars[:80])

    overlapping = [m for m in full if m.end_index < 80]
    assert len(truncated) == len(overlapping)
    for later, earlier in zip(overlapping, truncated, strict=True):
        assert later == earlier


def test_measure_windows_rejects_a_nonsensical_step() -> None:
    with pytest.raises(ValueError):
        CandidateConfig(
            window_bars=1,
            max_range_atr=1.0,
            max_net_move_atr=1.0,
            max_efficiency_ratio=0.5,
            efficiency_measure=EfficiencyMeasure.KAUFMAN,
        ).to_consolidation_config(minimum_bars=10, atr_period=14)
