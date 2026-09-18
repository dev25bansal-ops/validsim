"""Tests for Pydantic configuration models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from validsim.config import (
    EnvironmentSpec,
    RobotSpec,
    TaskConfig,
    ValidationRequest,
)


def _task(**overrides: object) -> TaskConfig:
    base = {
        "task_id": "pick-place",
        "robot": RobotSpec(name="franka"),
        "environment": EnvironmentSpec(name="kitchen"),
    }
    base.update(overrides)
    return TaskConfig(**base)  # type: ignore[arg-type]


class TestRobotAndEnvironment:
    def test_robot_defaults(self) -> None:
        robot = RobotSpec(name="franka")
        assert robot.dof == 7
        assert robot.urdf_path is None

    def test_robot_rejects_bad_dof(self) -> None:
        with pytest.raises(ValidationError):
            RobotSpec(name="x", dof=0)

    def test_environment_optional_scene(self) -> None:
        assert EnvironmentSpec(name="kitchen").scene_usd is None


class TestTaskConfig:
    def test_defaults(self) -> None:
        task = _task()
        assert task.episodes == 1000
        assert task.randomization == "full"
        assert task.adversarial_count == 0

    @pytest.mark.parametrize("bad", [0, 100001])
    def test_episode_bounds(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            _task(episodes=bad)

    @pytest.mark.parametrize("bad", [-1, 1001])
    def test_adversarial_bounds(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            _task(adversarial_count=bad)

    def test_randomization_literal_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _task(randomization="extreme")  # type: ignore[arg-type]

    def test_randomization_accepts_all_levels(self) -> None:
        for level in ("none", "partial", "full"):
            assert _task(randomization=level).randomization == level


class TestValidationRequest:
    def test_minimal_request(self) -> None:
        req = ValidationRequest(checkpoint_id="ckpt-1", task=_task())
        assert req.checkpoint_sha256 is None
        assert req.baseline_run_id is None

    def test_sha256_length_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest(checkpoint_id="c", task=_task(), checkpoint_sha256="abc")

    def test_full_request(self) -> None:
        digest = "a" * 64
        req = ValidationRequest(
            checkpoint_id="ckpt",
            checkpoint_sha256=digest,
            task=_task(),
            baseline_run_id="vrun-deadbeef",
        )
        assert req.checkpoint_sha256 == digest
        assert req.baseline_run_id == "vrun-deadbeef"
