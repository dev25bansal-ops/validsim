"""Shared validation pipeline: run -> score -> persist.

The synchronous API, the CLI ``run``/``validate`` command and the asynchronous
job worker all execute the same sequence of steps:

1. derive a deterministic seed from ``(checkpoint_id, task_id)``,
2. generate the adversarial scenarios for that seed,
3. run the nominal + adversarial episodes against a simulation backend,
4. evaluate and score the result (safety + composite scorecard),
5. optionally compare against a baseline run, and
6. persist the finished :class:`~validsim.store.memory.StoredRun`.

Before this module the sequence was duplicated verbatim in three places, so a
change to one (e.g. the scorecard inputs) could silently drift from the others.
:func:`run_and_score` owns the sequence once; the three entry points become thin
adapters that only supply their caller-specific inputs.

The function is deliberately free of HTTP and CLI concerns: it takes plain
engine arguments, knows nothing about FastAPI or Typer, and signals a missing
baseline with the domain-level :class:`BaselineNotFoundError` (which the API
maps to a 404) rather than an HTTP status code.

The two simulation primitives (:func:`~validsim.sim.runner.run_validation` and
:func:`~validsim.sim.create_backend`) are injectable. Each caller passes the
module-level reference it already exposes, so the existing monkeypatch-based
wiring tests (see ``tests/test_sim_factory.py``) keep intercepting the pipeline
through their own module namespace while the orchestration itself lives here.
When a caller omits them the pipeline falls back to the real implementations,
and when a caller supplies ``backend`` directly (the worker) ``create_backend``
is never invoked — exactly matching the pre-refactor behavior.
"""

from __future__ import annotations

from typing import Callable

from validsim.config import TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import compare
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
from validsim.scenarios.generator import ScenarioGenerator
from validsim.sim import create_backend as _default_create_backend
from validsim.sim.runner import (
    EpisodeResult,
    SimulationBackend,
    run_validation as _default_run_validation,
    stable_seed,
)
from validsim.store.memory import StoredRun, ValidationStore

__all__ = ["BaselineNotFoundError", "DEFAULT_THRESHOLD", "run_and_score"]

#: Composite score required to approve, mirroring the scorecard/CLI/worker default.
DEFAULT_THRESHOLD = 85.0

#: Signature of the injectable ``run_validation`` primitive (see :func:`run_and_score`).
RunValidationFn = Callable[..., "list[EpisodeResult]"]
#: Signature of the injectable ``create_backend`` factory (see :func:`run_and_score`).
CreateBackendFn = Callable[[], SimulationBackend]


class BaselineNotFoundError(LookupError):
    """Raised when a requested baseline run id is absent from the store.

    The synchronous API maps this to an HTTP 404; the CLI and worker never
    request a baseline, so they never observe it. The message matches the API's
    historical detail string exactly (``"baseline run <id> not found"``), which
    keeps the extraction behavior-preserving.
    """

    def __init__(self, baseline_run_id: str) -> None:
        """Record the offending ``baseline_run_id`` and format the message."""
        self.baseline_run_id = baseline_run_id
        super().__init__(f"baseline run {baseline_run_id} not found")


def run_and_score(
    task: TaskConfig,
    checkpoint_id: str,
    store: ValidationStore,
    *,
    backend: SimulationBackend | None = None,
    run_id: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    baseline_run_id: str | None = None,
    run_validation: RunValidationFn = _default_run_validation,
    create_backend: CreateBackendFn = _default_create_backend,
) -> StoredRun:
    """Run one validation end-to-end, score it, persist it and return the run.

    This is the single implementation shared by the API, CLI and job worker.
    It performs no HTTP- or CLI-specific work; callers pass only plain engine
    inputs and handle their own transport/presentation concerns around it.

    Args:
        task: The :class:`~validsim.config.TaskConfig` describing what to run.
        checkpoint_id: Checkpoint under validation (also seeds the run).
        store: Validation store the finished run is persisted to (and consulted
            for ``baseline_run_id``).
        backend: Simulation backend to execute episodes. When ``None`` (API,
            CLI) a fresh one is built via ``create_backend`` from the
            environment; the worker injects its own so this stays ``None``-free.
        run_id: Pre-assigned run id. When ``None`` a fresh
            :meth:`ValidationStore.new_run_id` is generated (API, CLI); the
            worker passes the job's own ``run_id`` so the queue result and the
            stored run share a key.
        threshold: Composite score needed for an ``APPROVE`` decision. Defaults
            to :data:`DEFAULT_THRESHOLD`, matching the API's historical
            "no threshold argument" behavior.
        baseline_run_id: Optional prior run id to compare against for
            regressions. Only the API supplies this.
        run_validation: Injectable ``run_validation`` primitive. Callers pass
            their module-level reference so test monkeypatching still applies.
        create_backend: Injectable backend factory, used only when ``backend``
            is ``None``. Callers pass their module-level reference for the same
            monkeypatching reason.

    Returns:
        The persisted :class:`StoredRun`. When the run id was free, this is the
        object just built. When it was already taken -- which happens whenever
        a caller reuses a run id it does not own, e.g. a worker retrying with
        its own ``spec.run_id`` -- the store is append-only and keeps the
        original record, so that stored record is returned instead. Either way
        the returned run is the one the store holds, never a verdict it never
        accepted.

    Raises:
        BaselineNotFoundError: ``baseline_run_id`` was given but is not present
            in ``store``. Raised after the episodes run (matching the previous
            API ordering) and before anything is persisted.
        Exception: Any error from the backend or engine propagates unchanged, so
            the job worker can still mark a job ``failed`` on backend faults.
    """
    seed = stable_seed(checkpoint_id, task.task_id)
    scenarios = ScenarioGenerator(seed=seed).generate(task.task_id, task.adversarial_count)
    resolved_backend = backend if backend is not None else create_backend()
    # Bind the checkpoint onto the task the backends receive. It arrives as its
    # own argument here (the run is about a checkpoint, the task describes the
    # work), but a backend only ever sees the task, so without this binding the
    # artifact under test never reaches the simulation at all.
    task = task.model_copy(update={"checkpoint_id": checkpoint_id})
    episodes = run_validation(task, resolved_backend, scenarios, seed=seed)

    evaluation = evaluate(episodes)
    safety = compute_safety(episodes)

    baseline = store.get(baseline_run_id) if baseline_run_id else None
    if baseline_run_id and baseline is None:
        raise BaselineNotFoundError(baseline_run_id)
    regression = compare(evaluation, baseline.evaluation, seed=seed) if baseline else None

    if run_id is None:
        run_id = ValidationStore.new_run_id()
    scorecard = build_scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task=task,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        regression=regression,
        threshold=threshold,
    )
    candidate = StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task.task_id,
        created_at=scorecard.created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        baseline_run_id=baseline_run_id,
        regression=regression,
    )
    # The store is append-only (see ValidationStore's contract): the first
    # write for a run_id is the record and a later write of the same id is
    # rejected. The worker passes its own spec.run_id, so a retry -- or a
    # crashed run re-executed -- can land on an id that is already taken. The
    # store keeps the original verdict, so returning the freshly built run
    # would hand the caller a scorecard the record never accepted (and, for a
    # worker retry, silently contradict what `gate` later reads). Re-read the
    # stored record instead: the answer to "what is the verdict for this id?"
    # is then always the one on the record.
    if not store.save_exists(candidate):
        stored = store.get(run_id)
        if stored is None:  # pragma: no cover - defensive
            raise RuntimeError(f"run {run_id} vanished from the store mid-save")
        return stored
    return candidate
