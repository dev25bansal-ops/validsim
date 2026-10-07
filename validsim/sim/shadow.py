"""Shadow-run harness: validate a real Isaac GPU worker without gating prod.

``docs/isaac-worker.md`` §5 describes the rollout plan, and step 2 is *shadow
runs*: drive the same checkpoint, with the same seeds, through both
:class:`~validsim.sim.runner.MockIsaacBackend` and
:class:`~validsim.sim.isaac_worker.IsaacWorkerBackend`, then compare the two.
Nothing user-facing reads the GPU numbers yet — a shadow run is a rehearsal, not
a verdict — so a worker that is unreachable, unhealthy, or drifting from the
mock is a *finding*, never a deploy blocker. This module is that rehearsal.

:class:`ShadowRunner` takes a worker URL (and a task spec at run time), drives a
*small* batch through :class:`IsaacWorkerBackend`, computes the same batch on
the deterministic :class:`MockIsaacBackend` with identical seeds, and compares
the two mean success rates. A :class:`ShadowReport` bundles the outcome:
whether the worker answered within the contract, the two success rates, their
delta, any contract violations, and a single ``passed`` flag. ``passed`` is
purely informational here — production keeps reading the mock until step 4 of
the rollout — but it gives the team a crisp "is this worker ready to promote?"
signal to track across consecutive runs.

Design decisions, all mirroring conventions already in the codebase:

* **No GPU, no socket.** The runner injects an :class:`httpx.BaseTransport` into
  :class:`IsaacWorkerBackend` (the same test seam ``tests/test_isaac_worker.py``
  uses), so a shadow run can be rehearsed entirely against
  ``httpx.MockTransport`` fakes. A live worker is only reached when you hand it
  a real ``transport=None``.
* **Loud, never fatal.** Every :class:`~validsim.sim.isaac_worker.SimWorkerError`
  (transport failure, HTTP error status, contract drift) is caught and recorded
  on the report rather than raised: a shadow run must always produce a report,
  because the whole point is to observe what the worker does.
* **Apples-to-apples seeds.** Both backends run the same ``episodes`` at the same
  ``base_seed`` (nominal seeds ``base_seed + i``, then one per scenario), exactly
  the seed scheme :func:`validsim.sim.runner.run_validation` uses, so the success
  rates are directly comparable.
* **Contract fixtures.** :func:`reference_contract_cases` ships a few canned
  request/response pairs pinned to the documented schema, and
  :func:`contract_violations_for` replays one through the *real* client parser,
  so the same fixtures double as a worker conformance check.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import httpx

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.isaac_worker import IsaacWorkerBackend, SimWorkerError
from validsim.sim.runner import EpisodeResult, MockIsaacBackend

__all__ = [
    "ContractCase",
    "ShadowReport",
    "ShadowRunner",
    "contract_violations_for",
    "reference_contract_cases",
]

#: Default number of episodes in a shadow batch: small on purpose. A shadow run
#: is a drift probe, not a full validation, so it must stay cheap on scarce GPU
#: time (a single PhysX episode can take minutes — see docs §4).
_DEFAULT_EPISODES = 20

#: Default success-rate tolerance band, in absolute probability. A well-calibrated
#: mock "should stay within a few points" (docs §5.3); 0.15 leaves headroom for
#: the sampling noise of a small batch while still catching a badly skewed worker.
_DEFAULT_TOLERANCE = 0.15

#: Seed for a shadow batch when the caller does not pin one.
_DEFAULT_BASE_SEED = 42

#: Base URL used only when replaying a reference case through the parser; no
#: network is touched (the transport is always a :class:`httpx.MockTransport`).
_CONTRACT_PROBE_URL = "http://contract-probe.local"


# -- the report --------------------------------------------------------------


@dataclass(frozen=True)
class ShadowReport:
    """Outcome of one shadow run: worker vs. mock, contract + drift.

    Attributes:
        worker_ok: ``True`` only when the worker answered the whole batch without
            raising (reachable, healthy, and contract-conformant). ``False``
            whenever a :class:`~validsim.sim.isaac_worker.SimWorkerError` was
            caught; the reason is mirrored into ``contract_violations``.
        mock_success_rate: Mean success rate of the deterministic mock over the
            same seeds (0.0-1.0).
        worker_success_rate: Mean success rate the worker reported (0.0-1.0);
            ``0.0`` when the worker never produced a usable batch.
        delta: ``worker_success_rate - mock_success_rate`` (signed; positive
            means the worker looks *better* than the mock). ``0.0`` when the
            worker failed to answer.
        contract_violations: Human-readable description of every contract or
            transport problem observed. Empty when ``worker_ok`` is ``True``.
        passed: ``True`` only when the worker answered cleanly *and* ``delta``
            fell within the configured tolerance band. Informational: production
            still gates on the mock until the worker is promoted (docs §5.4).
    """

    worker_ok: bool
    mock_success_rate: float
    worker_success_rate: float
    delta: float
    contract_violations: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (``contract_violations`` copied)."""
        data = asdict(self)
        data["contract_violations"] = list(self.contract_violations)
        return data

    def to_json(self, indent: int | None = None) -> str:
        """Serialize this report to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# -- helpers -----------------------------------------------------------------


def _success_rate(results: Sequence[EpisodeResult]) -> float:
    """Mean success over ``results`` (0.0 for an empty batch)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.success) / len(results)


