"""Tests for the rule-based adversarial scenario generator."""

from __future__ import annotations

import pytest

from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    AdversarialScenario,
    ScenarioGenerator,
)


class TestCategories:
    def test_exactly_twelve_categories(self) -> None:
        assert len(ADVERSARIAL_CATEGORIES) == 12
        assert len(set(ADVERSARIAL_CATEGORIES)) == 12

    def test_expected_category_names_present(self) -> None:
        expected = {
            "lighting_change", "object_property_change", "human_proximity",
            "unexpected_obstacle", "sensor_degradation", "mechanical_variation",
            "environmental_disturbance", "task_ambiguity",
            "multi_robot_interference", "emergency_scenario",
            "adversarial_input", "temporal_pressure",
        }
        assert set(ADVERSARIAL_CATEGORIES) == expected


class TestGeneration:
    def test_full_coverage_at_twelve(self) -> None:
        scenarios = ScenarioGenerator(seed=42).generate("pick-place", 12)
        assert [s.category for s in scenarios] == list(ADVERSARIAL_CATEGORIES)

    def test_cycling_order(self) -> None:
        scenarios = ScenarioGenerator(seed=7).generate("t", 25)
        for i, scenario in enumerate(scenarios):
            assert scenario.category == ADVERSARIAL_CATEGORIES[i % 12]

    def test_deterministic_for_same_seed_and_task(self) -> None:
        gen_a = ScenarioGenerator(seed=42)
        gen_b = ScenarioGenerator(seed=42)
        a, b = gen_a.generate("pick", 24), gen_b.generate("pick", 24)
        assert [(s.id, s.params, s.difficulty) for s in a] == [
            (s.id, s.params, s.difficulty) for s in b
        ]

    def test_task_id_changes_stream(self) -> None:
        gen = ScenarioGenerator(seed=42)
        a = gen.generate("task-a", 12)
        b = gen.generate("task-b", 12)
        assert [s.difficulty for s in a] != [s.difficulty for s in b]

    def test_difficulty_bounds_and_params(self) -> None:
        for scenario in ScenarioGenerator(seed=1).generate("t", 48):
            assert 0.0 <= scenario.difficulty <= 1.0
            assert scenario.params  # non-empty
            assert scenario.name.startswith(scenario.category[:4].title())

    def test_ids_unique(self) -> None:
        scenarios = ScenarioGenerator(seed=3).generate("t", 30)
        assert len({s.id for s in scenarios}) == 30

    def test_zero_and_negative(self) -> None:
        assert ScenarioGenerator().generate("t", 0) == []
        with pytest.raises(ValueError):
            ScenarioGenerator().generate("t", -1)


class TestScenarioDataclass:
    def test_rejects_bad_difficulty(self) -> None:
        with pytest.raises(ValueError):
            AdversarialScenario(id="x", category="lighting_change",
                                name="n", difficulty=1.5)

    def test_rejects_unknown_category(self) -> None:
        with pytest.raises(ValueError):
            AdversarialScenario(id="x", category="not_a_category", name="n")
