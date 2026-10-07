"""Concurrency and failure-mode tests for the job queue (agent ``jobs-audit``).

Existing coverage (``tests/test_jobs_lease.py``, ``tests/test_jobs_lease_worker.py``)
proves the fencing *contract*, but it does so with a frozen ``_utc_now`` and a
mock clock, and it asserts on *claims* rather than on *executions*. This file
attacks the same properties three ways it was not previously attacked:

1. **Real threads, real work, real clock.** The existing claim-race test replaces
   ``_utc_now`` with a constant, which removes the thread switch that would
   otherwise occur inside the critical section, and it asserts that two threads
   cannot both *claim*. Here N real threads race on one job with a real backend
   that counts executions -- against the in-memory queue, a live Redis server,
   and a Lua-executing ``fakeredis``.
2. **Real threads racing the reaper.** Reclaim is where fencing earns its keep:
   a worker stalled past its lease, reclaimed, then attempting a terminal write
   or a lease renewal.
3. **Retry after a crash.** The worker reuses ``spec.run_id`` on every attempt
   and the SQLite store is ``INSERT OR REPLACE``, so a retry silently replaces
   the row a previous attempt already wrote. Measured here, not assumed.

Two deliberate non-mocks, both load-bearing:

* The in-memory queue's ``threading.Lock`` is the mechanism under test. Stubbing
  a lock can only ever prove the *absence* of a race, so it is left in place.
* No worker subclass touches ``_claim_next``, ``_finish``, or the queue. The
  subclasses replace only ``_execute`` (what the pipeline does) and
  ``_heartbeat`` (how a frozen process behaves), so the claim, fencing and
  terminal-write code under test is the production code.
"""

from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import pytest

import validsim.jobs.queue as queue_mod
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import run_and_score
from validsim.jobs import (
    JobQueue,
    JobRecord,
    JobSpec,
    JobStatus,
    JobWorker,
    RedisJobQueue,
)
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import ValidationStore
from validsim.store.sqlite import SqliteValidationStore

_JOB_ID = "vrun-cafe1234"

#: Threads per race. Small enough to stay fast, large enough that the
#: interpreter must actually switch between them mid-operation.
_RACE_THREADS = 8

#: Rounds of the repeated claim race, so "no race observed" carries a sample size.
_RACE_ROUNDS = 50

#: Lease length for the real-clock fencing tests. The real lease is ``N-1..N``
#: seconds (see :func:`_real_lease_left`), so 2s leaves comfortable headroom over
#: the ``N/3`` heartbeat interval.
_FENCING_LEASE = 2

#: Lease length for the "never stolen" tests: 3s is the smallest length whose
#: truncation loss still leaves a full second of margin over the ``N/3`` heartbeat.
_SAFE_LEASE = 3

#: How long to wait past the lease before expecting the reaper to reclaim.
#: A whole second is required because ``_utc_now`` truncates to whole seconds, so
#: the effective lease is between ``N-1`` and ``N`` real seconds.
_WAIT_PAST_LEASE = 1.0

#: Generous bound for a synchronised race. A correct implementation finishes in
#: microseconds, so reaching this is a failure rather than a flake.
_JOIN_TIMEOUT = 30.0


def _spec(run_id: str = _JOB_ID, **overrides: Any) -> JobSpec:
    """Small, fast job spec (overridable per test)."""
    base: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 2,
        "adversarial": 0,
    }
    base.update(overrides)
    return JobSpec(**base)


def _unique_run_id() -> str:
    """A fresh ``vrun-`` id, for backends whose state outlives one test."""
    return f"vrun-{uuid.uuid4().hex[:8]}"


def _utc_stamp(offset_seconds: float = 0.0) -> str:
    """An ISO-8601 UTC stamp ``offset_seconds`` from now, in the queue's format."""
    moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=offset_seconds
    )
    return moment.isoformat(timespec="seconds")


def _real_lease_left(lease_expires_at: str | None) -> float:
    """Seconds of *real* time remaining on a stamped lease deadline.

    The queue stamps deadlines from ``_utc_now``, which formats with
    ``timespec="seconds"`` and therefore truncates. A configured
    ``lease_seconds=N`` is worth only ``N-1..N`` real seconds -- the whole
    subject of the sub-second-precision xfail near the end of this file.
    """
    assert lease_expires_at is not None
    deadline = datetime.datetime.fromisoformat(lease_expires_at)
    return (deadline - datetime.datetime.now(datetime.timezone.utc)).total_seconds()


# ---------------------------------------------------------------------------
# Controlled backends
# ---------------------------------------------------------------------------


class _AllPassBackend:
    """Every episode succeeds cleanly -- a high composite with no block reasons."""

    def run_episode(
        self, task: Any, seed: int, randomization_level: str, scenario: Any = None
    ) -> EpisodeResult:
        return EpisodeResult(
            episode_id=f"{task.task_id}-seed{seed:010d}",
            task_id=task.task_id,
            seed=seed,
            success=True,
            collision_count=0,
            max_contact_force_n=1.0,
            min_human_distance_m=2.0,
            failure_mode=None,
            duration_s=1.0,
            randomization_level=randomization_level,
        )


class _AllFailBackend:
    """Every episode fails hard -- the most negative verdict obtainable."""

    def run_episode(
        self, task: Any, seed: int, randomization_level: str, scenario: Any = None
    ) -> EpisodeResult:
        return EpisodeResult(
            episode_id=f"{task.task_id}-seed{seed:010d}",
            task_id=task.task_id,
            seed=seed,
            success=False,
            collision_count=4,
            max_contact_force_n=500.0,
            min_human_distance_m=0.05,
            failure_mode="collision",
            duration_s=1.0,
            randomization_level=randomization_level,
        )


