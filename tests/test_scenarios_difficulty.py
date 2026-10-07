"""Tests for the optional ``difficulty_bias`` and ``coverage`` additions.

These complement ``tests/test_scenarios.py`` (which must keep passing) and
focus on the new behavior:

* the default (``difficulty_bias=0.0``) output is byte-identical to the
  historical, unbiased formula for a fixed seed;
* a positive bias raises the mean difficulty relative to a negative bias;
* sampled difficulty is always clamped to ``[0.05, 0.95]``;
* ``coverage`` sums to ``n`` and gives full 12-category coverage for ``n >= 12``.
"""

from __future__ import annotations

import random
import statistics

import pytest

from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    _BASE_DIFFICULTY,
    _PARAM_SAMPLERS,
    _clamp,
    ScenarioGenerator,
)


def _reference_difficulties(seed: int, task_id: str, n: int) -> list[float]:
    """Reproduce the *pre-bias* difficulty formula with the exact RNG order.

    Mirrors :meth:`ScenarioGenerator.generate`: per scenario it draws the
    difficulty jitter first, then the category's parameter sampler, so the RNG
    stream advances identically to the production generator.
    """
    rng = random.Random(f"{seed}:{task_id}")
    out: list[float] = []
    for i in range(n):
        category = ADVERSARIAL_CATEGORIES[i % len(ADVERSARIAL_CATEGORIES)]
        difficulty = _clamp(
            _BASE_DIFFICULTY[category] + rng.uniform(-0.15, 0.15), 0.05, 0.95
        )
        out.append(round(difficulty, 4))
        _PARAM_SAMPLERS[category](rng)  # consume the same draws as generate()
    return out


class TestDefaultUnchanged:
    def test_default_matches_explicit_zero_bias(self) -> None:
        baseline = ScenarioGenerator(seed=1234).generate("pick-place", 36)
        explicit = ScenarioGenerator(seed=1234, difficulty_bias=0.0).generate(
            "pick-place", 36
        )
        assert [(s.id, s.category, s.params, s.difficulty) for s in baseline] == [
            (s.id, s.category, s.params, s.difficulty) for s in explicit
        ]

    def test_default_matches_historical_formula(self) -> None:
        # The default generator must equal the original unbiased computation.
        scenarios = ScenarioGenerator(seed=42).generate("ref", 48)
        expected = _reference_difficulties(42, "ref", 48)
        assert [s.difficulty for s in scenarios] == expected

    def test_zero_bias_hardcoded_reference_across_seeds(self) -> None:
        for seed in (0, 7, 99, 2024):
            scenarios = ScenarioGenerator(seed=seed).generate("task-x", 30)
            assert [s.difficulty for s in scenarios] == _reference_difficulties(
                seed, "task-x", 30
            )


class TestDifficultyBias:
    def test_positive_bias_raises_mean_vs_negative(self) -> None:
        harder = ScenarioGenerator(seed=99, difficulty_bias=0.5).generate("t", 60)
        easier = ScenarioGenerator(seed=99, difficulty_bias=-0.5).generate("t", 60)
        assert statistics.mean(s.difficulty for s in harder) > statistics.mean(
            s.difficulty for s in easier
        )

    def test_bias_monotonic_in_mean(self) -> None:
        means = [
            statistics.mean(
                s.difficulty
                for s in ScenarioGenerator(seed=5, difficulty_bias=b).generate("t", 72)
            )
            for b in (-1.0, -0.5, 0.0, 0.5, 1.0)
        ]
        assert means == sorted(means)
        assert means[0] < means[-1]

    def test_bias_does_not_perturb_rng_stream(self) -> None:
        # Only difficulty should change; ids/categories/params stay identical.
        base = ScenarioGenerator(seed=8, difficulty_bias=0.0).generate("t", 24)
        biased = ScenarioGenerator(seed=8, difficulty_bias=0.3).generate("t", 24)
        assert [s.id for s in base] == [s.id for s in biased]
        assert [s.category for s in base] == [s.category for s in biased]
        assert [s.params for s in base] == [s.params for s in biased]
        assert [s.difficulty for s in base] != [s.difficulty for s in biased]

    def test_clamping_respected_across_bias_range(self) -> None:
        for bias in (-1.0, -0.5, 0.0, 0.5, 1.0):
            for scenario in ScenarioGenerator(seed=5, difficulty_bias=bias).generate(
                "t", 48
            ):
                assert 0.05 <= scenario.difficulty <= 0.95

    def test_extreme_bias_hits_clamp_bounds(self) -> None:
        hard = ScenarioGenerator(seed=5, difficulty_bias=1.0).generate("t", 48)
        assert any(s.difficulty == 0.95 for s in hard)
        easy = ScenarioGenerator(seed=5, difficulty_bias=-1.0).generate("t", 48)
        assert any(s.difficulty == 0.05 for s in easy)

    def test_out_of_range_bias_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScenarioGenerator(difficulty_bias=1.0001)
        with pytest.raises(ValueError):
            ScenarioGenerator(difficulty_bias=-2.0)


class TestCoverage:
    def test_coverage_sums_to_n(self) -> None:
        gen = ScenarioGenerator(seed=11)
        for n in (0, 1, 5, 11, 12, 17, 30, 48):
            assert sum(gen.coverage("t", n).values()) == n

    def test_all_categories_always_present_as_keys(self) -> None:
        cov = ScenarioGenerator(seed=2).coverage("t", 3)
        assert set(cov) == set(ADVERSARIAL_CATEGORIES)
        assert sum(cov.values()) == 3

    def test_full_coverage_at_twelve(self) -> None:
        cov = ScenarioGenerator(seed=2).coverage("t", 12)
        assert set(cov) == set(ADVERSARIAL_CATEGORIES)
        assert all(cov[c] >= 1 for c in ADVERSARIAL_CATEGORIES)

    def test_full_coverage_above_twelve(self) -> None:
        for n in (12, 13, 25, 48):
            cov = ScenarioGenerator(seed=3).coverage("t", n)
            assert all(cov[c] >= 1 for c in ADVERSARIAL_CATEGORIES)

    def test_counts_match_cycling_pattern(self) -> None:
        # 25 = two full cycles (24) + one extra on the first category.
        cov = ScenarioGenerator(seed=2).coverage("t", 25)
        assert cov[ADVERSARIAL_CATEGORIES[0]] == 3
        assert cov[ADVERSARIAL_CATEGORIES[1]] == 2
        assert sum(cov.values()) == 25

    def test_coverage_agrees_with_generate(self) -> None:
        gen = ScenarioGenerator(seed=17, difficulty_bias=0.4)
        scenarios = gen.generate("task", 40)
        cov = gen.coverage("task", 40)
        manual: dict[str, int] = {c: 0 for c in ADVERSARIAL_CATEGORIES}
        for scenario in scenarios:
            manual[scenario.category] += 1
        assert cov == manual

    def test_coverage_negative_n_raises(self) -> None:
        with pytest.raises(ValueError):
            ScenarioGenerator().coverage("t", -1)
