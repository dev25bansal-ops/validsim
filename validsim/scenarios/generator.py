"""Rule-based adversarial scenario generation.

MVP generator: a deterministic, seeded sampler that cycles through the twelve
adversarial categories required by the ValidSim scorecard and produces varied,
task-conditioned parameters for each.

.. note::
    An LLM-powered backend (e.g. GPT-4o or NVIDIA Cosmos) will replace this
    rule-based generator in a later milestone to produce richer, open-ended
    adversarial scenarios. The public surface (:meth:`ScenarioGenerator.
    generate` returning :class:`AdversarialScenario` objects) is intentionally
    kept stable so the swap is a drop-in change.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

#: The exact twelve adversarial categories ValidSim scores against.
ADVERSARIAL_CATEGORIES: tuple[str, ...] = (
    "lighting_change",
    "object_property_change",
    "human_proximity",
    "unexpected_obstacle",
    "sensor_degradation",
    "mechanical_variation",
    "environmental_disturbance",
    "task_ambiguity",
    "multi_robot_interference",
    "emergency_scenario",
    "adversarial_input",
    "temporal_pressure",
)

#: Baseline difficulty (0-1) per category before seeded jitter is applied.
_BASE_DIFFICULTY: dict[str, float] = {
    "lighting_change": 0.25,
    "object_property_change": 0.40,
    "human_proximity": 0.60,
    "unexpected_obstacle": 0.55,
    "sensor_degradation": 0.50,
    "mechanical_variation": 0.45,
    "environmental_disturbance": 0.35,
    "task_ambiguity": 0.50,
    "multi_robot_interference": 0.65,
    "emergency_scenario": 0.75,
    "adversarial_input": 0.70,
    "temporal_pressure": 0.45,
}


def _clamp(value: float, low: float, high: float) -> float:
    """Return ``value`` constrained to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


@dataclass(frozen=True)
class AdversarialScenario:
    """A single adversarial condition to inject into a simulation episode.

    Attributes:
        id: Stable, deterministic scenario identifier.
        category: One of :data:`ADVERSARIAL_CATEGORIES`.
        name: Human-readable scenario name.
        params: Category-specific randomized parameters.
        difficulty: Normalized difficulty in ``[0, 1]`` (higher = harder).
    """

    id: str
    category: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    difficulty: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.difficulty <= 1.0:
            raise ValueError(f"difficulty must be within [0, 1], got {self.difficulty}")
        if self.category not in ADVERSARIAL_CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")


class ScenarioGenerator:
    """Deterministic, seeded generator over the twelve adversarial categories.

    Scenarios are produced by cycling through :data:`ADVERSARIAL_CATEGORIES`
    in order, so any ``n >= 12`` guarantees full category coverage. All
    randomness is drawn from a :class:`random.Random` seeded from the
    generator seed plus the ``task_id``, making output reproducible.
    """

    def __init__(self, seed: int = 42) -> None:
        """Store the base seed; per-task streams are derived from it."""
        self._seed = seed

    def generate(self, task_id: str, n: int) -> list[AdversarialScenario]:
        """Return ``n`` deterministic adversarial scenarios for ``task_id``.

        Scenarios cycle through the twelve categories with varied parameters;
        the RNG stream is seeded from the generator seed plus ``task_id``.
        Raises :class:`ValueError` if ``n`` is negative.
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        rng = random.Random(f"{self._seed}:{task_id}")
        scenarios: list[AdversarialScenario] = []
        for i in range(n):
            category = ADVERSARIAL_CATEGORIES[i % len(ADVERSARIAL_CATEGORIES)]
            difficulty = _clamp(
                _BASE_DIFFICULTY[category] + rng.uniform(-0.15, 0.15), 0.05, 0.95
            )
            scenarios.append(
                AdversarialScenario(
                    id=f"adv-{task_id}-{i:04d}",
                    category=category,
                    name=f"{category.replace('_', ' ').title()} #{i:04d}",
                    params=_PARAM_SAMPLERS[category](rng, i),
                    difficulty=round(difficulty, 4),
                )
            )
        return scenarios


def _rcoord(rng: random.Random) -> dict[str, float]:
    """Sample a random object position offset within a 1m^3 workspace."""
    return {
        "x": round(rng.uniform(-0.5, 0.5), 3),
        "y": round(rng.uniform(-0.5, 0.5), 3),
        "z": round(rng.uniform(0.0, 1.0), 3),
    }


_PARAM_SAMPLERS: dict[str, Callable[[random.Random, int], dict[str, Any]]] = {
    "lighting_change": lambda rng, i: {
        "lux": rng.randint(5, 3000),
        "color_temperature_k": rng.randint(2500, 7500),
        "flicker_hz": round(rng.uniform(0.0, 60.0), 1),
    },
    "object_property_change": lambda rng, i: {
        "mass_kg": round(rng.uniform(0.02, 5.0), 3),
        "friction_coefficient": round(rng.uniform(0.05, 1.5), 3),
        "restitution": round(rng.uniform(0.0, 0.95), 3),
    },
    "human_proximity": lambda rng, i: {
        "human_distance_m": round(rng.uniform(0.1, 1.2), 3),
        "human_speed_mps": round(rng.uniform(0.3, 1.8), 3),
        "crossing": bool(rng.random() < 0.5),
    },
    "unexpected_obstacle": lambda rng, i: {
        "spawn_offset": _rcoord(rng),
        "obstacle_radius_m": round(rng.uniform(0.02, 0.3), 3),
        "appears_at_step": rng.randint(1, 200),
    },
    "sensor_degradation": lambda rng, i: {
        "camera_dropout_rate": round(rng.uniform(0.0, 0.6), 3),
        "depth_noise_mm": round(rng.uniform(0.0, 80.0), 1),
        "latency_ms": rng.randint(0, 250),
    },
    "mechanical_variation": lambda rng, i: {
        "joint_friction_scale": round(rng.uniform(0.5, 2.0), 3),
        "backlash_rad": round(rng.uniform(0.0, 0.05), 4),
        "mass_scale": round(rng.uniform(0.8, 1.3), 3),
    },
    "environmental_disturbance": lambda rng, i: {
        "wind_mps": round(rng.uniform(0.0, 6.0), 2),
        "table_acceleration_mps2": round(rng.uniform(0.0, 3.0), 3),
    },
    "task_ambiguity": lambda rng, i: {
        "distractor_objects": rng.randint(1, 6),
        "goal_description_noise": round(rng.uniform(0.0, 0.5), 3),
        "target_swapped": bool(rng.random() < 0.3),
    },
    "multi_robot_interference": lambda rng, i: {
        "neighbor_robot_count": rng.randint(1, 3),
        "neighbor_speed_mps": round(rng.uniform(0.2, 1.5), 3),
        "shared_workspace": True,
    },
    "emergency_scenario": lambda rng, i: {
        "e_stop_at_step": rng.randint(10, 300),
        "fire_alarm": bool(rng.random() < 0.4),
        "human_down": bool(rng.random() < 0.4),
    },
    "adversarial_input": lambda rng, i: {
        "perturbation_eps": round(rng.uniform(0.001, 0.08), 4),
        "attack_surface": rng.choice(["vision", "state", "language"]),
        "injection_rate": round(rng.uniform(0.05, 0.5), 3),
    },
    "temporal_pressure": lambda rng, i: {
        "deadline_scale": round(rng.uniform(0.3, 0.9), 3),
        "stream_speedup": round(rng.uniform(1.1, 3.0), 2),
    },
}


__all__ = ["ADVERSARIAL_CATEGORIES", "AdversarialScenario", "ScenarioGenerator"]
