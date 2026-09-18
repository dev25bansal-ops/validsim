"""Pydantic v2 configuration models for ValidSim.

These models describe the robots, environments, tasks, and validation requests
that flow through the platform. They are intentionally strict so that bad
configuration is caught at the boundary (API/CLI) rather than deep inside the
simulation or evaluation engines.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

RandomizationLevel = Literal["none", "partial", "full"]


class RobotSpec(BaseModel):
    """Description of the robot under test.

    Attributes:
        name: Human-readable robot identifier (e.g. ``"franka_panda"``).
        urdf_path: Optional filesystem path to the robot's URDF description.
        dof: Degrees of freedom. Defaults to 7 (typical manipulator arm).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Robot identifier.")
    urdf_path: Optional[str] = Field(None, description="Path to the URDF file.")
    dof: int = Field(7, ge=1, le=40, description="Degrees of freedom.")


class EnvironmentSpec(BaseModel):
    """Description of the simulated environment/scene.

    Attributes:
        name: Human-readable environment identifier.
        scene_usd: Optional path to a USD scene asset.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Environment identifier.")
    scene_usd: Optional[str] = Field(None, description="Path to a USD scene.")


class TaskConfig(BaseModel):
    """Full description of a validation task.

    Attributes:
        task_id: Unique identifier for the task.
        robot: The :class:`RobotSpec` under test.
        environment: The :class:`EnvironmentSpec` the task runs in.
        episodes: Number of nominal (non-adversarial) episodes to run.
        randomization: Domain-randomization intensity.
        adversarial_count: Number of adversarial scenarios to generate/run.
    """

    task_id: str = Field(..., min_length=1, description="Task identifier.")
    robot: RobotSpec
    environment: EnvironmentSpec
    episodes: int = Field(1000, ge=1, le=100000, description="Nominal episodes.")
    randomization: RandomizationLevel = Field("full", description="Randomization level.")
    adversarial_count: int = Field(0, ge=0, le=1000, description="Adversarial episodes.")


class ValidationRequest(BaseModel):
    """Top-level request that triggers a validation run.

    Attributes:
        checkpoint_id: Identifier of the model checkpoint being validated.
        checkpoint_sha256: Optional SHA-256 digest of the checkpoint artifact.
        task: The :class:`TaskConfig` describing what to simulate.
        baseline_run_id: Optional prior run id used for regression comparison.
    """

    checkpoint_id: str = Field(..., min_length=1, description="Checkpoint identifier.")
    checkpoint_sha256: Optional[str] = Field(
        None,
        min_length=64,
        max_length=64,
        description="SHA-256 hex digest of the checkpoint.",
    )
    task: TaskConfig
    baseline_run_id: Optional[str] = Field(
        None, description="Run id to compare against for regressions."
    )


__all__ = [
    "RandomizationLevel",
    "RobotSpec",
    "EnvironmentSpec",
    "TaskConfig",
    "ValidationRequest",
]
