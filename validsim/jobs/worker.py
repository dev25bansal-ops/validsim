"""Background worker that drains the validation job queue.

:class:`JobWorker` is the consumer side of the async pipeline: a deployment
enqueues :class:`~validsim.jobs.models.JobSpec` records via the
:class:`~validsim.jobs.queue.JobQueue` (the API's ``POST /jobs`` route does
exactly this), then one or more workers call :meth:`JobWorker.run_once` (or the
blocking :meth:`JobWorker.run_forever`) to claim queued jobs and execute the
full validation pipeline — the same engine path the synchronous API uses
(:func:`~validsim.sim.runner.run_validation` ->
:func:`~validsim.engine.evaluation.evaluate` ->
:func:`~validsim.engine.safety.compute_safety` ->
:func:`~validsim.engine.scorecard.build_scorecard`) — persisting the finished
:class:`~validsim.store.memory.StoredRun` to a shared
:class:`~validsim.store.memory.ValidationStore`.

Lifecycle: each claimed job moves ``queued -> running -> done`` on success, or
``queued -> running -> failed`` when the backend or engine raises. On success
the record's ``result`` field stores the persisted run id, which is the job's
own ``run_id`` (the queue and the store share that key, see
:mod:`validsim.jobs.models`). The worker is deliberately dependency-light and
deterministic: seeds derive from ``stable_seed(checkpoint_id, task_id)``, so the
same spec always yields the same scorecard.

Shutdown: :meth:`JobWorker.run_forever` installs ``SIGTERM``/``SIGINT``
handlers by default, so a container stop flips the worker's stop event, the
in-flight job drains to its terminal state, and the process exits cleanly with
the previous signal handlers restored.
"""

from __future__ import annotations

import signal
import threading
from typing import Any

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import run_and_score
from validsim.jobs.models import JobRecord, JobSpec, JobStatus
from validsim.jobs.queue import JobQueue
from validsim.sim import create_backend
from validsim.sim.runner import SimulationBackend, run_validation
from validsim.store.memory import StoredRun, ValidationStore

__all__ = ["JobWorker"]

#: Default robot name for specs that only carry a task id (mirrors the CLI).
_DEFAULT_ROBOT = "franka_panda"
#: Default environment/scene name for the same reason.
_DEFAULT_ENVIRONMENT = "mock-scene"
#: Composite score required to approve (mirrors the CLI/API default).
_DEFAULT_THRESHOLD = 85.0
#: Signals that trigger a graceful shutdown when :meth:`JobWorker.run_forever`
#: installs its handlers (SIGTERM: container/orchestrator stop; SIGINT: Ctrl-C).
_SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGINT)


