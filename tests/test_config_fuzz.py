"""Randomized + example-based fuzz tests for the Pydantic config models.

The configuration boundary (:class:`~validsim.config.TaskConfig` and
:class:`~validsim.config.ValidationRequest`) must *reject* malformed payloads
with a :class:`pydantic.ValidationError` rather than silently accepting them.
This module hammers that boundary from two directions:

* a fixed-seed randomized sweep (~150 iterations) mutating one field at a time
  with values drawn from curated pools of known-bad inputs; and
* explicit parametrized cases covering the specific classes of failure the
  scorecard cares about — negative/zero/huge episode counts, wrong types,
  non-64-character SHA-256 digests, and out-of-taxonomy randomization literals.

Care is taken to avoid values Pydantic *legitimately coerces* (e.g. ``True``
or ``"1000"`` for an ``int`` field, a whole float like ``1000.0``); those are
accepted by design and are exercised in the positive-control test instead, so
the fuzz harness itself stays honest.

The source under test is never modified.
"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest
from pydantic import ValidationError

from validsim.config import (
    EnvironmentSpec,
    RobotSpec,
    TaskConfig,
    ValidationRequest,
)

# Fixed master seed -> the randomized mutation sweep is reproducible.
MASTER_SEED = 0x5EED_C0FF
# Iterations for the randomized mutation sweeps.
ITERATIONS = 150

# A SHA-256 digest is exactly 64 hex characters.
VALID_SHA = "a" * 64


def _valid_task_payload(**overrides: Any) -> dict[str, Any]:
    """Return a fully-valid TaskConfig payload with optional mutations."""
    payload: dict[str, Any] = {
        "task_id": "pick-place",
        "robot": RobotSpec(name="franka"),
        "environment": EnvironmentSpec(name="kitchen"),
    }
    payload.update(overrides)
    return payload


def _valid_request_payload(**overrides: Any) -> dict[str, Any]:
    """Return a fully-valid ValidationRequest payload with optional mutations."""
    payload: dict[str, Any] = {
        "checkpoint_id": "ckpt-1",
        "task": TaskConfig(**_valid_task_payload()),
    }
    payload.update(overrides)
    return payload


# --- Curated pools of KNOWN-BAD values (verified to raise ValidationError) ---
# NOTE: intentionally excludes values Pydantic coerces cleanly (True, "1000",
# 1000.0, whole-number floats) — those belong in the positive control below.
EPISODES_BAD: list[Any] = [
    0, -1, -5, -1000, 100001, 1000000, 10**9,
    "not-a-number", "", "1e5", None,
    3.5, -2.0, 0.5, float("nan"), float("inf"),
    [1, 2], {}, {"x": 1}, (1,),
]
ADVERSARIAL_BAD: list[Any] = [
    -1, -100, 1001, 5000, 10**9,
    "many", "-1", None, 1.5, float("nan"),
    [0], {}, (),
]
RANDOMIZATION_BAD: list[Any] = [
    "extreme", "FULL", "None", "Partial", "full ", " full",
    "", "random", None, 5, 0, True, ["full"], {}, "none\n",
]
TASK_ID_BAD: list[Any] = ["", None, 123, 0, {}, [], (1,), True]
ROBOT_BAD: list[Any] = [
    "not-a-robot", None, {}, {"name": ""}, {"name": None},
    {"name": 123}, {"name": "ok", "dof": 0}, {"dof": 7}, 123, [],
]
ENVIRONMENT_BAD: list[Any] = [
    "not-an-env", None, {}, {"name": ""}, {"name": None},
    {"name": 42}, 123, [],
]

# sha256: must be exactly 64 chars (any content). Bad = wrong length / wrong type.
SHA_BAD: list[Any] = [
    "abc", "", "a" * 63, "a" * 65, "a" * 128, "z" * 100,
    12345, 0, ["a" * 64], {"sha": "a" * 64}, object(),
]
CHECKPOINT_ID_BAD: list[Any] = ["", None, 123, {}, [], (1,), True]
TASK_FIELD_BAD: list[Any] = [None, "not-a-task", 123, {}, [], True]


class TestPositiveControl:
    """Sanity guards proving the harness accepts what Pydantic legitimately does.

    If any of these *failed*, the "bad" pools above would be mis-specified.
    """

    def test_coercible_values_are_accepted(self) -> None:
        # Whole-number float, numeric string and bool all coerce to int.
        assert TaskConfig(**_valid_task_payload(episodes=1000.0)).episodes == 1000
        assert TaskConfig(**_valid_task_payload(episodes="2500")).episodes == 2500
        assert TaskConfig(**_valid_task_payload(episodes=True)).episodes == 1
        # 64-char digest of any characters passes (no hex-pattern constraint).
        assert ValidationRequest(
            **_valid_request_payload(checkpoint_sha256="b" * 64)
        ).checkpoint_sha256 == "b" * 64

    def test_valid_baseline_builds(self) -> None:
        task = TaskConfig(**_valid_task_payload())
        req = ValidationRequest(**_valid_request_payload(task=task))
        assert req.task.task_id == "pick-place"


class TestTaskConfigExampleBadPayloads:
    """Explicit, named failure classes for TaskConfig."""

    @pytest.mark.parametrize("bad", [0, -1, -999, 100001, 10**12])
    def test_episode_out_of_range(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(episodes=bad))

    @pytest.mark.parametrize("bad", [-1, 1001, 999999])
    def test_adversarial_count_out_of_range(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(adversarial_count=bad))

    @pytest.mark.parametrize("bad", ["extreme", "FULL", "", "random", 5, None])
    def test_randomization_not_in_literal(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(randomization=bad))

    @pytest.mark.parametrize("bad", ["", None, 123, {}])
    def test_task_id_invalid(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(task_id=bad))

    @pytest.mark.parametrize("bad", ["nope", None, {}, {"name": ""}, 123])
    def test_robot_invalid(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(robot=bad))

    @pytest.mark.parametrize("bad", ["nope", None, {}, {"name": ""}, 123])
    def test_environment_invalid(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            TaskConfig(**_valid_task_payload(environment=bad))

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            TaskConfig()  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            TaskConfig(task_id="x")  # robot/environment missing

    def test_wrong_type_for_int_fields(self) -> None:
        for bad in ("not-a-number", [1, 2], {}, float("nan")):
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(episodes=bad))


class TestValidationRequestExampleBadPayloads:
    """Explicit, named failure classes for ValidationRequest."""

    @pytest.mark.parametrize("bad", ["abc", "", "a" * 63, "a" * 65, "a" * 128])
    def test_sha256_wrong_length(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest(**_valid_request_payload(checkpoint_sha256=bad))

    @pytest.mark.parametrize("bad", [12345, ["a" * 64], {"s": "a" * 64}])
    def test_sha256_wrong_type(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest(**_valid_request_payload(checkpoint_sha256=bad))

    @pytest.mark.parametrize("bad", ["", None, 123, {}])
    def test_checkpoint_id_invalid(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest(**_valid_request_payload(checkpoint_id=bad))

    @pytest.mark.parametrize("bad", [None, "nope", 123, {}])
    def test_task_field_invalid(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest(**_valid_request_payload(task=bad))

    def test_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            ValidationRequest()  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            ValidationRequest(checkpoint_id="c")  # task missing

    def test_nested_task_invalid_propagates(self) -> None:
        # A valid request whose nested task payload is malformed must fail.
        with pytest.raises(ValidationError):
            ValidationRequest(
                **_valid_request_payload(
                    task=_valid_task_payload(episodes=0)  # dict, not TaskConfig
                )
            )


class TestRandomizedMutationSweep:
    """Fixed-seed sweep mutating one field at a time with known-bad values."""

    def test_task_config_single_field_mutations(self) -> None:
        rng = random.Random(MASTER_SEED)
        pools = {
            "episodes": EPISODES_BAD,
            "adversarial_count": ADVERSARIAL_BAD,
            "randomization": RANDOMIZATION_BAD,
            "task_id": TASK_ID_BAD,
            "robot": ROBOT_BAD,
            "environment": ENVIRONMENT_BAD,
        }
        fields = list(pools)
        for _ in range(ITERATIONS):
            field = rng.choice(fields)
            bad = rng.choice(pools[field])
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(**{field: bad}))

    def test_validation_request_single_field_mutations(self) -> None:
        rng = random.Random(MASTER_SEED)
        pools = {
            "checkpoint_sha256": SHA_BAD,
            "checkpoint_id": CHECKPOINT_ID_BAD,
            "task": TASK_FIELD_BAD,
        }
        fields = list(pools)
        for _ in range(ITERATIONS):
            field = rng.choice(fields)
            bad = rng.choice(pools[field])
            with pytest.raises(ValidationError):
                ValidationRequest(**_valid_request_payload(**{field: bad}))

    def test_multi_field_corruption(self) -> None:
        """Several fields corrupted at once must still be rejected."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            payload = _valid_task_payload(
                episodes=rng.choice(EPISODES_BAD),
                adversarial_count=rng.choice(ADVERSARIAL_BAD),
                randomization=rng.choice(RANDOMIZATION_BAD),
            )
            with pytest.raises(ValidationError):
                TaskConfig(**payload)

    def test_huge_episode_values(self) -> None:
        """Absurdly large episode counts blow the ``le=100000`` bound."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            huge = rng.randrange(100001, 10**12)
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(episodes=huge))

    def test_negative_episode_values(self) -> None:
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            neg = -rng.randrange(1, 10**6)
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(episodes=neg))

    def test_non_64_char_sha_lengths(self) -> None:
        """Random lengths other than exactly 64 are rejected (None is allowed)."""
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            length = rng.choice(
                [ln for ln in range(0, 130) if ln != 64]
            )
            digest = "f" * length
            assert len(digest) != 64
            with pytest.raises(ValidationError):
                ValidationRequest(**_valid_request_payload(checkpoint_sha256=digest))

    def test_nan_and_inf_never_validate(self) -> None:
        """Non-finite floats are rejected for every numeric field."""
        for value in (float("nan"), float("inf"), float("-inf")):
            assert math.isnan(value) or math.isinf(value)
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(episodes=value))
            with pytest.raises(ValidationError):
                TaskConfig(**_valid_task_payload(adversarial_count=value))
