"""Simulation execution: backend interface plus a deterministic mock backend.

This module ships :class:`MockIsaacBackend`, a fast, seeded stand-in for the
real Isaac Sim / Isaac Lab backend, reproducing the observable contract
(outcomes, contact forces, human proximity, failure taxonomy) so the
evaluation pipeline runs without a GPU. Swapping in the real backend only
requires implementing :class:`SimulationBackend`.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from validsim.config import TaskConfig
from validsim.scenarios.generator import AdversarialScenario

#: Taxonomy of failure modes sampled when an episode fails.
FAILURE_MODES: tuple[str, ...] = (
    "collision", "timeout", "grasp_failure", "joint_limit",
    "perception_error", "unstable_placement", "emergency_stop",
)

#: Success-rate penalty applied per randomization level.
_RANDOMIZATION_PENALTY: dict[str, float] = {"none": 0.0, "partial": 0.10, "full": 0.20}


def stable_seed(*parts: str, default: int = 42) -> int:
    """Return a deterministic non-negative seed derived from string ``parts``.

    Unlike :func:`hash`, this is stable across processes, keeping validation
    reproducible for a given checkpoint/task pair.
    """
    joined = ":".join(parts)
    if not joined:
        return default
    return zlib.crc32(joined.encode("utf-8")) & 0x7FFFFFFF


@dataclass
class EpisodeResult:
    """Outcome of a single simulated episode (fully determined by ``seed``).

    Records goal achievement (``success``/``failure_mode`` from
    :data:`FAILURE_MODES`), safety observables (``collision_count``,
    ``max_contact_force_n``, ``min_human_distance_m`` — ``None`` when no human
    was in the scene), ``duration_s``, aggregated ``joint_states_summary``
    and the ``randomization_level`` applied.
    """

    episode_id: str
    task_id: str
    seed: int
    success: bool
    collision_count: int = 0
    max_contact_force_n: float = 0.0
    min_human_distance_m: float | None = None
    failure_mode: str | None = None
    duration_s: float = 0.0
    joint_states_summary: dict[str, float] = field(default_factory=dict)
    randomization_level: str = "full"


@runtime_checkable
class SimulationBackend(Protocol):
    """Contract every simulation backend must satisfy."""

    def run_episode(
        self, task: TaskConfig, seed: int, randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        """Run one episode and return its :class:`EpisodeResult`."""
        ...  # pragma: no cover - protocol definition


class MockIsaacBackend:
    """Deterministic, seeded mock of the Isaac Sim backend.

    Success is sampled from a base rate reduced by the randomization level
    and, for adversarial episodes, by the scenario difficulty; the per-episode
    ``seed`` fully determines the output.
    """

    def __init__(self, base_success_rate: float = 0.9, name: str = "mock-isaac") -> None:
        """Configure the base (nominal, no-randomization) success rate."""
        if not 0.0 <= base_success_rate <= 1.0:
            raise ValueError("base_success_rate must be within [0, 1]")
        self._base_success_rate = base_success_rate
        self.name = name

    def success_probability(
        self,
        randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> float:
        """Effective success probability for a given condition combination."""
        penalty = _RANDOMIZATION_PENALTY.get(randomization_level, 0.0)
        p = self._base_success_rate - penalty
        if scenario is not None:
            p -= scenario.difficulty * 0.5
        return max(0.02, min(0.99, p))

    def run_episode(
        self, task: TaskConfig, seed: int, randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        """Simulate one episode deterministically from ``seed``."""
        rng = random.Random(seed)
        success = rng.random() < self.success_probability(randomization_level, scenario)
        failure_mode = rng.choice(FAILURE_MODES) if not success else None

        collision_count = 0
        if failure_mode in {"collision", "grasp_failure", "emergency_stop"}:
            collision_count = rng.randint(1, 3)
        elif rng.random() < 0.05 * (1.0 + _RANDOMIZATION_PENALTY.get(randomization_level, 0.0)):
            collision_count = 1

        if collision_count:
            max_force = rng.uniform(45.0, 160.0)
        else:
            max_force = rng.uniform(2.0, 42.0)

        min_human_distance: float | None = None
        if scenario is not None and scenario.category == "human_proximity":
            min_human_distance = round(rng.uniform(0.05, 0.9), 3)
        elif rng.random() < 0.4:
            min_human_distance = round(rng.uniform(0.6, 2.5), 3)

        if failure_mode == "timeout":
            duration = rng.uniform(18.0, 35.0)
        elif success:
            duration = rng.uniform(4.0, 12.0)
        else:
            duration = rng.uniform(6.0, 20.0)

        summary: dict[str, float] = {
            "position_rms": round(rng.uniform(0.05, 1.2), 4),
            "velocity_rms": round(rng.uniform(0.01, 0.9), 4),
            "effort_max": round(rng.uniform(5.0, 120.0), 2),
            "dof": float(task.robot.dof),
        }

        return EpisodeResult(
            episode_id=f"{task.task_id}-seed{seed:010d}",
            task_id=task.task_id,
            seed=seed,
            success=success,
            collision_count=collision_count,
            max_contact_force_n=round(max_force, 2),
            min_human_distance_m=min_human_distance,
            failure_mode=failure_mode,
            duration_s=round(duration, 3),
            joint_states_summary=summary,
            randomization_level=randomization_level,
        )


def run_validation(
    task: TaskConfig,
    backend: SimulationBackend,
    scenarios: Sequence[AdversarialScenario],
    seed: int = 42,
) -> list[EpisodeResult]:
    """Execute nominal episodes then adversarial ones, deterministically.

    Each episode gets a unique seed derived from ``seed`` and its position;
    adversarial episodes inject their scenario (elevating failure
    probability). Returns ``task.episodes + len(scenarios)`` results.
    """
    results: list[EpisodeResult] = []
    for i in range(task.episodes):
        results.append(
            backend.run_episode(task, seed=seed + i, randomization_level=task.randomization)
        )
    for j, scenario in enumerate(scenarios):
        results.append(
            backend.run_episode(
                task,
                seed=seed + task.episodes + j,
                randomization_level=task.randomization,
                scenario=scenario,
            )
        )
    return results


__all__ = [
    "EpisodeResult",
    "FAILURE_MODES",
    "MockIsaacBackend",
    "SimulationBackend",
    "run_validation",
    "stable_seed",
]
