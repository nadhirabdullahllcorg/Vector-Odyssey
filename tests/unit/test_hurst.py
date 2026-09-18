"""Structure-function Hurst estimate (vo.observation.hurst)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.hurst import hurst_exponent

_INSTRUMENT = InstrumentId(platform="MT5", broker_server="x", broker_symbol="US100.n")
_T0 = datetime(2026, 9, 10, 7, 0, tzinfo=UTC)


def _bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            instrument_id=_INSTRUMENT,
            timeframe=Timeframe.M1,
            open_time_utc=_T0 + timedelta(minutes=i),
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            tick_volume=10,
            real_volume=0,
        )
        for i, c in enumerate(closes)
    ]


def test_straight_ramp_is_persistent_near_one():
    bars = _bars([100 + i for i in range(31)])
    h = hurst_exponent(bars, 30, period=20)
    assert h is not None and h > 0.8


def test_choppy_is_far_below_the_ramp():
    ramp = _bars([100 + i for i in range(31)])
    chop = _bars([100 + (i % 2) for i in range(31)])
    h_ramp = hurst_exponent(ramp, 30, period=20)
    h_chop = hurst_exponent(chop, 30, period=20)
    assert h_ramp is not None and h_chop is not None
    assert h_chop < h_ramp
    assert h_chop < 0.5


def test_none_before_enough_history():
    bars = _bars([100 + i for i in range(10)])
    assert hurst_exponent(bars, 9, period=20) is None


def test_flat_window_is_none_not_a_guess():
    bars = _bars([100.0] * 31)
    assert hurst_exponent(bars, 30, period=20) is None


def test_period_floor_is_enforced():
    with pytest.raises(ValueError, match="period must be >= 4"):
        hurst_exponent(_bars([100.0] * 10), 5, period=3)


# ── calibration (2026-09-18 audit, finding B1) ────────────────────────────
#
# What does this estimator read on series whose true Hurst exponent is
# known? Measured on synthetic data, pinned here so the scale every report
# quotes is documented and any estimator change is visible in a diff:
#
#   pure Gaussian random walk (true H = 0.5)  -> ~0.44 (0.40-0.47 across
#                                                 seeds and windows 20..200)
#   persistent drift (true H -> 1)            -> ~0.98
#   white noise around a level (anti-persistent) -> ~0.0 (NOT bounded to [0,1])
#
# So "Hurst below 0.5" on real data is not "below a random walk"; ~0.44 is.
# vo.telemetry.regime_validation.HURST_RANDOM_WALK_REFERENCE carries the
# reference the reports quote.


def _seeded_random(seed: int):  # type: ignore[no-untyped-def]
    import random

    return random.Random(seed)


def _synthetic(kind: str, n: int, seed: int = 7) -> list[float]:
    rng = _seeded_random(seed)
    closes: list[float] = []
    p = 20000.0
    for _ in range(n):
        if kind == "random_walk":
            p = p + rng.gauss(0.0, 5.0)
        elif kind == "trend":
            p = p + 2.0 + rng.gauss(0.0, 1.0)
        else:  # mean-reverting noise around a level
            p = 20000.0 + rng.gauss(0.0, 5.0)
        closes.append(p)
    return closes


def _mean_estimate(kind: str, period: int, seeds: tuple[int, ...] = (7, 11, 23)) -> float:
    kept: list[float] = []
    for seed in seeds:
        bars = _bars(_synthetic(kind, 4000, seed=seed))
        values = [hurst_exponent(bars, i, period=period) for i in range(period + 1, len(bars), 20)]
        kept.extend(v for v in values if v is not None)
    assert kept, "no estimates produced"
    return sum(kept) / len(kept)


@pytest.mark.parametrize("period", [20, 50, 100, 200])
def test_calibration_random_walk_reads_about_0_44_not_0_5(period: int) -> None:
    from vo.telemetry.regime_validation import HURST_RANDOM_WALK_REFERENCE

    estimate = _mean_estimate("random_walk", period)
    assert estimate == pytest.approx(HURST_RANDOM_WALK_REFERENCE, abs=0.05)
    assert estimate < 0.5  # the bias is real and downward -- do not "fix" this assert


def test_calibration_trend_reads_near_one() -> None:
    assert _mean_estimate("trend", 50) > 0.9


def test_calibration_mean_reverting_noise_reads_near_zero_and_is_not_bounded() -> None:
    # The structure-function slope goes to ~0 (and can dip below) for white
    # noise around a level: this estimator is a slope, not a bounded H.
    assert abs(_mean_estimate("mean_reverting", 50)) < 0.1
