"""Tests for :class:`validsim.jobs.worker.JobWorker`.

The worker is the consumer half of the async job pipeline: it claims a queued
job, runs the mock validation pipeline end-to-end, persists the finished run,
and records the terminal status. These tests cover the happy path (queued ->
running -> done plus persistence), the failure path (backend raises -> failed
with the error captured and nothing persisted), the empty-queue no-op, the
determinism guarantee, and ``run_forever`` draining a job before a clean stop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
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
        """The *last* attempt records the failure with the original message.

        A single-attempt budget is what makes this the terminal path: with the
        shipped default the first failure is a retry, so these assertions are
        about the attempt that runs out of budget, not about the first error.
        """
        queue = JobQueue(lease_seconds=300, max_attempts=1)
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
        queue = JobQueue(lease_seconds=300, max_attempts=1)
        store = ValidationStore()
        worker = JobWorker(queue, store, backend=_RaisingBackend())
        job_id = queue.enqueue(_spec()).job_id

        worker.run_once()

        assert store.get(job_id) is None
        assert len(store) == 0


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
#
# The retry policy used to be inverted. A worker exception was terminal on the
# very first attempt (no retry at all), while a *dead worker* had its job
# requeued forever with no bound and no signal. So a transient fault — a flaky
# filesystem, an OOM-killed sibling, a database briefly unreachable — became
# permanent, and a poison job looped until someone restarted the process. Both
# ends are now bounded by the same attempt budget, and both end in an
# observable terminal state.


class _Queue(JobQueue):
    """A queue whose retry backoff is zero, so retries need no clock movement.

    The production default (5s, doubling) is real and asserted separately; this
    subclass exists so a test about *whether* a retry happens is not also a test
    about waiting.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(retry_backoff=0.0, **kwargs)


def _flaky_backend(failures: int, message: str = "transient") -> Any:
    """A backend that raises for the first ``failures`` episodes of a job.

    Counting *episodes* rather than jobs is what makes this deterministic: the
    worker retries the job, the second attempt gets past the failing episodes
    and completes it, and no sleeping or timing is involved.
    """

    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def run_episode(self, *args: Any, **kwargs: Any) -> EpisodeResult:
            self.calls += 1
            if self.calls <= failures:
                raise RuntimeError(message)
            return MockIsaacBackend().run_episode(*args, **kwargs)

    return _Flaky()


@contextmanager
def _captured_records(logger_name: str = "validsim.jobs") -> Iterator[Any]:
    """Yield an accessor returning the :class:`logging.LogRecord` list captured.

    :func:`validsim.logging.configure_logging` deliberately sets
    ``propagate = False`` on the ``validsim`` logger so an embedding host that
    configures root logging does not receive every ValidSim record twice -- once
    as our JSON line and once in the host's own format. That is a real property
    worth keeping, but it also means pytest's ``caplog`` -- whose handler lives on
    the *root* logger -- can never observe a ValidSim record, so ``caplog.records``
    is always empty here no matter what the code does.

    Capturing at ``validsim.jobs`` instead is the faithful way to observe the
    emission: a handler on an ancestor logger still sees records logged on a
    descendant (``validsim.jobs.queue``, ``validsim.jobs.worker``) through
    ordinary propagation, and ``propagate = False`` only stops propagation
    *past* ``validsim``. Nothing about the production code changes -- the
    records yielded here are the very ones an operator's JSON handler receives.

    Attributes asserted on the returned records are the real structured fields
    (``validsim_job_dead_lettered``, ``validsim_job_id``, ...), so these tests
    check the signal itself rather than a substring of a rendered line.

    The handler is detached and the levels restored in ``finally``, so a test
    cannot leak a handler into the next one.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(logger_name)
    # The originating logger is `validsim.jobs.queue`; its effective level comes
    # from `validsim`, so lowering that (not just the handler) is what makes the
    # capture independent of a VALIDSIM_LOG_LEVEL set in the environment.
    root = logging.getLogger("validsim")
    previous_root_level = root.level
    previous_logger_level = logger.level
    handler = _Collector(level=logging.DEBUG)
    root.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield lambda: list(records)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_logger_level)
        root.setLevel(previous_root_level)


def _advance(moment: str, seconds: int) -> str:
    """``moment`` shifted forward by ``seconds`` (always a UTC ISO string)."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(moment) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


