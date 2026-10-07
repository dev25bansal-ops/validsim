"""Regression tests for the dead-letter signal on ``JobQueue.claim_next``.

The defect
----------
``claim_next`` built its ``dead_letters`` list and reported it from a loop
placed AFTER the ``with self._lock:`` block -- but the success path did
``return self._jobs[record.job_id]`` from INSIDE the lock. A call that settled a
spent job and then claimed a healthy one therefore returned before the report
loop was reached: the job reached its terminal ``dead`` state in silence.

Why the existing tests missed it
--------------------------------
``tests/test_jobs_worker.py`` covers the two shapes that happen NOT to hit the
early return -- the WORKER path (``test_dead_letter_is_reported_not_silent``) and
the REAPER path (``test_dead_letter_signal_is_emitted_by_the_reaper_too``).
Neither exercises ``claim_next``'s claim-and-settle-in-one-call shape, which
jobs-queue notes is the COMMON one: a worker polling a backlog routinely
dead-letters an abandoned job on the same pass that hands it the next unit.

Reachability
------------
The spent-but-QUEUED state is produced the way it happens in production --
``reap_expired`` returning an expired-lease job to the queue while PRESERVING
its attempt counter, so the next claim finds the budget already spent. No private
state is poked.

Every expected value here was measured by running the code, not inferred.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from validsim.jobs import JobQueue, JobSpec, JobStatus


def _spec(run_id: str, **overrides: object) -> JobSpec:
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 3,
        "adversarial": 0,
    }
    base.update(overrides)
    return JobSpec(**base)  # type: ignore[arg-type]


@contextmanager
def _captured() -> Iterator[Callable[[], list[logging.LogRecord]]]:
    """Capture ``validsim.jobs`` log records.

    ``validsim`` sets ``propagate = False``, so pytest's ``caplog`` (attached to
    the root logger) can never see these records. Same reasoning as
    ``_captured_records`` in ``test_jobs_worker.py``, mirrored here so both files
    observe the emission identically.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("validsim.jobs")
    root = logging.getLogger("validsim")
    prev_logger, prev_root = logger.level, root.level
    handler = _Collector(level=logging.DEBUG)
    root.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield lambda: list(records)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_logger)
        root.setLevel(prev_root)


def _dead(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if getattr(r, "validsim_job_dead_lettered", False)]


