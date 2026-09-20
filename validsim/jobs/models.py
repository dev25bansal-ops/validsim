"""Job data models for the asynchronous validation queue.

The queue decouples "what to run" (:class:`JobSpec`) from "where the job is in
its lifecycle" (:class:`JobRecord`). Both are frozen so callers can share
records across threads without defensive copying; state transitions produce a
new record via :func:`dataclasses.replace` rather than mutating in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = ["JobSpec", "JobStatus", "JobRecord"]


class JobStatus(str, Enum):
    """Lifecycle of a queued validation job."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class JobSpec:
    """What a validation job should run, decoupled from its queue record.

    Attributes:
        run_id: Identifier of the validation run. This is also the job's id,
            so ``run_id`` is the single key shared by the queue record and
            the eventual persisted validation result.
        checkpoint_id: Model checkpoint to validate.
        task_id: Task to execute.
        episodes: Number of nominal (non-adversarial) episodes.
        adversarial: Number of adversarial scenarios to generate/run.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    episodes: int = 1000
    adversarial: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this spec."""
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "episodes": self.episodes,
            "adversarial": self.adversarial,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSpec:
        """Rebuild a spec from a :meth:`to_dict` mapping."""
        return cls(
            run_id=data["run_id"],
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            episodes=int(data.get("episodes", 1000)),
            adversarial=int(data.get("adversarial", 0)),
        )


@dataclass(frozen=True)
class JobRecord:
    """Snapshot of a job's current state within the queue.

    Attributes:
        spec: The immutable job specification.
        status: Current :class:`JobStatus`.
        created_at: ISO-8601 UTC timestamp when the job was enqueued.
        started_at: ISO-8601 UTC timestamp when a worker claimed it, if any.
        finished_at: ISO-8601 UTC timestamp when it reached a terminal state.
        error: Error message, populated only when ``status`` is failed.
        result: Opaque pointer to the job's result artifact (e.g. the
            ``run_id`` of the produced validation run), set on completion.
    """

    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: str | None = None

    @property
    def job_id(self) -> str:
        """Job identifier (the spec's ``run_id``)."""
        return self.spec.run_id

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this record."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "spec": self.spec.to_dict(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        """Rebuild a record from a :meth:`to_dict` mapping."""
        return cls(
            spec=JobSpec.from_dict(data["spec"]),
            status=JobStatus(data["status"]),
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
            result=data.get("result"),
        )