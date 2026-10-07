"""Lease-epoch fencing: a stale worker must never finalize a reclaimed job.

A worker that stalls long enough for its lease to expire can wake up after
another worker has already reclaimed and finished the same job. The
``lease_epoch`` fencing token is what makes that safe -- every terminal
transition and every renewal must verify it, or a zombie worker can overwrite
the result of the worker that legitimately owns the job now.

Each test here was written against a concrete question ("can a stale worker
finalize the job?", "can it resurrect a dead-lettered one?") rather than to
raise coverage, and each is a real regression if the fencing is ever weakened.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from validsim.jobs.models import JobSpec, JobStatus
from validsim.jobs.queue import JobQueue

RUN_ID = "vrun-fence0001"


def _iso_in(seconds: float) -> str:
    """A UTC ISO-8601 instant ``seconds`` from now, in the queue's own format.

    Every queue timestamp is minted in UTC with a second-precision ``+00:00``
    offset, and the Redis reaper compares them as strings, so a test that
    fabricates one must match that format exactly.
    """
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


def _spec(run_id: str = RUN_ID) -> JobSpec:
    return JobSpec(run_id=run_id, checkpoint_id="ck", task_id="t")


class TestStaleWorkerCannotFinalizeAReclaimedJob:
    """The core guarantee: one job, one finalizer."""

    @staticmethod
    def _stale_then_fresh(lease_seconds: int = 10) -> tuple[JobQueue, str, int, int]:
        q = JobQueue(lease_seconds=lease_seconds)
        q.enqueue(_spec())
        first = q.claim_next()
        assert first is not None
        q.advance_clock(lease_seconds + 10)
        assert q.reap_expired() == [RUN_ID]
        second = q.claim_next()
        assert second is not None
        return q, first.job_id, first.lease_epoch, second.lease_epoch

    def test_reclaim_increments_the_epoch(self) -> None:
        """CONTROL: the token only fences if it actually changes on reclaim."""
        _, _, stale_epoch, fresh_epoch = self._stale_then_fresh()
        assert fresh_epoch > stale_epoch, (
            "a reclaim must advance lease_epoch, otherwise the fencing token is "
            "identical before and after and cannot distinguish the two workers"
        )

    def test_stale_worker_cannot_complete(self) -> None:
        q, job_id, stale_epoch, _ = self._stale_then_fresh()
        assert q.update_status(job_id, JobStatus.DONE, lease_epoch=stale_epoch) is None, (
            "a worker whose lease expired was allowed to finalize the job after "
            "another worker reclaimed it"
        )

    def test_stale_worker_cannot_fail_the_job(self) -> None:
        """Fencing must cover every terminal state, not just success.

        A stale worker reporting FAILED would be at least as damaging as one
        reporting DONE -- it would mark a job broken that another worker is
        still legitimately running.
        """
        q, job_id, stale_epoch, fresh_epoch = self._stale_then_fresh()
        assert q.update_status(job_id, JobStatus.FAILED, lease_epoch=stale_epoch) is None
        # ...and the rightful owner is unaffected.
        assert q.update_status(job_id, JobStatus.DONE, lease_epoch=fresh_epoch) is not None
        assert q.get(job_id).status is JobStatus.DONE

    def test_stale_worker_cannot_renew(self) -> None:
        """A zombie worker must not extend a lease it no longer holds."""
        q, job_id, stale_epoch, _ = self._stale_then_fresh()
        assert q.renew_lease(job_id, stale_epoch) is None

    def test_stale_worker_cannot_requeue_for_retry(self) -> None:
        """The retry path is fenced too, or a third concurrent run can start.

        Un-fenced, a stalled worker waking up after its lease was reclaimed
        would push the *new* owner's running job back into the ready list.
        This is asserted while the fresh worker still HOLDS the job, which is
        the window where the bug would do real damage.
        """
        q, job_id, stale_epoch, _ = self._stale_then_fresh()
        result = q.requeue_for_retry(
            job_id,
            lease_epoch=stale_epoch,
            error="boom",
            retry_after=_iso_in(300.0),
        )
        assert result is None, (
            "a stale worker requeued a job that another worker currently holds"
        )
        # The rightful owner still has it, still running, and still alone.
        assert q.get(job_id).status is JobStatus.RUNNING
        assert q.claim_next() is None

    def test_stale_worker_cannot_mark_a_job_succeeded_after_dead_letter(self) -> None:
        """A dead-lettered job is terminal; no epoch may resurrect it.

        Reclaim exhaustion dead-letters the job, which releases the lease and
        settles it. A worker still holding an old epoch must not be able to
        write a terminal result over that decision.
        """
        q = JobQueue(lease_seconds=10, max_attempts=1000, max_reclaims=1)
        q.enqueue(_spec())
        stale = q.claim_next()
        assert stale is not None
        # Reclaim 1 puts it back to `queued`; only a RUNNING job is reaped again.
        q.advance_clock(20)
        q.reap_expired()
        second = q.claim_next()
        assert second is not None
        # Reclaim 2 exceeds max_reclaims=1, so the job is dead-lettered.
        q.advance_clock(20)
        q.reap_expired()
        assert q.get(stale.job_id).status is JobStatus.DEAD
        assert (
            q.update_status(stale.job_id, JobStatus.DONE, lease_epoch=stale.lease_epoch)
            is None
        )
        assert q.get(stale.job_id).status is JobStatus.DEAD


class TestLeaseInvariants:
    def test_an_unexpired_lease_cannot_be_stolen(self) -> None:
        """A second worker must not claim a job that is still legitimately held."""
        q = JobQueue(lease_seconds=3600)
        q.enqueue(_spec())
        first = q.claim_next()
        assert first is not None
        assert q.claim_next() is None, (
            "a running job with an unexpired lease was handed to a second worker"
        )

    def test_requeue_pending_job_is_claimable_exactly_once(self) -> None:
        """A job awaiting retry must not be double-claimed across the cooldown."""
        q = JobQueue(lease_seconds=10)
        q.enqueue(_spec())
        first = q.claim_next()
        assert first is not None
        # retry_after must be a FUTURE instant: that is what puts the job into
        # its cooldown, and claim_next skips any job whose retry_after has not
        # yet expired.
        assert q.requeue_for_retry(
            first.job_id,
            lease_epoch=first.lease_epoch,
            error="x",
            retry_after=_iso_in(300.0),
        )
        assert q.claim_next() is None, (
            "a job cooling down after a failed attempt was claimable immediately"
        )
        q.advance_clock(3600)
        second = q.claim_next()
        assert second is not None
        assert second.lease_epoch > first.lease_epoch
        assert q.claim_next() is None, "the same job was claimed by two workers"

    @pytest.mark.parametrize("attempts", [1, 2, 3])
    def test_epoch_advances_on_every_claim(self, attempts: int) -> None:
        """Monotonic fencing tokens: every claim is a strictly new generation."""
        q = JobQueue(lease_seconds=10, max_attempts=10, max_reclaims=10)
        q.enqueue(_spec())
        seen = []
        for _ in range(attempts):
            record = q.claim_next()
            assert record is not None
            seen.append(record.lease_epoch)
            q.advance_clock(20)
            q.reap_expired()
        assert seen == sorted(set(seen)), f"epochs not strictly increasing: {seen}"
        assert seen[-1] == max(seen)
