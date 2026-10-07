"""Engine statistics benchmarks - the inline request-path hot spot.

``build_scorecard`` calls ``bootstrap_ci`` over one 0/1 float per episode on
every synchronous validation, so its cost *is* the request latency. These
benchmarks pin the O(n_resamples x n) shape and the per-episode slope so a
regression shows up in the diff.
"""

from __future__ import annotations

import random

import pytest

from validsim.engine.scorecard import _CI_N_RESAMPLES
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

from .conftest import time_benchmark

pytestmark = pytest.mark.benchmark

#: Episode counts TaskConfig permits (ge=1, le=100000).
EPISODE_TIERS = (1_000, 10_000, 50_000, 100_000)

#: Resample counts worth pinning: the inline default, the library default, and
#: a cheap floor that isolates the resampling loop from the final sort.
RESAMPLE_TIERS = (2, 100, 500, 1_000)


def _bernoulli(n: int, p: float = 0.82, seed: int = 7) -> list[float]:
    """0/1 sample shaped exactly like the CI input built in build_scorecard."""
    rng = random.Random(seed)
    return [1.0 if rng.random() < p else 0.0 for _ in range(n)]


@pytest.mark.parametrize("n", EPISODE_TIERS)
def test_bootstrap_ci_default_resamples(n: int) -> None:
    """Inline production shape: _CI_N_RESAMPLES (500) over n episodes."""
    values = _bernoulli(n)
    time_benchmark(
        f"bootstrap_ci_n{n}_resamples{_CI_N_RESAMPLES}",
        lambda: bootstrap_ci(values, n_resamples=_CI_N_RESAMPLES, seed=42),
    )


@pytest.mark.parametrize("n", (1_000, 10_000, 50_000))
def test_bootstrap_ci_resample_scaling(n: int) -> None:
    """Cost must scale linearly in resample count at a fixed n."""
    values = _bernoulli(n)
    for r in RESAMPLE_TIERS:
        time_benchmark(
            f"bootstrap_ci_n{n}_resamples{r}",
            lambda r=r: bootstrap_ci(values, n_resamples=r, seed=42),
        )


@pytest.mark.parametrize("n", (1_000, 10_000, 50_000))
def test_two_proportion_bootstrap_test(n: int) -> None:
    """Regression path (compare endpoint), default 1000 resamples."""
    a = _bernoulli(n, seed=1)
    b = _bernoulli(n, seed=2)
    time_benchmark(
        f"two_proportion_bootstrap_test_n{n}",
        lambda: two_proportion_bootstrap_test(a, b, seed=42),
    )
