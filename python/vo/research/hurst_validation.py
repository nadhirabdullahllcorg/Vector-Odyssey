"""
Does VO's Hurst estimator recover a Hurst exponent it is given?

This is the calibration the 2026-09-18 audit (B1) started and could not
finish. That pass compared the estimator against a plain random walk --
one point, H=0.5 -- found ~0.44, and recorded the offset. A single point
cannot distinguish "biased low everywhere" from "compressed toward the
middle" from "fine above 0.5 and broken below", and those three call for
completely different responses.

Here the estimator is run against synthetic series whose true H is known
by construction (vo.research.fbm), across a grid of H values and window
lengths. What comes out is a recovery table: for each (true H, window),
the mean estimate, its spread, and the bias. That table is what selects
`hurst_period` -- a window chosen because the estimator demonstrably
recovers known values there, rather than a number picked and hoped over.

WHAT COUNTS AS USABLE, stated before looking at any result so the bar
cannot move to fit what turns up:

  1. MONOTONIC. Estimates must rise with true H. An estimator whose
     ordering is wrong is worse than useless -- it inverts conclusions.
  2. SEPARATING. Trending (H=0.7) and mean-reverting (H=0.3) inputs must
     produce distributions that do not substantially overlap. If they
     overlap, no threshold placed between them can mean anything.
  3. Bias is acceptable if it is CONSISTENT. A known, stable offset can
     be corrected or simply read around. A bias that changes sign or
     magnitude across the range cannot.

Note the bar is deliberately NOT "unbiased". Every short-series Hurst
estimator is biased; demanding unbiasedness would reject all of them and
teach nothing. Ordering and separation are what a regime reading actually
needs.

No lookahead concern applies: these are synthetic series evaluated whole,
not a live decision path. This module is research tooling and nothing
imports it from anywhere near a trade.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from vo.market.bar import Bar
from vo.market.identity import InstrumentId
from vo.market.timeframe import Timeframe
from vo.observation.hurst import hurst_exponent
from vo.research.fbm import fractional_brownian_motion

_SYNTHETIC_INSTRUMENT = InstrumentId(
    platform="SYNTHETIC", broker_server="fbm", broker_symbol="FBM"
)
_SYNTHETIC_TF = Timeframe.from_mt5("PERIOD_M1")
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def bars_from_path(path: Sequence[float]) -> list[Bar]:
    """Wrap a synthetic price path in Bars so the real estimator can be
    called unchanged. High/low are set from the close with a token
    spread: the structure-function estimator reads closes only, and
    inventing intrabar shape would be adding information the synthetic
    process never had."""
    bars: list[Bar] = []
    for i, close in enumerate(path):
        bars.append(
            Bar(
                instrument_id=_SYNTHETIC_INSTRUMENT,
                timeframe=_SYNTHETIC_TF,
                open_time_utc=_EPOCH + timedelta(minutes=i),
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                tick_volume=1,
                real_volume=0,
                spread=1,
            )
        )
    return bars


@dataclass(frozen=True, slots=True)
class RecoveryCell:
    """How the estimator did at one (true H, window) pair."""

    true_hurst: float
    window: int
    trials: int
    mean_estimate: float
    stdev_estimate: float
    median_estimate: float

    @property
    def bias(self) -> float:
        return self.mean_estimate - self.true_hurst


def measure_recovery(
    *,
    true_hurst: float,
    window: int,
    trials: int,
    series_length: int | None = None,
    seed: int = 0,
) -> RecoveryCell:
    """Run the estimator `trials` times on independent synthetic paths of
    known `true_hurst`, reading at the final bar over `window`.

    Each trial uses its own seed, derived from `seed`, so a whole grid is
    reproducible while no two cells share a path.
    """
    if trials < 2:
        raise ValueError(f"trials must be >= 2 to report a spread, got {trials}")

    length = series_length if series_length is not None else window + 1
    if length < window + 1:
        raise ValueError(
            f"series_length {length} is too short for a window of {window}"
        )

    estimates: list[float] = []
    for trial in range(trials):
        path = fractional_brownian_motion(
            length, true_hurst, seed=seed * 100_003 + trial, start=20_000.0, scale=5.0
        )
        estimate = hurst_exponent(bars_from_path(path), length - 1, period=window)
        if estimate is not None:
            estimates.append(estimate)

    if len(estimates) < 2:
        raise ValueError(
            f"estimator returned too few values at H={true_hurst}, window={window}"
        )

    return RecoveryCell(
        true_hurst=true_hurst,
        window=window,
        trials=len(estimates),
        mean_estimate=statistics.mean(estimates),
        stdev_estimate=statistics.stdev(estimates),
        median_estimate=statistics.median(estimates),
    )


def build_recovery_grid(
    *,
    true_hursts: Sequence[float],
    windows: Sequence[int],
    trials: int,
    seed: int = 0,
) -> tuple[RecoveryCell, ...]:
    """The full table: every (true H, window) pair."""
    cells: list[RecoveryCell] = []
    for w_index, window in enumerate(windows):
        for h_index, true_hurst in enumerate(true_hursts):
            cells.append(
                measure_recovery(
                    true_hurst=true_hurst,
                    window=window,
                    trials=trials,
                    seed=seed + w_index * 1_009 + h_index * 31,
                )
            )
    return tuple(cells)


def is_monotonic(cells: Sequence[RecoveryCell]) -> bool:
    """Do mean estimates rise with true H, at one window? Criterion 1 --
    an estimator with the wrong ordering inverts conclusions."""
    ordered = sorted(cells, key=lambda c: c.true_hurst)
    means = [c.mean_estimate for c in ordered]
    return all(earlier <= later for earlier, later in pairwise(means))


def separation(low: RecoveryCell, high: RecoveryCell) -> float:
    """Distance between two cells' means in pooled standard deviations --
    the usual effect-size reading. Criterion 2: a threshold placed
    between two overlapping distributions cannot mean anything, and this
    is how overlapping they are.

    Returns inf when both spreads are zero (identical every trial), which
    is perfect separation rather than a division error.
    """
    pooled = math_sqrt_mean_square(low.stdev_estimate, high.stdev_estimate)
    if pooled == 0.0:
        return float("inf")
    return abs(high.mean_estimate - low.mean_estimate) / pooled


def math_sqrt_mean_square(a: float, b: float) -> float:
    return float(((a * a + b * b) / 2.0) ** 0.5)


def render_recovery_table(cells: Sequence[RecoveryCell]) -> str:
    """Markdown, grouped by window, with bias and spread shown alongside
    every estimate -- a mean without its spread invites reading a noisy
    number as a precise one."""
    lines: list[str] = [
        "# Hurst estimator recovery against known-H synthetic series",
        "",
        "Generated by vo.research.hurst_validation against "
        "vo.research.fbm (Hosking's exact method).",
        "",
        "`bias` is mean estimate minus true H. Acceptable if consistent; "
        "what matters is ordering and separation.",
        "",
    ]

    for window in sorted({cell.window for cell in cells}):
        at_window = sorted(
            (c for c in cells if c.window == window), key=lambda c: c.true_hurst
        )
        lines.append(f"## window = {window} bars")
        lines.append("")
        lines.append("| true H | mean | median | stdev | bias |")
        lines.append("|---:|---:|---:|---:|---:|")
        for cell in at_window:
            lines.append(
                f"| {cell.true_hurst:.2f} | {cell.mean_estimate:.3f} | "
                f"{cell.median_estimate:.3f} | {cell.stdev_estimate:.3f} | "
                f"{cell.bias:+.3f} |"
            )
        lines.append("")
        lines.append(f"- monotonic: **{'yes' if is_monotonic(at_window) else 'NO'}**")

        by_h = {cell.true_hurst: cell for cell in at_window}
        if 0.3 in by_h and 0.7 in by_h:
            lines.append(
                f"- separation H=0.3 vs H=0.7: "
                f"**{separation(by_h[0.3], by_h[0.7]):.2f}** pooled SD"
            )
        lines.append("")

    return "\n".join(lines)
