"""vo.research.fbm -- synthetic fractional Brownian motion with a known
Hurst exponent, the ground truth the estimator is calibrated against.

The generator is itself checked against theory here: if it does not
produce the autocovariance it claims, every calibration built on it is
worthless, so these are the tests that have to hold first."""

from __future__ import annotations

import statistics

import pytest

from vo.research.fbm import (
    FbmError,
    fgn_autocovariance,
    fractional_brownian_motion,
    fractional_gaussian_noise,
)


def _lag1_autocorrelation(series: list[float]) -> float:
    mean = statistics.mean(series)
    numerator = sum(
        (series[i] - mean) * (series[i + 1] - mean) for i in range(len(series) - 1)
    )
    denominator = sum((value - mean) ** 2 for value in series)
    return numerator / denominator


def test_autocovariance_at_lag_zero_is_unit_variance() -> None:
    for hurst in (0.2, 0.5, 0.8):
        assert fgn_autocovariance(0, hurst) == pytest.approx(1.0)


def test_autocovariance_matches_the_closed_form_at_lag_one() -> None:
    """gamma(1) = 0.5 * (2^2H - 2), straight from the definition."""
    for hurst in (0.2, 0.35, 0.5, 0.65, 0.8):
        expected = 0.5 * (2.0 ** (2.0 * hurst) - 2.0)
        assert fgn_autocovariance(1, hurst) == pytest.approx(expected)


def test_a_half_hurst_series_is_uncorrelated() -> None:
    """H=0.5 is ordinary Brownian motion: independent increments."""
    series = fractional_gaussian_noise(2000, 0.5, seed=1)

    assert _lag1_autocorrelation(series) == pytest.approx(0.0, abs=0.05)


def test_a_low_hurst_series_is_antipersistent() -> None:
    series = fractional_gaussian_noise(2000, 0.2, seed=1)
    expected = 0.5 * (2.0**0.4 - 2.0)

    assert _lag1_autocorrelation(series) == pytest.approx(expected, abs=0.06)


def test_a_high_hurst_series_is_persistent() -> None:
    series = fractional_gaussian_noise(2000, 0.8, seed=1)
    expected = 0.5 * (2.0**1.6 - 2.0)

    assert _lag1_autocorrelation(series) == pytest.approx(expected, abs=0.06)


def test_the_realised_variance_is_unit() -> None:
    series = fractional_gaussian_noise(2000, 0.7, seed=3)

    assert statistics.pvariance(series) == pytest.approx(1.0, abs=0.12)


def test_generation_is_deterministic_for_a_seed() -> None:
    """A calibration reference that is not reproducible is not a
    reference."""
    a = fractional_gaussian_noise(200, 0.6, seed=42)
    b = fractional_gaussian_noise(200, 0.6, seed=42)

    assert a == b


def test_different_seeds_give_different_paths() -> None:
    a = fractional_gaussian_noise(200, 0.6, seed=1)
    b = fractional_gaussian_noise(200, 0.6, seed=2)

    assert a != b


def test_the_module_does_not_use_the_global_random_state() -> None:
    """Seeding random globally must not change a generated path --
    otherwise anything else in the process can perturb a calibration."""
    import random

    random.seed(999)
    a = fractional_gaussian_noise(100, 0.6, seed=7)
    random.seed(1)
    b = fractional_gaussian_noise(100, 0.6, seed=7)

    assert a == b


def test_brownian_motion_is_the_cumulative_sum_of_its_noise() -> None:
    noise = fractional_gaussian_noise(50, 0.6, seed=5)
    path = fractional_brownian_motion(50, 0.6, seed=5, start=100.0, scale=2.0)

    assert path[0] == pytest.approx(100.0 + noise[0] * 2.0)
    assert path[-1] == pytest.approx(100.0 + sum(noise) * 2.0)


def test_an_out_of_range_hurst_is_refused() -> None:
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(FbmError, match="hurst must be in"):
            fractional_gaussian_noise(10, bad)


def test_a_nonsense_length_is_refused() -> None:
    with pytest.raises(FbmError, match="length must be"):
        fractional_gaussian_noise(0, 0.5)
