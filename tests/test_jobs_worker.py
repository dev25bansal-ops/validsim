"""Tests for :class:`validsim.jobs.worker.JobWorker`.

The worker is the consumer half of the async job pipeline: it claims a queued
job, runs the mock validation pipeline end-to-end, persists the finished run,
and records the terminal status. These tests cover the happy path (queued ->
running -> done plus persistence), the failure path (backend raises -> failed
with the error captured and nothing persisted), the empty-queue no-op, the
determinism guarantee, and ``run_forever`` draining a job before a clean stop.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from validsim.config import TaskConfig
from validsim.jobs import JobQueue, JobSpec, JobStatus, JobWorker
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import EpisodeResult, MockIsaacBackend
from validsim.store.memory import ValidationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(run_id: str = "vrun-cafe1234", **overrides: Any) -> JobSpec:
    """Build a small, fast job spec (overridable for each test)."""
    base: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 20,
        "adversarial": 0,
    }
    base.update(overrides)
    return JobSpec(**base)


class _RaisingBackend:
    """Backend whose every episode raises — drives the failure path."""

    def __init__(self, message: str = "boom") -> None:
        self._message = message

    def run_episode(
        self,
        task: TaskConfig,
        seed: int,
        randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        raise RuntimeError(self._message)


class _SpyBackend:
    """Delegates to the mock backend but snapshots the job status on first call.

    Used to prove the worker marks a job ``running`` *before* the pipeline
    executes, so an observer reading the queue mid-flight sees the transition.
    """

    def __init__(self, queue: JobQueue, job_id: str) -> None:
        self._queue = queue
        self._job_id = job_id
        self._inner = MockIsaacBackend()
        self.seen: JobStatus | None = None

    def run_episode(
        self,
        task: TaskConfig,
        seed: int,
        randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        if self.seen is None:
            record = self._queue.get(self._job_id)
            self.seen = record.status if record is not None else None
        return self._inner.run_episode(task, seed, randomization_level, scenario)


# ---------------------------------------------------------------------------
# run_once — happy path
# ---------------------------------------------------------------------------


class TestRunOnceSuccess:
    def test_transitions_queued_running_done_and_persists(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store)

        record = queue.enqueue(_spec())
        assert record.status is JobStatus.QUEUED

        done = worker.run_once()

        assert done is not None
        assert done.status is JobStatus.DONE
        assert done.started_at is not None
        assert done.finished_at is not None
        # result pointer is the persisted run id (== the job's own run_id)
        assert done.result == record.job_id
        # the queue reflects the terminal state
        assert queue.get(record.job_id) == done

    def test_run_is_persisted_to_store(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store)
        job_id = queue.enqueue(_spec()).job_id

        worker.run_once()

        stored = store.get(job_id)
        assert stored is not None
        assert stored.run_id == job_id
        assert stored.checkpoint_id == "ckpt-1"
        assert stored.task_id == "pick-place"
        assert stored.scorecard.run_id == job_id
        assert stored.scorecard.episode_count == 20
        assert len(store) == 1

    def test_marks_running_before_executing(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id
        spy = _SpyBackend(queue, job_id)

        JobWorker(queue, store, backend=spy).run_once()

        assert spy.seen is JobStatus.RUNNING


# ---------------------------------------------------------------------------
# run_once — failure path
# ---------------------------------------------------------------------------


class TestRunOnceFailure:
    def test_backend_error_marks_failed_and_captures_message(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store, backend=_RaisingBackend("kaboom"))

        queue.enqueue(_spec())
        failed = worker.run_once()

        assert failed is not None
        assert failed.status is JobStatus.FAILED
        assert "kaboom" in (failed.error or "")
        assert failed.result is None
        assert failed.finished_at is not None

    def test_failure_persists_nothing(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store, backend=_RaisingBackend())
        job_id = queue.enqueue(_spec()).job_id

        worker.run_once()

        assert store.get(job_id) is None
        assert len(store) == 0


# ---------------------------------------------------------------------------
# run_once — empty queue & determinism
# ---------------------------------------------------------------------------


class TestRunOnceEdgeCases:
    def test_empty_queue_returns_none(self) -> None:
        worker = JobWorker(JobQueue(), ValidationStore())
        assert worker.run_once() is None

    def test_done_job_is_not_reprocessed(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store)
        queue.enqueue(_spec())

        assert worker.run_once() is not None
        # second call finds nothing queued
        assert worker.run_once() is None

    def test_same_spec_is_deterministic(self) -> None:
        scores = []
        for run_id in ("vrun-aaaa1111", "vrun-aaaa1111"):
            queue = JobQueue()
            store = ValidationStore()
            queue.enqueue(_spec(run_id))
            JobWorker(queue, store).run_once()
            scores.append(store.get(run_id).scorecard.composite_score)  # type: ignore[union-attr]

        assert scores[0] == scores[1]


# ---------------------------------------------------------------------------
# run_forever
# ---------------------------------------------------------------------------


class TestRunForever:
    def test_processes_queued_job_then_stops_cleanly(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store)
        job_id = queue.enqueue(_spec()).job_id

        thread = threading.Thread(
            target=worker.run_forever, kwargs={"poll_seconds": 0.01}, daemon=True
        )
        thread.start()
        try:
            deadline = time.time() + 2.0
            status: JobStatus | None = None
            while time.time() < deadline:
                record = queue.get(job_id)
                status = record.status if record is not None else None
                if status is JobStatus.DONE:
                    break
                time.sleep(0.01)
            assert status is JobStatus.DONE
            assert store.get(job_id) is not None
        finally:
            worker.stop()
            thread.join(timeout=2.0)

        assert not thread.is_alive()
