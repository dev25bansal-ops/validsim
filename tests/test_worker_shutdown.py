"""Graceful-shutdown tests for :class:`validsim.jobs.worker.JobWorker`.

``run_forever`` now installs ``SIGTERM``/``SIGINT`` handlers by default so a
container stop drains the in-flight job and exits cleanly. These tests cover:
the installed handler triggers ``stop`` and the loop returns promptly once the
running job finishes; previous handlers are restored on exit; registration is
skipped silently when the loop runs off the main thread; opting out with
``install_signal_handlers=False`` registers nothing; and a direct ``stop()``
(or ``stop()`` while idle) still drains/exits quickly. ``run_once`` behaviour
is pinned by ``tests/test_jobs_worker.py`` and is untouched here.
"""

from __future__ import annotations

import inspect
import signal
import threading
import time
from typing import Any

import validsim.jobs.worker as worker_module
from validsim.config import TaskConfig
from validsim.jobs import JobQueue, JobSpec, JobStatus, JobWorker
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import EpisodeResult, MockIsaacBackend
from validsim.store.memory import ValidationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(run_id: str = "vrun-stop0001", **overrides: Any) -> JobSpec:
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


class _SlowBackend:
    """Mock backend that pauses inside the first episode.

    The gate lets a test observe the worker *mid-job*: ``started`` flips when
    the first episode begins, while the episode itself keeps sleeping, so the
    caller can trigger shutdown and assert the in-flight job still drains to
    its terminal state before the loop exits.
    """

    def __init__(self, delay: float = 0.3) -> None:
        self._inner = MockIsaacBackend()
        self._delay = delay
        self.started = threading.Event()

    def run_episode(
        self,
        task: TaskConfig,
        seed: int,
        randomization_level: str,
        scenario: AdversarialScenario | None = None,
    ) -> EpisodeResult:
        if not self.started.is_set():
            self.started.set()
            time.sleep(self._delay)
        return self._inner.run_episode(task, seed, randomization_level, scenario)


class _FakeSignalRegistry:
    """Stand-in for ``signal.signal`` that records installs and restores.

    ``signal.signal`` refuses to run outside the main thread, so tests that
    want to exercise the handler *wiring* patch this in: it captures the
    handlers the worker installs (the test then calls them directly,
    simulating signal delivery) and the restore calls made on loop exit.
    """

    def __init__(self) -> None:
        self.installed: dict[Any, Any] = {}
        self.restored: dict[Any, Any] = {}

    def __call__(self, sig: Any, handler: Any) -> Any:
        if sig in self.installed:
            # Second touch of the same signal is the restore-on-exit path.
            self.restored[sig] = handler
            return self.installed[sig]
        self.installed[sig] = handler
        return signal.getsignal(sig)


