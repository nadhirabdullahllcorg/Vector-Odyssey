"""
Synthetic fractional Brownian motion with a KNOWN Hurst exponent --
the ground truth VO's estimator has never been checked against.

WHY THIS EXISTS. The 2026-09-18 research audit (finding B1) calibrated
vo.observation.hurst against a plain random walk, found it reads ~0.44
where 0.50 was expected, and recorded the offset. That establishes the
estimator is biased at ONE value. It says nothing about whether the bias
is the same at H=0.3 or H=0.7, whether the estimator compresses the range
toward the middle, or at what window length it becomes usable at all --
and `hurst_period` is currently 20 bars, far below the ~500 that the
literature treats as a floor. None of that is answerable without series
whose true H is known by construction.

METHOD: HOSKING'S RECURSION, not Cholesky. Both generate exact
fractional Gaussian noise; Cholesky factorises the full n*n
autocovariance matrix at O(n^3), which is impractical in pure Python at
the lengths that matter here. Hosking (1984) walks the same covariance
structure with a Durbin-Levinson recursion at O(n^2) -- exact, not an
approximation, and fast enough for n in the low thousands. Circulant
embedding (Davies-Harte) is faster still at O(n log n) but needs an FFT,
which would mean numpy; this project's dependency discipline (audit R10)
keeps numpy out of everything except the hmmlearn extra, and an exact
O(n^2) method costs nothing worth that exception.

Definitions, so the generated series means what it should:

  fractional Gaussian noise (fGn) has autocovariance
      gamma(k) = 0.5 * (|k-1|^2H - 2|k|^2H + |k+1|^2H)
  and fractional Brownian motion is its cumulative sum. H = 0.5 gives
  independent increments -- an ordinary random walk. H > 0.5 is
  persistent (a move tends to be followed by a move the same way), H <
  0.5 antipersistent.

DETERMINISTIC BY DEFAULT. Every generator takes a seed and uses its own
random.Random instance rather than the module-global one, so a
validation run is reproducible and cannot be perturbed by anything else
in the process seeding random. Reproducibility is the whole point of a
calibration reference.
"""

from __future__ import annotations

import math
import random


class FbmError(ValueError):
    """Raised for a structurally invalid fBm request."""


def fgn_autocovariance(lag: int, hurst: float) -> float:
    """Autocovariance of unit-variance fractional Gaussian noise at
    `lag`. gamma(0) = 1.0 by construction."""
    if lag < 0:
        raise FbmError(f"lag cannot be negative, got {lag}")

    two_h = 2.0 * hurst
    k = float(lag)
    left = float(abs(k - 1.0) ** two_h)
    middle = float(k**two_h)
    right = float((k + 1.0) ** two_h)
    return 0.5 * (left - 2.0 * middle + right)


def fractional_gaussian_noise(
    length: int, hurst: float, *, seed: int = 0
) -> list[float]:
    """
    `length` samples of exact unit-variance fractional Gaussian noise,
    by Hosking's recursion.

    Each new sample is drawn from its conditional distribution given
    every previous one, with the conditional mean and variance carried
    forward by a Durbin-Levinson update -- so the realised series has the
    exact target autocovariance, not an approximation to it.
    """
    if length < 1:
        raise FbmError(f"length must be >= 1, got {length}")
    if not (0.0 < hurst < 1.0):
        raise FbmError(f"hurst must be in (0, 1), got {hurst}")

    rng = random.Random(seed)
    series: list[float] = [rng.gauss(0.0, 1.0)]
    if length == 1:
        return series

    gamma = [fgn_autocovariance(k, hurst) for k in range(length)]

    # phi holds the current prediction coefficients; variance is the
    # conditional variance of the next sample given all previous ones.
    phi: list[float] = []
    variance = 1.0

    for n in range(1, length):
        # New reflection coefficient, then the Levinson update of the
        # earlier coefficients in place.
        numerator = gamma[n] - sum(phi[j] * gamma[n - 1 - j] for j in range(len(phi)))
        reflection = numerator / variance

        updated = [phi[j] - reflection * phi[len(phi) - 1 - j] for j in range(len(phi))]
        updated.append(reflection)
        phi = updated

        variance *= 1.0 - reflection * reflection
        if variance <= 0.0:
            # Numerically possible at extreme H over long series; the
            # honest response is to stop rather than emit samples drawn
            # from a degenerate distribution.
            raise FbmError(
                f"prediction variance collapsed at sample {n} (H={hurst}) -- "
                f"series too long for stable generation at this H"
            )

        mean = sum(phi[j] * series[n - 1 - j] for j in range(len(phi)))
        series.append(mean + math.sqrt(variance) * rng.gauss(0.0, 1.0))

    return series


def fractional_brownian_motion(
    length: int, hurst: float, *, seed: int = 0, start: float = 0.0, scale: float = 1.0
) -> list[float]:
    """Cumulative sum of fGn -- a path whose true Hurst exponent is
    `hurst` by construction. `start`/`scale` place it on a realistic
    price-like level without changing its scaling behaviour, which is
    invariant to both."""
    noise = fractional_gaussian_noise(length, hurst, seed=seed)

    path: list[float] = []
    running = start
    for step in noise:
        running += step * scale
        path.append(running)
    return path