class JobWorker:
    """Consumes queued validation jobs and persists their scorecard results.

    The worker holds references to a :class:`~validsim.jobs.queue.JobQueue` (to
    claim and update jobs), a :class:`~validsim.store.memory.ValidationStore`
    (to persist finished runs), and a
    :class:`~validsim.sim.runner.SimulationBackend` (defaulting to whatever
    :func:`validsim.sim.create_backend` selects from ``VALIDSIM_BACKEND`` — the
    deterministic mock unless the GPU worker is configured). It performs
    no I/O of its own beyond those collaborators, so it is trivial to drive from
    a test or a long-running process.
    """

    def __init__(
        self,
        queue: JobQueue,
        store: ValidationStore,
        backend: SimulationBackend | None = None,
        *,
        threshold: float = _DEFAULT_THRESHOLD,
        robot_name: str = _DEFAULT_ROBOT,
        environment_name: str = _DEFAULT_ENVIRONMENT,
    ) -> None:
        """Wire the worker to a queue, a store, and a simulation backend.

        Args:
            queue: Source of queued jobs and sink for status transitions.
            store: Where finished :class:`StoredRun` records are persisted.
            backend: Simulation backend to execute episodes; defaults to
                :func:`validsim.sim.create_backend` (the deterministic mock
                unless ``VALIDSIM_BACKEND=isaac`` selects the GPU worker).
            threshold: Composite score needed for an ``APPROVE`` decision.
            robot_name: Robot name used to build the task config.
            environment_name: Environment name used to build the task config.
        """
        self._queue = queue
        self._store = store
        self._backend: SimulationBackend = (
            backend if backend is not None else create_backend()
        )
        self._threshold = threshold
        self._robot_name = robot_name
        self._environment_name = environment_name
        self._stop = threading.Event()

    # -- public API ---------------------------------------------------------

    def stop(self) -> None:
        """Signal :meth:`run_forever` to exit after its current iteration."""
        self._stop.set()

    def run_once(self) -> JobRecord | None:
        """Claim and execute one queued job, returning its final record.

        Returns ``None`` when no job is currently queued (the caller may retry
        later). On success the returned record is ``done`` with ``result`` set
        to the persisted run id; on failure it is ``failed`` with ``error`` set
        to the exception message. The job is marked ``running`` before the
        pipeline executes so observers see the intermediate state.
        """
        spec = self._claim_next()
        if spec is None:
            return None
        job_id = spec.run_id
        self._queue.update_status(job_id, JobStatus.RUNNING)
        try:
            run = self._execute(spec)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the worker
            return self._queue.update_status(job_id, JobStatus.FAILED, error=str(exc))
        return self._queue.update_status(job_id, JobStatus.DONE, result=run.run_id)

    def run_forever(
        self,
        poll_seconds: float = 1.0,
        install_signal_handlers: bool = True,
    ) -> None:
        """Loop :meth:`run_once` until :meth:`stop` is called.

        When no job is available the worker idles for at most ``poll_seconds``,
        waking immediately if :meth:`stop` is called during the wait. This makes
        the loop responsive to shutdown without busy-spinning.

        By default the loop also installs handlers for ``SIGTERM`` and
        ``SIGINT`` that call :meth:`stop`, so a container stop (or Ctrl-C)
        drains the in-flight job and exits cleanly instead of being killed
        mid-episode. The previous handlers are restored when the loop exits.
        Handler registration only works from the main thread; when
        :meth:`run_forever` runs elsewhere the installation is skipped
        silently and the caller must invoke :meth:`stop` itself. Pass
        ``install_signal_handlers=False`` to opt out of registration entirely.
        """
        previous = self._install_signal_handlers() if install_signal_handlers else {}
        try:
            while not self._stop.is_set():
                if self.run_once() is None:
                    self._stop.wait(poll_seconds)
        finally:
            self._restore_signal_handlers(previous)

    # -- internals ----------------------------------------------------------

    def _install_signal_handlers(self) -> dict[signal.Signals, Any]:
        """Point :data:`_SHUTDOWN_SIGNALS` at :meth:`stop`, returning old handlers.

        ``signal.signal`` only works from the main thread; when registration
        is refused (worker thread, unsupported platform) the installation is
        skipped silently and the returned mapping holds whatever *was* set up,
        so :meth:`_restore_signal_handlers` can unwind exactly that much.

        Returns:
            Mapping of each successfully registered signal to the handler that
            was in place before (empty when nothing could be installed).
        """

        def _handle_shutdown(signum: int, frame: Any) -> None:
            # stop() only flips the worker's Event, which is reentrant-safe
            # for the same thread the handler interrupts; the loop itself
            # checks the flag between jobs, so an in-flight run drains first.
            self.stop()

        previous: dict[signal.Signals, Any] = {}
        for sig in _SHUTDOWN_SIGNALS:
            try:
                previous[sig] = signal.signal(sig, _handle_shutdown)
            except (ValueError, OSError, RuntimeError):
                # ValueError: not the main thread. Fall back silently to the
                # manual stop() path; callers embed the worker themselves.
                continue
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: dict[signal.Signals, Any]) -> None:
        """Re-install the handlers captured by :meth:`_install_signal_handlers`.

        Best-effort: a failed restore (e.g. the loop exited from a different
        thread than it started on) is swallowed, mirroring the silent
        fallback used during installation.
        """
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, RuntimeError):  # pragma: no cover
                continue

    def _claim_next(self) -> JobSpec | None:
        """Return the spec of the oldest queued job, or ``None`` if none.

        The queue's :meth:`~validsim.jobs.queue.JobQueue.list` preserves FIFO
        insertion order, so the first ``queued`` record is the next to run.
        """
        for record in self._queue.list():
            if record.status is JobStatus.QUEUED:
                return record.spec
        return None

    def _execute(self, spec: JobSpec) -> StoredRun:
        """Run the validation pipeline for ``spec`` and persist the run.

        Delegates the shared engine sequence to
        :func:`validsim.engine.pipeline.run_and_score`, injecting the worker's
        own backend (so the env-selected default resolved in ``__init__`` is
        honored and never re-created here) and the job's ``run_id`` — the stored
        run reuses ``spec.run_id`` so the queue record's ``result`` points
        directly at the persisted artifact (the shared-key contract from
        :mod:`validsim.jobs.models`). ``run_validation`` is passed from this
        module's namespace so the wiring tests can keep intercepting it here.

        Raises:
            Exception: Any error from the backend or engine; the caller records
                it on the job as a failure.
        """
        task = TaskConfig(
            task_id=spec.task_id,
            robot=RobotSpec(name=self._robot_name),
            environment=EnvironmentSpec(name=self._environment_name),
            episodes=spec.episodes,
            adversarial_count=spec.adversarial,
        )
        return run_and_score(
            task,
            spec.checkpoint_id,
            self._store,
            backend=self._backend,
            run_id=spec.run_id,
            threshold=self._threshold,
            run_validation=run_validation,
        )
