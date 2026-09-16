"""
Hurst exponent (minimal, v1) -- [VO-D], pulled forward from Phase 15 as
regime instrumentation.

Alongside the Efficiency Ratio, a second "how directional is this?" read,
by a different mechanism, so the regime engine can cite it as evidence:

    H > 0.5   persistent / trending (an expansion-like tape)
    H ~ 0.5   random-walk-like
    H < 0.5   mean-reverting / choppy (a consolidation-like tape)

Estimator: the structure-function / lag method. For each lag tau, take the
RMS of tau-spaced close-to-close differences over the window; those RMS
values scale like tau**H, so H is the slope of log(rms) vs log(tau),
fitted by ordinary least squares. Chosen because it is simple, pure-Python
(no numpy dependency), fully determined by its own window (reproducible
bar-by-bar), and needs no seed -- the same auditability reasons atr.py
uses an SMA rather than Wilder smoothing.

DELIBERATELY MINIMAL, and flagged as such: this is not the rescaled-range
(R/S) Hurst, nor a multi-order generalized Hurst. Phase 15 (Hurst +
Efficiency Ratio) is where the estimator, its window choices and its own
methodology_version get formalized; this is the least that lets Phase 13
cite Hurst as evidence today. Like ER, it is INSTRUMENTATION -- it never
classifies the regime and never reaches a trade decision (G2).

No lookahead: reads only bars[<= index].
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from vo.market.bar import Bar


def hurst_exponent(
    bars: Sequence[Bar],
    index: int,
    *,
    period: int,
) -> float | None:
    """
    Structure-function Hurst estimate over the `period + 1` closes ending
    at and including `index`.

    None -- never a partial-window guess -- when fewer than `period + 1`
    closes exist up to `index`, when the window is too short to fit a slope
    over at least two lags, or when the window is perfectly flat (no
    variation to measure, so H is genuinely undefined). Same
    insufficient-information honesty as atr_ticks / efficiency_ratio.
    """
    if period < 4:
        # Need enough points for a couple of lags and a slope; below this a
        # Hurst estimate is noise, so refuse rather than emit a fake number.
        raise ValueError(f"period must be >= 4 for a Hurst estimate, got {period}")

    if index < 0 or index >= len(bars):
        raise IndexError(f"index {index} out of range for {len(bars)} bars")

    if index < period:
        return None

    closes = [bars[i].close for i in range(index - period, index + 1)]
    max_lag = period // 2
    if max_lag < 2:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for lag in range(2, max_lag + 1):
        diffs = [closes[k] - closes[k - lag] for k in range(lag, len(closes))]
        if not diffs:
            continue
        rms = math.sqrt(sum(d * d for d in diffs) / len(diffs))
        if rms <= 0.0:
            continue  # no movement at this lag -- skip, do not log(0)
        xs.append(math.log(lag))
        ys.append(math.log(rms))

    if len(xs) < 2:
        return None  # flat / degenerate window -- H undefined, don't guess

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0.0:
        return None

    return cov / var_x
