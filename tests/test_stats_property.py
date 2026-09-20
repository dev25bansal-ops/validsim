"""Property-based tests for the pure-Python bootstrap statistics helpers.

This module deliberately avoids any external property-testing framework
(``hypothesis`` and friends). Instead it drives a fixed master
``random.Random`` seed through ~50 randomized iterations per property, so the
suite stays dependency-free yet exercises the functions across a wide space of
inputs. Because the master seed is pinned, every iteration is fully
deterministic: a green run is reproducible byte-for-byte and there is no room
for flakiness.

The properties checked here complement (and do not duplicate) the example-based
assertions in ``tests/test_stats.py`` and ``tests/test_stats_edge.py``:

``bootstrap_ci``
    * the percentile interval always brackets the point estimate for data
      bounded in ``[0, 1]`` (and ``0 <= low <= point <= high <= 1``);
    * ``low <= point <= high`` holds for continuous ``[0, 1]`` samples too;
    * the interval width is non-decreasing as the confidence level rises
      (the resample stream is identical across confidence levels for a fixed
      seed, so widening coverage can only push the bounds outward);
    * the result is deterministic for a fixed seed.

``two_proportion_bootstrap_test``
    * the smoothed p-value estimate always lies in ``[0, 1]``;
    * two identical distributions are never flagged as different (the p-value
      sits above a comfortable floor);
    * swapping the samples ``(a, b) -> (b, a)`` leaves ``|delta|`` unchanged.

The source under test is never modified.
"""

from __future__ import annotations

import random
import statistics

import pytest

from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

# Fixed master seed -> the whole randomized sweep is reproducible.
MASTER_SEED = 0xC0FFEE
# Number of random inputs each property is exercised against. Kept modest so
# the whole sweep stays a couple of seconds (the bootstrap is pure-Python);
# 50 randomized inputs still cover the input space well for these properties.
ITERATIONS = 50

# Confidence ladder used for the monotonic-width property. Increasing order is
# essential: each step must produce an interval at least as wide as the last.
CONFIDENCE_LADDER = (0.50, 0.80, 0.90, 0.95, 0.99)


def _bernoulli_sample(rng: random.Random, n: int, p: float) -> list[float]:
    """Draw ``n`` i.i.d. 0/1 indicators with success probability ``p``."""
    return [1.0 if rng.random() < p else 0.0 for _ in range(n)]


def _uniform_sample(rng: random.Random, n: int) -> list[float]:
    """Draw ``n`` i.i.d. floats bounded in ``[0, 1]``."""
    return [rng.random() for _ in range(n)]


class TestBootstrapCiProperties:
    def test_ci_contains_point_estimate_for_unit_interval_data(self) -> None:
        """For data confined to ``[0, 1]`` the CI must bracket the point
        estimate and stay inside the data's own bounds."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(5, 80)
            p = rng.uniform(0.05, 0.95)
            values = _bernoulli_sample(rng, n, p)
            seed = rng.randrange(1, 10**9)
            low, high, point = bootstrap_ci(values, n_resamples=80, seed=seed)
            assert 0.0 <= low <= point <= high <= 1.0

    def test_low_le_point_le_high_for_continuous_data(self) -> None:
        """The literal ``low <= point <= high`` ordering also holds for
        continuous ``[0, 1]`` samples (mean statistic)."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(5, 80)
            values = _uniform_sample(rng, n)
            seed = rng.randrange(1, 10**9)
            low, high, point = bootstrap_ci(values, n_resamples=80, seed=seed)
            assert low <= point <= high
            # The point estimate is exactly the sample mean by default.
            assert point == pytest.approx(statistics.fmean(values))

    def test_ci_width_non_decreasing_in_confidence(self) -> None:
        """Raising the confidence level can never shrink the interval.

        With a fixed seed the bootstrap resample stream is identical across
        confidence levels, so ``low`` can only move left and ``high`` only
        move right as coverage grows -> width is monotone non-decreasing.
        """
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(10, 80)
            p = rng.uniform(0.05, 0.95)
            values = _bernoulli_sample(rng, n, p)
            seed = rng.randrange(1, 10**9)

            widths: list[float] = []
            for confidence in CONFIDENCE_LADDER:
                low, high, _ = bootstrap_ci(
                    values, n_resamples=80, confidence=confidence, seed=seed
                )
                widths.append(high - low)

            for narrower, wider in zip(widths, widths[1:]):
                assert narrower <= wider + 1e-12

    def test_ci_deterministic_for_fixed_seed(self) -> None:
        """Identical inputs and seed must yield byte-identical results."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(5, 80)
            p = rng.uniform(0.05, 0.95)
            values = _bernoulli_sample(rng, n, p)
            seed = rng.randrange(1, 10**9)
            first = bootstrap_ci(values, n_resamples=80, seed=seed)
            second = bootstrap_ci(values, n_resamples=80, seed=seed)
            assert first == second


class TestTwoProportionProperties:
    def test_p_value_always_in_unit_interval(self) -> None:
        """The +1/+1 smoothed permutation p-value is bounded by ``[0, 1]``."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            na = rng.randint(5, 80)
            nb = rng.randint(5, 80)
            pa = rng.uniform(0.05, 0.95)
            pb = rng.uniform(0.05, 0.95)
            a = _bernoulli_sample(rng, na, pa)
            b = _bernoulli_sample(rng, nb, pb)
            seed = rng.randrange(1, 10**9)
            _, p_value, significant = two_proportion_bootstrap_test(
                a, b, n_resamples=60, seed=seed
            )
            assert 0.0 <= p_value <= 1.0
            # ``significant`` must agree with the configured threshold (0.05).
            assert significant == (p_value < 0.05)

    def test_identical_distributions_yield_p_above_floor(self) -> None:
        """Two identical distributions (here: the very same sample on both
        sides, so ``delta == 0``) must never be reported as different; the
        smoothed p-value collapses to its maximum, comfortably above any
        significance floor."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(5, 80)
            p = rng.uniform(0.05, 0.95)
            sample = _bernoulli_sample(rng, n, p)
            seed = rng.randrange(1, 10**9)
            delta, p_value, significant = two_proportion_bootstrap_test(
                sample, list(sample), n_resamples=60, seed=seed
            )
            assert delta == pytest.approx(0.0, abs=1e-12)
            assert p_value > 0.05
            assert not significant

    def test_symmetric_swap_preserves_abs_delta(self) -> None:
        """Swapping the samples negates ``delta`` but leaves its magnitude
        (and the significance decision) untouched."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            na = rng.randint(5, 80)
            nb = rng.randint(5, 80)
            pa = rng.uniform(0.05, 0.95)
            pb = rng.uniform(0.05, 0.95)
            a = _bernoulli_sample(rng, na, pa)
            b = _bernoulli_sample(rng, nb, pb)
            seed = rng.randrange(1, 10**9)

            delta_ab, _, _ = two_proportion_bootstrap_test(
                a, b, n_resamples=60, seed=seed
            )
            delta_ba, _, _ = two_proportion_bootstrap_test(
                b, a, n_resamples=60, seed=seed
            )
            assert abs(delta_ab) == pytest.approx(abs(delta_ba))
            # ``delta`` is exactly antisymmetric under the swap.
            assert delta_ab == pytest.approx(-delta_ba)
