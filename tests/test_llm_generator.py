"""Tests for the LLM-powered adversarial scenario generator.

All tests run offline: scenario generation is exercised through fake
:class:`ScenarioProvider` stubs, and the HTTP layer of
:class:`OpenAICompatibleProvider` is monkeypatched — no network I/O occurs.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from validsim.scenarios import generator as generator_mod
from validsim.scenarios import llm_generator as llm_mod
from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    ScenarioGenerator,
)
from validsim.scenarios.llm_generator import (
    LLMScenarioGenerator,
    OpenAICompatibleProvider,
    ScenarioParseError,
    ScenarioProviderError,
    build_scenario_prompt,
    create_scenario_generator,
)


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


def clean_json(n: int, category: str = "lighting_change") -> str:
    return json.dumps([scenario_obj(category) for _ in range(n)])


class TestCleanOutput:
    def test_valid_json_array_is_accepted_verbatim(self) -> None:
        provider = FakeProvider([clean_json(3)])
        gen = LLMScenarioGenerator(provider, fallback=ScenarioGenerator())
        scenarios = gen.generate("pick-place", 3)

        assert len(scenarios) == 3
        assert [s.id for s in scenarios] == ["llm-0", "llm-1", "llm-2"]
        assert all(s.category == "lighting_change" for s in scenarios)
        assert all(s.params == {"lux": 100} for s in scenarios)
        assert gen.fallback_used is False
        assert gen.last_fallback_reason is None
        assert gen.scenarios_generated == 3

    def test_prompt_is_versioned_and_sent_to_provider(self) -> None:
        provider = FakeProvider([clean_json(1)])
        LLMScenarioGenerator(provider).generate("my-task", 1)
        prompt = provider.prompts[0]
        assert "my-task" in prompt
        assert llm_mod._PROMPT_VERSION in prompt


class TestMessyOutput:
    def test_fenced_json_with_prose_is_tolerated(self) -> None:
        raw = (
            "Sure! Here are your adversarial scenarios:\n"
            "```json\n" + clean_json(2, "sensor_degradation") + "\n```\n"
            "Let me know if you need more."
        )
        gen = LLMScenarioGenerator(FakeProvider([raw]))
        scenarios = gen.generate("t", 2)
        assert len(scenarios) == 2
        assert all(s.category == "sensor_degradation" for s in scenarios)
        assert gen.fallback_used is False

    def test_difficulty_is_clamped_or_defaulted(self) -> None:
        payload = json.dumps(
            [
                scenario_obj(name="too hot", difficulty=5.0),
                scenario_obj(name="too cold", difficulty=-3.0),
                scenario_obj(name="not a number", difficulty="hard"),
                scenario_obj(name="missing", difficulty=None),
            ]
        )
        gen = LLMScenarioGenerator(FakeProvider([payload]))
        scenarios = gen.generate("t", 4)
        assert [s.difficulty for s in scenarios] == [1.0, 0.0, 0.5, 0.5]

    def test_params_and_name_edge_cases(self) -> None:
        payload = json.dumps(
            [
                scenario_obj(params="not-a-dict"),
                {k: v for k, v in scenario_obj().items() if k != "params"},
                scenario_obj(name="   "),
                scenario_obj(name=42),
                "not-an-object",
            ]
        )
        gen = LLMScenarioGenerator(FakeProvider([payload]))
        scenarios = gen.generate("t", 2)
        assert len(scenarios) == 2
        assert all(s.params == {} for s in scenarios)
        # The 2 valid items exactly satisfy n=2: no top-up, no fallback flag.
        assert gen.fallback_used is False
        assert gen.last_fallback_reason is None


class TestInvalidCategories:
    def test_unknown_categories_dropped_and_topup_from_fallback(self) -> None:
        payload = json.dumps(
            [
                scenario_obj(category="laser_blindness"),
                scenario_obj(category="lighting_change", name="keep me"),
                scenario_obj(category=""),
                scenario_obj(category="LIGHTING_CHANGE"),
            ]
        )
        gen = LLMScenarioGenerator(FakeProvider([payload]), fallback=ScenarioGenerator(seed=5))
        scenarios = gen.generate("t", 6)

        assert len(scenarios) == 6
        assert all(s.category in ADVERSARIAL_CATEGORIES for s in scenarios)
        assert scenarios[0].id == "llm-0"
        assert scenarios[0].name == "keep me"
        # The remaining five were topped up deterministically from the fallback.
        assert [s.id for s in scenarios[1:]] == [f"adv-t-{i:04d}" for i in range(5)]
        assert gen.fallback_used is True
        assert gen.last_fallback_reason is not None

    def test_extra_items_are_truncated_to_n(self) -> None:
        gen = LLMScenarioGenerator(FakeProvider([clean_json(9)]))
        scenarios = gen.generate("t", 4)
        assert len(scenarios) == 4
        assert [s.id for s in scenarios] == ["llm-0", "llm-1", "llm-2", "llm-3"]


class TestFallbackBehaviour:
    def test_garbage_response_falls_back_wholesale(self) -> None:
        provider = FakeProvider(["I cannot help with that. [unclosed"])
        fallback = ScenarioGenerator(seed=11)
        gen = LLMScenarioGenerator(provider, fallback=fallback)
        scenarios = gen.generate("t", 12)

        assert scenarios == fallback.generate("t", 12)
        assert gen.fallback_used is True
        assert "ScenarioParseError" in (gen.last_fallback_reason or "")

    def test_provider_exception_retries_then_falls_back(self) -> None:
        provider = FakeProvider([ScenarioProviderError("boom")])
        gen = LLMScenarioGenerator(provider, max_retries=2)
        scenarios = gen.generate("t", 3)

        assert len(scenarios) == 3
        assert provider.calls == 3  # 1 initial attempt + 2 retries
        assert gen.fallback_used is True
        assert "boom" in (gen.last_fallback_reason or "")

    def test_transient_failure_recovers_on_retry(self) -> None:
        provider = FakeProvider([ScenarioProviderError("flaky"), clean_json(2)])
        gen = LLMScenarioGenerator(provider, max_retries=1)
        scenarios = gen.generate("t", 2)

        assert len(scenarios) == 2
        assert provider.calls == 2
        assert gen.fallback_used is False
        assert gen.last_fallback_reason is None

    def test_zero_and_negative_n(self) -> None:
        provider = FakeProvider([clean_json(1)])
        gen = LLMScenarioGenerator(provider)
        assert gen.generate("t", 0) == []
        assert provider.calls == 0  # never asks the model for nothing
        with pytest.raises(ValueError):
            gen.generate("t", -1)

    def test_scenarios_generated_accumulates_across_calls(self) -> None:
        gen = LLMScenarioGenerator(FakeProvider([clean_json(2)]))
        gen.generate("a", 2)
        gen.generate("b", 5)
        assert gen.scenarios_generated == 7

    def test_satisfies_rule_based_contract(self) -> None:
        """Drop-in compatible: same call shape and result type as the MVP."""
        provider = FakeProvider(["total garbage"])
        gen = LLMScenarioGenerator(provider)
        scenarios = gen.generate("t", 12)
        assert {s.category for s in scenarios} == set(ADVERSARIAL_CATEGORIES)


class TestPrompt:
    def test_prompt_contains_schema_taxonomy_examples_and_exclusivity(self) -> None:
        prompt = build_scenario_prompt("pick-place", 5, ADVERSARIAL_CATEGORIES)
        assert "Return ONLY the JSON array" in prompt
        assert "STRICT JSON array" in prompt
        assert "pick-place" in prompt
        for category in ADVERSARIAL_CATEGORIES:
            assert category in prompt
        assert '"difficulty"' in prompt and '"params"' in prompt
        # Two few-shot examples: both category values must appear.
        assert prompt.count('"category"') >= 2
        assert "lighting_change" in prompt and "sensor_degradation" in prompt


class TestOpenAICompatibleProvider:
    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDSIM_LLM_API_KEY", raising=False)
        with pytest.raises(ScenarioProviderError, match="API key"):
            OpenAICompatibleProvider()

    def test_env_defaults_and_url_joining(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_LLM_API_KEY", "sk-test")
        monkeypatch.setenv("VALIDSIM_LLM_BASE_URL", "https://gw.example.com/v1/")
        monkeypatch.setenv("VALIDSIM_LLM_MODEL", "custom-model")
        provider = OpenAICompatibleProvider()
        assert provider.url == "https://gw.example.com/v1/chat/completions"
        assert provider.model == "custom-model"

    def test_complete_posts_bearer_request_and_extracts_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {"choices": [{"message": {"content": "hello scenarios"}}]}

        class FakeClient:
            def __init__(self, **kwargs: Any) -> None:
                captured["client_kwargs"] = kwargs

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def post(self, url: str, json: Any = None, headers: Any = None) -> FakeResponse:
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return FakeResponse()

        monkeypatch.setattr(llm_mod.httpx, "Client", FakeClient)
        provider = OpenAICompatibleProvider(api_key="sk-abc", base_url="https://x.dev/v1")
        assert provider.complete("make scenarios") == "hello scenarios"
        assert captured["url"] == "https://x.dev/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer sk-abc"
        assert captured["json"]["temperature"] == 0.8
        assert captured["json"]["messages"] == [{"role": "user", "content": "make scenarios"}]

    def test_http_error_becomes_provider_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class ErrorResponse:
            status_code = 500
            request = httpx.Request("POST", "https://x.dev/v1/chat/completions")

            def raise_for_status(self) -> None:
                response = httpx.Response(500, request=self.request)
                raise httpx.HTTPStatusError("server error", request=self.request, response=response)

            def json(self) -> dict[str, Any]:
                raise AssertionError("unreachable")

        class FakeClient:
            def __init__(self, **kwargs: Any) -> None:
                return None

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def post(self, url: str, json: Any = None, headers: Any = None) -> ErrorResponse:
                return ErrorResponse()

        monkeypatch.setattr(llm_mod.httpx, "Client", FakeClient)
        provider = OpenAICompatibleProvider(api_key="sk-abc")
        with pytest.raises(ScenarioProviderError, match="HTTP 500"):
            provider.complete("hi")


class TestFactory:
    def test_returns_rule_based_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDSIM_LLM_API_KEY", raising=False)
        gen = create_scenario_generator()
        assert isinstance(gen, ScenarioGenerator)
        assert not isinstance(gen, LLMScenarioGenerator)

    def test_returns_llm_generator_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_LLM_API_KEY", "sk-presence-check")
        gen = create_scenario_generator()
        assert isinstance(gen, LLMScenarioGenerator)
        assert isinstance(gen.provider, OpenAICompatibleProvider)


class TestParseHelper:
    def test_extract_json_array_rejects_non_arrays(self) -> None:
        with pytest.raises(ScenarioParseError):
            llm_mod._extract_json_array('{"category": "lighting_change"}')
        with pytest.raises(ScenarioParseError):
            llm_mod._extract_json_array("no brackets at all")