def _frozen_clock(clock: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``_utc_now`` so lease expiry is driven by the test, not wall time."""
    import validsim.jobs.queue as queue_mod

    monkeypatch.setattr(queue_mod, "_utc_now", lambda: clock["now"])


class TestClaimNextReportsDeadLettersItSettles:
    def test_claim_next_reports_a_dead_letter_and_still_claims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The claim-and-settle shape must emit the signal AND hand out the work.

        A's budget is spent and it is back in the queue, B is healthy, and one
        ``claim_next()`` must do both jobs: mark A dead *and* report it, then
        claim B. Before the fix A still reached ``dead`` (the transition
        happened inside the lock) but nothing was reported.
        """
        clock = {"now": "2026-09-25T00:00:00+00:00"}
        _frozen_clock(clock, monkeypatch)
        queue = JobQueue(lease_seconds=300, max_attempts=1)

        spent = queue.enqueue(_spec(run_id="vrun-claimdl1")).job_id
        healthy = queue.enqueue(_spec(run_id="vrun-claimok1")).job_id

        first = queue.claim_next()
        assert first is not None and first.job_id == spent
        assert first.attempt == 1

        # The lease expires; the reaper returns the job with its attempt counter
        # intact, so the budget is spent but the status is QUEUED again.
        clock["now"] = "2026-09-25T00:10:00+00:00"
        queue.reap_expired()
        requeued = queue.get(spent)
        assert requeued is not None
        assert requeued.status is JobStatus.QUEUED
        assert requeued.attempt == 1, "the reaper must preserve the spent attempt count"

        # THE SHAPE: one call settles the spent job and claims the healthy one.
        with _captured() as records:
            claimed = queue.claim_next()
            captured = records()

        assert claimed is not None, "claim_next stopped claiming after settling a job"
        assert claimed.job_id == healthy
        assert queue.get(healthy).status is JobStatus.RUNNING

        dead = _dead(captured)
        assert dead, (
            "claim_next settled a spent job but emitted no dead-letter signal; "
            "the job reached `dead` in silence"
        )
        assert [r.validsim_job_id for r in dead] == [spent]
        assert dead[0].validsim_job_reason == "retry-budget-exhausted"
        assert dead[0].validsim_job_attempts == 1
        assert dead[0].levelno >= logging.WARNING
        assert spent in "\n".join(r.getMessage() for r in captured)

        settled = queue.get(spent)
        assert settled is not None and settled.status is JobStatus.DEAD
        assert "attempt" in (settled.error or "")

    def test_settling_a_spent_job_alone_still_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONTROL: the no-healthy-job case must keep signalling after the fix.

        This is the asymmetry pin. Pre-fix, this shape DID signal (it fell
        through to the end of the function) while the claim-and-settle shape did
        not. A test covering only the settling case would pass on the broken
        code; one covering only claim-and-settle would not show what changed.
        Asserting both makes the regression legible -- and stops a "fix" that
        merely deletes the report loop from looking green.
        """
        clock = {"now": "2026-09-25T00:00:00+00:00"}
        _frozen_clock(clock, monkeypatch)
        queue = JobQueue(lease_seconds=300, max_attempts=1)

        only = queue.enqueue(_spec(run_id="vrun-claimsolo")).job_id
        assert queue.claim_next() is not None
        clock["now"] = "2026-09-25T00:10:00+00:00"
        queue.reap_expired()

        with _captured() as records:
            claimed = queue.claim_next()
            captured = records()

        # Nothing healthy left to hand out, so the early return cannot be the
        # reason the signal appeared -- it came from the report loop.
        assert claimed is None
        dead = _dead(captured)
        assert dead, "the settled-a-spent-job-alone shape lost its signal"
        assert [r.validsim_job_id for r in dead] == [only]
        assert dead[0].validsim_job_reason == "retry-budget-exhausted"
        assert queue.get(only).status is JobStatus.DEAD

    def test_a_healthy_claim_alone_emits_no_dead_letter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NEGATIVE CONTROL: the loop must not invent reports.

        Without this, a ``claim_next`` that reported on every call would satisfy
        the two tests above while alerting on every poll.
        """
        clock = {"now": "2026-09-25T00:00:00+00:00"}
        _frozen_clock(clock, monkeypatch)
        queue = JobQueue(lease_seconds=300, max_attempts=3)

        healthy = queue.enqueue(_spec(run_id="vrun-claimok2")).job_id

        with _captured() as records:
            claimed = queue.claim_next()
            captured = records()

        assert claimed is not None and claimed.job_id == healthy
        assert not _dead(captured), "a healthy claim emitted a dead-letter signal"

    def test_the_settled_job_is_not_left_claimable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """State must change too, not just the reporting.

        Pins that the settled job leaves the queue for good. A fix that only
        moved the report loop could satisfy the signal tests above while the job
        stayed claimable forever.
        """
        clock = {"now": "2026-09-25T00:00:00+00:00"}
        _frozen_clock(clock, monkeypatch)
        queue = JobQueue(lease_seconds=300, max_attempts=1)

        spent = queue.enqueue(_spec(run_id="vrun-claimgone")).job_id
        assert queue.claim_next() is not None
        clock["now"] = "2026-09-25T00:10:00+00:00"
        queue.reap_expired()

        assert queue.claim_next() is None  # settles it
        # Every later poll must find nothing: `dead` is terminal.
        for _ in range(3):
            assert queue.claim_next() is None
        assert queue.get(spent).status is JobStatus.DEAD