# -- the runner --------------------------------------------------------------


class ShadowRunner:
    """Drive a small batch on both backends and compare their success rates.

    A single runner is reusable across tasks and seeds; only the worker URL and
    the shadow policy (batch size, tolerance) are fixed at construction. The
    :class:`IsaacWorkerBackend` is built fresh on each :meth:`run` so no
    connection pool outlives a call, and the injected ``transport`` is the seam
    that keeps a shadow run GPU-free.

    Args:
        worker_url: Worker root, e.g. ``"http://worker-gpu:8090"``. Required —
            a shadow run is meaningless without a worker to shadow. A trailing
            slash is normalized away (matching :class:`IsaacWorkerBackend`).
        api_key: Optional bearer token forwarded to the worker.
        transport: Injected :class:`httpx.BaseTransport` for the worker client —
            pass an ``httpx.MockTransport`` to rehearse without a GPU (the
            default ``None`` reaches the real worker over the network).
        mock: The comparison backend; defaults to a fresh
            :class:`~validsim.sim.runner.MockIsaacBackend`. Injecting one lets a
            team shadow against a differently-calibrated mock.
        episodes: Size of the shadow batch (nominal episodes; scenarios are
            added on top). Must be ``>= 1``.
        tolerance: Half-width of the acceptable ``|delta|`` band. Must be
            ``>= 0``.
        timeout: Per-request timeout handed to the worker client.

    Raises:
        ValueError: If ``worker_url`` is empty, ``episodes < 1``, or
            ``tolerance < 0``.
    """

    #: Diagnostic label, mirroring ``MockIsaacBackend.name`` / ``"isaac-worker"``.
    name: str = "shadow"

    def __init__(
        self,
        worker_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        mock: MockIsaacBackend | None = None,
        episodes: int = _DEFAULT_EPISODES,
        tolerance: float = _DEFAULT_TOLERANCE,
        timeout: float = 30.0,
    ) -> None:
        url = (worker_url or "").strip().rstrip("/")
        if not url:
            raise ValueError(
                "ShadowRunner requires a non-empty worker_url (the worker to shadow)."
            )
        if int(episodes) < 1:
            raise ValueError(f"episodes must be >= 1, got {episodes}")
        if float(tolerance) < 0.0:
            raise ValueError(f"tolerance must be >= 0, got {tolerance}")
        self._worker_url = url
        self._api_key = api_key
        self._transport = transport
        self._mock = mock if mock is not None else MockIsaacBackend()
        self._episodes = int(episodes)
        self._tolerance = float(tolerance)
        self._timeout = float(timeout)

    # -- introspection -------------------------------------------------------

    @property
    def worker_url(self) -> str:
        """Resolved worker root the shadow run posts to."""
        return self._worker_url

    @property
    def tolerance(self) -> float:
        """Configured success-rate tolerance band half-width."""
        return self._tolerance

    # -- run -----------------------------------------------------------------

    def run(
        self,
        task: TaskConfig,
        *,
        base_seed: int = _DEFAULT_BASE_SEED,
        scenarios: Sequence[AdversarialScenario] | None = None,
        episodes: int | None = None,
    ) -> ShadowReport:
        """Shadow ``task`` on both backends and compare their success rates.

        Runs ``episodes`` nominal episodes (plus one per ``scenario``) on the
        mock and, in a single batched ``POST /episodes/run``, on the worker —
        both at the same ``base_seed`` so the results are directly comparable.
        The worker's failures never propagate: they are captured on the report.

        Args:
            task: The task specification to shadow.
            base_seed: Seed for episode 0; episode ``i`` uses ``base_seed + i``.
            scenarios: Optional adversarial scenarios appended after the nominal
                episodes on *both* backends.
            episodes: Per-run batch size override; defaults to the runner's.

        Returns:
            A :class:`ShadowReport` describing worker health, the two success
            rates, their delta, and whether the run passed the tolerance band.
        """
        batch = self._episodes if episodes is None else int(episodes)
        if batch < 1:
            raise ValueError(f"episodes must be >= 1, got {batch}")
        level = task.randomization
        scenario_list = list(scenarios or [])

        mock_results = self._run_mock(task, base_seed, level, scenario_list, batch)
        mock_rate = round(_success_rate(mock_results), 4)

        violations: list[str] = []
        worker_ok = False
        worker_rate = 0.0
        try:
            worker_results = self._run_worker(task, base_seed, level, scenario_list, batch)
            worker_ok = True
            worker_rate = round(_success_rate(worker_results), 4)
        except SimWorkerError as exc:  # loud, never fatal: a shadow run always reports
            violations.append(str(exc))

        delta = round(worker_rate - mock_rate, 4)
        passed = worker_ok and abs(delta) <= self._tolerance
        return ShadowReport(
            worker_ok=worker_ok,
            mock_success_rate=mock_rate,
            worker_success_rate=worker_rate,
            delta=delta,
            contract_violations=violations,
            passed=passed,
        )

    # -- per-backend batch drivers ------------------------------------------

    def _run_mock(
        self,
        task: TaskConfig,
        base_seed: int,
        level: str,
        scenarios: Sequence[AdversarialScenario],
        episodes: int,
    ) -> list[EpisodeResult]:
        """Run the shadow batch on the deterministic mock, seeds aligned to the worker."""
        results = [
            self._mock.run_episode(task, seed=base_seed + i, randomization_level=level)
            for i in range(episodes)
        ]
        for j, scenario in enumerate(scenarios):
            results.append(
                self._mock.run_episode(
                    task,
                    seed=base_seed + episodes + j,
                    randomization_level=level,
                    scenario=scenario,
                )
            )
        return results

    def _run_worker(
        self,
        task: TaskConfig,
        base_seed: int,
        level: str,
        scenarios: Sequence[AdversarialScenario],
        episodes: int,
    ) -> list[EpisodeResult]:
        """Run the shadow batch on the worker in one round-trip, then release the client."""
        backend = IsaacWorkerBackend(
            base_url=self._worker_url,
            api_key=self._api_key,
            timeout=self._timeout,
            transport=self._transport,
        )
        try:
            return backend.run_episodes(
                task,
                base_seed=base_seed,
                episodes=episodes,
                randomization_level=level,
                scenarios=scenarios,
            )
        finally:
            backend.close()


