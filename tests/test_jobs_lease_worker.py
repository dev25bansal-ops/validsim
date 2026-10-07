"""Tests for the worker side of job leases (catalog item 07).

Two properties live here. First, a worker must heartbeat while it executes, or
a job that legitimately outlives its lease would be reaped and executed twice
— the exact duplicate-run failure item 07 exists to prevent. Second, a worker
whose lease was already reclaimed must not overwrite the new owner's claim when
it finally finishes; that is what the fencing epoch on the terminal write is
for.

The heartbeat tests wait on an event rather than sleeping, so they assert a
real renewal happened mid-execution instead of racing a timer.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

import validsim.jobs.queue as queue_mod
from validsim.jobs import JobQueue, JobRecord, JobSpec, JobStatus, JobWorker
from validsim.store.memory import ValidationStore

_JOB_ID = "vrun-cafe1234"
#: Lease length used by the heartbeat tests. Renewal happens at a third of this,
#: so a single renewal lands quickly without any fixed sleep.
_TEST_LEASE = 1


def _spec(run_id: str = _JOB_ID, **overrides: Any) -> JobSpec:
    base: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 20,
        "adversarial": 0,
    }
    base.update(overrides)
    return JobSpec(**base)


class _SlowWorker(JobWorker):
    """Worker whose pipeline blocks until the test releases it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.release = threading.Event()
        self.executions = 0

    def _execute(self, spec: JobSpec) -> Any:
        self.executions += 1
        self.started.set()
        self.release.wait(timeout=10.0)
        return SimpleNamespace(run_id=spec.run_id)


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Freeze the queue clock so lease expiry is deterministic."""
    holder = {"now": "2026-09-25T00:00:00+00:00"}
    monkeypatch.setattr(queue_mod, "_utc_now", lambda: holder["now"])
    return holder

@pytest.fixture()
def renewal_probe(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record every lease renewal and expose an event for the first one."""
    seen: dict[str, Any] = {"epochs": [], "first": threading.Event()}
    real = JobQueue.renew_lease

    def spy(self: JobQueue, job_id: str, epoch: int) -> JobRecord | None:
        result = real(self, job_id, epoch)
        if result is not None:
            seen["epochs"].append(epoch)
            seen["first"].set()
        return result

    monkeypatch.setattr(JobQueue, "renew_lease", spy)
    return seen


# ---------------------------------------------------------------------------
# Fenced terminal write
# ---------------------------------------------------------------------------


class TestFencedCompletion:
    def _running(self) -> tuple[JobQueue, JobRecord]:
        queue = JobQueue(lease_seconds=300)
        queue.enqueue(_spec())
        claimed = queue.claim_next()
        assert claimed is not None
        return queue, claimed

    def test_matching_epoch_completes(self) -> None:
        queue, claimed = self._running()
        done = queue.update_status(
            claimed.job_id,
            JobStatus.DONE,
            result=_JOB_ID,
            lease_epoch=claimed.lease_epoch,
        )
        assert done is not None and done.status is JobStatus.DONE

    def test_stale_epoch_is_rejected(self) -> None:
        queue, claimed = self._running()
        assert (
            queue.update_status(
                claimed.job_id,
                JobStatus.DONE,
                result="x",
                lease_epoch=claimed.lease_epoch + 1,
            )
            is None
        )

    def test_queued_job_cannot_be_completed_by_a_lease(self) -> None:
        queue = JobQueue(lease_seconds=300)
        jid = queue.enqueue(_spec()).job_id
        assert (
            queue.update_status(jid, JobStatus.DONE, result="x", lease_epoch=1) is None
        )

    def test_unfenced_update_still_works(self) -> None:
        """Callers that pass no epoch (the HTTP router) keep the old behaviour."""
        queue, claimed = self._running()
        done = queue.update_status(claimed.job_id, JobStatus.DONE, result="x")
        assert done is not None and done.status is JobStatus.DONE


class TestZombieCannotClobber:
    def test_reclaimed_job_is_not_overwritten_by_the_old_worker(
        self, clock: dict[str, str]
    ) -> None:
        queue = JobQueue(lease_seconds=300)
        queue.enqueue(_spec())
        zombie = queue.claim_next()
        assert zombie is not None
        # The lease expires and the reaper hands the job to a new worker.
        clock["now"] = "2026-09-25T00:05:01+00:00"
        assert queue.reap_expired() == [zombie.job_id]
        new_owner = queue.claim_next()
        assert new_owner is not None
        assert new_owner.lease_epoch != zombie.lease_epoch
        # The zombie wakes up and tries to record its result.
        assert (
            queue.update_status(
                zombie.job_id,
                JobStatus.DONE,
                result="zombie",
                lease_epoch=zombie.lease_epoch,
            )
            is None
        )
        current = queue.get(zombie.job_id)
        assert current is not None
        assert current.status is JobStatus.RUNNING
        assert current.result is None


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def _run_until_renewed(
        self, probe: dict[str, Any]
    ) -> tuple[JobWorker, JobQueue, JobQueue]:
        queue = JobQueue(lease_seconds=_TEST_LEASE)
        queue.enqueue(_spec())
        worker = _SlowWorker(queue, ValidationStore())
        thread = threading.Thread(target=worker.run_once)
        thread.start()
        assert worker.started.wait(timeout=5.0), "pipeline never started"
        # A renewal must land while the job is still executing.
        assert probe["first"].wait(timeout=5.0), "lease was never renewed mid-run"
        worker.release.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        return worker, queue, queue

    def test_lease_is_renewed_while_the_job_runs(
        self, renewal_probe: dict[str, Any]
    ) -> None:
        self._run_until_renewed(renewal_probe)
        assert renewal_probe["epochs"], "expected at least one lease renewal"

    def test_a_long_job_is_not_reaped_mid_run(
        self, renewal_probe: dict[str, Any]
    ) -> None:
        worker, queue, _ = self._run_until_renewed(renewal_probe)
        final = queue.get(_JOB_ID)
        assert final is not None and final.status is JobStatus.DONE
        # The job ran exactly once: it was heartbeated, not stolen and re-run.
        assert worker.executions == 1

    def test_renewal_stops_once_the_job_finishes(self) -> None:
        queue = JobQueue(lease_seconds=_TEST_LEASE)
        queue.enqueue(_spec())
        worker = _SlowWorker(queue, ValidationStore())
        worker.release.set()
        result = worker.run_once()
        assert result is not None and result.status is JobStatus.DONE
        # No background thread may keep renewing a finished job.
        final = queue.get(_JOB_ID)
        assert final is not None and final.lease_expires_at is None