class TestRetryPolicy:
    def _queue_with_budget(self, attempts: int) -> tuple[_Queue, JobSpec]:
        queue = _Queue(lease_seconds=300, max_attempts=attempts)
        spec = _spec(run_id="vrun-retry001", episodes=3)
        queue.enqueue(spec)
        return queue, spec

    def test_a_transient_failure_is_retried_and_can_succeed(self) -> None:
        """A first-attempt failure must not be terminal — that was the bug.

        The job goes back to ``queued`` so the next claim picks it up, and the
        second attempt completes it.
        """
        queue = _Queue(lease_seconds=300)
        store = ValidationStore()
        worker = JobWorker(queue, store, backend=_flaky_backend(failures=1))
        job_id = queue.enqueue(_spec(run_id="vrun-flaky001", episodes=3)).job_id

        first = worker.run_once()

        assert first is not None
        assert first.status is not JobStatus.DONE, "a transient fault was made terminal"
        record = queue.get(job_id)
        assert record is not None
        assert record.status is JobStatus.QUEUED, "the job must be retried, not dropped"
        assert record.attempt == 1

        second = worker.run_once()

        assert second is not None and second.status is JobStatus.DONE
        assert store.get(job_id) is not None
        assert queue.get(job_id).attempt == 2

    def test_attempts_are_bounded_and_the_last_one_fails(self) -> None:
        """The budget caps the loop; the final attempt records the failure."""
        attempts = 3
        queue, spec = self._queue_with_budget(attempts)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("always"))

        outcomes = [worker.run_once() for _ in range(attempts)]

        assert all(outcome is not None for outcome in outcomes)
        # Every attempt before the last goes back to queued for a retry.
        assert [outcome.status for outcome in outcomes[:-1]] == [JobStatus.QUEUED] * (
            attempts - 1
        )
        assert outcomes[-1].status is JobStatus.FAILED
        assert "always" in (outcomes[-1].error or "")
        # Nothing left to claim: the loop terminated instead of spinning.
        assert worker.run_once() is None
        assert queue.get(spec.run_id).attempt == attempts

    def test_a_poison_job_terminates_rather_than_looping_forever(self) -> None:
        """The invariant: no job is retried without bound."""
        attempts = 4
        queue, spec = self._queue_with_budget(attempts)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("poison"))

        for _ in range(attempts * 3):
            if worker.run_once() is None:
                break

        record = queue.get(spec.run_id)
        assert record is not None
        assert record.status is JobStatus.FAILED
        assert record.attempt == attempts

    def test_retry_backoff_is_recorded_and_grows(self) -> None:
        """Each requeue is delayed, so a failing backend cannot be hot-spun."""
        queue = JobQueue(lease_seconds=300, max_attempts=4, retry_backoff=30.0)
        spec = _spec(run_id="vrun-backoff1", episodes=3)
        queue.enqueue(spec)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("boom"))

        record = worker.run_once()
        assert record is not None
        assert record.attempt == 1
        first_delay = queue.retry_delay_for(spec.run_id)

        # Past the first backoff, so the second attempt can be reached at all.
        queue.advance_clock(60)
        second = worker.run_once()
        assert second is not None
        second_delay = queue.retry_delay_for(spec.run_id)

        assert first_delay is not None
        assert second_delay is not None
        assert second_delay > first_delay, "backoff did not grow between attempts"

    def test_a_cooling_job_does_not_block_the_jobs_behind_it(self) -> None:
        """Backoff must delay one job, not the whole queue.

        The claim scans past a cooling job to the next candidate; a worker that
        stopped at the first cooling entry would let one failing job idle the
        entire backlog for the length of its backoff.
        """
        queue = JobQueue(lease_seconds=300, max_attempts=4, retry_backoff=300.0)
        cooling = queue.enqueue(_spec(run_id="vrun-cool0001", episodes=3)).job_id
        ready = queue.enqueue(_spec(run_id="vrun-ready001", episodes=3)).job_id
        failing = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("boom"))

        assert failing.run_once() is not None  # the first job fails and backs off
        assert queue.get(cooling).status is JobStatus.QUEUED

        claimed = queue.claim_next()

        assert claimed is not None
        assert claimed.job_id == ready

    def test_a_job_is_not_claimed_before_its_backoff_elapses(self) -> None:
        """``claim_next`` must skip a job that is still cooling down."""
        queue = JobQueue(lease_seconds=300, max_attempts=4, retry_backoff=300.0)
        spec = _spec(run_id="vrun-cooloff1", episodes=3)
        queue.enqueue(spec)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("boom"))
        worker.run_once()

        # A second worker polling now must skip the cooling job...
        assert (
            JobWorker(queue, ValidationStore(), backend=_RaisingBackend()).run_once()
            is None
        )
        # ...and must still see it once the backoff has elapsed.
        queue.advance_clock(10_000)
        assert queue.claim_next() is not None

    def test_dead_letter_is_reported_not_silent(self) -> None:
        """A job that exhausts its budget must be *observable*.

        Before the fix a dead worker's job was requeued forever and no counter
        existed, so a job that could never finish looked exactly like one that
        was merely slow. A terminal outcome therefore has to leave a trace an
        operator can alert on.
        """
        attempts = 2
        queue = _Queue(lease_seconds=300, max_attempts=attempts)
        spec = _spec(run_id="vrun-signal01", episodes=3)
        queue.enqueue(spec)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("always"))

        with _captured_records() as records:
            for _ in range(attempts):
                worker.run_once()
            captured = records()

        messages = "\n".join(record.getMessage() for record in captured)
        assert spec.run_id in messages, "the dead-lettered job id was never reported"
        assert any(
            getattr(record, "validsim_job_dead_lettered", False)
            for record in captured
        ), "no structured dead-letter signal was emitted"
        # The signal must be a WARNING an alerting rule can match on, and must
        # name the reason and the budgets that were spent -- a bare "job failed"
        # cannot distinguish a poison payload from a flaky backend.
        dead = [
            record
            for record in captured
            if getattr(record, "validsim_job_dead_lettered", False)
        ]
        assert dead, "no dead-letter record was captured"
        assert all(record.levelno >= logging.WARNING for record in dead)
        assert dead[0].validsim_job_id == spec.run_id
        assert "attempt" in dead[0].validsim_job_reason
        assert dead[0].validsim_job_attempts == attempts

    def test_dead_letter_signal_is_emitted_by_the_reaper_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spending the *reclaim* budget is a dead-letter as well.

        A job whose worker dies repeatedly never goes through ``run_once``'s
        error path, so the reaper is the only place that can notice the budget
        running out. Silence there is the original "no signal" hole.
        """
        import validsim.jobs.queue as queue_mod

        clock = {"now": "2026-09-25T00:00:00+00:00"}
        monkeypatch.setattr(queue_mod, "_utc_now", lambda: clock["now"])
        # max_attempts is raised so the *reclaim* budget is the one under test.
        # Both default to 3, and `claim_next` settles an exhausted job on the
        # attempt budget before the reaper can spend the reclaim one, so at the
        # shipped defaults this loop would dead-letter through the wrong branch.
        queue = JobQueue(lease_seconds=300, max_attempts=1000)
        job_id = queue.enqueue(_spec(run_id="vrun-reapsig1")).job_id

        with _captured_records() as records:
            for _ in range(queue_mod._MAX_RECLAIMS + 2):
                queue.claim_next()
                clock["now"] = _advance(clock["now"], 400)
                queue.reap_expired()
            captured = records()

        record = queue.get(job_id)
        assert record is not None and record.status is JobStatus.DEAD
        messages = "\n".join(entry.getMessage() for entry in captured)
        assert job_id in messages
        assert any(
            getattr(entry, "validsim_job_dead_lettered", False) for entry in captured
        )
        # ...and it must be the *reaper's* signal, not the attempt budget's.
        # Without this the test could pass while proving nothing about the branch
        # it names.
        reasons = {
            entry.validsim_job_reason
            for entry in captured
            if getattr(entry, "validsim_job_dead_lettered", False)
        }
        assert reasons == {"reclaim-budget-exhausted"}
        assert "reclaim" in (record.error or "")

    def test_a_worker_exception_keeps_its_message_on_the_last_attempt(self) -> None:
        """The final attempt preserves the original error for the operator."""
        queue = JobQueue(lease_seconds=300, max_attempts=1)
        spec = _spec(run_id="vrun-lastmsg1", episodes=3)
        queue.enqueue(spec)
        worker = JobWorker(queue, ValidationStore(), backend=_RaisingBackend("disk full"))

        outcome = worker.run_once()

        assert outcome is not None
        assert outcome.status is JobStatus.FAILED
        assert "disk full" in (outcome.error or "")
        assert outcome.finished_at is not None

    def test_a_success_after_retries_is_reported_normally(self) -> None:
        """Recovered jobs are plain ``done`` — a retry leaves no scar."""
        # `_Queue` zeroes the backoff so the retry is reachable without waiting,
        # matching every sibling retry test in this file. The shipped 5s default
        # is asserted separately in test_retry_backoff_is_recorded_and_grows.
        queue = _Queue(lease_seconds=300, max_attempts=5)
        job_id = queue.enqueue(_spec(run_id="vrun-recover", episodes=3)).job_id
        store = ValidationStore()
        worker = JobWorker(queue, store, backend=_flaky_backend(failures=1))

        first = worker.run_once()
        assert first is not None and first.status is JobStatus.QUEUED
        outcome = worker.run_once()

        assert outcome is not None and outcome.status is JobStatus.DONE
        record = queue.get(job_id)
        assert record is not None
        assert record.attempt == 2
        assert record.error is None
        assert store.get(job_id) is not None


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

    def test_two_workers_never_both_execute_one_queued_job(self) -> None:
        class CoordinatedQueue(JobQueue):
            """Makes the old list-then-update claim path deterministically race."""

            def __init__(self) -> None:
                super().__init__()
                self._legacy_list_barrier = threading.Barrier(2)

            def list(self) -> list[JobStatus]:
                records = super().list()
                if records and records[0].status is JobStatus.QUEUED:
                    self._legacy_list_barrier.wait(timeout=2.0)
                return [record.status for record in records]

        queue = CoordinatedQueue()
        store = ValidationStore()
        job_id = queue.enqueue(_spec(episodes=1)).job_id
        executions: list[str] = []
        executions_lock = threading.Lock()

        class CountingBackend:
            def run_episode(
                self,
                task: TaskConfig,
                seed: int,
                randomization_level: str,
                scenario: AdversarialScenario | None = None,
            ) -> EpisodeResult:
                with executions_lock:
                    executions.append(task.task_id)
                return MockIsaacBackend().run_episode(
                    task, seed, randomization_level, scenario
                )

        workers = [
            JobWorker(queue, store, backend=CountingBackend()),
            JobWorker(queue, store, backend=CountingBackend()),
        ]
        start = threading.Barrier(2)
        results: list[object] = [None, None]
        errors: list[str] = []

        def run(index: int) -> None:
            start.wait(timeout=2.0)
            try:
                results[index] = workers[index].run_once()
            except Exception as exc:  # noqa: BLE001 - surface thread failures in main thread
                errors.append(str(exc))

        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)
            assert not thread.is_alive()

        assert errors == []
        assert executions == ["pick-place"]
        assert sum(result is not None for result in results) == 1
        assert sum(result is None for result in results) == 1
        assert store.get(job_id) is not None
        assert queue.get(job_id).status is JobStatus.DONE

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


# ---------------------------------------------------------------------------
# Gate parity: the job's own threshold and baseline must reach the scorecard
# ---------------------------------------------------------------------------
#
# The spec carried neither a threshold nor a baseline, so the worker always
# gated at its own default of 85 and never compared against anything. The same
# checkpoint therefore got a different verdict depending on whether it was
# validated synchronously (which honors the caller's knobs) or asynchronously
# (which could not even be told them) — and because notification severity is
# read off the scorecard's ``threshold``/``deploy_decision`` pair, the wrong
# hooks fired on top of the wrong verdict.


class TestSpecGateReachesTheScorecard:
    def test_spec_threshold_is_used_instead_of_the_worker_default(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        spec = _spec(run_id="vrun-gate0001", episodes=10, adversarial=3, threshold=99.9)
        queue.enqueue(spec)

        # The worker is left at its own permissive default on purpose.
        JobWorker(queue, store, threshold=1.0).run_once()

        run = store.get(spec.run_id)
        assert run is not None
        assert run.scorecard.threshold == pytest.approx(99.9)
        assert run.scorecard.deploy_decision == "BLOCK"

    def test_spec_threshold_of_none_keeps_the_worker_default(self) -> None:
        """``None`` means "unspecified", so the worker's own default applies."""
        queue = JobQueue()
        store = ValidationStore()
        spec = _spec(run_id="vrun-gate0002", episodes=3)
        assert spec.threshold is None
        queue.enqueue(spec)

        JobWorker(queue, store, threshold=42.0).run_once()

        run = store.get(spec.run_id)
        assert run is not None
        assert run.scorecard.threshold == pytest.approx(42.0)

    def test_spec_round_trip_keeps_the_gate_fields(self) -> None:
        """The new fields survive persistence, or a restart silently re-gates."""
        spec = _spec(threshold=72.5, baseline_run_id="vrun-base0001")
        restored = JobSpec.from_dict(spec.to_dict())
        assert restored == spec

    def test_sync_and_async_agree_for_the_same_checkpoint(self) -> None:
        """The parity property itself, asserted end to end.

        The same checkpoint at the same threshold must produce the same
        ``deploy_decision`` and the same notification severity on both paths.
        Before the fix the async path could not be told a threshold at all, so
        it gated at 85 and could return ``APPROVE`` where the sync path returned
        ``BLOCK`` — and severity is derived from that pair, so a
        ``min_severity`` filter on notification hooks picked the wrong side too.
        """
        from validsim.engine.pipeline import run_and_score
        from validsim.jobs.worker import run_validation
        from validsim.notify.dispatcher import severity_from_scorecard

        threshold = 99.5
        spec = _spec(run_id="vrun-parity01", episodes=10, adversarial=3, threshold=threshold)

        # Sync: the caller supplies the threshold, as the sync endpoint does.
        sync_store = ValidationStore()
        sync_run = run_and_score(
            TaskConfig(
                task_id=spec.task_id,
                robot=RobotSpec(name="franka_panda"),
                environment=EnvironmentSpec(name="mock-scene"),
                episodes=spec.episodes,
                adversarial_count=spec.adversarial,
            ),
            spec.checkpoint_id,
            sync_store,
            threshold=threshold,
            run_validation=run_validation,
        )

        # Async: the same threshold travels on the spec.
        async_queue = JobQueue()
        async_store = ValidationStore()
        async_queue.enqueue(spec)
        JobWorker(async_queue, async_store).run_once()
        async_run = async_store.get(spec.run_id)
        assert async_run is not None

        assert async_run.scorecard.deploy_decision == sync_run.scorecard.deploy_decision
        assert async_run.scorecard.threshold == sync_run.scorecard.threshold
        assert severity_from_scorecard(
            async_run.scorecard.to_dict()
        ) == severity_from_scorecard(sync_run.scorecard.to_dict())

    def test_baseline_run_id_reaches_the_pipeline(self) -> None:
        """A baseline named on the spec must actually be compared against.

        Otherwise the async path silently omits the regression analysis the sync
        path performs, and a run looks clean only because nothing was compared.
        """
        from validsim.engine.pipeline import run_and_score
        from validsim.jobs.worker import run_validation

        baseline_store = ValidationStore()
        baseline_id = "vrun-base0001"
        baseline_store.save(
            run_and_score(
                TaskConfig(
                    task_id="pick-place",
                    robot=RobotSpec(name="franka_panda"),
                    environment=EnvironmentSpec(name="mock-scene"),
                    episodes=3,
                ),
                "ckpt-1",
                baseline_store,
                run_id=baseline_id,
                run_validation=run_validation,
            )
        )

        queue = JobQueue()
        # The worker resolves the baseline from its own store, so the baseline
        # has to be present there too — exactly as in a shared deployment.
        store = ValidationStore()
        baseline = baseline_store.get(baseline_id)
        assert baseline is not None
        store.save(baseline)
        spec = _spec(
            run_id="vrun-basel01", episodes=10, adversarial=3, baseline_run_id=baseline_id
        )
        queue.enqueue(spec)

        JobWorker(queue, store).run_once()

        run = store.get(spec.run_id)
        assert run is not None
        assert run.baseline_run_id == baseline_id
        assert run.regression is not None, "no regression was computed"

    def test_unknown_baseline_fails_the_job_not_the_worker(self) -> None:
        """A missing baseline is a job-level error the retry policy absorbs.

        The sync path answers 404; the async path has no HTTP, so the job must
        end ``failed`` carrying the message, and the worker must survive to take
        the next job rather than dying with it.
        """
        queue = JobQueue(lease_seconds=300, max_attempts=2, retry_backoff=0.0)
        store = ValidationStore()
        spec = _spec(
            run_id="vrun-nobase1", episodes=10, adversarial=3, baseline_run_id="vrun-missing"
        )
        queue.enqueue(spec)
        worker = JobWorker(queue, store)

        first = worker.run_once()
        second = worker.run_once()

        assert first is not None and first.status is JobStatus.QUEUED
        assert second is not None and second.status is JobStatus.FAILED
        assert "vrun-missing" in (second.error or "")
        # The worker itself is unharmed and still drains the queue.
        queue.enqueue(_spec(run_id="vrun-after01", episodes=3))
        assert worker.run_once() is not None
