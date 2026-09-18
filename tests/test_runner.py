"""Tests for the simulation runner and the deterministic mock backend."""

from __future__ import annotations

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import ScenarioGenerator
from validsim.sim.runner import (
    FAILURE_MODES,
    EpisodeResult,
    MockIsaacBackend,
    SimulationBackend,
    run_validation,
    stable_seed,
)

SEED = 42


def _task(episodes: int = 60, adversarial: int = 12) -> TaskConfig:
    return TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka"),
        environment=EnvironmentSpec(name="kitchen"),
        episodes=episodes,
        adversarial_count=adversarial,
    )


class TestMockBackend:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(MockIsaacBackend(), SimulationBackend)

    def test_episode_is_deterministic(self) -> None:
        task = _task()
        a = MockIsaacBackend().run_episode(task, seed=SEED, randomization_level="full")
        b = MockIsaacBackend().run_episode(task, seed=SEED, randomization_level="full")
        assert a == b

    def test_different_seeds_diverge(self) -> None:
        task = _task()
        backend = MockIsaacBackend()
        outcomes = {
            backend.run_episode(task, seed=s, randomization_level="full").success
            for s in range(200)
        }
        assert outcomes == {True, False}  # both outcomes observed

    def test_success_rate_near_base(self) -> None:
        task = _task()
        backend = MockIsaacBackend(base_success_rate=0.9)
        results = [backend.run_episode(task, seed=s, randomization_level="none") for s in range(800)]
        rate = sum(r.success for r in results) / len(results)
        assert 0.83 < rate < 0.96

    def test_randomization_lowers_success(self) -> None:
        backend = MockIsaacBackend()
        assert (
            backend.success_probability("full")
            < backend.success_probability("none")
        )

    def test_bad_base_rate_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            MockIsaacBackend(base_success_rate=1.4)


class TestRunValidation:
    def test_episode_count(self) -> None:
        task = _task(episodes=50, adversarial=10)
        scenarios = ScenarioGenerator(seed=SEED).generate(task.task_id, task.adversarial_count)
        results = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        assert len(results) == 60
        assert all(isinstance(r, EpisodeResult) for r in results)

    def test_deterministic_batch(self) -> None:
        task = _task()
        scenarios = ScenarioGenerator(seed=SEED).generate(task.task_id, task.adversarial_count)
        a = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        b = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        assert [(r.success, r.failure_mode, r.max_contact_force_n) for r in a] == [
            (r.success, r.failure_mode, r.max_contact_force_n) for r in b
        ]

    def test_failure_taxonomy_and_success_fields(self) -> None:
        task = _task(episodes=200, adversarial=24)
        scenarios = ScenarioGenerator(seed=SEED).generate(task.task_id, task.adversarial_count)
        results = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        for r in results:
            assert r.duration_s > 0
            assert r.max_contact_force_n > 0
            assert "position_rms" in r.joint_states_summary
            if r.success:
                assert r.failure_mode is None
            else:
                assert r.failure_mode in FAILURE_MODES

    def test_adversarial_episodes_are_harder(self) -> None:
        task = _task(episodes=600, adversarial=120)
        scenarios = ScenarioGenerator(seed=SEED).generate(task.task_id, task.adversarial_count)
        results = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        nominal = results[: task.episodes]
        adversarial = results[task.episodes:]
        nominal_rate = sum(r.success for r in nominal) / len(nominal)
        adversarial_rate = sum(r.success for r in adversarial) / len(adversarial)
        assert adversarial_rate < nominal_rate - 0.1

    def test_seeds_unique_per_episode(self) -> None:
        task = _task(episodes=30, adversarial=6)
        scenarios = ScenarioGenerator(seed=SEED).generate(task.task_id, task.adversarial_count)
        results = run_validation(task, MockIsaacBackend(), scenarios, seed=SEED)
        assert len({r.seed for r in results}) == len(results)


class TestStableSeed:
    def test_stable_and_nonnegative(self) -> None:
        assert stable_seed("ckpt", "task") == stable_seed("ckpt", "task")
        assert stable_seed("ckpt", "task") >= 0

    def test_parts_matter(self) -> None:
        assert stable_seed("a", "b") != stable_seed("b", "a")
