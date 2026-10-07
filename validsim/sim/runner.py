"""Simulation execution: backend interface plus a deterministic mock backend.

This module ships :class:`MockIsaacBackend`, a fast, seeded stand-in for the
real Isaac Sim / Isaac Lab backend, reproducing the observable contract
(outcomes, contact forces, human proximity, failure taxonomy) so the
evaluation pipeline runs without a GPU. Swapping in the real backend only
requires implementing :class:`SimulationBackend`.

What the mock reacts to
-----------------------
``docs/isaac-worker.md`` §3 claims an episode is a pure function of
``(seed, task, checkpoint_id, randomization_level, scenario)``. A mock that
ignored the checkpoint and a scenario's parameters would violate that claim in
the one direction that matters: four different adversarial scenes would return
**bit-identical** episodes, so no capability built on it could be measured. The
mock therefore consumes every one of those five inputs:

``seed``
    Seeds the sole :class:`random.Random` of the episode.
``task``
    ``task_id`` names the episode, ``robot.dof`` is reported verbatim.
``checkpoint_id``
    Selects one of five discrete success-rate tiers (:data:`_CHECKPOINT_OFFSET_STEPS`).
    Deliberately **not** an RNG input: the checkpoint shifts the *probability*,
    never the stream, so ``(checkpoint, seed)`` still reproduces exactly.
``randomization_level``
    A success-rate penalty (:data:`_RANDOMIZATION_PENALTY`) and a collision rate.
``scenario``
    ``difficulty`` shifts the success probability, while ``category`` and
    ``params`` additionally select the failure-mode mix, the contact-force band,
    the reported human distance, the episode duration and the joint effort --
    see :data:`_PARAM_STRESS_BY_CATEGORY`.

The physics is *plausible*, not accurate: a mock exists to make the contract
observable and replayable, not to integrate contacts. What it must never be is
invariant to an input the scorecard claims to depend on.
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from validsim.config import TaskConfig
from validsim.scenarios.generator import AdversarialScenario

#: Taxonomy of failure modes sampled when an episode fails.
FAILURE_MODES: tuple[str, ...] = (
    "collision", "timeout", "grasp_failure", "joint_limit",
    "perception_error", "unstable_placement", "emergency_stop",
)

#: Success-rate penalty applied per randomization level.
_RANDOMIZATION_PENALTY: dict[str, float] = {"none": 0.0, "partial": 0.10, "full": 0.20}

#: The five discrete success-rate tiers a ``checkpoint_id`` maps onto.
#:
#: Deliberately a small, finite, *visible* set rather than a continuum: a
#: checkpoint is a policy artifact of unknown provenance, so the mock ranks it
#: coarsely. A smoother model would be a deliberate change, not a drift.
_CHECKPOINT_OFFSET_STEPS: tuple[float, ...] = (-0.12, -0.06, 0.0, 0.06, 0.12)

#: Maximum fraction of the success probability a scenario's concrete
#: *parameters* may remove, on top of the ``difficulty * 0.5`` term.
#:
#: Small on purpose. ``difficulty`` is the coarse, generator-supplied knob and
#: already dominates; the parameters only refine it, so two scenarios with the
#: same difficulty but opposite lighting are measurably -- but not wildly --
#: different.
_PARAM_STRESS_SCALE = 0.12

#: Multiplier applied to a contact force per unit of parameter stress.
_FORCE_STRESS_GAIN = 0.5

#: Extra collision probability per unit of parameter stress.
_COLLISION_STRESS_GAIN = 2.0


def stable_seed(*parts: str, default: int = 42) -> int:
    """Return a deterministic non-negative seed derived from string ``parts``.

    Unlike :func:`hash`, this is stable across processes, keeping validation
    reproducible for a given checkpoint/task pair.
    """
    joined = ":".join(parts)
    if not joined:
        return default
    return zlib.crc32(joined.encode("utf-8")) & 0x7FFFFFFF


def _checkpoint_offset(checkpoint_id: str | None) -> float:
    """Success-probability offset for ``checkpoint_id``.

    A pure ``crc32`` of the id, so the tier of a given checkpoint is stable
    across processes and interpreter runs (no :func:`hash` anywhere). ``None``
    and the empty string both mean "no artifact identified" and score against
    the configured base rate, unchanged.

    Note this is a *probability* shift only. It is deliberately not folded into
    the episode's RNG stream: the stream is seeded from ``seed`` alone, which
    is what makes ``(checkpoint, seed)`` reproducible while different
    checkpoints still produce different episodes in distribution.
    """
    if not checkpoint_id:
        return 0.0
    index = zlib.crc32(checkpoint_id.encode("utf-8")) % len(_CHECKPOINT_OFFSET_STEPS)
    return _CHECKPOINT_OFFSET_STEPS[index]


# -- scenario parameters -----------------------------------------------------


def _unit(value: float, low: float, high: float) -> float:
    """Map ``value`` from ``[low, high]`` onto ``[0, 1]``, clamped at both ends.

    The building block of every stress function below: it turns one physical
    quantity sampled by :data:`~validsim.scenarios.generator._PARAM_SAMPLERS`
    into a "how close is this to the worst case" fraction.
    """
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _flatten_params(params: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested scenario parameters into dotted keys.

    Iterates ``sorted(params)`` at every level, so the result -- and therefore
    the whole episode -- never depends on the insertion order of the dict the
    generator happened to build. A one-key-reordering that changed the outcome
    would make ``scenario`` only *nominally* an input.
    """
    flat: dict[str, Any] = {}
    for key in sorted(params):
        value = params[key]
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten_params(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def _number(params: Mapping[str, Any], name: str, default: float) -> float:
    """Read ``name`` from flattened ``params`` as a finite float.

    Falls back to ``default`` for a missing, non-numeric or non-finite value:
    a scenario carrying a malformed parameter must still produce a replayable
    episode rather than raise in the middle of a validation run.
    """
    value = params.get(name, default)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _flag(params: Mapping[str, Any], name: str) -> float:
    """Read a boolean scenario parameter as ``0.0`` or ``1.0``."""
    return 1.0 if params.get(name) else 0.0


def _stress_lighting(params: Mapping[str, Any]) -> float:
    """Darkness dominates; flicker adds on top.

    Perceptually, a 5-lux scene with 60 Hz flicker is the worst case and 3000
    lux with no flicker the best, so this is monotone in the right direction
    and the audit's "5 lux is harder than 3000 lux" holds at any flicker.
    """
    lux = max(_number(params, "lux", 3000.0), 1.0)
    span = math.log10(3000.0) - math.log10(5.0)
    dark = _unit(math.log10(3000.0) - math.log10(lux), 0.0, span)
    return 0.7 * dark + 0.3 * _unit(_number(params, "flicker_hz", 0.0), 0.0, 60.0)


def _stress_object_property(params: Mapping[str, Any]) -> float:
    """A graspable 0.5 kg / 0.6-friction object is the easy case."""
    mass = max(_number(params, "mass_kg", 0.5), 1e-3)
    off_nominal = _unit(abs(math.log10(mass / 0.5)), 0.0, math.log10(20.0))
    slick = 1.0 - _unit(_number(params, "friction_coefficient", 0.6), 0.05, 0.8)
    bouncy = _unit(_number(params, "restitution", 0.0), 0.3, 0.95)
    return max(off_nominal, max(slick, bouncy))


def _stress_human_proximity(params: Mapping[str, Any]) -> float:
    """A person who is close, fast and crossing the workspace is the hard case."""
    close = 1.0 - _unit(_number(params, "human_distance_m", 1.2), 0.1, 1.2)
    fast = _unit(_number(params, "human_speed_mps", 0.3), 0.3, 1.8)
    return 0.60 * close + 0.25 * fast + 0.15 * _flag(params, "crossing")


def _stress_obstacle(params: Mapping[str, Any]) -> float:
    """A large obstacle appearing early in the middle of the workspace."""
    radius = _unit(_number(params, "obstacle_radius_m", 0.0), 0.02, 0.3)
    early = 1.0 - _unit(_number(params, "appears_at_step", 200.0), 1.0, 200.0)
    offset = max(
        abs(_number(params, "spawn_offset.x", 0.5)),
        abs(_number(params, "spawn_offset.y", 0.5)),
        abs(_number(params, "spawn_offset.z", 0.5)),
    )
    centred = 1.0 - _unit(offset, 0.0, 0.5)
    return max(radius, max(early, centred))


def _stress_sensor(params: Mapping[str, Any]) -> float:
    """Any one of dropout, depth noise or latency is enough to be hard."""
    return max(
        _unit(_number(params, "camera_dropout_rate", 0.0), 0.0, 0.6),
        max(
            _unit(_number(params, "depth_noise_mm", 0.0), 0.0, 80.0),
            _unit(_number(params, "latency_ms", 0.0), 0.0, 250.0),
        ),
    )


def _stress_mechanical(params: Mapping[str, Any]) -> float:
    """Departure from the nominal mechanism: friction, backlash, payload."""
    friction = min(1.0, abs(_number(params, "joint_friction_scale", 1.0) - 1.0))
    backlash = _unit(_number(params, "backlash_rad", 0.0), 0.0, 0.05)
    payload = _unit(abs(_number(params, "mass_scale", 1.0) - 1.0), 0.0, 0.3)
    return max(friction, max(backlash, payload))


def _stress_environment(params: Mapping[str, Any]) -> float:
    """Wind and a shaking table; either alone is enough."""
    return max(
        _unit(_number(params, "wind_mps", 0.0), 0.0, 6.0),
        _unit(_number(params, "table_acceleration_mps2", 0.0), 0.0, 3.0),
    )


def _stress_ambiguity(params: Mapping[str, Any]) -> float:
    """Distractors, a noisier goal description, or a swapped target."""
    return max(
        _unit(_number(params, "distractor_objects", 1.0), 1.0, 6.0),
        max(
            _unit(_number(params, "goal_description_noise", 0.0), 0.0, 0.5),
            _flag(params, "target_swapped"),
        ),
    )


def _stress_interference(params: Mapping[str, Any]) -> float:
    """Neighbour count and speed, amplified when the workspace is shared."""
    return (
        0.45 * _unit(_number(params, "neighbor_robot_count", 1.0), 1.0, 3.0)
        + 0.35 * _unit(_number(params, "neighbor_speed_mps", 0.2), 0.2, 1.5)
        + 0.20 * _flag(params, "shared_workspace")
    )


def _stress_emergency(params: Mapping[str, Any]) -> float:
    """An early e-stop, compounded by a fire alarm or a person on the floor."""
    return (
        0.50 * (1.0 - _unit(_number(params, "e_stop_at_step", 300.0), 10.0, 300.0))
        + 0.25 * _flag(params, "fire_alarm")
        + 0.25 * _flag(params, "human_down")
    )


def _stress_input(params: Mapping[str, Any]) -> float:
    """Perturbation magnitude and injection rate, by attack surface."""
    surface = 1.0 if str(params.get("attack_surface", "vision")) in ("vision", "state") else 0.6
    return (
        0.50 * _unit(_number(params, "perturbation_eps", 0.001), 0.001, 0.08)
        + 0.30 * _unit(_number(params, "injection_rate", 0.05), 0.05, 0.5)
        + 0.20 * surface
    )


def _stress_temporal(params: Mapping[str, Any]) -> float:
    """A tighter deadline and a faster stream are the hard combination."""
    return (
        0.60 * (1.0 - _unit(_number(params, "deadline_scale", 0.9), 0.3, 0.9))
        + 0.40 * _unit(_number(params, "stream_speedup", 1.1), 1.1, 3.0)
    )


#: Per-category parameter stress, in ``[0, 1]``.
#:
#: Each entry is a pure function of the *flattened* parameters, so it is
#: deterministic and order-independent, and every parameter named by
#: :data:`~validsim.scenarios.generator._PARAM_SAMPLERS` for that category is
#: consumed. An unknown category contributes no stress rather than raising: a
#: future thirteenth category must not break a replay of an old run.
_PARAM_STRESS_BY_CATEGORY: dict[str, Any] = {
    "lighting_change": _stress_lighting,
    "object_property_change": _stress_object_property,
    "human_proximity": _stress_human_proximity,
    "unexpected_obstacle": _stress_obstacle,
    "sensor_degradation": _stress_sensor,
    "mechanical_variation": _stress_mechanical,
    "environmental_disturbance": _stress_environment,
    "task_ambiguity": _stress_ambiguity,
    "multi_robot_interference": _stress_interference,
    "emergency_scenario": _stress_emergency,
    "adversarial_input": _stress_input,
    "temporal_pressure": _stress_temporal,
}

#: How much each adversarial category tilts the failure-mode mix.
#:
#: Values are relative weights on top of the uniform baseline, so a mode absent
#: from a category's row stays at its baseline rate. The point is that the
#: failure-taxonomy distribution -- one of the three signals docs §5.3 says the
#: shadow harness should track -- actually varies with the injection, instead of
#: being seven equally likely labels whatever happened in the scene.
_CATEGORY_FAILURE_BIAS: dict[str, dict[str, float]] = {
    "lighting_change": {"perception_error": 1.0, "grasp_failure": 0.4},
    "object_property_change": {"grasp_failure": 1.0, "unstable_placement": 0.6},
    "human_proximity": {"emergency_stop": 1.0, "collision": 0.5},
    "unexpected_obstacle": {"collision": 1.0, "timeout": 0.3},
    "sensor_degradation": {"perception_error": 1.0, "timeout": 0.3},
    "mechanical_variation": {"joint_limit": 1.0, "unstable_placement": 0.4},
    "environmental_disturbance": {"collision": 0.6, "unstable_placement": 0.6},
    "task_ambiguity": {"grasp_failure": 0.6, "timeout": 0.6},
    "multi_robot_interference": {"collision": 0.8, "timeout": 0.4},
    "emergency_scenario": {"emergency_stop": 1.0, "timeout": 0.5},
    "adversarial_input": {"perception_error": 0.8, "unstable_placement": 0.5},
    "temporal_pressure": {"timeout": 1.0, "grasp_failure": 0.3},
}

#: Weight multiplier applied to a biased failure mode (1.0 = un-biased).
_FAILURE_BIAS_GAIN = 6.0

#: Failure modes that a physical contact forces, regardless of category.
_CONTACT_FAILURE_MODES: frozenset[str] = frozenset(
    {"collision", "grasp_failure", "emergency_stop"}
)


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

    Success is sampled from a base rate reduced by the randomization level, by
    the checkpoint's tier and, for adversarial episodes, by the scenario
    difficulty and by a stress term derived from the scenario's concrete
    ``category``/``params``. The same scenario additionally selects the
    failure-mode mix, the contact-force band, the reported human distance, the
    duration and the joint effort, so a scene change is visible in the episode
    rather than only in an aggregate.

    The per-episode ``seed`` fully determines the output *given* the other
    inputs, and ``checkpoint_id`` never enters the RNG stream -- it moves the
    probability only, so ``(checkpoint, seed)`` replays exactly.
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
        checkpoint_id: str | None = None,
    ) -> float:
        """Effective success probability for a given condition combination.

        Args:
            randomization_level: One of ``none|partial|full``; an unknown level
                carries no penalty.
            scenario: The injected adversarial condition, if any. Contributes
                ``difficulty * 0.5`` plus a parameter-stress cut.
            checkpoint_id: The artifact under test, if any. Contributes one of
                :data:`_CHECKPOINT_OFFSET_STEPS`.

        Returns:
            The probability clamped to ``[0.02, 0.99]``. The floor is
            deliberate: a mock that could report a certain failure would make a
            single unlucky batch indistinguishable from a systematic defect.
        """
        penalty = _RANDOMIZATION_PENALTY.get(randomization_level, 0.0)
        p = self._base_success_rate - penalty + _checkpoint_offset(checkpoint_id)
        if scenario is not None:
            p -= scenario.difficulty * 0.5
            p *= 1.0 - _PARAM_STRESS_SCALE * param_stress(scenario)
        return max(0.02, min(0.99, p))

    def run_episode(
        self, task: TaskConfig, seed: int, randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        """Simulate one episode deterministically from ``seed``."""
        # The stream is seeded from ``seed`` alone. The checkpoint shifts the
        # success probability but must not consume a draw, or the same
        # (checkpoint, seed) would stop reproducing.
        rng = random.Random(seed)
        stress = param_stress(scenario)
        success = rng.random() < self.success_probability(
            randomization_level, scenario, task.checkpoint_id
        )
        failure_mode = self._draw_failure_mode(rng, scenario) if not success else None

        collision_count = 0
        if failure_mode in _CONTACT_FAILURE_MODES:
            collision_count = rng.randint(1, 3)
        elif rng.random() < (
            0.05
            * (1.0 + _RANDOMIZATION_PENALTY.get(randomization_level, 0.0))
            * (1.0 + _COLLISION_STRESS_GAIN * stress)
        ):
            collision_count = 1

        # A more stressful scene produces harder contacts. This is the channel
        # that makes two scenarios of equal difficulty but different parameters
        # differ *per episode* rather than only in a long-run average.
        force_gain = 1.0 + _FORCE_STRESS_GAIN * stress
        if collision_count:
            max_force = rng.uniform(45.0, 160.0) * force_gain
        else:
            max_force = rng.uniform(2.0, 42.0) * force_gain

        min_human_distance: float | None = None
        if _human_in_scene(scenario):
            nominal = _number(_flatten_params(scenario.params), "human_distance_m", 0.6)
            min_human_distance = round(max(0.02, nominal * rng.uniform(0.55, 1.65)), 3)
        # Every other scene has no human in it, so ``min_human_distance_m`` stays
        # ``None``. docs/isaac-worker.md sec.3 is explicit: "``human_proximity``
        # runs must report ``min_human_distance_m``; every other scene reports
        # ``null`` rather than omitting the key." The removed branch invented a
        # distance on a 40% coin flip, which is not noise but a fabricated
        # *human-proximity safety observable* for scenes with nobody in them.
        # ``compute_safety`` counts any non-``None`` value as a human being
        # present, so ~41% of human-free episodes entered the proximity
        # denominator and a nominal (never human-present) run published a
        # ``min_human_proximity_m`` of 0.621 m -- a safety claim about a person
        # who was never simulated. It also made the mock contradict the contract
        # it models, and docs sec.5 step 3 asks a shadow run to track this
        # observable precisely because a worker that correctly returns ``null``
        # reads as drift against a mock that invents readings.

        if failure_mode == "timeout":
            duration = rng.uniform(18.0, 35.0)
        elif success:
            duration = rng.uniform(4.0, 12.0)
        else:
            duration = rng.uniform(6.0, 20.0)
        duration *= _duration_scale(scenario)

        effort = rng.uniform(5.0, 120.0) * _effort_scale(scenario)
        summary: dict[str, float] = {
            "position_rms": round(rng.uniform(0.05, 1.2), 4),
            "velocity_rms": round(rng.uniform(0.01, 0.9), 4),
            "effort_max": round(effort, 2),
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

    def _draw_failure_mode(
        self, rng: random.Random, scenario: AdversarialScenario | None
    ) -> str:
        """Sample a failure mode from the category's tilted weights.

        Consumes exactly one draw, so adding the tilt does not change how many
        draws any later observable gets -- the stream stays a function of
        ``seed`` alone.
        """
        weights = [1.0] * len(FAILURE_MODES)
        if scenario is not None:
            bias = _CATEGORY_FAILURE_BIAS.get(scenario.category, {})
            for index, mode in enumerate(FAILURE_MODES):
                weights[index] = 1.0 + _FAILURE_BIAS_GAIN * bias.get(mode, 0.0)
        threshold = rng.random() * sum(weights)
        cumulative = 0.0
        for mode, weight in zip(FAILURE_MODES, weights):
            cumulative += weight
            if threshold < cumulative:
                return mode
        return FAILURE_MODES[-1]  # pragma: no cover - float guard


def param_stress(scenario: AdversarialScenario | None) -> float:
    """Combined ``[0, 1]`` stress implied by a scenario's parameters.

    ``0.0`` for no scenario, an unknown category, or an empty parameter dict.
    Public because the scorecard-facing question "how hard was that scene, on
    its own terms?" is answerable without re-deriving it.
    """
    if scenario is None or not scenario.params:
        return 0.0
    stress = _PARAM_STRESS_BY_CATEGORY.get(scenario.category)
    if stress is None:
        return 0.0
    return max(0.0, min(1.0, float(stress(_flatten_params(scenario.params)))))


def _human_in_scene(scenario: AdversarialScenario | None) -> bool:
    """Whether a person is physically present, i.e. a distance must be reported.

    ``docs/isaac-worker.md`` §3 requires a ``min_human_distance_m`` from every
    ``human_proximity`` run; an ``emergency_scenario`` that puts a person on the
    floor is the same situation under a different name, so it reports one too.
    """
    if scenario is None:
        return False
    if scenario.category == "human_proximity":
        return True
    return scenario.category == "emergency_scenario" and bool(
        scenario.params.get("human_down")
    )


def _duration_scale(scenario: AdversarialScenario | None) -> float:
    """Duration multiplier: a tighter deadline and faster stream finish sooner."""
    if scenario is None or scenario.category != "temporal_pressure":
        return 1.0
    params = _flatten_params(scenario.params)
    deadline = _number(params, "deadline_scale", 0.9)
    speedup = max(1.0, _number(params, "stream_speedup", 1.1))
    return max(0.25, min(1.5, deadline / speedup))


def _effort_scale(scenario: AdversarialScenario | None) -> float:
    """Joint-effort multiplier: a heavier payload demands more torque.

    ``1.0`` (i.e. the historical behaviour) whenever the scenario says nothing
    about mass, so a nominal episode is bit-for-bit what it was.
    """
    if scenario is None:
        return 1.0
    params = _flatten_params(scenario.params)
    if scenario.category == "object_property_change":
        mass = max(_number(params, "mass_kg", 0.5), 0.02)
    elif scenario.category == "mechanical_variation":
        mass = 0.5 * _number(params, "mass_scale", 1.0)
    else:
        return 1.0
    return max(0.5, min(4.0, 0.6 + 0.8 * mass))


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
    "param_stress",
    "run_validation",
    "stable_seed",
]
