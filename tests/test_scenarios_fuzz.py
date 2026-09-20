"""Randomized (property-style) fuzz tests for adversarial scenario generation.

This module deliberately avoids any external property-testing framework
(``hypothesis`` and friends). Instead it drives a fixed master
``random.Random`` seed through ~150 randomized iterations per property, so the
suite stays dependency-free yet sweeps a wide space of ``(seed, task_id, n)``
inputs. Because the master seed is pinned, every iteration is fully
deterministic: a green run is reproducible byte-for-byte and there is no room
for flakiness.

Two surfaces are stress-tested:

``ScenarioGenerator.generate`` (rule-based, deterministic)
    * always returns exactly ``n`` scenarios;
    * every returned ``category`` belongs to :data:`ADVERSARIAL_CATEGORIES`;
    * every ``difficulty`` stays inside the clamped ``[0.05, 0.95]`` band;
    * scenario ``id``s are unique within a batch;
    * the whole batch is reproducible for the same ``(seed, task_id)``;
    * a non-zero ``difficulty_bias`` never perturbs ids/categories/params and
      still keeps difficulty inside the clamp band;
    * any ``n >= 12`` guarantees full twelve-category coverage.

``LLMScenarioGenerator.generate`` (untrusted model output)
    * fed deliberately hostile payloads — non-taxonomy categories, absurd
      difficulty values, prose-wrapped JSON, the empty array, total garbage,
      malformed JSON, top-level objects and arrays of non-objects — it must
      *never* raise and must *always* hand back exactly ``n`` schema-valid
      scenarios, degrading to the deterministic rule-based fallback whenever
      the model output cannot satisfy the contract.

The source under test is never modified.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Callable

from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    AdversarialScenario,
    ScenarioGenerator,
)
from validsim.scenarios.llm_generator import (
    LLMScenarioGenerator,
    ScenarioProviderError,
)

# Fixed master seed -> the whole randomized sweep is reproducible.
MASTER_SEED = 0x5CE_0F00  # "scenario fuzz"
# Number of random inputs each generator property is exercised against.
ITERATIONS = 150
# Smaller budget for the LLM sweep: each call may run a fallback batch.
LLM_ITERATIONS = 80

# Characters used to synthesize plausible-but-arbitrary task ids.
_TASK_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


def _random_task_id(rng: random.Random) -> str:
    """Return a random, non-empty task id of length 1..14."""
    length = rng.randint(1, 14)
    return "".join(rng.choice(_TASK_ID_ALPHABET) for _ in range(length))


def _scenario_key(s: AdversarialScenario) -> tuple[str, str, dict[str, Any], float]:
    """A comparable fingerprint capturing everything ``generate`` decides."""
    return (s.id, s.category, s.params, s.difficulty)


class TestRuleBasedProperties:
    """Invariants that must hold for *every* random ``(seed, task_id, n)``."""

    def test_returns_exactly_n_scenarios(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 60)
            scenarios = ScenarioGenerator(seed=seed).generate(task_id, n)
            assert len(scenarios) == n

    def test_categories_always_in_taxonomy(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 60)
            scenarios = ScenarioGenerator(seed=seed).generate(task_id, n)
            assert all(s.category in ADVERSARIAL_CATEGORIES for s in scenarios)

    def test_difficulty_always_within_clamp_band(self) -> None:
        """The generator clamps sampled difficulty into ``[0.05, 0.95]``."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 60)
            scenarios = ScenarioGenerator(seed=seed).generate(task_id, n)
            for s in scenarios:
                assert 0.05 <= s.difficulty <= 0.95

    def test_ids_unique_within_batch(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 60)
            scenarios = ScenarioGenerator(seed=seed).generate(task_id, n)
            ids = [s.id for s in scenarios]
            assert len(set(ids)) == n
            assert all(ids)  # no empty identifiers

    def test_determinism_for_same_seed_and_task(self) -> None:
        """Two generators sharing a seed, fed the same task_id, agree exactly."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 60)
            first = ScenarioGenerator(seed=seed).generate(task_id, n)
            second = ScenarioGenerator(seed=seed).generate(task_id, n)
            assert [_scenario_key(s) for s in first] == [_scenario_key(s) for s in second]

    def test_full_coverage_when_n_at_least_twelve(self) -> None:
        """Any ``n >= 12`` cycles through all twelve categories at least once."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(12, 60)
            scenarios = ScenarioGenerator(seed=seed).generate(task_id, n)
            assert {s.category for s in scenarios} == set(ADVERSARIAL_CATEGORIES)

    def test_difficulty_bias_preserves_structure_and_bounds(self) -> None:
        """Bias shifts difficulty only: ids/categories/params are untouched and
        the clamped ``[0.05, 0.95]`` band still holds at every bias."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 40)
            bias = rng.uniform(-1.0, 1.0)
            unbiased = ScenarioGenerator(seed=seed).generate(task_id, n)
            biased = ScenarioGenerator(seed=seed, difficulty_bias=bias).generate(task_id, n)
            assert [s.id for s in biased] == [s.id for s in unbiased]
            assert [s.category for s in biased] == [s.category for s in unbiased]
            assert [s.params for s in biased] == [s.params for s in unbiased]
            for s in biased:
                assert 0.05 <= s.difficulty <= 0.95

    def test_params_non_empty_and_well_formed(self) -> None:
        """Every scenario carries a non-empty dict of category parameters."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            seed = rng.randrange(0, 2**31)
            task_id = _random_task_id(rng)
            n = rng.randint(1, 40)
            for s in ScenarioGenerator(seed=seed).generate(task_id, n):
                assert isinstance(s.params, dict) and s.params
                assert s.name.startswith(s.category[:4].title())


