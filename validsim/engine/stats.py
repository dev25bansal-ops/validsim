"""Lightweight bootstrap statistics for ValidSim scoring.

Implemented in pure Python (``random`` + ``statistics``) so the core engine
carries no numeric dependency. The sample sizes the platform scores against
(hundreds of thousands of episodes at most, resampled ~1000 times) run
comfortably in process; the public signatures are fixed so a NumPy/CuPy
backend can drop in behind them without touching callers.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Callable, Sequence

__all__ = ["bootstrap_ci", "two_proportion_bootstrap_test"]


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; ``q`` in ``[0, 1]``, input pre-sorted."""
    if not sorted_values:
        raise ValueError("percentile of empty sequence is undefined")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = q * (len(sorted_values) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = statistics.mean,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Percentile bootstrap confidence interval for an arbitrary statistic.

    Args:
        values: Observed sample (non-empty).
        statistic: Aggregation function over a resample (defaults to mean).
        n_resamples: Bootstrap resample count (>= 2).
        confidence: Interval coverage in ``(0, 1)``.
        seed: RNG seed for reproducibility.

    Returns:
        ``(low, high, point)`` where ``point`` is the statistic on the
        original sample and ``[low, high]`` is the percentile bootstrap CI.

    Raises:
        ValueError: On empty input or invalid parameters.
    """
    if not values:
        raise ValueError("values must be non-empty")
    if n_resamples < 2:
        raise ValueError("n_resamples must be >= 2")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be within (0, 1)")

    rng = random.Random(seed)
    n = len(values)
    point = float(statistic(values))
    estimates = sorted(
        float(statistic([values[rng.randrange(n)] for _ in range(n)]))
        for _ in range(n_resamples)
    )
    alpha = (1.0 - confidence) / 2.0
    low = _percentile(estimates, alpha)
    high = _percentile(estimates, 1.0 - alpha)
    return low, high, point


def two_proportion_bootstrap_test(
    a: Sequence[float],
    b: Sequence[float],
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, bool]:
    """Two-sided permutation test comparing the means of two 0/1 samples.

    ``delta`` is ``mean(a) - mean(b)``. Under the null hypothesis the labels
    are exchangeable, so we repeatedly shuffle the pooled sample and record
    how often the permuted gap is at least as extreme as the observed one.

    Args:
        a: Success indicators (0/1) for the current run.
        b: Success indicators (0/1) for the baseline run.
        n_resamples: Permutation iterations (>= 2).
        confidence: Significance level is ``1 - confidence``.
        seed: RNG seed for reproducibility.

    Returns:
        ``(delta, p_value_estimate, significant)``.

    Raises:
        ValueError: On empty inputs or invalid parameters.
    """
    if not a or not b:
        raise ValueError("both samples must be non-empty")
    if n_resamples < 2:
        raise ValueError("n_resamples must be >= 2")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be within (0, 1)")

    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    pooled = list(a) + list(b)
    total_ones = sum(pooled)
    rng = random.Random(seed)

    extreme = 0
    for _ in range(n_resamples):
        rng.shuffle(pooled)
        ones_a = sum(pooled[:na])
        perm_delta = ones_a / na - (total_ones - ones_a) / nb
        if abs(perm_delta) >= abs(delta) - 1e-12:
            extreme += 1

    # +1/+1 smoothing keeps the p-value estimate strictly above zero.
    p_value = (extreme + 1) / (n_resamples + 1)
    significant = p_value < (1.0 - confidence)
    return delta, p_value, significant
