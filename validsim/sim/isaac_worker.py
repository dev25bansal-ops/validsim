"""HTTP client adapter for ValidSim's containerized Isaac Sim / Isaac Lab worker.

This module speaks the wire contract documented in ``docs/isaac-worker.md``:
:class:`IsaacWorkerBackend` satisfies the same
:class:`~validsim.sim.runner.SimulationBackend` interface as
:class:`~validsim.sim.runner.MockIsaacBackend`, so :func:`~validsim.sim.runner.
run_validation` drives it unchanged — each :meth:`run_episode` is one
``POST /episodes/run`` whose JSON reply is mapped back onto
:class:`~validsim.sim.runner.EpisodeResult`.

The GPU worker itself is deliberately **not** in this repository: it is a
separate container image (Isaac Sim 4.x headless on an A100, ~15 GB) that gets
built once DGX Cloud credits land. Until then this client is exercised only
against ``httpx.MockTransport`` fakes, which is exactly why the contract is
written down and pinned here rather than inferred from a live service.

Design decisions, all mirroring conventions already in the codebase:

* **Deferred configuration.** ``base_url`` / ``api_key`` fall back to the
  ``VALIDSIM_ISAAC_WORKER_URL`` / ``VALIDSIM_ISAAC_WORKER_KEY`` environment
  variables, and a missing URL raises ``ValueError`` at *first use* rather than
  at construction — the same lazy pattern as
  :class:`validsim.store.postgres.PostgresValidationStore`, so ``create_backend()``
  can always be called without a GPU host in the loop.
* **Loud contract drift.** Every :class:`EpisodeResult` field must be present
  in the reply, must have the right type, and unknown keys are rejected. A
  silently defaulted safety observable (contact force, human proximity) would
  corrupt the scorecard, which is the one thing this platform exists to prevent.
* **Retry policy.** Connect/timeout failures get two attempts; HTTP status
  errors get none, because a 4xx/5xx is a deterministic answer, not a flaky
  link, and re-driving a GPU episode is expensive.
"""

from __future__ import annotations

import json
import os
from dataclasses import fields as _dataclass_fields
from typing import Any

import httpx

from validsim.config import TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import FAILURE_MODES, EpisodeResult

__all__ = ["SimWorkerError", "IsaacWorkerBackend"]

#: Env var supplying the default worker base URL.
_WORKER_URL_ENV = "VALIDSIM_ISAAC_WORKER_URL"

#: Env var supplying the default bearer token.
_WORKER_KEY_ENV = "VALIDSIM_ISAAC_WORKER_KEY"

#: Total attempts for connect/timeout failures (1 initial + 1 retry).
_MAX_ATTEMPTS = 2

#: Randomization levels accepted on the wire (mirrors ``config.RandomizationLevel``).
_VALID_RANDOMIZATION: tuple[str, ...] = ("none", "partial", "full")

#: Exact episode keys the worker must send, derived from :class:`EpisodeResult`
#: so the wire contract and the dataclass cannot drift apart unnoticed.
_EPISODE_FIELDS: tuple[str, ...] = tuple(f.name for f in _dataclass_fields(EpisodeResult))


