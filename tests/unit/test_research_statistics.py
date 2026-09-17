"""Unit tests for vo.research.statistics -- the pure-stdlib percentile,
Mann-Whitney U, binomial test, and Wilson score interval primitives
shared across this project's research modules (Phase 13a's Validation
Report v1, and now Phase 15/17). Moved out of test_regime_validation.py
unchanged (same hand-verifiable numeric examples) when the functions
themselves moved out of vo.telemetry.regime_validation and into this
module -- a test relocation, not a rewrite."""

from __future__ import annotations

import pytest

from vo.research.statistics import (
    binomial_test,
    mann_whitney_u,
    percentile,
    wilson_score_interval,
)

# ── percentile ──────────────────────────────────────────────────────────


def test_percentile_matches_hand_computed_values() -> None:
    values = [float(v) for v in range(10, 101, 10)]  # 10..100
    assert percentile(values, 50) == pytest.approx(55.0)
    assert percentile(values, 25) == pytest.approx(32.5)


def test_percentile_falls_back_to_the_single_value_for_n_equals_1() -> None:
    assert percentile([42.0], 90) == 42.0


# ── Mann-Whitney U (formula verification) ────────────────────────────────


def test_mann_whitney_u_on_completely_separated_groups() -> None:
    result = mann_whitney_u([1, 2, 3], [4, 5, 6], label="t")
    assert result.u == pytest.approx(0.0)
    assert result.z == pytest.approx(-1.9639610121239315)
    assert result.p_value == pytest.approx(0.049534613435626706)


def test_mann_whitney_u_on_identical_groups_gives_a_large_p_value() -> None:
    result = mann_whitney_u([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], label="t")
    assert result.p_value is not None
    assert result.p_value > 0.5


def test_mann_whitney_u_handles_empty_group() -> None:
    result = mann_whitney_u([], [1, 2, 3], label="t")
    assert result.z is None
    assert result.p_value is None


# ── binomial test / Wilson interval (formula verification) ──────────────


def test_binomial_test_matches_hand_computed_values() -> None:
    result = binomial_test(55, 100, 0.5)
    assert result is not None
    assert result.z == pytest.approx(0.9)
    assert result.p_value == pytest.approx(0.36812025069351906)


def test_wilson_score_interval_matches_hand_computed_values() -> None:
    interval = wilson_score_interval(55, 100)
    assert interval is not None
    low, high = interval
    assert low == pytest.approx(0.45244602997442135)
    assert high == pytest.approx(0.6438546202048803)


def test_wilson_score_interval_none_for_zero_n() -> None:
    assert wilson_score_interval(0, 0) is None
