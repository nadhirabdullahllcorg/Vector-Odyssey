"""vo.valco.lrx_consolidation -- contained and directionally inefficient.

Case list: valid consolidation, insufficient bars, range too large, net
movement too large, efficiency too directional, missing ATR, zero
denominator, genuine zeros, threshold boundaries, both efficiency
measures, and the UNCONFIGURED default that refuses to guess."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.valco.lrx_consolidation import (
    ConsolidationConfig,
    ConsolidationQualification,
    EfficiencyMeasure,
    build_consolidation_event,
    detect_consolidation,
    measure_consolidation,
)

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


def _chop(count: int, centre: float = 20_000.0, half: float = 5.0) -> list[Bar]:
    """Alternating bars: real movement, no net progress. The shape
    consolidation is supposed to recognise."""
    bars: list[Bar] = []
    for i in range(count):
        up = i % 2 == 0
        open_ = centre - half / 2 if up else centre + half / 2
        close = centre + half / 2 if up else centre - half / 2
        bars.append(
            _bar(i, open_=open_, high=centre + half, low=centre - half, close=close)
        )
    return bars


def _trend(count: int, start: float = 20_000.0, step: float = 10.0) -> list[Bar]:
    """Every bar closes above the last. Small bars, perfectly
    directional -- the case that proves 'low ATR' is not consolidation."""
    bars: list[Bar] = []
    price = start
    for i in range(count):
        bars.append(
            _bar(
                i,
                open_=price,
                high=price + step + 1.0,
                low=price - 1.0,
                close=price + step,
            )
        )
        price += step
    return bars


def _flat(count: int, price: float = 20_000.0) -> list[Bar]:
    """Every bar opens and closes at the same price: the body path is
    zero, so the body efficiency ratio is undefined."""
    return [
        _bar(i, open_=price, high=price, low=price, close=price)
        for i in range(count)
    ]


def _config(**overrides) -> ConsolidationConfig:
    base = ConsolidationConfig(
        window_bars=10,
        minimum_bars=10,
        max_range_atr=5.0,
        max_net_move_atr=1.0,
        max_efficiency_ratio=0.4,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def _detect(bars, index=None, **overrides):
    return detect_consolidation(
        bars, len(bars) - 1 if index is None else index, _config(**overrides),
        tick_size=_TICK,
    )


# ── the default refuses to guess ──────────────────────────────────────────


def test_a_config_with_no_limits_is_unconfigured_not_a_pass() -> None:
    """A detector that called every window a consolidation because
    nobody had chosen thresholds yet would be worse than one that
    refuses to answer."""
    bare = ConsolidationConfig()

    assert bare.max_range_atr is None
    assert bare.max_net_move_atr is None
    assert bare.max_efficiency_ratio is None
    assert bare.configured is False

    verdict = detect_consolidation(_chop(30), 29, bare, tick_size=_TICK)

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.UNCONFIGURED
    assert verdict.is_consolidation is False
    # ...and the measurements are still there, which is the whole point.
    assert verdict.measurement.range_points > 0.0
    assert verdict.measurement.bar_count == 20


def test_measurements_are_produced_regardless_of_any_verdict() -> None:
    for config in (ConsolidationConfig(), _config(), _config(max_range_atr=0.001)):
        verdict = detect_consolidation(_chop(30), 29, config, tick_size=_TICK)
        assert verdict is not None
        assert verdict.measurement.range_high > verdict.measurement.range_low
        assert verdict.measurement.total_path_points > 0.0


# ── containment and inefficiency are separate requirements ────────────────


def test_choppy_contained_price_is_consolidation() -> None:
    verdict = _detect(_chop(30))

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.CONSOLIDATION
    assert verdict.reason is None


def test_a_quiet_trend_is_not_consolidation_even_though_bars_are_small() -> None:
    """The case that makes 'ATR is low' the wrong definition. These bars
    are small and price goes somewhere."""
    verdict = _detect(_trend(30))

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.NOT_CONSOLIDATION
    assert verdict.reason is not None


def test_range_containment_is_scale_invariant() -> None:
    """Forty-times-bigger bars produce the same range_atr, because ATR
    scales with them. That is the property ATR-relative containment
    exists for: "contained" means contained RELATIVE TO CURRENT
    VOLATILITY, not small in points. An earlier draft of this test
    expected the wide window to fail and was wrong about the detector,
    not the other way round."""
    tight = measure_consolidation(_chop(30, half=5.0), 29, _config(), tick_size=_TICK)
    wide = measure_consolidation(_chop(30, half=200.0), 29, _config(), tick_size=_TICK)

    assert tight is not None and wide is not None
    assert wide.range_points > tight.range_points * 10
    assert tight.range_atr == pytest.approx(wide.range_atr)

    for bars in (_chop(30, half=5.0), _chop(30, half=200.0)):
        verdict = detect_consolidation(bars, 29, _config(), tick_size=_TICK)
        assert verdict is not None
        assert verdict.qualification is ConsolidationQualification.CONSOLIDATION


def test_a_range_wide_relative_to_volatility_fails_containment() -> None:
    measurement = measure_consolidation(_chop(30), 29, _config(), tick_size=_TICK)
    assert measurement is not None and measurement.range_atr is not None

    verdict = _detect(_chop(30), max_range_atr=measurement.range_atr / 2.0)

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.NOT_CONSOLIDATION
    assert "range" in verdict.reason


def test_large_net_movement_fails_containment() -> None:
    verdict = _detect(_trend(30), max_efficiency_ratio=None, max_range_atr=None)

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.NOT_CONSOLIDATION
    assert "net move" in verdict.reason


def test_high_directional_efficiency_fails_on_its_own() -> None:
    verdict = _detect(_trend(30), max_range_atr=None, max_net_move_atr=None)

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.NOT_CONSOLIDATION
    assert "efficiency" in verdict.reason


def test_every_failing_limit_is_reported_not_just_the_first() -> None:
    verdict = _detect(_trend(30), max_range_atr=0.001)

    assert verdict is not None
    assert verdict.reason is not None
    assert verdict.reason.count(";") >= 1


# ── history, missing data, undefined values ───────────────────────────────


def test_insufficient_history_produces_no_measurement() -> None:
    """Never a partial-window figure, matching atr_ticks and
    efficiency_ratio."""
    bars = _chop(30)

    assert measure_consolidation(bars, 5, _config(), tick_size=_TICK) is None
    assert _detect(bars, index=5) is None
    assert _detect(bars, index=9) is not None


def test_an_out_of_range_index_produces_nothing() -> None:
    bars = _chop(30)

    for index in (-1, len(bars), len(bars) + 10):
        assert measure_consolidation(bars, index, _config(), tick_size=_TICK) is None


def test_missing_atr_makes_atr_relative_values_none_not_zero() -> None:
    bars = _chop(30)

    measurement = measure_consolidation(
        bars, 29, _config(atr_period=500), tick_size=_TICK
    )

    assert measurement is not None
    assert measurement.range_atr is None
    assert measurement.net_move_atr is None
    assert measurement.mean_range_atr is None
    # The point-denominated measurements are unaffected.
    assert measurement.range_points > 0.0
    assert measurement.mean_range_points > 0.0


def test_an_atr_limit_with_no_atr_is_unmeasurable_not_failed() -> None:
    verdict = _detect(_chop(30), atr_period=500)

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.UNMEASURABLE
    assert verdict.qualification is not ConsolidationQualification.NOT_CONSOLIDATION


def test_a_zero_body_path_leaves_the_body_ratio_undefined() -> None:
    """Every bar closed at its open, so the denominator is zero. 0/0 is
    undefined, not zero."""
    measurement = measure_consolidation(
        _flat(30), 29, _config(), tick_size=_TICK
    )

    assert measurement is not None
    assert measurement.total_path_points == 0.0
    assert measurement.body_efficiency_ratio is None


def test_an_undefined_efficiency_with_a_limit_set_is_unmeasurable() -> None:
    verdict = detect_consolidation(
        _flat(30),
        29,
        _config(
            efficiency_measure=EfficiencyMeasure.BODY,
            max_range_atr=None,
            max_net_move_atr=None,
        ),
        tick_size=_TICK,
    )

    assert verdict is not None
    assert verdict.qualification is ConsolidationQualification.UNMEASURABLE


def test_a_genuine_zero_stays_zero() -> None:
    """A flat window really does have zero range and zero net move.
    Missing became None in this codebase; measured zeros stay zero."""
    measurement = measure_consolidation(
        _flat(30), 29, _config(), tick_size=_TICK
    )

    assert measurement is not None
    assert measurement.range_points == 0.0
    assert measurement.net_move_points == 0.0
    assert measurement.mean_range_points == 0.0


# ── the two efficiency measures ───────────────────────────────────────────


def test_both_efficiency_ratios_are_always_recorded() -> None:
    measurement = measure_consolidation(_chop(30), 29, _config(), tick_size=_TICK)

    assert measurement is not None
    assert measurement.kaufman_efficiency_ratio is not None
    assert measurement.body_efficiency_ratio is not None


def test_the_configured_measure_selects_which_ratio_binds() -> None:
    measurement = measure_consolidation(_chop(30), 29, _config(), tick_size=_TICK)

    assert measurement is not None
    assert (
        measurement.efficiency(EfficiencyMeasure.KAUFMAN)
        == measurement.kaufman_efficiency_ratio
    )
    assert (
        measurement.efficiency(EfficiencyMeasure.BODY)
        == measurement.body_efficiency_ratio
    )


def test_the_default_measure_is_the_calibrated_one() -> None:
    """Kaufman is what the regime engine and entry timing already use.
    A second, differently-shaped 'efficiency' silently becoming the
    primary is the failure this default prevents."""
    assert ConsolidationConfig().efficiency_measure is EfficiencyMeasure.KAUFMAN


def test_a_trend_is_directional_under_both_measures() -> None:
    measurement = measure_consolidation(_trend(30), 29, _config(), tick_size=_TICK)

    assert measurement is not None
    assert measurement.kaufman_efficiency_ratio is not None
    assert measurement.body_efficiency_ratio is not None
    assert measurement.kaufman_efficiency_ratio > 0.9
    assert measurement.body_efficiency_ratio > 0.9


# ── boundaries ────────────────────────────────────────────────────────────


def test_a_value_exactly_at_a_limit_passes() -> None:
    """The limits are maxima: "no greater than". Exactly at the limit is
    within it, which is the opposite convention to the break detectors
    and is stated here so the asymmetry is deliberate rather than
    discovered later."""
    measurement = measure_consolidation(_chop(30), 29, _config(), tick_size=_TICK)
    assert measurement is not None and measurement.range_atr is not None

    exact = _detect(_chop(30), max_range_atr=measurement.range_atr)
    assert exact is not None
    assert exact.qualification is ConsolidationQualification.CONSOLIDATION

    just_under = _detect(_chop(30), max_range_atr=measurement.range_atr - 0.001)
    assert just_under is not None
    assert just_under.qualification is ConsolidationQualification.NOT_CONSOLIDATION


def test_window_and_minimum_bars_are_validated() -> None:
    with pytest.raises(ValueError, match="window_bars"):
        ConsolidationConfig(window_bars=1)
    with pytest.raises(ValueError, match="minimum_bars"):
        ConsolidationConfig(minimum_bars=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        ConsolidationConfig(window_bars=5, minimum_bars=10)


def test_the_window_never_reads_past_its_index() -> None:
    bars = _chop(40)

    full = measure_consolidation(bars, 29, _config(), tick_size=_TICK)
    truncated = measure_consolidation(bars[:30], 29, _config(), tick_size=_TICK)

    assert full == truncated


# ── the event ─────────────────────────────────────────────────────────────


def test_a_qualified_verdict_becomes_an_immutable_event() -> None:
    verdict = _detect(_chop(30))
    assert verdict is not None

    event = build_consolidation_event(verdict)

    assert event.range_high > event.range_low
    assert event.bar_count == 10
    assert event.qualification is ConsolidationQualification.CONSOLIDATION
    assert event.event_at_utc == event.end_time == verdict.measurement.end_time
    assert event.contains((event.range_high + event.range_low) / 2)
    assert event.escapes_above(event.range_high + 1.0)
    assert event.escapes_below(event.range_low - 1.0)
    assert not event.escapes_above(event.range_high)


def test_an_unqualified_verdict_cannot_become_an_event() -> None:
    """Otherwise the CERR machine could start a cycle from a window
    nobody classified."""
    for config in (ConsolidationConfig(), _config(max_range_atr=0.0001)):
        verdict = detect_consolidation(_chop(30), 29, config, tick_size=_TICK)
        assert verdict is not None
        with pytest.raises(ValueError, match="cannot build"):
            build_consolidation_event(verdict)


def test_the_event_carries_geometry_not_liquidity_levels() -> None:
    """range_high/range_low are geometry. Deciding what price is a level
    belongs to the Reference Level and Liquidity engines, and this module
    must not reach for them."""
    import ast
    import inspect

    import vo.valco.lrx_consolidation as module

    tree = ast.parse(inspect.getsource(module))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "vo.valco.lrx_levels" not in imported
    assert "vo.time.levels" not in imported
    assert "vo.market.levels" not in imported


def test_identical_inputs_produce_identical_output() -> None:
    bars = _chop(30)
    runs = [_detect(bars) for _ in range(5)]

    assert all(run is not None for run in runs)
    assert all(run == runs[0] for run in runs)
