"""Tests for the pure-Python bootstrap statistics helpers."""

from __future__ import annotations

import random
import statistics

import pytest

from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

SEED = 42


def _bernoulli(p: float, n: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [1.0 if rng.random() < p else 0.0 for _ in range(n)]


class TestBootstrapCi:
    def test_contains_point_estimate(self) -> None:
        values = _bernoulli(0.7, 400, seed=SEED)
        low, high, point = bootstrap_ci(values, seed=SEED)
        assert point == pytest.approx(statistics.mean(values))
        assert low <= point <= high

    def test_point_is_sample_mean_by_default(self) -> None:
        values = [1.0, 0.0, 1.0, 1.0, 0.0]
        _, _, point = bootstrap_ci(values, seed=SEED)
        assert point == pytest.approx(0.6)

    def test_interval_widens_with_smaller_samples(self) -> None:
        small = _bernoulli(0.7, 60, seed=1)
        large = _bernoulli(0.7, 2000, seed=2)
        lo_s, hi_s, _ = bootstrap_ci(small, n_resamples=400, seed=SEED)
        lo_l, hi_l, _ = bootstrap_ci(large, n_resamples=400, seed=SEED)
        assert (hi_l - lo_l) < (hi_s - lo_s)

    def test_custom_statistic(self) -> None:
        values = list(range(1, 51))
        low, high, point = bootstrap_ci(values, statistic=max, seed=SEED)
        assert point == 50
        assert 40 <= low <= high <= 50

    def test_rejects_empty_and_bad_params(self) -> None:
        with pytest.raises(ValueError):
            bootstrap_ci([])
        with pytest.raises(ValueError):
            bootstrap_ci([1.0], n_resamples=1)
        with pytest.raises(ValueError):
            bootstrap_ci([1.0], confidence=1.5)

    def test_deterministic_given_seed(self) -> None:
        values = _bernoulli(0.5, 100, seed=9)
        a = bootstrap_ci(values, n_resamples=200, seed=SEED)
        b = bootstrap_ci(values, n_resamples=200, seed=SEED)
        assert a == b


class TestTwoProportionTest:
    def test_detects_large_difference(self) -> None:
        a = _bernoulli(0.95, 500, seed=1)
        b = _bernoulli(0.75, 500, seed=2)
        delta, p_value, significant = two_proportion_bootstrap_test(a, b, seed=SEED)
        assert delta == pytest.approx(0.20, abs=0.08)  # mean(a) - mean(b) = 0.95 - 0.75
        assert p_value < 0.05
        assert significant

    def test_no_difference_is_not_significant(self) -> None:
        a = _bernoulli(0.8, 400, seed=3)
        b = _bernoulli(0.8, 400, seed=4)
        delta, p_value, significant = two_proportion_bootstrap_test(a, b, seed=SEED)
        assert abs(delta) < 0.06
        assert p_value > 0.05
        assert not significant

    def test_p_value_in_unit_interval(self) -> None:
        a = _bernoulli(0.6, 100, seed=5)
        b = _bernoulli(0.6, 100, seed=6)
        _, p_value, _ = two_proportion_bootstrap_test(a, b, n_resamples=200, seed=SEED)
        assert 0.0 < p_value <= 1.0

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            two_proportion_bootstrap_test([], [1.0])