# -- reference contract fixtures --------------------------------------------


@dataclass(frozen=True)
class ContractCase:
    """A canned request/response pair pinned to the documented worker schema.

    Attributes:
        name: Short identifier for the scenario the case exercises.
        request: The ``POST /episodes/run`` body, exactly as
            :class:`~validsim.sim.isaac_worker.IsaacWorkerBackend` sends it.
        response: The ``{"episodes": [...]}`` reply a conforming worker sends
            back for that request.
    """

    name: str
    request: dict[str, Any]
    response: dict[str, Any]


def _episode(
    seed: int,
    *,
    success: bool,
    level: str,
    task_id: str = "pick-place",
    **overrides: Any,
) -> dict[str, Any]:
    """Build one contract-valid episode object echoing ``seed`` and ``level``."""
    episode: dict[str, Any] = {
        "episode_id": f"{task_id}-seed{seed:010d}",
        "task_id": task_id,
        "seed": seed,
        "success": success,
        "collision_count": 0 if success else 2,
        "max_contact_force_n": 12.5 if success else 140.0,
        "min_human_distance_m": None,
        "failure_mode": None if success else "collision",
        "duration_s": 7.25 if success else 9.0,
        "joint_states_summary": {
            "position_rms": 0.4,
            "velocity_rms": 0.1,
            "effort_max": 33.0,
            "dof": 7,
        },
        "randomization_level": level,
    }
    episode.update(overrides)
    return episode