class SimWorkerError(RuntimeError):
    """The Isaac worker was unreachable, unhealthy, or broke the contract.

    Attributes:
        status_code: HTTP status of the offending response, or ``None`` when
            the failure was transport-level (connect/timeout) or a response
            that never made it to a parsed HTTP status.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# -- payload helpers ---------------------------------------------------------


def _scenario_payload(scenario: AdversarialScenario | None) -> list[dict[str, Any]]:
    """Serialize one scenario into the ``scenarios`` list of the request body.

    ``AdversarialScenario.name`` is display-only and intentionally not sent;
    the worker keys everything off ``id``/``category``/``params``/``difficulty``.
    """
    if scenario is None:
        return []
    return [
        {
            "id": scenario.id,
            "category": scenario.category,
            "params": dict(scenario.params),
            "difficulty": scenario.difficulty,
        }
    ]


def _scenarios_payload(
    scenarios: list[AdversarialScenario] | None,
) -> list[dict[str, Any]]:
    """Serialize many scenarios into the ``scenarios`` list of the request body.

    This is the batched counterpart of :func:`_scenario_payload`: it applies the
    identical per-scenario wire shape to each entry and concatenates the results,
    so a batch request is byte-for-byte what the single-episode path would send
    for the same scenarios. ``None`` and an empty list both mean "no adversarial
    episodes" and serialize to an empty list.
    """
    return [payload for scenario in (scenarios or []) for payload in _scenario_payload(scenario)]


def _body_snippet(response: httpx.Response, limit: int = 200) -> str:
    """Return a truncated, single-line excerpt of a response body for errors."""
    try:
        text = response.text
    except Exception:  # pragma: no cover - defensive: body already consumed
        return "<unreadable body>"
    text = " ".join(text.split())
    if not text:
        return "<empty body>"
    return text if len(text) <= limit else text[:limit] + "..."


def _contract_error(
    label: str, detail: str, *, status_code: int | None = None
) -> SimWorkerError:
    """Build a ``SimWorkerError`` tagged as a contract mismatch."""
    return SimWorkerError(
        f"Isaac worker contract mismatch: {label} {detail} "
        "See docs/isaac-worker.md for the exact episode schema the worker must speak.",
        status_code=status_code,
    )


def _matches(value: Any, kinds: tuple[type, ...]) -> bool:
    """Whether ``value`` is an instance of ``kinds``, with bool handled strictly.

    ``bool`` is deliberately excluded from numeric slots: JSON ``true`` is a
    Python ``int`` subclass, so ``isinstance(True, int)`` would otherwise let a
    swapped ``success``/``collision_count`` pair through unnoticed.
    """
    if isinstance(value, bool):
        return bool in kinds
    return isinstance(value, kinds)


def _want(
    item: dict[str, Any],
    key: str,
    label: str,
    kinds: tuple[type, ...],
    *,
    allow_none: bool = False,
) -> Any:
    """Return ``item[key]`` if it has one of ``kinds`` (``+ null`` if allowed).

    Raises:
        SimWorkerError: On a type deviation from the documented episode schema.
    """
    value = item[key]
    if value is None and allow_none:
        return None
    if _matches(value, kinds):
        return value
    expected = " or ".join(k.__name__ for k in kinds) + (" or null" if allow_none else "")
    raise _contract_error(
        label, f"field {key!r} must be {expected}, got {type(value).__name__} {value!r}"
    )


def _want_summary(item: dict[str, Any], key: str, label: str) -> dict[str, float]:
    """Return ``item[key]`` as a ``str -> float`` mapping, or raise."""
    value = item[key]
    if not isinstance(value, dict):
        raise _contract_error(
            label, f"field {key!r} must be a JSON object, got {type(value).__name__} {value!r}"
        )
    summary: dict[str, float] = {}
    for name, number in value.items():
        if not isinstance(name, str):  # pragma: no cover - JSON keys are always strings
            raise _contract_error(label, f"field {key!r} keys must be strings, got {name!r}")
        if not _matches(number, (int, float)):
            raise _contract_error(
                label, f"field {key!r}[{name!r}] must be a number, got {number!r}"
            )
        summary[name] = float(number)
    return summary


def _episode_from_dict(
    item: Any, index: int, base_seed: int, *, expected_level: str | None = None
) -> EpisodeResult:
    """Map one JSON episode object onto :class:`EpisodeResult`.

    Args:
        item: The episode object from the worker's ``episodes`` list.
        index: Position within that list; drives the expected seed echo.
        base_seed: ``seed`` sent in the request; the worker must return
            ``base_seed + index`` for position ``index`` (the same per-episode
            seed derivation :func:`validsim.sim.runner.run_validation` uses).
        expected_level: The ``randomization_level`` requested, when the caller
            knows it; a worker silently running a different condition would
            mislabel every robustness number downstream.

    Raises:
        SimWorkerError: On any structural, type, or semantic deviation from the
            documented contract.
    """
    label = f"episodes[{index}]"
    if not isinstance(item, dict):
        raise _contract_error(label, f"must be a JSON object, got {type(item).__name__}")

    unknown = sorted(set(item) - set(_EPISODE_FIELDS))
    if unknown:
        raise _contract_error(
            label,
            f"sent unknown field(s) {', '.join(repr(k) for k in unknown)}; the episode keys are "
            f"exactly {', '.join(_EPISODE_FIELDS)}",
        )
    missing = sorted(set(_EPISODE_FIELDS) - set(item))
    if missing:
        raise _contract_error(
            label,
            f"is missing required field(s) {', '.join(repr(k) for k in missing)}; every "
            "EpisodeResult field is mandatory on the wire (use null for the optional ones)",
        )

    distance = _want(item, "min_human_distance_m", label, (int, float), allow_none=True)
    episode = EpisodeResult(
        episode_id=_want(item, "episode_id", label, (str,)),
        task_id=_want(item, "task_id", label, (str,)),
        seed=_want(item, "seed", label, (int,)),
        success=_want(item, "success", label, (bool,)),
        collision_count=_want(item, "collision_count", label, (int,)),
        max_contact_force_n=float(_want(item, "max_contact_force_n", label, (int, float))),
        min_human_distance_m=None if distance is None else float(distance),
        failure_mode=_want(item, "failure_mode", label, (str,), allow_none=True),
        duration_s=float(_want(item, "duration_s", label, (int, float))),
        joint_states_summary=_want_summary(item, "joint_states_summary", label),
        randomization_level=_want(item, "randomization_level", label, (str,)),
    )

    # Semantic checks: type-correct but contract-invalid values are the ones
    # most likely to quietly skew a scorecard, so they fail loudly too.
    if episode.seed != base_seed + index:
        raise _contract_error(
            label,
            f"echoed seed {episode.seed}, expected {base_seed + index} (request seed "
            f"{base_seed} plus position {index}); seeded determinism is part of the contract",
        )
    if episode.collision_count < 0:
        raise _contract_error(
            label, f"field 'collision_count' must be >= 0, got {episode.collision_count}"
        )
    if episode.failure_mode is not None and episode.failure_mode not in FAILURE_MODES:
        raise _contract_error(
            label,
            f"failure_mode {episode.failure_mode!r} is not in the ValidSim taxonomy "
            f"({', '.join(FAILURE_MODES)})",
        )
    if episode.randomization_level not in _VALID_RANDOMIZATION:
        raise _contract_error(
            label,
            f"randomization_level {episode.randomization_level!r} must be one of "
            f"{', '.join(_VALID_RANDOMIZATION)}",
        )
    if expected_level is not None and episode.randomization_level != expected_level:
        raise _contract_error(
            label,
            f"ran randomization_level {episode.randomization_level!r} although "
            f"{expected_level!r} was requested",
        )
    return episode


def _episodes_from_reply(
    data: Any, *, expected: int, base_seed: int, expected_level: str | None = None
) -> list[EpisodeResult]:
    """Validate the top-level reply envelope and map it to episode results."""
    if not isinstance(data, dict):
        raise _contract_error("reply", f"must be a JSON object, got {type(data).__name__}")
    if "episodes" not in data:
        keys = ", ".join(sorted(map(str, data))) or "nothing"
        raise _contract_error(
            "reply",
            f"has no top-level 'episodes' key (got {keys})",
        )
    raw = data["episodes"]
    if not isinstance(raw, list):
        raise _contract_error(
            "reply", f"'episodes' must be a JSON array, got {type(raw).__name__}"
        )
    if len(raw) != expected:
        raise _contract_error(
            "reply",
            f"returned {len(raw)} episode(s) but {expected} were requested "
            f"(episodes + scenarios = {expected})",
        )
    return [
        _episode_from_dict(item, i, base_seed, expected_level=expected_level)
        for i, item in enumerate(raw)
    ]


# -- the backend -------------------------------------------------------------


class IsaacWorkerBackend:
    """Simulation backend that delegates episodes to a remote GPU worker.

    Implements the same :meth:`run_episode` signature as
    :class:`~validsim.sim.runner.MockIsaacBackend`, so it can be dropped into
    :func:`~validsim.sim.runner.run_validation` unchanged. Construction performs
    no I/O: the :class:`httpx.Client` and the base-URL check happen on first
    use.

    Args:
        base_url: Worker root, e.g. ``"http://worker-gpu:8090"``; defaults to
            ``VALIDSIM_ISAAC_WORKER_URL``.
        api_key: Bearer token; defaults to ``VALIDSIM_ISAAC_WORKER_KEY``. When
            unset, no ``Authorization`` header is sent.
        timeout: Per-request timeout in seconds, applied to connect *and* read
            (a single GPU episode can take minutes, so batch with care).
        transport: Injected :class:`httpx.BaseTransport` — the test seam used
            by ``tests/test_isaac_worker.py`` (``httpx.MockTransport``) so the
            suite never touches a real network.
    """

    #: Mirrors ``MockIsaacBackend.name`` for diagnostics/logging parity.
    name: str = "isaac-worker"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get(_WORKER_URL_ENV) or "").strip().rstrip("/")
        self._api_key = (api_key or os.environ.get(_WORKER_KEY_ENV) or "").strip() or None
        self._timeout = float(timeout)
        self._transport = transport
        self._client: httpx.Client | None = None

    # -- introspection -------------------------------------------------------

    @property
    def base_url(self) -> str | None:
        """Resolved worker root, or ``None`` when unconfigured."""
        return self._base_url or None

    @property
    def has_api_key(self) -> bool:
        """Whether a bearer token is configured (the token itself stays private)."""
        return self._api_key is not None

    # -- lifecycle -----------------------------------------------------------

    def _require_base_url(self) -> str:
        """Return the base URL or fail fast (at first use, not construction).

        Raises:
            ValueError: If neither the ``base_url`` argument nor
                ``VALIDSIM_ISAAC_WORKER_URL`` provides one.
        """
        if not self._base_url:
            raise ValueError(
                "No Isaac Sim worker URL configured: pass base_url= or set the "
                f"{_WORKER_URL_ENV} environment variable."
            )
        return self._base_url

    def _get_client(self) -> httpx.Client:
        """Return the lazily created, reused :class:`httpx.Client`."""
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, transport=self._transport)
        return self._client

    def close(self) -> None:
        """Close the pooled HTTP client (the next request transparently reopens it)."""
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def __enter__(self) -> "IsaacWorkerBackend":
        """Use as a context manager so the connection pool is always released."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying client on scope exit."""
        self.close()

    # -- SimulationBackend interface -----------------------------------------

    def run_episode(
        self, task: TaskConfig, seed: int, randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        """Run exactly one episode remotely and return its result.

        Sends ``episodes: 1`` (or ``0`` when the episode *is* the adversarial
        one) plus the single scenario, i.e. the degenerate case of the batched
        contract documented in ``docs/isaac-worker.md``: the worker returns
        ``episodes + len(scenarios)`` results, so this always yields exactly one.

        Raises:
            ValueError: If no worker URL is configured.
            SimWorkerError: On transport failure after retries, an HTTP error
                status, or any deviation from the episode contract.
        """
        scenarios = _scenario_payload(scenario)
        payload = self._build_payload(
            task,
            seed,
            randomization_level,
            scenarios,
            # The scenario, when present, *is* the episode being run: asking for
            # one nominal episode plus one adversarial would return two results.
            episodes=0 if scenarios else 1,
        )
        results = self._post_run(payload)
        return results[0]

    def run_episodes(
        self,
        task: TaskConfig,
        base_seed: int,
        episodes: int,
        randomization_level: str,
        scenarios: list[AdversarialScenario] | None = None,
    ) -> list[EpisodeResult]:
        """Run a batch of episodes in a single ``POST /episodes/run`` round-trip.

        This is the batching counterpart of :meth:`run_episode`: where the
        single-episode path pays one round-trip per episode (the degenerate
        batch-of-one the wire contract already permits), this submits
        ``episodes`` nominal runs plus any ``scenarios`` adversarial runs in one
        request and splits the reply back into per-episode results. The worker
        returns ``episodes + len(scenarios)`` results, nominal first, each
        echoing ``base_seed + position`` — so the returned list is exactly that
        length, ordered, and every entry is validated through the same strict
        contract checks as :meth:`run_episode` (exact field set, seed echo,
        ``failure_mode`` taxonomy, randomization-level echo).

        Args:
            task: The task specification driving the batch.
            base_seed: Seed for position 0; episode ``i`` must echo
                ``base_seed + i`` (matching
                :func:`validsim.sim.runner.run_validation`'s per-episode seeds).
            episodes: Number of nominal (non-adversarial) episodes to run.
            randomization_level: Level requested for the whole batch; echoed
                per episode and checked against it.
            scenarios: Adversarial scenarios appended after the nominal
                episodes; each becomes one extra episode. ``None``/empty means
                a nominal-only batch.

        Returns:
            The validated :class:`EpisodeResult` list, nominal episodes first
            then one per scenario, in request order. An empty batch (no
            ``episodes`` and no ``scenarios``) is a no-op that returns ``[]``
            without touching the network.

        Raises:
            ValueError: If ``episodes`` is negative, or no worker URL is
                configured (the latter only when the batch actually posts).
            SimWorkerError: On transport failure after retries, an HTTP error
                status, or any deviation from the episode contract.
        """
        if episodes < 0:
            raise ValueError(f"episodes must be >= 0, got {episodes}")
        scenario_list = list(scenarios or [])
        if episodes == 0 and not scenario_list:
            return []  # empty batch: nothing to run, so never hit the network
        payload = self._build_payload(
            task,
            base_seed,
            randomization_level,
            _scenarios_payload(scenario_list),
            episodes=episodes,
        )
        return self._post_run(payload)

    def is_alive(self) -> bool:
        """Probe ``GET /health``; ``True`` only for a 2xx answer.

        Any transport or HTTP-level problem yields ``False`` — this is a
        monitoring probe, so it must never raise. A *missing* base URL is still
        a configuration bug rather than an unhealthy worker, so that alone
        raises :class:`ValueError` (consistent with :meth:`run_episode`).
        """
        url = f"{self._require_base_url()}/health"
        try:
            response = self._get_client().get(url, headers=self._headers())
        except httpx.HTTPError:
            return False
        return response.is_success

    # -- request plumbing ----------------------------------------------------

    @staticmethod
    def _build_payload(
        task: TaskConfig,
        seed: int,
        randomization_level: str,
        scenarios: list[dict[str, Any]],
        episodes: int = 1,
    ) -> dict[str, Any]:
        """Assemble the ``POST /episodes/run`` body from a task + scenarios.

        ``episodes`` is the number of *nominal* episodes requested; adversarial
        ones are implied by ``scenarios``, so the worker must return
        ``episodes + len(scenarios)`` results, nominal first (see
        ``docs/isaac-worker.md``). ``episodes: 0`` is therefore valid and means
        "run only the injected scenarios".

        ``checkpoint_id`` identifies the policy artifact under test and is
        ``null`` when the caller did not supply one. It is the field a worker
        needs to know *which policy to load* -- without it a conformant worker
        can only ever run a default policy, and every scorecard it produced
        would be a statement about that default rather than about the
        checkpoint. A worker should echo it back on each episode so the client
        can verify the right artifact was loaded.
        """
        return {
            "task_id": task.task_id,
            "robot": task.robot.model_dump(),
            "environment": task.environment.model_dump(),
            "checkpoint_id": task.checkpoint_id,
            "seed": seed,
            "episodes": episodes,
            "randomization_level": randomization_level,
            "scenarios": scenarios,
        }

    def _headers(self) -> dict[str, str]:
        """Return auth headers (empty when no API key is configured)."""
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _post_run(self, payload: dict[str, Any]) -> list[EpisodeResult]:
        """Send one run request and return the parsed, validated episodes."""
        url = f"{self._require_base_url()}/episodes/run"
        response = self._transact(url, payload)
        try:
            data = json.loads(response.content)
        except ValueError as exc:
            raise SimWorkerError(
                f"Isaac worker contract mismatch: POST {url} replied with a non-JSON body "
                f"(HTTP {response.status_code}): {_body_snippet(response)}. "
                "See docs/isaac-worker.md for the exact episode schema the worker must speak.",
                status_code=response.status_code,
            ) from exc
        expected = int(payload["episodes"]) + len(payload["scenarios"])
        return _episodes_from_reply(
            data,
            expected=expected,
            base_seed=int(payload["seed"]),
            expected_level=str(payload["randomization_level"]),
        )

    def _transact(self, url: str, payload: dict[str, Any]) -> httpx.Response:
        """POST with retry-on-transport-failure; map failures to :class:`SimWorkerError`.

        Connect/timeout errors are retried up to :data:`_MAX_ATTEMPTS` times.
        HTTP status errors are raised immediately — no retry — so a worker that
        rejects the request is not hammered with duplicate GPU work.
        """
        client = self._get_client()
        last_error: httpx.TransportError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = client.post(url, json=payload, headers=self._headers())
            except httpx.TransportError as exc:
                last_error = exc
                continue
            if response.status_code >= 400:
                raise SimWorkerError(
                    f"Isaac worker returned HTTP {response.status_code} for POST {url}: "
                    f"{_body_snippet(response)}",
                    status_code=response.status_code,
                ) from None
            return response
        raise SimWorkerError(
            f"Isaac worker unreachable at {url} after {_MAX_ATTEMPTS} attempt(s) "
            f"({type(last_error).__name__}: {last_error})"
        ) from last_error
