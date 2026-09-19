"""vo.research.hurst_validation -- does the estimator recover a Hurst it
was given? Trial counts here are small for speed; the real calibration
runs live in the research report, not the test suite."""

from __future__ import annotations

import pytest

from vo.research.hurst_validation import (
    RecoveryCell,
    bars_from_path,
    build_recovery_grid,
    is_monotonic,
    measure_recovery,
    render_recovery_table,
    separation,
)


def _cell(true_hurst: float, mean: float, stdev: float = 0.1) -> RecoveryCell:
    return RecoveryCell(
        true_hurst=true_hurst,
        window=128,
        trials=30,
        mean_estimate=mean,
        stdev_estimate=stdev,
        median_estimate=mean,
    )


def test_a_path_becomes_bars_the_real_estimator_can_read() -> None:
    bars = bars_from_path([100.0, 101.0, 102.0])

    assert len(bars) == 3
    assert bars[1].close == 101.0
    assert bars[0].open_time_utc < bars[1].open_time_utc


def test_recovery_reports_a_spread_not_just_a_point() -> None:
    """A mean without its spread invites reading a noisy number as a
    precise one."""
    cell = measure_recovery(true_hurst=0.5, window=64, trials=6)

    assert cell.trials == 6
    assert cell.stdev_estimate > 0.0
    assert 0.0 <= cell.mean_estimate <= 1.0


def test_the_estimator_orders_high_hurst_above_low() -> None:
    """Criterion 1. An estimator with the wrong ordering inverts every
    conclusion drawn from it, so this is the one that must never fail."""
    low = measure_recovery(true_hurst=0.3, window=128, trials=12, seed=11)
    high = measure_recovery(true_hurst=0.7, window=128, trials=12, seed=11)

    assert high.mean_estimate > low.mean_estimate


def test_bias_is_reported_signed() -> None:
    assert _cell(0.5, 0.44).bias == pytest.approx(-0.06)


def test_monotonicity_detects_a_broken_ordering() -> None:
    assert is_monotonic([_cell(0.3, 0.25), _cell(0.5, 0.44), _cell(0.7, 0.62)]) is True
    assert is_monotonic([_cell(0.3, 0.60), _cell(0.5, 0.44), _cell(0.7, 0.62)]) is False


def test_separation_is_measured_in_pooled_standard_deviations() -> None:
    wide = separation(_cell(0.3, 0.25, stdev=0.1), _cell(0.7, 0.65, stdev=0.1))
    narrow = separation(_cell(0.3, 0.25, stdev=0.4), _cell(0.7, 0.35, stdev=0.4))

    assert wide == pytest.approx(4.0)
    assert narrow < 1.0, "overlapping distributions cannot support a threshold"


def test_identical_distributions_separate_infinitely_rather_than_dividing_by_zero() -> None:
    assert separation(_cell(0.3, 0.2, stdev=0.0), _cell(0.7, 0.6, stdev=0.0)) == float("inf")


def test_a_grid_covers_every_pair() -> None:
    cells = build_recovery_grid(
        true_hursts=[0.3, 0.7], windows=[32, 64], trials=4
    )

    assert len(cells) == 4
    assert {(c.true_hurst, c.window) for c in cells} == {
        (0.3, 32), (0.7, 32), (0.3, 64), (0.7, 64)
    }


def test_the_table_reports_ordering_and_spread() -> None:
    table = render_recovery_table(
        [_cell(0.3, 0.25), _cell(0.5, 0.44), _cell(0.7, 0.62)]
    )

    assert "window = 128 bars" in table
    assert "monotonic: **yes**" in table
    assert "separation H=0.3 vs H=0.7" in table
    assert "stdev" in table


def test_too_few_trials_to_report_a_spread_is_refused() -> None:
    with pytest.raises(ValueError, match="trials must be >= 2"):
        measure_recovery(true_hurst=0.5, window=32, trials=1)