def _request(
    seed: int,
    episodes: int,
    level: str,
    scenarios: list[dict[str, Any]] | None = None,
    *,
    task_id: str = "pick-place",
    checkpoint_id: str | None = "ckpt-v41",
) -> dict[str, Any]:
    """Build a ``POST /episodes/run`` body in the documented wire shape.

    ``checkpoint_id`` is included because it is the field that tells a worker
    which policy to load. A fixture that omitted it would pass against a
    contract incapable of expressing the product's core input -- conformance
    would certify the defect rather than catch it.
    """
    return {
        "task_id": task_id,
        "robot": {"name": "franka_panda", "urdf_path": "robots/franka.urdf", "dof": 7},
        "environment": {"name": "kitchen", "scene_usd": "scenes/kitchen.usda"},
        "checkpoint_id": checkpoint_id,
        "seed": seed,
        "episodes": episodes,
        "randomization_level": level,
        "scenarios": scenarios or [],
    }


def reference_contract_cases() -> tuple[ContractCase, ...]:
    """Return canned request/response pairs for worker contract checks.

    These mirror the examples in ``docs/isaac-worker.md`` and cover the shapes a
    shadow run and a conformance suite must handle: a single nominal episode, an
    adversarial ``human_proximity`` episode (which must report
    ``min_human_distance_m``), and a multi-episode batch with mixed outcomes.
    Each response is deliberately contract-valid, so replaying it through the
    real client via :func:`contract_violations_for` yields no violations; mutate
    a copy to confirm the checks actually bite.
    """
    return (
        ContractCase(
            name="nominal-single",
            request=_request(42, episodes=1, level="full"),
            response={"episodes": [_episode(42, success=True, level="full")]},
        ),
        ContractCase(
            name="adversarial-human-proximity",
            request=_request(
                42,
                episodes=0,
                level="full",
                scenarios=[
                    {
                        "id": "adv-pick-place-0000",
                        "category": "human_proximity",
                        "params": {
                            "human_distance_m": 0.42,
                            "human_speed_mps": 1.1,
                            "crossing": True,
                        },
                        "difficulty": 0.6,
                    }
                ],
            ),
            response={
                "episodes": [
                    _episode(
                        42,
                        success=False,
                        level="full",
                        min_human_distance_m=0.31,
                    )
                ]
            },
        ),
        ContractCase(
            name="batch-mixed",
            request=_request(100, episodes=3, level="partial"),
            response={
                "episodes": [
                    _episode(100, success=True, level="partial"),
                    _episode(101, success=False, level="partial"),
                    _episode(102, success=True, level="partial"),
                ]
            },
        ),
    )


def _task_from_request(request: dict[str, Any]) -> TaskConfig:
    """Reconstruct a :class:`TaskConfig` from a canned request body."""
    return TaskConfig(
        task_id=str(request["task_id"]),
        robot=RobotSpec(**request["robot"]),
        environment=EnvironmentSpec(**request["environment"]),
        episodes=max(1, int(request.get("episodes", 1))),
        randomization=str(request["randomization_level"]),  # type: ignore[arg-type]
        checkpoint_id=request.get("checkpoint_id"),
    )


def _scenarios_from_request(request: dict[str, Any]) -> list[AdversarialScenario]:
    """Rebuild :class:`AdversarialScenario` objects from a canned request body."""
    scenarios: list[AdversarialScenario] = []
    for entry in request.get("scenarios", []):
        scenarios.append(
            AdversarialScenario(
                id=str(entry["id"]),
                category=str(entry["category"]),
                name=str(entry.get("name") or entry["id"]),
                params=dict(entry.get("params", {})),
                difficulty=float(entry["difficulty"]),
            )
        )
    return scenarios


def contract_violations_for(
    case: ContractCase, *, base_url: str = _CONTRACT_PROBE_URL
) -> list[str]:
    """Replay ``case`` through the real client's contract parser.

    Builds an :class:`IsaacWorkerBackend` wired to an ``httpx.MockTransport``
    that serves ``case.response``, then drives it with the request's own
    parameters. A conforming pair yields an empty list; any deviation the client
    would reject (missing/unknown field, bad type, misaligned seed echo, unknown
    taxonomy, wrong episode count) is returned as a one-element violation list.
    No network or GPU is involved.

    Args:
        case: The canned request/response pair to check.
        base_url: Placeholder worker root for the probe client (never contacted).

    Returns:
        A list of violation strings — empty when the response conforms.
    """
    request = case.request

    def _serve(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=case.response)

    backend = IsaacWorkerBackend(base_url=base_url, transport=httpx.MockTransport(_serve))
    try:
        backend.run_episodes(
            _task_from_request(request),
            base_seed=int(request["seed"]),
            episodes=int(request["episodes"]),
            randomization_level=str(request["randomization_level"]),
            scenarios=_scenarios_from_request(request),
        )
    except SimWorkerError as exc:
        return [str(exc)]
    finally:
        backend.close()
    return []
