"""Edge-case tests for the pure-Python bootstrap statistics helpers.

This module deliberately complements ``tests/test_stats.py`` rather than
duplicating it. The happy paths (point estimate inside the interval, default
mean statistic, interval widening, custom statistic, basic parameter
rejection, same-seed determinism, large/no difference detection) already live
in ``test_stats.py``. Here we probe the boundaries: degenerate samples, tiny
``n``, extreme confidence levels, RNG-seed sensitivity, empty-input
combinations for the two-proportion test, p-value monotonicity, and a
wall-clock performance guard for large samples.
"""

from __future__ import annotations

import random
import statistics
import time

import pytest

from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

SEED = 42


def _bernoulli(p: float, n: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [1.0 if rng.random() < p else 0.0 for _ in range(n)]


class TestBootstrapCiDegenerateSamples:
    def test_all_identical_sample_has_zero_width_ci(self) -> None:
        # Every resample of a constant sample is the same constant, so the
        # percentile bounds must collapse onto the point estimate exactly.
        values = [7.5] * 12
        low, high, point = bootstrap_ci(values, n_resamples=200, seed=SEED)
        assert point == pytest.approx(7.5)
        assert low == pytest.approx(7.5)
        assert high == pytest.approx(7.5)
        assert (high - low) == 0.0

    def test_all_zeros_sample(self) -> None:
        low, high, point = bootstrap_ci([0.0, 0.0, 0.0, 0.0], seed=SEED)
        assert point == 0.0
        assert low == 0.0
        assert high == 0.0

    def test_single_element_sample(self) -> None:
        # n == 1: randrange(1) is always 0, so every resample equals the value.
        low, high, point = bootstrap_ci([3.25], n_resamples=50, seed=SEED)
        assert point == pytest.approx(3.25)
        assert low == pytest.approx(3.25)
        assert high == pytest.approx(3.25)
        assert high - low == 0.0

    def test_two_element_sample_bounds_are_valid(self) -> None:
        values = [0.0, 1.0]
        low, high, point = bootstrap_ci(values, n_resamples=100, seed=SEED)
        # Resamples of [0,1] can only have means in {0, 0.5, 1}.
        assert point == pytest.approx(0.5)
        assert 0.0 <= low <= point <= high <= 1.0


class TestBootstrapCiSmallN:
    def test_very_small_n_returns_ordered_interval(self) -> None:
        values = [1.0, 0.0, 1.0]
        low, high, point = bootstrap_ci(values, n_resamples=64, seed=SEED)
        assert low <= point <= high

    def test_minimum_n_resamples_is_accepted(self) -> None:
        # n_resamples == 2 is the documented lower bound (>= 2).
        values = _bernoulli(0.5, 40, seed=3)
        low, high, point = bootstrap_ci(values, n_resamples=2, seed=SEED)
        assert low <= point <= high

    def test_custom_statistic_on_tiny_sample(self) -> None:
        low, high, point = bootstrap_ci(
            [2.0, 9.0], statistic=statistics.median, n_resamples=32, seed=SEED
        )
        assert point == pytest.approx(5.5)
        assert low <= point <= high


class TestBootstrapCiConfidenceLevels:
    def test_confidence_999_is_valid_and_contains_point(self) -> None:
        values = _bernoulli(0.5, 300, seed=11)
        low, high, point = bootstrap_ci(values, confidence=0.999, seed=SEED)
        assert low <= point <= high

    def test_higher_confidence_widens_interval(self) -> None:
        # Same data and same seed: a 99.9% interval must be at least as wide
        # as the 95% interval, with bounds pushed outward.
        values = _bernoulli(0.5, 400, seed=12)
        lo95, hi95, _ = bootstrap_ci(values, confidence=0.95, seed=SEED)
        lo999, hi999, _ = bootstrap_ci(values, confidence=0.999, seed=SEED)
        assert lo999 <= lo95
        assert hi999 >= hi95
        assert (hi999 - lo999) >= (hi95 - lo95)

    @pytest.mark.parametrize("bad_confidence", [0.0, -0.5, 1.0, 1.0001])
    def test_rejects_out_of_range_confidence(self, bad_confidence: float) -> None:
        with pytest.raises(ValueError):
            bootstrap_ci([1.0, 2.0, 3.0], confidence=bad_confidence)


class TestBootstrapCiDeterminism:
    def test_same_seed_repeated_calls_are_identical(self) -> None:
        values = _bernoulli(0.5, 150, seed=21)
        first = bootstrap_ci(values, n_resamples=250, seed=SEED)
        second = bootstrap_ci(values, n_resamples=250, seed=SEED)
        assert first == second

    def test_different_seed_preserves_point_but_moves_bounds(self) -> None:
        # The point estimate does not depend on the RNG, so it is stable
        # across seeds; the resampled percentile bounds are not.
        values = _bernoulli(0.5, 200, seed=22)
        a = bootstrap_ci(values, n_resamples=250, seed=1)
        b = bootstrap_ci(values, n_resamples=250, seed=2)
        assert a[2] == b[2]  # identical point estimate
        assert (a[0], a[1]) != (b[0], b[1])  # bounds differ across seeds

    def test_point_estimate_is_independent_of_seed(self) -> None:
        values = _bernoulli(0.4, 120, seed=23)
        points = {bootstrap_ci(values, n_resamples=100, seed=s)[2] for s in range(5)}
        assert len(points) == 1
        assert next(iter(points)) == pytest.approx(statistics.mean(values))


class TestBootstrapCiPerformance:
    def test_large_sample_completes_within_time_budget(self) -> None:
        # A large observed sample with a small resample count stays fast.
        values = _bernoulli(0.5, 30000, seed=31)
        start = time.perf_counter()
        low, high, point = bootstrap_ci(values, n_resamples=20, seed=SEED)
        elapsed = time.perf_counter() - start
        assert low <= point <= high
        # Generous ceiling so the test guards against pathological blow-ups
        # without being flaky on slow CI runners.
        assert elapsed < 20.0


class TestTwoProportionEmptyInputs:
    def test_both_samples_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([], [])

    def test_first_sample_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([], [1.0, 0.0])

    def test_second_sample_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([1.0, 0.0], [])

    def test_single_element_samples_are_accepted(self) -> None:
        delta, p_value, _ = two_proportion_bootstrap_test([1.0], [0.0], n_resamples=50)
        assert delta == pytest.approx(1.0)
        assert 0.0 < p_value <= 1.0


class TestTwoProportionParameterValidation:
    @pytest.mark.parametrize("bad_confidence", [0.0, 1.0, -1.0])
    def test_rejects_out_of_range_confidence(self, bad_confidence: float) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([1.0], [0.0], confidence=bad_confidence)

    def test_rejects_n_resamples_below_minimum(self) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([1.0], [0.0], n_resamples=1)

    def test_minimum_n_resamples_is_accepted(self) -> None:
        _, p_value, _ = two_proportion_bootstrap_test(
            [1.0, 0.0], [0.0, 1.0], n_resamples=2
        )
        assert 0.0 < p_value <= 1.0

    def test_p_value_smoothing_keeps_estimate_positive(self) -> None:
        # +1/+1 smoothing means the smallest attainable p is 1/(n_resamples+1).
        a = _bernoulli(0.99, 400, seed=41)
        b = _bernoulli(0.01, 400, seed=42)
        _, p_value, _ = two_proportion_bootstrap_test(a, b, n_resamples=100, seed=SEED)
        assert p_value >= 1.0 / 101.0


class TestTwoProportionMonotonicity:
    def test_p_value_decreases_as_difference_grows(self) -> None:
        # Deterministic data + fixed test seed -> stable p ordering. A near
        # null pair must yield a larger p than a moderate pair, which in turn
        # must exceed a starkly separated pair. Sample size (n=60) is chosen
        # so the moderate effect stays above the +1/+1 smoothing floor and the
        # three levels are cleanly separated rather than all saturating.
        n = 60
        close_a, close_b = _bernoulli(0.5, n, 10), _bernoulli(0.5, n, 11)
        med_a, med_b = _bernoulli(0.6, n, 10), _bernoulli(0.4, n, 11)
        far_a, far_b = _bernoulli(0.8, n, 10), _bernoulli(0.2, n, 11)

        _, p_close, _ = two_proportion_bootstrap_test(
            close_a, close_b, n_resamples=200, seed=SEED
        )
        _, p_med, _ = two_proportion_bootstrap_test(
            med_a, med_b, n_resamples=200, seed=SEED
        )
        _, p_far, _ = two_proportion_bootstrap_test(
            far_a, far_b, n_resamples=200, seed=SEED
        )
        assert p_close > p_med > p_far

    def test_identical_samples_yield_near_zero_delta(self) -> None:
        sample = _bernoulli(0.5, 300, seed=51)
        delta, p_value, significant = two_proportion_bootstrap_test(
            sample, list(sample), n_resamples=200, seed=SEED
        )
        assert delta == pytest.approx(0.0, abs=1e-12)
        assert p_value > 0.05
        assert not significant
