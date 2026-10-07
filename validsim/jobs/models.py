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

__all__ = ["JobSpec", "JobStatus", "JobRecord", "TERMINAL_STATUSES"]


class JobStatus(str, Enum):
    """Lifecycle of a queued validation job.

    ``dead`` is the dead-letter state: the job's retry budget was spent without
    it ever completing, so it was abandoned rather than failed. It is kept
    distinct from ``failed`` because the two mean different things to whoever
    reads the queue — ``failed`` ran and reported an error, ``dead`` never got
    to report anything (its worker died, or its payload was unreadable). Folding
    them together would hide exactly the poison-job case an operator needs to
    see, and would make the retry budget invisible in the job history.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


#: Statuses a job never leaves. Both the reaper and the SSE stream key off this
#: set, so a newly added terminal status cannot be forgotten by one of them:
#: anything not listed here is still eligible for reclaim or for polling.
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.DEAD}
)


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
        threshold: Composite score required for an ``APPROVE`` decision, or
            ``None`` to use the worker's own default. Carrying the gate on the
            spec is what makes the async path agree with the synchronous one:
            without it every queued run was scored at 85, so the same checkpoint
            could come back ``APPROVE`` here and ``BLOCK`` from
            ``POST /validations``, and the notification severity derived from
            that pair would disagree as well.
        baseline_run_id: Optional prior run to compare against for regressions,
            resolved from the worker's store. ``None`` means "no comparison",
            matching the sync request's default.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    episodes: int = 1000
    adversarial: int = 0
    threshold: float | None = None
    baseline_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this spec."""
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "episodes": self.episodes,
            "adversarial": self.adversarial,
            "threshold": self.threshold,
            "baseline_run_id": self.baseline_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSpec:
        """Rebuild a spec from a :meth:`to_dict` mapping.

        ``threshold`` and ``baseline_run_id`` are read with ``.get`` so a
        payload persisted before they existed still deserializes; a missing
        threshold means "use the default", which is the same thing the sync
        endpoint treats ``None`` as.
        """
        threshold = data.get("threshold")
        return cls(
            run_id=data["run_id"],
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            episodes=int(data.get("episodes", 1000)),
            adversarial=int(data.get("adversarial", 0)),
            threshold=None if threshold is None else float(threshold),
            baseline_run_id=data.get("baseline_run_id"),
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
        error: Error message, populated when ``status`` is ``failed`` (the
            attempt that raised) or ``dead`` (the budget was exhausted).
        result: Opaque pointer to the job's result artifact (e.g. the
            ``run_id`` of the produced validation run), set on completion.
        lease_expires_at: ISO-8601 UTC timestamp after which the current claim
            is considered abandoned and the job may be reclaimed. ``None``
            whenever the job is not held under an active lease.
        lease_epoch: Monotonic fencing counter, incremented on every claim.
            A worker that was stalled past its lease and lost the job to a
            reaper cannot renew or complete the reclaimed job because its
            epoch no longer matches.
        attempt: Number of attempts already started, so ``0`` on a fresh job
            and ``1`` once a worker has claimed it. Bounds the retry loop: a
            worker failure returns the job to ``queued`` only while
            ``attempt < max_attempts``.
        reclaim_count: Number of times the reaper has taken this job back from
            a worker that died holding it. Independent of ``attempt``, because
            a dead worker never gets far enough to increment it — without a
            separate counter those jobs would loop forever.
        retry_after: ISO-8601 UTC instant before which the job must not be
            claimed again (the retry backoff). ``None`` when no backoff applies.
    """

    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: str | None = None
    lease_expires_at: str | None = None
    lease_epoch: int = 0
    attempt: int = 0
    reclaim_count: int = 0
    retry_after: str | None = None

    @property
    def job_id(self) -> str:
        """Job identifier (the spec's ``run_id``)."""
        return self.spec.run_id

    @property
    def is_terminal(self) -> bool:
        """Whether this job has reached a state it will never leave."""
        return self.status in TERMINAL_STATUSES

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
            "lease_expires_at": self.lease_expires_at,
            "lease_epoch": self.lease_epoch,
            "attempt": self.attempt,
            "reclaim_count": self.reclaim_count,
            "retry_after": self.retry_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        """Rebuild a record from a :meth:`to_dict` mapping.

        The lease and retry fields are optional so a payload persisted before
        they existed still deserializes instead of raising ``KeyError``.
        """
        return cls(
            spec=JobSpec.from_dict(data["spec"]),
            status=JobStatus(data["status"]),
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
            result=data.get("result"),
            lease_expires_at=data.get("lease_expires_at"),
            lease_epoch=int(data.get("lease_epoch", 0)),
            attempt=int(data.get("attempt", 0)),
            reclaim_count=int(data.get("reclaim_count", 0)),
            retry_after=data.get("retry_after"),
        )