# ---------------------------------------------------------------------------
# LLM path: hostile, untrusted model output must degrade gracefully.
# ---------------------------------------------------------------------------
class FakeProvider:
    """Provider stub replaying a single canned response; records every prompt."""

    def __init__(self, response: str | Exception) -> None:
        self._response = response
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _obj(category: Any, name: Any, params: Any, difficulty: Any) -> dict[str, Any]:
    return {"category": category, "name": name, "params": params, "difficulty": difficulty}


# Each factory returns a raw provider response string that a misbehaving model
# might emit. None of them may crash the generator or yield an invalid batch.
def _payload_non_taxonomy_only(rng: random.Random) -> str:
    bogus = ["laser_blindness", "quantum_glitch", "LIGHTING_CHANGE", "", "banana_stand"]
    items = [_obj(rng.choice(bogus), f"bad {i}", {}, 0.5) for i in range(rng.randint(1, 8))]
    return json.dumps(items)


def _payload_absurd_difficulty(rng: random.Random) -> str:
    bad = [1e9, -1e9, -0.001, 1.0001, "hard", None, True, float("nan"), float("inf")]
    items = [
        _obj(rng.choice(list(ADVERSARIAL_CATEGORIES)), f"d {i}", {}, rng.choice(bad))
        for i in range(rng.randint(1, 8))
    ]
    return json.dumps(items)


def _payload_prose_wrapped(rng: random.Random) -> str:
    items = [
        _obj(cat, f"glare {i}", {"lux": 100}, 0.3)
        for i, cat in enumerate(ADVERSARIAL_CATEGORIES[: rng.randint(1, 12)])
    ]
    return (
        "Sure! Here you go:\n```json\n"
        + json.dumps(items)
        + "\n```\nHope that helps, let me know if you need more!"
    )


def _payload_empty_array(_: random.Random) -> str:
    return "[]"


def _payload_total_garbage(rng: random.Random) -> str:
    words = ["lorem", "ipsum", "I", "cannot", "help", "{", "}", "[unclosed", "###"]
    return " ".join(rng.choice(words) for _ in range(rng.randint(3, 20)))


def _payload_malformed_json(_: random.Random) -> str:
    return '[{"category": "lighting_change", "name": "oops", '


def _payload_top_level_object(_: random.Random) -> str:
    return json.dumps({"category": "lighting_change", "name": "not an array"})


def _payload_array_of_non_objects(rng: random.Random) -> str:
    junk: list[Any] = [1, 2.5, "text", None, True, [1, 2], {"x": 1}]
    return json.dumps(rng.sample(junk, k=rng.randint(2, len(junk))))


def _payload_missing_keys(rng: random.Random) -> str:
    items: list[Any] = []
    for _ in range(rng.randint(1, 6)):
        item = _obj(rng.choice(list(ADVERSARIAL_CATEGORIES)), "named", {}, 0.5)
        # Drop a random required key to exercise tolerant validation.
        item.pop(rng.choice(["category", "name", "params", "difficulty"]), None)
        items.append(item)
    return json.dumps(items)