def _start_worker_thread(worker: JobWorker, **kwargs: Any) -> threading.Thread:
    """Run ``worker.run_forever(**kwargs)`` on a daemon thread."""
    thread = threading.Thread(target=worker.run_forever, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Signal-handler path: install -> deliver -> drain -> exit -> restore
# ---------------------------------------------------------------------------


class TestSignalHandlerPath:
    def test_handler_stops_loop_after_draining_in_flight_job(
        self, monkeypatch: Any
    ) -> None:
        queue = JobQueue()
        store = ValidationStore()
        backend = _SlowBackend(delay=0.3)
        worker = JobWorker(queue, store, backend=backend)
        job_id = queue.enqueue(_spec()).job_id

        registry = _FakeSignalRegistry()
        monkeypatch.setattr(worker_module.signal, "signal", registry)

        # install_signal_handlers defaults to True — container-style startup.
        thread = _start_worker_thread(worker, poll_seconds=0.01)
        try:
            assert backend.started.wait(2.0), "worker never entered the job"
            # Handlers are installed before the loop claims anything.
            assert set(registry.installed) == {signal.SIGTERM, signal.SIGINT}

            # Simulate a container stop: the OS invokes the SIGTERM handler.
            registry.installed[signal.SIGTERM](signal.SIGTERM, None)

            thread.join(timeout=3.0)
            assert not thread.is_alive(), "run_forever did not return promptly"
        finally:
            worker.stop()
            thread.join(timeout=2.0)

        # The in-flight job drained to done and was persisted, not truncated.
        record = queue.get(job_id)
        assert record is not None
        assert record.status is JobStatus.DONE
        assert store.get(job_id) is not None
        # Previous handlers were restored on exit (not left dangling).
        assert set(registry.restored) == {signal.SIGTERM, signal.SIGINT}
        for sig, handler in registry.restored.items():
            assert handler is not registry.installed[sig]

    def test_sigint_handler_is_wired_to_stop_too(self, monkeypatch: Any) -> None:
        queue = JobQueue()
        worker = JobWorker(queue, ValidationStore())
        registry = _FakeSignalRegistry()
        monkeypatch.setattr(worker_module.signal, "signal", registry)

        thread = _start_worker_thread(worker, poll_seconds=0.01)
        deadline = time.time() + 2.0
        while len(registry.installed) < 2 and time.time() < deadline:
            time.sleep(0.005)
        assert signal.SIGINT in registry.installed

        registry.installed[signal.SIGINT](signal.SIGINT, None)
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert worker._stop.is_set()  # the handler must flip the stop event


# ---------------------------------------------------------------------------
# Off-main-thread fallback: registration is skipped silently
# ---------------------------------------------------------------------------


class TestOffMainThreadFallback:
    def test_registration_skipped_silently_in_worker_thread(self) -> None:
        # Real signal.signal: raises ValueError off the main thread. The
        # worker must swallow it, keep looping, and still honour stop().
        queue = JobQueue()
        store = ValidationStore()
        worker = JobWorker(queue, store)
        job_id = queue.enqueue(_spec("vrun-thread01")).job_id

        errors: list[BaseException] = []

        def _run() -> None:
            try:
                worker.run_forever(poll_seconds=0.01)  # install defaults to True
            except BaseException as exc:  # noqa: BLE001 - surfaced in assertion
                errors.append(exc)

        thread = threading.Thread(target=_run, daemon=True)
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
        finally:
            worker.stop()
            thread.join(timeout=2.0)

        assert not thread.is_alive()
        assert errors == [], f"run_forever raised on a worker thread: {errors}"

    def test_main_thread_installs_and_restores_real_handlers(self) -> None:
        # run_forever on the *main* thread: registration really happens and
        # the originals come back after the loop exits.
        worker = JobWorker(JobQueue(), ValidationStore())
        before_int = signal.getsignal(signal.SIGINT)
        installed_seen: list[Any] = []

        def _stopper() -> None:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                handler = signal.getsignal(signal.SIGTERM)
                if callable(handler):
                    installed_seen.append(handler)
                    break
                time.sleep(0.005)
            worker.stop()

        thread = threading.Thread(target=_stopper, daemon=True)
        thread.start()
        worker.run_forever(poll_seconds=0.01)  # main thread, default install
        thread.join(timeout=2.0)

        assert installed_seen, "SIGTERM handler was never installed"
        assert signal.getsignal(signal.SIGINT) is before_int
        assert signal.getsignal(signal.SIGTERM) is not installed_seen[0]


# ---------------------------------------------------------------------------
# Opt-out flag and drain/promptness guarantees
# ---------------------------------------------------------------------------


class TestInstallOptOut:
    def test_flag_defaults_to_true(self) -> None:
        params = inspect.signature(JobWorker.run_forever).parameters
        assert params["install_signal_handlers"].default is True

    def test_disabled_installation_touches_signal_module_not_at_all(
        self, monkeypatch: Any
    ) -> None:
        calls: list[tuple[Any, Any]] = []

        def _spy(sig: Any, handler: Any) -> Any:
            calls.append((sig, handler))
            return signal.getsignal(sig)

        monkeypatch.setattr(worker_module.signal, "signal", _spy)
        worker = JobWorker(JobQueue(), ValidationStore())

        thread = _start_worker_thread(
            worker, poll_seconds=0.01, install_signal_handlers=False
        )
        time.sleep(0.05)
        worker.stop()
        thread.join(timeout=2.0)

        assert not thread.is_alive()
        assert calls == []


class TestDrainAndPromptness:
    def test_direct_stop_mid_job_completes_it_then_returns(self) -> None:
        queue = JobQueue()
        store = ValidationStore()
        backend = _SlowBackend(delay=0.3)
        worker = JobWorker(queue, store, backend=backend)
        job_id = queue.enqueue(_spec("vrun-drain01")).job_id

        thread = _start_worker_thread(
            worker, poll_seconds=0.01, install_signal_handlers=False
        )
        assert backend.started.wait(2.0), "worker never entered the job"
        worker.stop()  # shutdown requested while the episode is in flight
        thread.join(timeout=3.0)

        assert not thread.is_alive(), "run_forever did not return promptly"
        record = queue.get(job_id)
        assert record is not None
        assert record.status is JobStatus.DONE
        assert store.get(job_id) is not None

    def test_stop_wakes_a_long_idle_wait_immediately(self) -> None:
        worker = JobWorker(JobQueue(), ValidationStore())
        thread = _start_worker_thread(
            worker, poll_seconds=5.0, install_signal_handlers=False
        )
        time.sleep(0.05)  # let the loop settle into the long Event.wait

        started = time.monotonic()
        worker.stop()
        thread.join(timeout=2.0)

        assert not thread.is_alive()
        assert time.monotonic() - started < 1.0, "stop() did not wake the idle wait"