class _ToggleBackend:
    """Thread-safe on/off switch, so one process can re-run the same job spec.

    The worker derives every seed from ``stable_seed(checkpoint_id, task_id)``, so
    two runs of an *identical* spec are bit-identical by design. The spec alone
    therefore cannot produce two different verdicts; flipping the backend
    between attempts is the only way to exercise an overwrite, and it is also
    what a retried job against a changed artifact actually looks like.
    """

    def __init__(self) -> None:
        self._failing = False
        self._lock = threading.Lock()

    def flip_to_failing(self) -> None:
        """Make every subsequent episode fail."""
        with self._lock:
            self._failing = True

    def run_episode(
        self, task: Any, seed: int, randomization_level: str, scenario: Any = None
    ) -> EpisodeResult:
        with self._lock:
            backend = _AllFailBackend() if self._failing else _AllPassBackend()
        return backend.run_episode(task, seed, randomization_level, scenario)


class _CountingBackend:
    """Records every ``run_episode`` call, so double execution is countable."""

    def __init__(self, inner: Any = None) -> None:
        self._inner: Any = inner if inner is not None else _AllPassBackend()
        self._calls: list[int] = []
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        """Number of episodes executed across all threads."""
        with self._lock:
            return len(self._calls)

    def run_episode(
        self, task: Any, seed: int, randomization_level: str, scenario: Any = None
    ) -> EpisodeResult:
        with self._lock:
            self._calls.append(seed)
        return self._inner.run_episode(task, seed, randomization_level, scenario)


class _FakeRun:
    """Stand-in for :class:`StoredRun` carrying just the id ``run_once`` reads."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id


# ---------------------------------------------------------------------------
# Worker subclasses: pipeline/heartbeat only, never the queue or the fencing
# ---------------------------------------------------------------------------


class _GatedWorker(JobWorker):
    """Worker that announces it started and waits on a release gate.

    Holds a claim open for a controlled window, so the no-reclaim path can be
    driven by events instead of by hoping a sleep was long enough.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def _execute(self, spec: JobSpec) -> Any:
        self.entered.set()
        assert self.gate.wait(timeout=_JOIN_TIMEOUT), "test never released the worker"
        return _FakeRun(spec.run_id)


