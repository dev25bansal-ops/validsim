"""LLM-powered adversarial scenario generation (Week-4 upgrade).

This module adds an optional large-language-model backend on top of the
rule-based :class:`~validsim.scenarios.generator.ScenarioGenerator`. The
public surface is intentionally identical — :meth:`LLMScenarioGenerator.
generate` accepts ``(task_id, n)`` and returns ``list[AdversarialScenario]``
— so the LLM path is a drop-in replacement wherever the rule-based
generator is used today. :func:`create_scenario_generator` picks the right
backend from the environment so callers can swap without code changes.

.. warning::
    **Production notes.** All prompt text is authored and *versioned in this
    module* (see :data:`_PROMPT_VERSION` and :func:`build_scenario_prompt`);
    changing a prompt is a schema-visible change and should bump the version.
    Model output is treated as **untrusted data**: every candidate scenario
    is schema-validated against the twelve-category taxonomy
    (:data:`~validsim.scenarios.generator.ADVERSARIAL_CATEGORIES`) and its
    difficulty is clamped before it is ever constructed as an
    :class:`~validsim.scenarios.generator.AdversarialScenario`. LLM output is
    never executed as code, never interpolated into shell commands, and never
    reaches the simulator unvalidated. Any provider or parse failure degrades
    gracefully to the deterministic rule-based fallback, so a missing or
    misbehaving LLM endpoint can never take validation runs down with it.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

import httpx

from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    AdversarialScenario,
    ScenarioGenerator,
)

__all__ = [
    "ScenarioProviderError",
    "ScenarioParseError",
    "ScenarioProvider",
    "OpenAICompatibleProvider",
    "build_scenario_prompt",
    "LLMScenarioGenerator",
    "create_scenario_generator",
]

#: Semantic version of the prompt template below. Bump on every material
#: change so prompt revisions are auditable alongside scenario metadata.
_PROMPT_VERSION = "2025-w24-v1"

#: Environment variables consulted for provider defaults.
_BASE_URL_ENV = "VALIDSIM_LLM_BASE_URL"
_API_KEY_ENV = "VALIDSIM_LLM_API_KEY"
_MODEL_ENV = "VALIDSIM_LLM_MODEL"

#: Used when ``VALIDSIM_LLM_BASE_URL`` is unset (OpenAI-compatible endpoint).
_DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Chat model used when neither the ``model`` argument nor
#: ``VALIDSIM_LLM_MODEL`` provides one.
_DEFAULT_MODEL = "gpt-4o-mini"


class ScenarioProviderError(RuntimeError):
    """Raised when the LLM backend is misconfigured or the request fails."""


class ScenarioParseError(ValueError):
    """Raised when a provider response cannot be parsed as a JSON array."""


@runtime_checkable
class ScenarioProvider(Protocol):
    """Minimal contract for any text-completion backend.

    Implementations take a fully rendered prompt string and return the raw
    assistant text. Raising :class:`ScenarioProviderError` (or any other
    exception) on failure is the expected signal for the generator to fall
    back to rule-based scenarios.
    """

    def complete(self, prompt: str) -> str:
        """Return the raw completion text for ``prompt``."""
        ...  # pragma: no cover - protocol definition


class OpenAICompatibleProvider:
    """Provider speaking the OpenAI ``/chat/completions`` HTTP dialect.

    Works against OpenAI itself and any compatible gateway (Azure OpenAI,
    vLLM, Ollama's OpenAI shim, NVIDIA NIM, ...) by pointing
    ``VALIDSIM_LLM_BASE_URL`` at the deployment root.

    Args:
        base_url: API root, e.g. ``https://api.openai.com/v1``. Falls back to
            ``VALIDSIM_LLM_BASE_URL``, then :data:`_DEFAULT_BASE_URL`.
        api_key: Bearer token. Falls back to ``VALIDSIM_LLM_API_KEY``.
            Required — :class:`ScenarioProviderError` is raised if absent.
        model: Chat model name. Falls back to ``VALIDSIM_LLM_MODEL``, then
            ``"gpt-4o-mini"``.
        timeout: Per-request timeout in seconds.

    Raises:
        ScenarioProviderError: If no API key is available.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Resolve endpoint, key, and model from args/env and validate config."""
        resolved_base = (
            base_url
            or os.environ.get(_BASE_URL_ENV, "").strip()
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        resolved_key = api_key or os.environ.get(_API_KEY_ENV, "").strip()
        if not resolved_key:
            raise ScenarioProviderError(
                f"no API key configured: pass api_key= or set {_API_KEY_ENV}"
            )
        self._url = f"{resolved_base}/chat/completions"
        self._api_key = resolved_key
        self._model = (
            model or os.environ.get(_MODEL_ENV, "").strip() or _DEFAULT_MODEL
        )
        self._timeout = timeout

    @property
    def url(self) -> str:
        """Fully-qualified chat-completions endpoint for this provider."""
        return self._url

    @property
    def model(self) -> str:
        """Chat model name used for requests."""
        return self._model

    def complete(self, prompt: str) -> str:
        """POST ``prompt`` as a single user message; return message content.

        Raises:
            ScenarioProviderError: On any transport error, non-2xx status,
                malformed JSON body, or missing ``choices[0].message.content``.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            # Creative variation is the point of adversarial generation;
            # structural discipline is enforced by schema validation instead.
            "temperature": 0.8,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(self._url, json=payload, headers=headers)
                response.raise_for_status()
                data: Any = response.json()
        except httpx.HTTPStatusError as exc:
            raise ScenarioProviderError(
                f"HTTP {exc.response.status_code} from {self._url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ScenarioProviderError(f"LLM request failed: {exc}") from exc
        except ValueError as exc:
            raise ScenarioProviderError(f"LLM response was not valid JSON: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ScenarioProviderError(
                "malformed chat-completion response (missing choices[0].message.content)"
            ) from exc
        if not isinstance(content, str):
            raise ScenarioProviderError("chat-completion content is not a string")
        return content


def build_scenario_prompt(
    task_id: str,
    n: int,
    categories: Sequence[str] = ADVERSARIAL_CATEGORIES,
) -> str:
    """Render the versioned (:data:`_PROMPT_VERSION`) scenario-generation prompt.

    The prompt demands a *strict* JSON array of exactly ``n`` objects with a
    fixed schema, restricts ``category`` to the exact taxonomy strings, and
    embeds two few-shot examples to anchor the output shape.

    Args:
        task_id: Identifier of the task under validation (context only).
        n: Number of scenarios requested from the model.
        categories: Allowed category strings; defaults to the canonical
            twelve-category taxonomy.

    Returns:
        The fully rendered prompt text.
    """
    taxonomy = ", ".join(categories)
    return f"""\
You are an adversarial test-scenario designer for robot-simulation validation \
(prompt version {_PROMPT_VERSION}).

Task under validation: "{task_id}"

Return a STRICT JSON array containing exactly {n} scenario objects. Each array \
element MUST be a JSON object with EXACTLY these keys:
- "category": one of the following exact strings (no variants, no synonyms): \
[{taxonomy}]
- "name": a short human-readable string naming the concrete scenario.
- "params": a JSON object of simulation parameters (numbers, strings, or \
booleans) appropriate to the category.
- "difficulty": a number between 0 and 1 (higher = harder for the robot).

Two examples of correctly formatted elements (do not copy them verbatim; \
invent varied, realistic adversarial conditions):
[
  {{
    "category": "lighting_change",
    "name": "Sudden sunset glare during grasp",
    "params": {{"lux": 9200, "color_temperature_k": 3100, "flicker_hz": 12.5}},
    "difficulty": 0.35
  }},
  {{
    "category": "sensor_degradation",
    "name": "Depth camera dropout mid-reach",
    "params": {{"camera_dropout_rate": 0.45, "depth_noise_mm": 62.0, "latency_ms": 180}},
    "difficulty": 0.72
  }}
]

Return ONLY the JSON array — no prose, no commentary, no markdown fences. \
Output {n} elements total.
"""


def _clamp01(value: float) -> float:
    """Constrain ``value`` to the inclusive ``[0.0, 1.0]`` difficulty range."""
    return max(0.0, min(1.0, value))


def _extract_json_array(text: str) -> list[Any]:
    """Parse the first JSON array out of raw model output.

    Tolerates markdown code fences (```json ... ```) and leading/trailing
    prose by slicing from the first ``[`` to the last ``]`` before parsing.

    Raises:
        ScenarioParseError: If no bracket-delimited array is present, the
            slice is not valid JSON, or the parsed value is not a list.
    """
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ScenarioParseError("no JSON array found in model output")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ScenarioParseError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ScenarioParseError("model output JSON is not an array")
    return data


def _coerce_difficulty(raw: Any) -> float:
    """Best-effort numeric difficulty: float-coercible and finite → clamped.

    Anything else (missing, string, ``None``, NaN/inf) degrades to 0.5.
    """
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(value):
        return 0.5
    return round(_clamp01(value), 4)


def _validate_item(item: Any) -> AdversarialScenario | None:
    """Convert one parsed JSON element into a scenario, or ``None`` to drop it.

    Drop rules: non-object elements, categories outside the taxonomy, and
    empty/non-string names. ``params`` defaults to ``{}`` unless a dict is
    provided; ``difficulty`` is clamped into ``[0, 1]`` (default 0.5).
    """
    if not isinstance(item, dict):
        return None
    category = item.get("category")
    if not isinstance(category, str) or category not in ADVERSARIAL_CATEGORIES:
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    params = item.get("params")
    return AdversarialScenario(
        id="",  # placeholder; indices are assigned by the generator
        category=category,
        name=name.strip(),
        params=params if isinstance(params, dict) else {},
        difficulty=_coerce_difficulty(item.get("difficulty")),
    )


class LLMScenarioGenerator:
    """Adversarial scenario generator backed by an LLM provider.

    Drop-in replacement for :class:`~validsim.scenarios.generator.
    ScenarioGenerator`: :meth:`generate` always returns exactly ``n`` valid
    :class:`~validsim.scenarios.generator.AdversarialScenario` objects. The
    model is asked for ``n`` scenarios; whatever survives schema validation
    is topped up deterministically from the rule-based ``fallback``. If every
    provider attempt fails (transport or parse errors, after ``max_retries``
    retries), the entire batch comes from the fallback so the simulator is
    never starved.

    Attributes:
        scenarios_generated: Cumulative count of scenarios produced across
            all :meth:`generate` calls.
        last_run_used_fallback: Whether the most recent call used fallback
            scenarios (either as top-up or wholesale).
        last_fallback_reason: Human-readable reason for the most recent
            fallback use, or ``None`` when the last call was clean.
    """

    def __init__(
        self,
        provider: ScenarioProvider,
        fallback: ScenarioGenerator | None = None,
        max_retries: int = 1,
    ) -> None:
        """Bind a provider, a deterministic fallback, and a retry budget.

        Args:
            provider: Text-completion backend used to invent scenarios.
            fallback: Rule-based generator for top-up and error recovery;
                defaults to a fresh :class:`ScenarioGenerator`.
            max_retries: Extra attempts after the first provider/parse
                failure (total attempts = ``1 + max_retries``).
        """
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self._provider = provider
        self._fallback = fallback if fallback is not None else ScenarioGenerator()
        self._max_retries = max_retries
        self.scenarios_generated = 0
        self.last_run_used_fallback = False
        self.last_fallback_reason: str | None = None

    @property
    def provider(self) -> ScenarioProvider:
        """The text-completion backend this generator queries."""
        return self._provider

    @property
    def fallback_used(self) -> bool:
        """Whether the *last* :meth:`generate` call touched the fallback."""
        return self.last_run_used_fallback

    def generate(self, task_id: str, n: int) -> list[AdversarialScenario]:
        """Return ``n`` adversarial scenarios for ``task_id``.

        Flow: render prompt → ask the provider → extract and schema-validate
        the JSON array → top up shortfalls from the deterministic fallback.
        On repeated provider/parse errors the full batch comes from the
        fallback and :attr:`last_fallback_reason` records why.

        Raises:
            ValueError: If ``n`` is negative (mirrors the rule-based API).
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        self.last_run_used_fallback = False
        self.last_fallback_reason = None
        if n == 0:
            return []

        prompt = build_scenario_prompt(task_id, n)
        accepted: list[AdversarialScenario] = []
        last_error: str | None = None

        for _attempt in range(1 + self._max_retries):
            try:
                raw = self._provider.complete(prompt)
                parsed = _extract_json_array(raw)
            except Exception as exc:  # noqa: BLE001 - any provider failure degrades
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            # This attempt parsed cleanly; any earlier transient error no
            # longer explains a validation shortfall below.
            last_error = None
            accepted = self._assign_ids(_validate_item(item) for item in parsed)[:n]
            break

        if last_error is not None and not accepted:
            # Every attempt failed: degrade wholesale to deterministic output.
            scenarios = self._fallback.generate(task_id, n)
            self.last_run_used_fallback = True
            self.last_fallback_reason = last_error
        else:
            needed = n - len(accepted)
            if needed > 0:
                accepted.extend(self._fallback.generate(task_id, needed))
                self.last_run_used_fallback = True
                self.last_fallback_reason = (
                    last_error
                    if last_error is not None
                    else f"only {len(accepted) - needed} of {n} LLM scenarios passed validation"
                )
            scenarios = accepted

        self.scenarios_generated += len(scenarios)
        return scenarios

    @staticmethod
    def _assign_ids(items: Iterable[AdversarialScenario | None]) -> list[AdversarialScenario]:
        """Drop invalid items and assign stable ``llm-<i>`` ids to survivors."""
        scenarios: list[AdversarialScenario] = []
        for item in items:
            if item is None:
                continue
            scenarios.append(
                AdversarialScenario(
                    id=f"llm-{len(scenarios)}",
                    category=item.category,
                    name=item.name,
                    params=item.params,
                    difficulty=item.difficulty,
                )
            )
        return scenarios


def create_scenario_generator(
    seed: int = 42,
) -> LLMScenarioGenerator | ScenarioGenerator:
    """Environment-driven factory so callers can swap backends with no code change.

    Returns an :class:`LLMScenarioGenerator` wired to
    :class:`OpenAICompatibleProvider` when ``VALIDSIM_LLM_API_KEY`` is set;
    otherwise the deterministic rule-based :class:`ScenarioGenerator` (the
    historical default). The rule-based path performs no network I/O and is
    fully reproducible, which is why it remains the default.

    Args:
        seed: Seed for the rule-based fallback (and the pure rule-based path).
    """
    if os.environ.get(_API_KEY_ENV, "").strip():
        return LLMScenarioGenerator(
            OpenAICompatibleProvider(), fallback=ScenarioGenerator(seed=seed)
        )
    return ScenarioGenerator(seed=seed)
