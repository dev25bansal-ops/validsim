"""Tests for category-diversity coverage of the LLM scenario generator.

All tests run offline: scenario generation is exercised through fake
:class:`ScenarioProvider` stubs replaying canned JSON responses, and the
deterministic rule-based fallback is used for top-ups — no network I/O occurs.

The guarantee under test: after parsing LLM output, any of the twelve
:data:`ADVERSARIAL_CATEGORIES` missing from the accepted scenarios is topped
up from the rule-based fallback *before* the remaining count is filled, and
the run diagnostics record how many categories were missing.
"""

from __future__ import annotations

import json
from typing import Any

from validsim.scenarios.generator import ADVERSARIAL_CATEGORIES, ScenarioGenerator
from validsim.scenarios.llm_generator import LLMScenarioGenerator


class FakeProvider:
    """Provider stub replaying canned responses; records every prompt."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        outcome = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def scenario_obj(category: str = "lighting_change", **overrides: Any) -> dict[str, Any]:
    """Build one schema-valid scenario dict for embedding in fake responses."""
    base: dict[str, Any] = {
        "category": category,
        "name": f"Test {category}",
        "params": {"lux": 100},
        "difficulty": 0.4,
    }
    base.update(overrides)
    return base


def coverage_payload(categories: list[str]) -> str:
    """JSON array with one scenario per category in ``categories``."""
    return json.dumps([scenario_obj(category=c) for c in categories])


class TestCategoryCoverage:
    def test_single_category_response_gets_all_missing_categories_topped_up(
        self,
    ) -> None:
        """LLM returns one scenario of one category → the other 11 categories
        are topped up before any plain count fill, giving full coverage."""
        provider = FakeProvider([coverage_payload(["lighting_change"])])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=3))
        scenarios = gen.generate("pick-place", 12)

        assert len(scenarios) == 12
        covered = {s.category for s in scenarios}
        assert covered == set(ADVERSARIAL_CATEGORIES)
        # The LLM scenario comes first, then one top-up per missing category.
        assert scenarios[0].category == "lighting_change"
        top_ups = scenarios[1:]
        assert len(top_ups) == 11
        assert len({s.category for s in top_ups}) == 11
        assert all(s.id.startswith("adv-") for s in top_ups)
        assert gen.fallback_used is True
        # Diagnostics report the LLM's own coverage gap: all 12 categories
        # minus the single one the LLM provided.
        assert gen.last_run_diagnostics["missing_categories"] == 11

    def test_missing_categories_topped_up_before_remaining_count(self) -> None:
        """With room for top-ups, missing categories are prioritized: each
        missing category appears exactly once among the fallback top-ups."""
        provider = FakeProvider([coverage_payload(["emergency_scenario"] * 2)])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=7))
        scenarios = gen.generate("t", 5)

        assert len(scenarios) == 5
        top_ups = scenarios[2:]
        # 3 top-ups: one per missing category (priority order), since room=3
        # < missing=11 — the first 3 missing categories in canonical order
        # must be covered, and none of them duplicated.
        top_up_categories = [s.category for s in top_ups]
        assert len(set(top_up_categories)) == len(top_up_categories)
        assert top_up_categories == list(ADVERSARIAL_CATEGORIES[:3])
        assert gen.last_run_diagnostics["missing_categories"] == 11
        assert gen.last_run_diagnostics["topup_from_fallback"] == 3

    def test_full_taxonomy_response_needs_no_category_topup(self) -> None:
        """LLM already covers all 12 categories → no diversity top-up, and
        diagnostics report zero missing categories."""
        provider = FakeProvider([coverage_payload(list(ADVERSARIAL_CATEGORIES))])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=9))
        scenarios = gen.generate("t", 12)

        assert len(scenarios) == 12
        assert {s.category for s in scenarios} == set(ADVERSARIAL_CATEGORIES)
        assert all(s.id.startswith("llm-") for s in scenarios)
        assert gen.fallback_used is False
        assert gen.last_fallback_reason is None
        assert gen.last_run_diagnostics == {
            "missing_categories": 0,
            "topup_from_fallback": 0,
        }

    def test_count_shortfall_filled_after_category_topups(self) -> None:
        """Full coverage + fewer scenarios than n → only a plain count top-up
        happens, no duplicate category injections."""
        provider = FakeProvider([coverage_payload(list(ADVERSARIAL_CATEGORIES))])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=13))
        scenarios = gen.generate("t", 15)

        assert len(scenarios) == 15
        assert {s.category for s in scenarios[:12]} == set(ADVERSARIAL_CATEGORIES)
        assert all(s.id.startswith("adv-") for s in scenarios[12:])
        assert gen.last_run_diagnostics["missing_categories"] == 0
        assert gen.last_run_diagnostics["topup_from_fallback"] == 3
        assert gen.fallback_used is True

    def test_invalid_items_reduce_coverage_and_trigger_topup(self) -> None:
        """Invalid LLM entries (bad category) are dropped; the categories they
        would have covered count as missing and are topped up."""
        payload = json.dumps(
            [scenario_obj(category="laser_blindness")] * 4  # type: ignore[list-item]
        )
        provider = FakeProvider([payload])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=21))
        scenarios = gen.generate("t", 6)

        # All LLM items were invalid → accepted is empty → all 12 categories
        # were missing; 6 top-ups cover the first 6 canonical categories.
        assert len(scenarios) == 6
        top_up_categories = [s.category for s in scenarios]
        assert top_up_categories == list(ADVERSARIAL_CATEGORIES[:6])
        assert gen.last_run_diagnostics["missing_categories"] == 12
        assert gen.last_run_diagnostics["topup_from_fallback"] == 6
        assert gen.fallback_used is True
        assert gen.last_fallback_reason is not None

    def test_no_duplicate_categories_within_category_topups(self) -> None:
        """Whatever the LLM mix, category top-ups never duplicate a category
        already present in the accepted scenarios."""
        provider = FakeProvider(
            [
                coverage_payload(
                    ["lighting_change", "human_proximity", "temporal_pressure"]
                )
            ]
        )
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=33))
        scenarios = gen.generate("t", 12)

        assert len(scenarios) == 12
        assert {s.category for s in scenarios} == set(ADVERSARIAL_CATEGORIES)
        top_ups = scenarios[3:]
        top_up_set = {s.category for s in top_ups}
        assert len(top_up_set) == len(top_ups)  # no duplicates among top-ups
        assert top_up_set.isdisjoint(
            {"lighting_change", "human_proximity", "temporal_pressure"}
        )
        assert gen.last_run_diagnostics["missing_categories"] == 9
        assert gen.last_run_diagnostics["topup_from_fallback"] == 9


class TestDiagnostics:
    def test_diagnostics_reset_between_calls(self) -> None:
        provider = FakeProvider(
            [coverage_payload(["lighting_change"]), coverage_payload(list(ADVERSARIAL_CATEGORIES))]
        )
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=41))

        gen.generate("t", 3)
        # 1 accepted LLM scenario → 11 of 12 categories missing from LLM output.
        assert gen.last_run_diagnostics["missing_categories"] == 11

        gen.generate("t", 12)
        assert gen.last_run_diagnostics == {
            "missing_categories": 0,
            "topup_from_fallback": 0,
        }

    def test_wholesale_fallback_reports_zero_missing_categories(self) -> None:
        """Full fallback degradation is complete coverage by construction."""
        provider = FakeProvider(["not json at all"])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator(seed=55))
        scenarios = gen.generate("t", 12)

        assert len(scenarios) == 12
        assert {s.category for s in scenarios} == set(ADVERSARIAL_CATEGORIES)
        assert gen.last_run_diagnostics == {
            "missing_categories": 0,
            "topup_from_fallback": 12,
        }

    def test_zero_n_leaves_neutral_diagnostics(self) -> None:
        gen = LLMScenarioGenerator(FakeProvider([]))
        assert gen.generate("t", 0) == []
        assert gen.last_run_diagnostics == {
            "missing_categories": 0,
            "topup_from_fallback": 0,
        }