def _payload_params_wrong_type(rng: random.Random) -> str:
    items = [
        _obj(cat, "p", rng.choice(["not-a-dict", 123, None, [1, 2], "x"]), 0.5)
        for cat in ADVERSARIAL_CATEGORIES[: rng.randint(1, 12)]
    ]
    return json.dumps(items)


#: Payload factories exercised by the LLM fuzz sweep.
_LLM_PAYLOAD_FACTORIES: list[Callable[[random.Random], str]] = [
    _payload_non_taxonomy_only,
    _payload_absurd_difficulty,
    _payload_prose_wrapped,
    _payload_empty_array,
    _payload_total_garbage,
    _payload_malformed_json,
    _payload_top_level_object,
    _payload_array_of_non_objects,
    _payload_missing_keys,
    _payload_params_wrong_type,
]

#: Factories that can never satisfy the batch on their own, so the generator
#: must route through the deterministic fallback every time.
_MUST_FALL_BACK = {
    _payload_non_taxonomy_only.__name__,
    _payload_empty_array.__name__,
    _payload_total_garbage.__name__,
    _payload_malformed_json.__name__,
    _payload_top_level_object.__name__,
    _payload_array_of_non_objects.__name__,
}


def _assert_valid_batch(scenarios: list[AdversarialScenario], n: int) -> None:
    """Assert the universal post-condition of ``LLMScenarioGenerator.generate``."""
    assert len(scenarios) == n
    ids = [s.id for s in scenarios]
    assert len(set(ids)) == n  # unique within the batch
    assert all(ids)  # never an empty identifier
    for s in scenarios:
        assert s.category in ADVERSARIAL_CATEGORIES
        assert 0.0 <= s.difficulty <= 1.0
        assert isinstance(s.params, dict)
        assert isinstance(s.name, str) and s.name


class TestLLMAdversarialProperties:
    """No hostile model output may raise or yield an invalid scenario."""

    def test_never_raises_and_always_valid(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(LLM_ITERATIONS):
            factory = rng.choice(_LLM_PAYLOAD_FACTORIES)
            raw = factory(rng)
            n = rng.randint(1, 24)
            gen = LLMScenarioGenerator(
                FakeProvider(raw), fallback=ScenarioGenerator(seed=rng.randrange(0, 2**31))
            )
            scenarios = gen.generate(_random_task_id(rng), n)
            _assert_valid_batch(scenarios, n)

    def test_must_use_fallback_when_output_is_unusable(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(LLM_ITERATIONS):
            factory = rng.choice(_LLM_PAYLOAD_FACTORIES)
            raw = factory(rng)
            n = rng.randint(1, 24)
            gen = LLMScenarioGenerator(FakeProvider(raw))
            scenarios = gen.generate(_random_task_id(rng), n)
            _assert_valid_batch(scenarios, n)
            if factory.__name__ in _MUST_FALL_BACK:
                assert gen.fallback_used is True

    def test_provider_exception_never_propagates(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(LLM_ITERATIONS):
            exc = rng.choice(
                [
                    ScenarioProviderError("boom"),
                    RuntimeError("unexpected"),
                    ValueError("bad"),
                    TimeoutError("slow"),
                ]
            )
            n = rng.randint(1, 24)
            gen = LLMScenarioGenerator(FakeProvider(exc), max_retries=rng.randint(0, 2))
            scenarios = gen.generate(_random_task_id(rng), n)
            _assert_valid_batch(scenarios, n)
            assert gen.fallback_used is True

    def test_difficulty_coerced_into_unit_interval(self) -> None:
        """Absurd numeric/string difficulties collapse into ``[0, 1]``."""
        rng = random.Random(MASTER_SEED)
        for _ in range(LLM_ITERATIONS):
            raw = _payload_absurd_difficulty(rng)
            n = rng.randint(1, 12)
            gen = LLMScenarioGenerator(FakeProvider(raw))
            scenarios = gen.generate(_random_task_id(rng), n)
            for s in scenarios:
                assert 0.0 <= s.difficulty <= 1.0
                assert not (math.isnan(s.difficulty) or math.isinf(s.difficulty))
