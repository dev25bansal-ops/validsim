"""Pydantic v2 configuration models for ValidSim.

These models describe the robots, environments, tasks, and validation requests
that flow through the platform. They are intentionally strict so that bad
configuration is caught at the boundary (API/CLI) rather than deep inside the
simulation or evaluation engines.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RandomizationLevel = Literal["none", "partial", "full"]

#: Environment variable naming the directory that all asset paths resolve beneath.
ASSET_ROOT_ENV = "VALIDSIM_ASSET_ROOT"


def _is_absolute_path(value: str) -> bool:
    """Return True for paths considered absolute on any supported platform.

    ``os.path.isabs`` follows the host platform; the two ``PurePath`` flavours
    additionally catch POSIX absolute paths on Windows and Windows absolute/UNC
    paths on POSIX so the path fields behave consistently everywhere.
    """
    return (
        os.path.isabs(value)
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


def _validate_asset_path(value: str | None) -> str | None:
    """Reject filesystem paths that could escape :envvar:`VALIDSIM_ASSET_ROOT`.

    Absolute paths, ``..`` traversal segments, and NUL bytes are rejected so a
    configuration payload cannot steer a loader outside the asset root.
    """
    if value is None:
        return value
    if "\x00" in value:
        raise ValueError("Asset path must not contain NUL bytes.")
    if _is_absolute_path(value):
        raise ValueError(f"Asset path must be relative, got absolute path: {value!r}")
    if ".." in value.replace("\\", "/").split("/"):
        raise ValueError(f"Asset path must not contain '..' traversal segments: {value!r}")
    return value


def _asset_root() -> Path:
    """Return the resolved asset root, defaulting to the working directory."""
    raw = os.environ.get(ASSET_ROOT_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def resolve_asset_path(path: str | Path) -> Path:
    """Resolve *path* against the asset root and verify it stays inside.

    Args:
        path: Relative asset path, e.g. ``"robots/franka.urdf"``.

    Returns:
        The absolute, symlink-resolved path beneath :envvar:`VALIDSIM_ASSET_ROOT`.

    Raises:
        ValueError: If the resolved path escapes the asset root.
    """
    root = _asset_root()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Asset path escapes the asset root: {path!r} -> {candidate!r}")
    return candidate


class RobotSpec(BaseModel):
    """Description of the robot under test.

    Attributes:
        name: Human-readable robot identifier (e.g. ``"franka_panda"``).
        urdf_path: Optional filesystem path to the robot's URDF description.
        dof: Degrees of freedom. Defaults to 7 (typical manipulator arm).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Robot identifier.")
    urdf_path: str | None = Field(None, description="Path to the URDF file.")
    dof: int = Field(7, ge=1, le=40, description="Degrees of freedom.")

    @field_validator("urdf_path", mode="after")
    @classmethod
    def _validate_urdf_path(cls, value: str | None) -> str | None:
        return _validate_asset_path(value)


class EnvironmentSpec(BaseModel):
    """Description of the simulated environment/scene.

    Attributes:
        name: Human-readable environment identifier.
        scene_usd: Optional path to a USD scene asset.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="Environment identifier.")
    scene_usd: str | None = Field(None, description="Path to a USD scene.")

    @field_validator("scene_usd", mode="after")
    @classmethod
    def _validate_scene_usd(cls, value: str | None) -> str | None:
        return _validate_asset_path(value)


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
    checkpoint_sha256: str | None = Field(
        None,
        min_length=64,
        max_length=64,
        description="SHA-256 hex digest of the checkpoint.",
    )
    task: TaskConfig
    baseline_run_id: str | None = Field(
        None, description="Run id to compare against for regressions."
    )


__all__ = [
    "ASSET_ROOT_ENV",
    "EnvironmentSpec",
    "RandomizationLevel",
    "RobotSpec",
    "TaskConfig",
    "ValidationRequest",
    "resolve_asset_path",
]