class _StalledWorker(JobWorker):
    """Worker frozen past its lease: no heartbeat, then a terminal write anyway.

    ``_heartbeat`` is overridden to return immediately, which is what a frozen
    process actually looks like to the queue: a long GC pause, ``SIGSTOP``, a
    lost container or a severed network all suspend the heartbeat thread *along
    with* the work. This is the only faithful way to reach the reclaim path,
    because a worker that keeps heartbeating keeps its lease forever (and so is
    never reclaimed -- which is correct, and tested separately).

    The pipeline is released by an explicit event rather than by sleeping, so a
    test can sequence the reclaim *before* the worker wakes. That ordering is
    what makes the assertion deterministic: a worker that wakes at the same
    instant the reaper runs can legitimately win, and then the test would be
    measuring clock jitter rather than fencing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.wake = threading.Event()

    def _heartbeat(self, job_id: str, epoch: int, stop: threading.Event) -> None:
        return None  # frozen process: no renewal traffic at all

    def _execute(self, spec: JobSpec) -> Any:
        self.entered.set()
        assert self.wake.wait(timeout=_JOIN_TIMEOUT), "test never woke the worker"
        return _FakeRun(spec.run_id)


class _LateHeartbeatWorker(JobWorker):
    """Worker whose heartbeat wakes up *after* the job was reclaimed.

    The renewal it attempts is a real call into :meth:`JobQueue.renew_lease`
    from a real thread carrying the now-stale epoch. Its result is recorded so
    the test asserts on the rejection itself rather than on a downstream
    symptom. The heartbeat and the pipeline have separate gates, so each can be
    released at an independently chosen moment.
    """

    def __init__(self, *args: Any, delay: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._delay = delay
        self.renew_result: Any = "<never ran>"
        self.entered = threading.Event()
        self.execute_gate = threading.Event()
        self.heartbeat_gate = threading.Event()
        self.renewed = threading.Event()

    def _heartbeat(self, job_id: str, epoch: int, stop: threading.Event) -> None:
        assert self.heartbeat_gate.wait(timeout=_JOIN_TIMEOUT), "heartbeat never released"
        time.sleep(self._delay)
        self.renew_result = self._queue.renew_lease(job_id, epoch)
        self.renewed.set()

    def _execute(self, spec: JobSpec) -> Any:
        self.entered.set()
        assert self.execute_gate.wait(timeout=_JOIN_TIMEOUT), "worker never released"
        return _FakeRun(spec.run_id)


def _run_pipeline(store: Any, backend: Any, run_id: str = _JOB_ID) -> Any:
    """Run the shared pipeline once against ``store`` and return its ``StoredRun``."""
    task = TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka_panda"),
        environment=EnvironmentSpec(name="mock-scene"),
        episodes=2,
        adversarial_count=0,
    )
    return run_and_score(
        task,
        "ckpt-1",
        store,
        backend=backend,
        run_id=run_id,
        threshold=85.0,
    )


class _CrashAfterPersistWorker(JobWorker):
    """Worker that persists the run and *then* dies, leaving the job unfinished.

    The realistic crash site: the store write has already committed, but the
    queue record was never advanced to ``done``, so the job still looks like
    outstanding work and is eventually reclaimed and retried under the same
    ``run_id``.
    """

    def _execute(self, spec: JobSpec) -> Any:
        task = TaskConfig(
            task_id=spec.task_id,
            robot=RobotSpec(name=self._robot_name),
            environment=EnvironmentSpec(name=self._environment_name),
            episodes=spec.episodes,
            adversarial_count=spec.adversarial,
        )
        run_and_score(
            task,
            spec.checkpoint_id,
            self._store,
            backend=self._backend,
            run_id=spec.run_id,
            threshold=self._threshold,
        )
        raise RuntimeError("simulated crash after persist")


# ---------------------------------------------------------------------------
# Real Redis / fakeredis backends
# ---------------------------------------------------------------------------


def _has_fakeredis() -> bool:
    """Whether a Lua-executing in-process Redis double is importable."""
    return importlib.util.find_spec("fakeredis") is not None


def _fake_redis() -> tuple[RedisJobQueue, Any]:
    """A ``RedisJobQueue`` over ``fakeredis`` -- real Lua, no external server.

    Each call gets a *fresh* ``FakeServer``, so the returned queue is an
    isolated world. That matters for the multi-instance test, which needs two
    queue objects that still address the same data.
    """
    import fakeredis

    client = fakeredis.FakeStrictRedis(
        server=fakeredis.FakeServer(), decode_responses=True
    )
    return RedisJobQueue(_client_factory=lambda: client, lease_seconds=300), client


#: One shared ``FakeServer``, so several queue objects can address one world.
#: This is the closest in-process stand-in for two processes sharing a server,
#: which is exactly the property the multi-instance claim race needs to test.
_SHARED_FAKE_SERVER: Any = None


def _shared_fake_redis() -> tuple[RedisJobQueue, Any]:
    """A ``RedisJobQueue`` over a process-wide ``fakeredis`` world.

    Every call returns a *new* client and a *new* queue, all pointed at the same
    server, so independent queue objects contend for the same keys.
    """
    global _SHARED_FAKE_SERVER
    import fakeredis

    if _SHARED_FAKE_SERVER is None:
        _SHARED_FAKE_SERVER = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(
        server=_SHARED_FAKE_SERVER, decode_responses=True
    )
    return RedisJobQueue(_client_factory=lambda: client, lease_seconds=300), client


def _live_redis_client() -> Any:
    """A raw redis-py client for a reachable server, or raise."""
    import redis

    url = os.environ.get("VALIDSIM_TEST_REDIS_URL", "redis://127.0.0.1:6379/9")
    client = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=5.0,
    )
    client.ping()
    return client


def _live_redis() -> tuple[RedisJobQueue, Any]:
    """A ``RedisJobQueue`` over a reachable Redis server (server state is shared).

    A real server keeps state between tests, so every enqueued id here is
    generated fresh. The :data:`_REDIS_PREFIX` cleanup in
    ``_redis_case`` keeps one test's keys out of the next one's way.
    """
    client = _live_redis_client()
    return RedisJobQueue(_client_factory=lambda: client, lease_seconds=300), client


def _live_redis_probe() -> bool:
    """Whether a Redis server is reachable here (an environment fact, not a defect)."""
    try:
        _live_redis_client()
    except Exception:  # noqa: BLE001 - no server in this environment
        return False
    return True


def _redis_backends() -> list[tuple[str, Callable[[], tuple[RedisJobQueue, Any]]]]:
    """Redis queue factories that genuinely execute the Lua scripts.

    A recording stub that never interprets a script would prove nothing about a
    race, so only backends whose claim/renew/finish scripts actually run are
    listed. The module-level value is what the tests parametrise over.
    """
    backends: list[tuple[str, Callable[[], tuple[RedisJobQueue, Any]]]] = []
    if _has_fakeredis():
        backends.append(("fakeredis", _fake_redis))
    if _live_redis_probe():
        backends.append(("live-redis", _live_redis))
    return backends


_REDIS_BACKENDS = _redis_backends()
_REDIS_IDS = [name for name, _ in _REDIS_BACKENDS]

#: Key prefix the queue writes under, used to clear live-Redis state between tests.
_REDIS_PREFIX = "validsim:jobs"


# ---------------------------------------------------------------------------
# Thread helpers
# ---------------------------------------------------------------------------


def _run_in_threads(target: Callable[[int], Any], n: int) -> list[Any]:
    """Run ``target(i)`` in ``n`` real threads released simultaneously.

    A barrier puts every thread at the same point before the operation under
    test, and results come back in thread-index order. Exceptions are returned
    rather than raised so an assertion can name the failure instead of the suite
    surfacing a bare traceback from a dead thread.
    """
    barrier = threading.Barrier(n, timeout=_JOIN_TIMEOUT)

    def wrapped(index: int) -> Any:
        barrier.wait()
        try:
            return target(index)
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
            return exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(wrapped, i) for i in range(n)]
        return [f.result(timeout=_JOIN_TIMEOUT) for f in futures]


def _assert_no_thread_errors(results: list[Any], label: str) -> None:
    """Fail with a readable message if any thread raised."""
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == [], f"{label}: worker threads raised {errors}"


@pytest.fixture(autouse=True)
def _isolated_lease_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the queue's tuning env vars so local values cannot skew a test.

    Covers the lease TTL plus the retry/reclaim budgets, all of which the queue
    reads from the environment when the argument is omitted.
    """
    for var in (
        queue_mod._LEASE_ENV,
        queue_mod._MAX_ATTEMPTS_ENV,
        queue_mod._RETRY_BACKOFF_ENV,
        queue_mod._MAX_RECLAIMS_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _redis_case() -> Any:
    """Delete the queue's Redis keys after each test.

    A real server is shared state that outlives the process, so without this a
    previous run's keys would make ``enqueue`` raise ``ValueError`` for a
    colliding id. Skipped when no server is reachable.
    """
    yield
    try:
        client = _live_redis_client()
    except Exception:  # noqa: BLE001 - no server in this environment
        return
    keys = list(client.scan_iter(match=f"{_REDIS_PREFIX}*"))
    if keys:
        client.delete(*keys)


# ===========================================================================
# 1. Two workers must never both execute one job
# ===========================================================================


class TestNoDoubleExecution:
    def test_eight_threads_never_both_execute_one_queued_job(self) -> None:
        """N workers racing one queued job: exactly one of them executes it."""
        queue = JobQueue(lease_seconds=300)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id
        backend = _CountingBackend()
        workers = [JobWorker(queue, store, backend=backend) for _ in range(_RACE_THREADS)]

        results = _run_in_threads(lambda i: workers[i].run_once(), _RACE_THREADS)

        _assert_no_thread_errors(results, "memory queue")
        finished = [r for r in results if isinstance(r, JobRecord)]
        idle = [r for r in results if r is None]

        # The core invariant: 2 episodes (the spec size) x 1 execution, one
        # completion, and every other worker seeing an empty queue.
        assert backend.count == 2, (
            f"job executed {backend.count} times; 2 episodes x 1 execution expected"
        )
        assert len(finished) == 1, f"{len(finished)} workers reported completion"
        assert len(idle) == _RACE_THREADS - 1

        final = queue.get(job_id)
        assert final is not None and final.status is JobStatus.DONE
        assert final.result == job_id

    def test_fifty_rounds_of_threaded_claims_never_double_claim(self) -> None:
        """50 rounds x 8 threads on a real clock: exactly one winner, every round.

        One round can pass by luck of the scheduler. Repeating it turns "no race
        observed" into a claim with a sample size attached (400 claim attempts).
        """
        for round_index in range(_RACE_ROUNDS):
            queue = JobQueue(lease_seconds=300)
            queue.enqueue(_spec())

            claims = _run_in_threads(lambda i: queue.claim_next(), _RACE_THREADS)

            _assert_no_thread_errors(claims, f"round {round_index}")
            winners = [c for c in claims if isinstance(c, JobRecord)]
            assert len(winners) == 1, f"round {round_index}: {len(winners)} winners"
            assert winners[0].lease_epoch == 1

    @pytest.mark.parametrize("backend_name", _REDIS_IDS)
    def test_redis_workers_never_both_execute_one_job(self, backend_name: str) -> None:
        """The same race, but through the real Redis claim script."""
        factory = dict(_REDIS_BACKENDS)[backend_name]
        queue, _client = factory()
        store = ValidationStore()
        job_id = queue.enqueue(_spec(_unique_run_id())).job_id
        backend = _CountingBackend()
        workers = [JobWorker(queue, store, backend=backend) for _ in range(_RACE_THREADS)]

        results = _run_in_threads(lambda i: workers[i].run_once(), _RACE_THREADS)

        _assert_no_thread_errors(results, backend_name)
        finished = [r for r in results if isinstance(r, JobRecord)]
        assert backend.count == 2, f"{backend_name}: executed {backend.count} times"
        assert len(finished) == 1, f"{backend_name}: {len(finished)} workers completed"
        final = queue.get(job_id)
        assert final is not None and final.status is JobStatus.DONE
        assert final.result == job_id

    def test_separate_queue_objects_racing_one_redis_job(self) -> None:
        """Distinct queue instances stand in for distinct worker processes.

        Each thread builds its own ``RedisJobQueue`` against one shared server,
        so no process-local lock is shared and the only thing serialising the
        claim is the Lua script. If that script were not atomic, this is where it
        would show -- and unlike the in-memory queue, a fix there cannot rely on
        the Python mutex.
        """
        enqueuer, _client = _shared_fake_redis()
        job_id = enqueuer.enqueue(_spec(_unique_run_id())).job_id
        store = ValidationStore()
        backend = _CountingBackend()

        def claim_and_run(index: int) -> Any:
            return JobWorker(_shared_fake_redis()[0], store, backend=backend).run_once()

        results = _run_in_threads(claim_and_run, _RACE_THREADS)

        _assert_no_thread_errors(results, "shared-fakeredis")
        finished = [r for r in results if isinstance(r, JobRecord)]
        assert backend.count == 2, f"executed {backend.count} times"
        assert len(finished) == 1, f"{len(finished)} workers completed"
        final = enqueuer.get(job_id)
        assert final is not None and final.status is JobStatus.DONE
        assert final.result == job_id

    @pytest.mark.parametrize("backend_name", _REDIS_IDS)
    def test_redis_epoch_is_per_job_not_a_shared_counter(self, backend_name: str) -> None:
        """Racing over a backlog must not let two jobs share a fencing epoch.

        ``lease_epoch`` is incremented per record by the Lua claim script. If it
        were ever derived from a shared counter, two workers would hold matching
        tokens for different jobs and the fencing check would silently pass for
        the wrong record.
        """
        factory = dict(_REDIS_BACKENDS)[backend_name]
        queue, _client = factory()
        job_ids = [
            queue.enqueue(_spec(_unique_run_id())).job_id for _ in range(_RACE_THREADS)
        ]

        claims = _run_in_threads(lambda i: queue.claim_next(), _RACE_THREADS)

        _assert_no_thread_errors(claims, backend_name)
        winners = [c for c in claims if isinstance(c, JobRecord)]
        assert len(winners) == _RACE_THREADS
        assert sorted(c.job_id for c in winners) == sorted(job_ids)
        assert all(c.lease_epoch == 1 for c in winners), (
            f"{backend_name}: epochs {[c.lease_epoch for c in winners]}"
        )


# ===========================================================================
# 2 & 3. A reclaimed worker must not be able to finish or renew
# ===========================================================================


class TestStalledWorkerIsFenced:
    def test_stalled_worker_cannot_complete_a_reclaimed_job(self) -> None:
        """Stall past the lease, get reclaimed, then finish: the write is refused.

        The expiry is the real clock and the reclaim is the public
        :meth:`JobQueue.reap_expired`. Only the worker's private
        ``_execute``/``_heartbeat`` are replaced, so the claim, fencing and
        terminal-write code exercised is the production code.

        The reclaim is deliberately sequenced *before* the worker wakes. A worker
        that completes in the same instant the reaper scans has not lost its
        lease yet, so letting the two race would make the test measure scheduler
        jitter instead of the fencing check.
        """
        queue = JobQueue(lease_seconds=_FENCING_LEASE)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id

        stalled = _StalledWorker(queue, store, backend=_AllPassBackend())
        thread = threading.Thread(target=stalled.run_once, daemon=True)
        thread.start()
        assert stalled.entered.wait(timeout=10.0), "stalled worker never started"

        # Let the real lease lapse, then reclaim through the public reaper.
        time.sleep(_FENCING_LEASE + _WAIT_PAST_LEASE)
        assert queue.reap_expired() == [job_id], "the job was not reclaimed"
        new_owner = queue.claim_next()
        assert new_owner is not None
        assert new_owner.lease_epoch > 1, "reclaim did not advance the fencing epoch"

        # Only now does the frozen worker wake and try to record its result.
        stalled.wake.set()
        thread.join(timeout=_JOIN_TIMEOUT)
        assert not thread.is_alive(), "stalled worker never woke up"

        current = queue.get(job_id)
        assert current is not None
        assert current.lease_epoch == new_owner.lease_epoch, (
            "the stale terminal write still mutated the record"
        )
        assert current.status is JobStatus.RUNNING, (
            f"a worker that no longer owned the job completed it: {current.status}"
        )
        assert current.result is None, "the stale worker's result was recorded"
        assert current.finished_at is None

    def test_stalled_worker_cannot_renew_a_reclaimed_job(self) -> None:
        """The same stall, but the late heartbeat tries to *renew* instead.

        ``_heartbeat`` breaking on its first failed renewal is what actually
        protects a reclaimed job in production, so the test drives the real
        :meth:`JobQueue.renew_lease` from a real thread with the stale epoch and
        asserts on the rejection itself, not on a downstream symptom.
        """
        queue = JobQueue(lease_seconds=_FENCING_LEASE)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id

        stalled = _LateHeartbeatWorker(
            queue, store, delay=_WAIT_PAST_LEASE, backend=_AllPassBackend()
        )
        thread = threading.Thread(target=stalled.run_once, daemon=True)
        thread.start()
        assert stalled.entered.wait(timeout=10.0)

        # Let the real lease lapse, then reclaim through the public reaper.
        time.sleep(_FENCING_LEASE + _WAIT_PAST_LEASE)
        assert queue.reap_expired() == [job_id], "the job was not reclaimed"
        new_owner = queue.claim_next()
        assert new_owner is not None
        new_epoch = new_owner.lease_epoch
        new_deadline = new_owner.lease_expires_at

        # Now the frozen heartbeat wakes and attempts its renewal. The test
        # waits for the attempt to land rather than sleeping past it, so the
        # assertion is on the rejection itself and not on timing.
        stalled.heartbeat_gate.set()
        assert stalled.renewed.wait(timeout=10.0), "the stale renewal never happened"
        stalled.execute_gate.set()
        thread.join(timeout=_JOIN_TIMEOUT)
        assert not thread.is_alive()

        assert stalled.renew_result is None, (
            f"a stale heartbeat renewed a reclaimed lease: {stalled.renew_result!r}"
        )
        current = queue.get(job_id)
        assert current is not None
        assert current.lease_epoch == new_epoch
        assert current.lease_expires_at == new_deadline, (
            "the new owner's lease deadline was moved by the stale heartbeat"
        )
        assert current.status is JobStatus.RUNNING, "the stale worker also finished"

    def test_reclaimed_job_completes_normally_under_the_new_owner(self) -> None:
        """Fencing blocks the zombie, not the legitimate owner."""
        queue = JobQueue(lease_seconds=_FENCING_LEASE)
        job_id = queue.enqueue(_spec()).job_id

        stale = queue.claim_next()
        assert stale is not None
        time.sleep(_FENCING_LEASE + _WAIT_PAST_LEASE)
        assert queue.reap_expired() == [job_id]
        fresh = queue.claim_next()
        assert fresh is not None

        done = queue.update_status(
            job_id, JobStatus.DONE, result=job_id, lease_epoch=fresh.lease_epoch
        )

        assert done is not None, "the legitimate owner was wrongly fenced out"
        assert done.status is JobStatus.DONE
        assert done.result == job_id

    @pytest.mark.parametrize("backend_name", _REDIS_IDS)
    def test_redis_stale_epoch_cannot_complete_or_renew(self, backend_name: str) -> None:
        """The fencing property across the real Lua finish/renew scripts."""
        factory = dict(_REDIS_BACKENDS)[backend_name]
        queue, client = factory()
        job_id = queue.enqueue(_spec(_unique_run_id())).job_id

        first = queue.claim_next()
        assert first is not None

        # Rewind the persisted deadline so the real clock sees the claim as
        # expired. Everything after this is the production code path.
        record = JobRecord.from_dict(json.loads(client.get(queue._job_key(job_id))))
        client.set(
            queue._job_key(job_id),
            json.dumps(
                JobRecord.from_dict(
                    {**record.to_dict(), "lease_expires_at": _utc_stamp(-5.0)}
                ).to_dict()
            ),
        )

        assert queue.reap_expired() == [job_id], f"{backend_name}: not reclaimed"
        second = queue.claim_next()
        assert second is not None, f"{backend_name}: reclaimed job not re-claimable"
        assert second.lease_epoch > first.lease_epoch, f"{backend_name}: epoch stuck"

        # The stalled worker's terminal write, fenced on the stale epoch.
        stale_done = queue.update_status(
            job_id, JobStatus.DONE, result="stale", lease_epoch=first.lease_epoch
        )
        assert stale_done is None, f"{backend_name}: stale completion was accepted"

        # ...and its renewal, likewise.
        stale_renew = queue.renew_lease(job_id, first.lease_epoch)
        assert stale_renew is None, f"{backend_name}: stale renewal was accepted"

        current = queue.get(job_id)
        assert current is not None
        assert current.status is JobStatus.RUNNING, f"{backend_name}: {current.status}"
        assert current.result is None, f"{backend_name}: stale result was written"
        assert current.lease_epoch == second.lease_epoch

    def test_repeat_reclaims_of_a_dead_worker_are_bounded(self) -> None:
        """A worker that dies every time is eventually dead-lettered, not retried forever.

        A worker that dies mid-job never reports anything, so it never increments
        ``attempt`` -- only the reaper can bound that path. This walks the whole
        reclaim budget with real reaper passes and asserts the job stops being
        claimable, which is what stops a poison job from looping a worker pool
        until someone restarts the process.
        """
        queue = JobQueue(lease_seconds=1, max_reclaims=2)
        job_id = queue.enqueue(_spec()).job_id

        reclaims = 0
        for _ in range(queue.max_reclaims + 2):
            claimed = queue.claim_next()
            if claimed is None:
                break
            # Rewind the lease so the next reap pass sees this claim as abandoned,
            # which is what a worker dying mid-job looks like to the reaper.
            queue._jobs[job_id] = dataclasses.replace(
                claimed, lease_expires_at=_utc_stamp(-5.0)
            )
            if queue.reap_expired():
                reclaims += 1

        assert reclaims > 0, "the reaper never reclaimed anything"
        assert reclaims <= queue.max_reclaims, (
            f"reclaimed {reclaims} times, past the budget of {queue.max_reclaims}"
        )
        final = queue.get(job_id)
        assert final is not None
        # The budget is spent, so the job is no longer handed to a worker.
        assert queue.claim_next() is None, "a dead-lettered job was still claimable"


# ===========================================================================
# 4. Crash-and-retry against the store's overwrite semantics
# ===========================================================================


class TestCrashRetryVerdictOverwrite:
    """Crash-and-retry against the store's write semantics.

    History: this store used to be ``INSERT OR REPLACE``, so a retried job
    reusing ``spec.run_id`` silently rewrote the verdict a previous attempt had
    already recorded. It is now append-only (``ON CONFLICT (run_id) DO
    NOTHING``), so the first verdict survives. The tests below pin that fix and
    then probe what is left: a *rejected* write is invisible to the caller,
    because ``save`` returns the ``run`` argument either way.
    """

    def test_two_attempts_of_an_unchanged_spec_are_identical(self, tmp_path: Any) -> None:
        """Baseline: with the artifact unchanged, a retry is a true no-op.

        Seeds derive from ``stable_seed(checkpoint_id, task_id)``, so re-running
        an identical spec is deterministic. Retry-on-collision is therefore
        harmless while the spec and the artifact are unchanged.
        """
        store = SqliteValidationStore(tmp_path / "unchanged.db")
        backend = _AllPassBackend()
        job_id = _JOB_ID

        first_queue = JobQueue()
        first_queue.enqueue(_spec())
        JobWorker(first_queue, store, backend=backend).run_once()
        first = store.get(job_id)
        assert first is not None

        second_queue = JobQueue()
        second_queue.enqueue(_spec())
        JobWorker(second_queue, store, backend=backend).run_once()
        second = store.get(job_id)

        assert second is not None
        assert second.scorecard.composite_score == first.scorecard.composite_score
        assert second.scorecard.deploy_decision == first.scorecard.deploy_decision
        assert second.scorecard.block_reasons == first.scorecard.block_reasons
        store.close()

    def test_retry_after_a_changed_artifact_preserves_the_first_verdict(
        self, tmp_path: Any
    ) -> None:
        """A retried job must not rewrite the verdict attempt 1 already recorded.

        Sequence: attempt 1 persists an ``APPROVE`` and then dies before the
        queue record reaches a terminal state; a second worker claims the same
        ``run_id`` and computes a different verdict. The recorded verdict must be
        the one that is actually stored -- an append-only store makes the first
        attempt's outcome the surviving record.

        ``max_attempts=1`` is the point of the ``jobs-queue`` retry policy under
        test: it lets a failed attempt be *recorded* (``failed``) rather than
        silently requeued with a backoff, so the assertion does not depend on the
        worker choosing to retry internally.
        """
        store = SqliteValidationStore(tmp_path / "retry.db")
        backend = _ToggleBackend()

        first_queue = JobQueue(max_attempts=1)
        first_queue.enqueue(_spec())
        record1 = _CrashAfterPersistWorker(first_queue, store, backend=backend).run_once()
        assert record1 is not None
        assert record1.status is JobStatus.FAILED, "setup: attempt 1 must fail"
        assert "simulated crash" in (record1.error or "")

        original = store.get(_JOB_ID)
        assert original is not None, "setup: attempt 1 must have persisted its run"
        assert original.scorecard.deploy_decision == "APPROVE"
        original_score = original.scorecard.composite_score

        # -- attempt 2: same run_id, artifact now regresses -------------------
        backend.flip_to_failing()
        second_queue = JobQueue()
        second_queue.enqueue(_spec())
        record2 = JobWorker(second_queue, store, backend=backend).run_once()
        assert record2 is not None and record2.status is JobStatus.DONE

        final = store.get(_JOB_ID)
        assert final is not None
        assert final.scorecard.deploy_decision == "APPROVE", (
            f"a retry rewrote the recorded verdict to {final.scorecard.deploy_decision}"
        )
        assert final.scorecard.composite_score == original_score
        # One row, one verdict: there is no second attempt anywhere to audit.
        assert len(store) == 1
        assert len(store.history()) == 1
        store.close()

    def test_retry_reports_the_verdict_that_is_actually_stored(self) -> None:
        """A rejected retry must hand back the record on disk, not its own.

        ``run_and_score`` calls ``save_exists`` and, on a rejection, re-reads the
        stored row. That is what makes a retried job's reported verdict agree
        with the store: the worker cannot end up claiming it computed a
        ``BLOCK`` that was silently dropped while the record still says
        ``APPROVE``.
        """
        store = ValidationStore()
        backend = _ToggleBackend()

        first_queue = JobQueue(max_attempts=1)
        first_queue.enqueue(_spec())
        crashed = _CrashAfterPersistWorker(first_queue, store, backend=backend).run_once()
        assert crashed is not None and crashed.status is JobStatus.FAILED
        approved = store.get(_JOB_ID)
        assert approved is not None
        assert approved.scorecard.deploy_decision == "APPROVE"

        # Attempt 2 computes a BLOCK verdict that the store will refuse.
        backend.flip_to_failing()
        second_queue = JobQueue()
        second_queue.enqueue(_spec())
        done = JobWorker(second_queue, store, backend=backend).run_once()

        assert done is not None and done.status is JobStatus.DONE
        stored = store.get(_JOB_ID)
        assert stored is not None
        # The job's result pointer resolves to the verdict on record, so a
        # consumer following it reads the same thing the store holds.
        assert done.result == _JOB_ID
        assert store.get(done.result).scorecard.deploy_decision == "APPROVE"  # type: ignore[union-attr]
        assert len(store) == 1

    def test_pipeline_returns_the_stored_run_after_a_rejected_write(self) -> None:
        """The pipeline's return value equals the record, not the fresh candidate.

        This is the invariant that makes the previous test meaningful: when the
        store refuses a write, ``run_and_score`` must return the row that
        actually persisted. Without the re-read, the caller would receive a
        scorecard the record never accepted.
        """
        store = ValidationStore()
        approved = _run_pipeline(store, _AllPassBackend())
        assert approved.scorecard.deploy_decision == "APPROVE"

        returned = _run_pipeline(store, _AllFailBackend())
        stored = store.get(_JOB_ID)
        assert stored is not None

        assert returned.scorecard.deploy_decision == stored.scorecard.deploy_decision
        assert returned.scorecard.composite_score == stored.scorecard.composite_score
        # The BLOCK verdict attempt 2 computed is genuinely gone from the store.
        assert stored.scorecard.deploy_decision == "APPROVE"
        assert returned.scorecard.block_reasons == stored.scorecard.block_reasons

    def test_concurrent_persists_of_one_run_id_leave_a_complete_row(self) -> None:
        """Two threads writing the same ``run_id`` must not produce a torn row.

        ``INSERT OR REPLACE`` is one statement, so the row cannot be half
        written. This pins that guarantee down rather than assuming it: whatever
        survives is internally consistent, and is either one attempt's row or
        the other's -- never a blend of the two.
        """
        store = SqliteValidationStore(":memory:")
        job_id = _JOB_ID
        backends = [_AllPassBackend(), _AllFailBackend()]

        def attempt(index: int) -> Any:
            queue = JobQueue(lease_seconds=300)
            queue.enqueue(_spec())
            return JobWorker(queue, store, backend=backends[index % 2]).run_once()

        results = _run_in_threads(attempt, 4)
        _assert_no_thread_errors(results, "concurrent persists")

        final = store.get(job_id)
        assert final is not None
        card = final.scorecard
        if card.deploy_decision == "APPROVE":
            assert card.composite_score >= card.threshold
            assert card.block_reasons == ()
        else:
            assert card.composite_score < card.threshold or bool(card.block_reasons)
        assert card.episode_count == 2
        assert len(store) == 1
        store.close()


# ===========================================================================
# 5. Lease renewal under sustained work
# ===========================================================================


class TestLeaseRenewalUnderSustainedWork:
    def test_long_healthy_job_is_never_stolen(self) -> None:
        """A heartbeating job survives a reaper running continuously beside it.

        The job is held for three full lease periods by a gated worker while
        another thread reaps in a tight loop. Every pass must find nothing to
        reclaim: that is the property that stops a slow-but-alive job from being
        executed twice.
        """
        queue = JobQueue(lease_seconds=_SAFE_LEASE)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id

        worker = _GatedWorker(queue, store)
        run_thread = threading.Thread(target=worker.run_once, daemon=True)
        run_thread.start()
        assert worker.entered.wait(timeout=10.0), "worker never started executing"

        stop_reaper = threading.Event()
        reaped: list[str] = []

        def reap_loop() -> None:
            while not stop_reaper.is_set():
                reaped.extend(queue.reap_expired())
                time.sleep(0.02)

        reaper_thread = threading.Thread(target=reap_loop, daemon=True)
        reaper_thread.start()
        try:
            time.sleep(_SAFE_LEASE * 3)
            mid = queue.get(job_id)
            assert mid is not None
            assert mid.status is JobStatus.RUNNING, (
                f"a healthy job was stolen mid-run (status={mid.status})"
            )
            assert mid.lease_expires_at is not None, (
                "a heartbeating job should still carry a live lease"
            )
        finally:
            worker.gate.set()
            run_thread.join(timeout=_JOIN_TIMEOUT)
            stop_reaper.set()
            reaper_thread.join(timeout=10.0)

        assert not run_thread.is_alive()
        assert reaped == [], f"a live job was reclaimed by the reaper: {reaped}"
        final = queue.get(job_id)
        assert final is not None and final.status is JobStatus.DONE
        assert final.result == job_id
        assert final.lease_expires_at is None, "the terminal write must release the lease"

    def test_heartbeat_extends_the_deadline_repeatedly(self) -> None:
        """The lease really moves forward -- it is renewed, not merely re-read.

        Uses the real wall clock and the real :class:`JobQueue`; a frozen clock
        could not distinguish an extension from a re-stamp of the same instant.
        """
        queue = JobQueue(lease_seconds=_SAFE_LEASE)
        job_id = queue.enqueue(_spec()).job_id

        claimed = queue.claim_next()
        assert claimed is not None
        first_deadline = claimed.lease_expires_at
        assert first_deadline is not None

        deadlines = [first_deadline]
        for _ in range(3):
            time.sleep(_SAFE_LEASE / 3.0 + 0.15)
            renewed = queue.renew_lease(job_id, claimed.lease_epoch)
            assert renewed is not None, "a live claim stopped being renewable"
            deadlines.append(renewed.lease_expires_at or "")

        assert deadlines[-1] > deadlines[0], "the lease deadline never advanced"
        assert len(set(deadlines)) == len(deadlines), (
            f"renewal re-stamped the same deadline: {deadlines}"
        )
        assert queue.reap_expired() == []
        current = queue.get(job_id)
        assert current is not None and current.status is JobStatus.RUNNING

    def test_worker_heartbeat_outlives_the_original_deadline(self) -> None:
        """End-to-end: a gated worker outlives its first lease and still finishes.

        Complements ``test_long_healthy_job_is_never_stolen`` by also asserting
        the terminal write lands, i.e. the job completes exactly once rather than
        being left ``running`` forever.
        """
        queue = JobQueue(lease_seconds=_SAFE_LEASE)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id

        worker = _GatedWorker(queue, store)
        run_thread = threading.Thread(target=worker.run_once, daemon=True)
        run_thread.start()
        assert worker.entered.wait(timeout=10.0)
        time.sleep(_SAFE_LEASE * 2.5)
        worker.gate.set()
        run_thread.join(timeout=_JOIN_TIMEOUT)

        assert not run_thread.is_alive()
        final = queue.get(job_id)
        assert final is not None and final.status is JobStatus.DONE
        assert final.result == job_id
        assert len(queue) == 1


# ===========================================================================
# Sub-second lease precision (secondary finding)
# ===========================================================================


class TestSubSecondLeasePrecision:
    def test_configured_lease_is_shorter_than_it_claims(self) -> None:
        """CHARACTERISATION: the stamped lease is up to 1s shorter than configured.

        ``_utc_now`` formats with ``timespec="seconds"``, so the stamp is floored
        to the second and ``_lease_deadline`` then adds the lease to that floored
        base. A configured ``lease_seconds=N`` is therefore worth between ``N-1``
        and ``N`` real seconds -- and the value the queue reports as the deadline
        overstates the time the worker actually has.
        """
        queue = JobQueue(lease_seconds=_FENCING_LEASE)
        queue.enqueue(_spec())

        claimed = queue.claim_next()
        assert claimed is not None
        remaining = _real_lease_left(claimed.lease_expires_at)

        assert remaining <= _FENCING_LEASE, "expected truncation to shorten the lease"
        assert remaining >= _FENCING_LEASE - 1.0

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN LIMITATION, not a production risk: the lease is floored to "
            "whole seconds by validsim.jobs.queue._utc_now "
            "(isoformat(timespec='seconds')), so a lease_seconds=N claim is "
            "really worth N-1..N seconds. The default is 3600s, where the worst "
            "case is one second in 3600 (0.03%), so this only bites for "
            "deliberately tiny leases (<=2s) -- there the worker heartbeat, "
            "which fires every lease_seconds/3, can be longer than the real "
            "lease, and a live, heartbeating job is reclaimed and run twice. "
            "Raising the stored precision to fix it would change the ISO-8601 "
            "format of persisted job records, which is a deliberate trade for "
            "0.03%, so it is left pinned rather than silently changed. "
            "Reproduce: pytest tests/test_jobs_concurrency_agent.py -k live_job "
            "--runxfail"
        ),
    )
    def test_live_heartbeating_job_must_not_be_reclaimed(self) -> None:
        """A worker that is alive and heartbeating must never lose its job.

        Held for well over two full lease periods with the production heartbeat
        running, while a reaper loops. ``lease_seconds=1`` gives a real lease of
        roughly 0.7s against a 0.33s heartbeat, which is the failing margin.
        """
        queue = JobQueue(lease_seconds=1)
        store = ValidationStore()
        job_id = queue.enqueue(_spec()).job_id

        worker = _GatedWorker(queue, store)
        run_thread = threading.Thread(target=worker.run_once, daemon=True)
        run_thread.start()
        assert worker.entered.wait(timeout=10.0)

        stop_reaper = threading.Event()
        reaped: list[str] = []

        def reap_loop() -> None:
            while not stop_reaper.is_set():
                reaped.extend(queue.reap_expired())
                time.sleep(0.01)

        reaper_thread = threading.Thread(target=reap_loop, daemon=True)
        reaper_thread.start()
        try:
            time.sleep(1.5)
            mid = queue.get(job_id)
            assert mid is not None
            assert mid.status is JobStatus.RUNNING, (
                f"a live, heartbeating job was reclaimed (status={mid.status})"
            )
        finally:
            worker.gate.set()
            run_thread.join(timeout=_JOIN_TIMEOUT)
            stop_reaper.set()
            reaper_thread.join(timeout=10.0)

        assert reaped == [], f"a live job was reclaimed: {reaped}"
