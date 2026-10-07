"""LLM-backed adversarial scenario generation — **opt-in, and not yet wired in**.

This module implements an optional large-language-model backend on top of the
rule-based :class:`~validsim.scenarios.generator.ScenarioGenerator`. The public
surface is intentionally identical — :meth:`LLMScenarioGenerator.generate`
accepts ``(task_id, n)`` and returns ``list[AdversarialScenario]` — so the LLM
path is a drop-in replacement wherever the rule-based generator is used.

.. warning::
    **This backend is not reachable from any entry point today.** The only
    production code that generates scenarios is
    :func:`validsim.engine.pipeline.run_and_score`, which hardcodes
    ``ScenarioGenerator(seed=seed).generate(...)`` (``validsim/engine/pipeline.py:131``).
    The API, the CLI and the job worker all funnel through that one function, so
    :func:`create_scenario_generator` below has **zero production callers** and
    setting ``VALIDSIM_LLM_API_KEY`` changes nothing about a validation run.

    Treat this module as a *tested, unexercised* capability, not a shipped
    feature. :func:`current_scenario_backend` exists so an operator can ask the
    process which backend is actually in use instead of inferring it from the
    environment. Wiring the pipeline to the factory is a deliberate future
    change, and it is gated behind :data:`_ENABLE_ENV` so that when it happens
    the default behaviour — and the default cost — do not change.

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
    reaches the simulator unvalidated.

.. note::
    **Safety property.** Every provider or parse failure degrades to the
    deterministic rule-based generator, and so does every *validation*
    shortfall, so a missing, slow, rate-limited or misbehaving LLM endpoint can
    never take a validation run down or starve it of scenarios. ``generate``
    either returns exactly ``n`` scenarios or raises ``ValueError`` for a
    negative ``n``; it never returns a short batch and never propagates a
    provider exception. This is the invariant the test-suite exists to hold.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

import httpx

from validsim.scenarios.generator import (
    ADVERSARIAL_CATEGORIES,
    AdversarialScenario,
    ScenarioGenerator,
)

__all__ = [
    "current_scenario_backend",
    "llm_enabled",
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

#: Explicit opt-in switch for the LLM backend. Unset (or any non-truthy
#: value) keeps the deterministic rule-based generator. This is separate from
#: the API key on purpose: the presence of a credential must not by itself
#: change the behaviour of a validation run.
_ENABLE_ENV = "VALIDSIM_LLM_ENABLED"

#: Values of :data:`_ENABLE_ENV` that count as "on". Anything else is off.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

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
        # One client per provider, reused across every request and retry.
        self._client = httpx.Client(timeout=self._timeout)

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

        The underlying :class:`httpx.Client` is created once per provider and
        reused, so a retry does not pay for a fresh TCP/TLS handshake. The
        client is closed by :meth:`close` (and by :meth:`__enter__`/
        :meth:`__exit__`), which the owning
        :class:`LLMScenarioGenerator` calls when it is done. A provider that
        is never closed leaks the connection pool until the process exits,
        which is acceptable for a long-lived generator and preferable to
        rebuilding the pool on every single request.

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
            response = self._client.post(self._url, json=payload, headers=headers)
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

    def close(self) -> None:
        """Release the underlying connection pool. Safe to call repeatedly."""
        self._client.close()

    def __enter__(self) -> "OpenAICompatibleProvider":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False


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


#: Suffix appended to the prompt on each retry. Replaying a byte-identical
#: request is a hot loop against a rate limiter, and many gateways cache by
#: prompt hash — so the retry names the attempt and asks for a fresh sample.
_RETRY_NOTE = (
    "\n\n[ValidSim retry note] This is attempt {attempt} of {total}. The previous "
    "response was unusable. Produce a different set of scenarios: vary the names "
    "and parameters, and output exactly {n} elements."
)


def _retry_note(prompt: str, attempt: int, total: int) -> str:
    """Return ``prompt`` annotated as retry number ``attempt`` of ``total``.

    The versioned body is preserved verbatim and the note is appended, so the
    prompt version recorded in the audit trail still describes the whole
    request. ``n`` is recovered from the body's own "Output N elements"
    instruction so this helper needs no extra argument.
    """
    count = _requested_count(prompt)
    return prompt + _RETRY_NOTE.format(attempt=attempt, total=total, n=count)


def _requested_count(prompt: str) -> int:
    """Extract the requested element count from a rendered prompt.

    Parses the ``Output N elements total.`` tail that
    :func:`build_scenario_prompt` emits. Falls back to ``0`` if the marker is
    absent, which only affects the wording of the retry note.
    """
    marker = "Output "
    index = prompt.rfind(marker)
    if index == -1:
        return 0
    digits = ""
    for char in prompt[index + len(marker) :]:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else 0


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
    is topped up deterministically from the rule-based ``fallback`` — first
    prioritizing the canonical categories the LLM output failed to cover
    (category-diversity guarantee), then filling any remaining count. If
    every provider attempt fails (transport or parse errors, after
    ``max_retries`` retries), the entire batch comes from the fallback so
    the simulator is never starved.

    Attributes:
        scenarios_generated: Cumulative count of scenarios produced across
            all :meth:`generate` calls.
        last_run_used_fallback: Whether the most recent call used fallback
            scenarios (either as top-up or wholesale).
        last_fallback_reason: Human-readable reason for the most recent
            fallback use, or ``None`` when the last call was clean.
        last_run_diagnostics: Dict describing the most recent call; always
            contains ``missing_categories`` (count of the twelve canonical
            categories absent from the accepted LLM scenarios) and
            ``topup_from_fallback`` (number of fallback scenarios used).
    """

    def __init__(
        self,
        provider: ScenarioProvider,
        fallback: ScenarioGenerator | None = None,
        max_retries: int = 1,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 8.0,
    ) -> None:
        """Bind a provider, a deterministic fallback, and a retry budget.

        Args:
            provider: Text-completion backend used to invent scenarios.
            fallback: Rule-based generator for top-up and error recovery;
                defaults to a fresh :class:`ScenarioGenerator`.
            max_retries: Extra attempts after the first provider/parse
                failure (total attempts = ``1 + max_retries``).
            backoff_base_s: Delay before the *first* retry. Each subsequent
                retry doubles it. ``0.0`` disables sleeping entirely, which is
                what the test-suite and non-interactive callers want.
            backoff_max_s: Ceiling on any single backoff delay, so a large
                ``max_retries`` cannot stall a run for minutes.

        Raises:
            ValueError: If ``max_retries < 0`` or a backoff value is negative
                or ``backoff_max_s < backoff_base_s``.
        """
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        if backoff_base_s < 0 or backoff_max_s < 0:
            raise ValueError(
                "backoff values must be >= 0, got "
                f"backoff_base_s={backoff_base_s}, backoff_max_s={backoff_max_s}"
            )
        if backoff_max_s < backoff_base_s:
            raise ValueError(
                f"backoff_max_s ({backoff_max_s}) must be >= "
                f"backoff_base_s ({backoff_base_s})"
            )
        self._provider = provider
        self._fallback = fallback if fallback is not None else ScenarioGenerator()
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self.scenarios_generated = 0
        self.last_run_used_fallback = False
        self.last_fallback_reason: str | None = None
        self.last_run_diagnostics: dict[str, Any] = {}

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
        the JSON array → top up missing canonical categories from the
        deterministic fallback → fill any remaining count. On repeated
        provider/parse errors the full batch comes from the fallback and
        :attr:`last_fallback_reason` records why.

        Retries are real: attempt 1 sends the versioned prompt verbatim and each
        later attempt appends :data:`_RETRY_NOTE` and is preceded by a
        geometric, capped :meth:`_backoff_delay` sleep. A run therefore costs at
        most ``1 + max_retries`` provider calls and
        ``sum(backoff)`` seconds, and ``backoff_base_s=0.0`` removes the sleeping
        entirely for non-interactive callers.

        This method either returns exactly ``n`` valid scenarios or raises
        ``ValueError``; it never returns a short batch and never propagates a
        provider exception.

        Args:
            task_id: Identifier of the task under validation.
            n: Number of scenarios to produce. ``0`` returns ``[]`` without
                calling the provider at all.

        Returns:
            Exactly ``n`` schema-valid scenarios. Every id is task-scoped and
            unique within the batch.

        Raises:
            ValueError: If ``n`` is negative (mirrors the rule-based API).
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        self.last_run_used_fallback = False
        self.last_fallback_reason = None
        self.last_run_diagnostics = {"missing_categories": 0, "topup_from_fallback": 0}
        if n == 0:
            return []

        prompt = build_scenario_prompt(task_id, n)
        accepted: list[AdversarialScenario] = []
        last_error: str | None = None

        total_attempts = 1 + self._max_retries
        for attempt in range(1, total_attempts + 1):
            try:
                # Attempt 1 sends the versioned prompt verbatim. Later
                # attempts append a retry note: replaying a byte-identical
                # request to a rate-limited or briefly-down endpoint is a
                # hot loop that the provider is likely to reject again, so
                # the retry must differ in more than timing.
                attempt_prompt = (
                    prompt
                    if attempt == 1
                    else _retry_note(prompt, attempt, total_attempts)
                )
                raw = self._provider.complete(attempt_prompt)
                parsed = _extract_json_array(raw)
            except Exception as exc:  # noqa: BLE001 - any provider failure degrades
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < total_attempts:
                    time.sleep(self._backoff_delay(attempt))
                continue
            # This attempt parsed cleanly; any earlier transient error no
            # longer explains a validation shortfall below.
            last_error = None
            accepted = self._assign_ids(
                (_validate_item(item) for item in parsed), task_id
            )[:n]
            break

        if last_error is not None and not accepted:
            # Every attempt failed: degrade wholesale to deterministic output.
            scenarios = self._fallback.generate(task_id, n)
            self.last_run_used_fallback = True
            self.last_fallback_reason = last_error
            self.last_run_diagnostics = {
                "missing_categories": 0,
                "topup_from_fallback": n,
            }
        else:
            # Category-diversity guarantee: BEFORE filling any remaining
            # count, top up every canonical category the LLM output failed
            # to cover, drawing from the deterministic fallback. Top-ups
            # count toward ``n`` so the batch-size contract is preserved.
            llm_count = len(accepted)
            missing = self._missing_categories(accepted)
            room = max(0, n - llm_count)
            self.last_run_diagnostics = {
                "missing_categories": len(missing),
                "topup_from_fallback": 0,
            }
            fallback_batch: list[AdversarialScenario] = []
            if missing and room > 0:
                fallback_batch.extend(
                    self._top_up_missing_categories(task_id, missing[:room])
                )
            # The count-fill stage draws exactly the shortfall, so
            # ``len(fallback_batch) - needed`` is precisely the number of
            # category-diversity top-ups. ``needed`` is 0 exactly when the
            # top-ups already filled the batch, in which case no reason is
            # reported (nothing was short).
            needed = n - llm_count - len(fallback_batch)
            if needed > 0:
                fallback_batch.extend(self._fallback.generate(task_id, needed))
            if fallback_batch:
                self.last_run_used_fallback = True
                self.last_run_diagnostics["topup_from_fallback"] = len(fallback_batch)
                accepted.extend(self._with_batch_ids(fallback_batch, task_id))
                top_up_count = len(fallback_batch) - needed
                if top_up_count > 0:
                    self.last_fallback_reason = (
                        f"LLM output missed {len(missing)} of "
                        f"{len(ADVERSARIAL_CATEGORIES)} categories; topped up "
                        f"{top_up_count} from the rule-based fallback"
                    )
                elif needed > 0:
                    self.last_fallback_reason = (
                        f"only {llm_count} of {n} LLM scenarios passed validation"
                    )
            scenarios = accepted

        self.scenarios_generated += len(scenarios)
        return scenarios

    def _backoff_delay(self, attempt: int) -> float:
        """Return the sleep, in seconds, before the retry that follows ``attempt``.

        Geometric (``base * 2**(attempt-1)``) and capped at
        ``backoff_max_s`` so a large ``max_retries`` cannot stall a run.
        """
        if self._backoff_base_s <= 0:
            return 0.0
        return min(self._backoff_max_s, self._backoff_base_s * (2 ** (attempt - 1)))

    @staticmethod
    def _missing_categories(
        scenarios: Sequence[AdversarialScenario],
    ) -> list[str]:
        """Return the canonical categories absent from ``scenarios``, in order."""
        present = {scenario.category for scenario in scenarios}
        return [cat for cat in ADVERSARIAL_CATEGORIES if cat not in present]

    def _top_up_missing_categories(
        self,
        task_id: str,
        missing: Sequence[str],
    ) -> list[AdversarialScenario]:
        """Pick one scenario per missing category, preserving ``missing`` order.

        The rule-based fallback derives its RNG from ``(seed, task_id)`` only,
        so repeated single-scenario draws are identical; instead, one batch of
        ``n = len(ADVERSARIAL_CATEGORIES)`` scenarios is drawn — its cycling
        layout guarantees every category appears exactly once — and each
        missing category is served from the first matching candidate.
        """
        pool = {
            s.category: s
            for s in reversed(
                self._fallback.generate(task_id, len(ADVERSARIAL_CATEGORIES))
            )
        }
        topped_up: list[AdversarialScenario] = []
        for category in missing:
            candidate = pool.get(category)
            if candidate is None:
                # Deterministic fallback failed to produce the category
                # (should not happen); skip rather than duplicate coverage.
                continue
            topped_up.append(candidate)
        return topped_up

    @staticmethod
    def _with_batch_ids(
        scenarios: Sequence[AdversarialScenario], task_id: str
    ) -> list[AdversarialScenario]:
        """Re-id fallback scenarios as ``adv-<task>-<i>`` (contiguous, stable).

        Category top-ups and plain count fills are merged into one batch, so
        ids are reassigned contiguously from 0 — keeping fallback ids unique
        within the batch and deterministic regardless of how the batch was
        assembled.
        """
        return [
            AdversarialScenario(
                id=f"adv-{task_id}-{i:04d}",
                category=s.category,
                name=s.name,
                params=s.params,
                difficulty=s.difficulty,
            )
            for i, s in enumerate(scenarios)
        ]

    @staticmethod
    def _assign_ids(
        items: Iterable[AdversarialScenario | None], task_id: str
    ) -> list[AdversarialScenario]:
        """Drop invalid items and assign stable ``llm-<task_id>-<i>`` ids.

        The task id is part of the identifier. It previously was not, so
        ``generate("task-A", 2)`` and ``generate("task-B", 2)`` both returned
        ``["llm-0", "llm-1"]``: two distinct batches shared every id, and any
        id-keyed store would have silently overwritten one task's scenarios
        with another's. Task-scoping makes LLM ids consistent with the
        rule-based path's ``adv-<task_id>-<i>`` ids, which were always
        task-scoped.
        """
        scenarios: list[AdversarialScenario] = []
        for item in items:
            if item is None:
                continue
            scenarios.append(
                AdversarialScenario(
                    id=f"llm-{task_id}-{len(scenarios)}",
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

    .. warning::
        **This factory has no production caller.** The only production path that
        builds scenarios is :func:`validsim.engine.pipeline.run_and_score`, which
        hardcodes ``ScenarioGenerator(seed=seed).generate(...)`` at
        ``validsim/engine/pipeline.py:131``. Nothing in the shipped API, CLI or
        job worker calls this function, so setting the environment variables
        below has **no effect on a validation run** until the pipeline is
        changed to route through the factory. It is a working, tested
        opt-in surface kept for that future change — not a live feature.

    Returns an :class:`LLMScenarioGenerator` only when the feature is explicitly
    opted into *and* a key is configured:

    * ``VALIDSIM_LLM_ENABLED`` — the opt-in switch. Unset, empty, ``0``, ``false``
        or ``no`` leaves the deterministic rule-based :class:`ScenarioGenerator`
        selected. The rule-based path performs no network I/O and is fully
        reproducible, which is why it is the default and why an API key alone
        no longer silently changes behaviour.
    * ``VALIDSIM_LLM_API_KEY`` — the credential. Required *and* sufficient
        (base URL and model fall back to OpenAI-compatible defaults).

    If the opt-in is on but no key is present, the factory **degrades to the
    rule-based generator** rather than raising: a misconfigured optional feature
    must not take a validation run down.

    Args:
        seed: Seed for the rule-based fallback (and the pure rule-based path).

    Returns:
        The selected generator. Never raises for a missing/!invalid LLM
        configuration.
    """
    fallback = ScenarioGenerator(seed=seed)
    if not llm_enabled():
        return fallback
    if not os.environ.get(_API_KEY_ENV, "").strip():
        return fallback
    try:
        return LLMScenarioGenerator(OpenAICompatibleProvider(), fallback=fallback)
    except ScenarioProviderError:
        # Never let an optional feature's misconfiguration break a run.
        return fallback


def llm_enabled() -> bool:
    """Whether ``VALIDSIM_LLM_ENABLED`` opts into the LLM backend.

    Only an explicit truthy value enables it. Anything else — unset, empty,
    ``0``, ``false``, ``no``, ``off`` — leaves the deterministic path selected.
    """
    return os.environ.get(_ENABLE_ENV, "").strip().lower() in _TRUTHY


def current_scenario_backend() -> str:
    """Return a short label naming the backend a run would actually use.

    This reports the *effective* backend for the current environment, which is
    always :func:`validsim.engine.pipeline.run_and_score`'s hardcoded
    rule-based :class:`~validsim.scenarios.generator.ScenarioGenerator` unless
    the pipeline is changed to call :func:`create_scenario_generator`. When the
    opt-in is on but unusable, it names the misconfiguration instead of
    implying the LLM path is active.

    Returns:
        ``"rule-based"``, ``"llm"``, or ``"rule-based (misconfigured: ...) ..."``.
    """
    if not llm_enabled():
        return "rule-based"
    if not os.environ.get(_API_KEY_ENV, "").strip():
        return "rule-based (misconfigured: no API key)"
    try:
        OpenAICompatibleProvider()
    except ScenarioProviderError as exc:
        return f"rule-based (misconfigured: {exc})"
    return "llm"
