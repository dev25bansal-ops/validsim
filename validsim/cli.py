"""ValidSim command-line interface.

``run`` executes a validation locally against the configured backend (see
:func:`validsim.sim.create_backend`; the mock backend by default), persists the
full run to the configured validation store (``VALIDSIM_STORE``; see
:func:`validsim.store.create_store`), and caches the scorecard as JSON (path
overridable via ``VALIDSIM_CACHE_FILE``) so follow-up commands ``status`` /
``scorecard`` / ``gate`` can inspect it across processes. Those three commands
target a run via either ``--run-id`` or ``--latest`` (the newest cached run);
exactly one of the two is required.
``gate`` exits non-zero on ``BLOCK``, making it directly usable in CI.
``delete`` removes a run from the store — CLI parity for the previously
API-only run-deletion — targeting it the same way (``--run-id`` or
``--latest``); it exits 0 on success and 2 when the run is absent.
``health`` reads the local store and job queue directly (no HTTP call) and
prints a readiness summary mirroring the ``/api/v1/health`` payload — version,
store/job-queue backend labels and the stored-run count — always exiting 0.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import typer

from validsim import __version__
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
from validsim.engine.pipeline import run_and_score
from validsim.engine.regression import compare as compare_regression
from validsim.engine.scorecard import Scorecard
from validsim.jobs.models import JobSpec
from validsim.jobs.queue import create_job_queue
from validsim.jobs.worker import JobWorker
from validsim.sim import create_backend
from validsim.sim.runner import run_validation, stable_seed
from validsim.store import create_store
from validsim.store.memory import StoredRun

app = typer.Typer(
    name="validsim",
    help="ValidSim — Sim-to-Real CI/CD validation for robot foundation models.",
    no_args_is_help=True,
    add_completion=False,
)

#: Sub-command group for managing the asynchronous validation job queue.
#: ``validsim job enqueue`` adds a job; ``validsim jobs`` lists them and
#: ``validsim worker`` consumes them (both are top-level commands below).
job_app = typer.Typer(
    name="job",
    help="Manage asynchronous validation jobs (enqueue for later worker runs).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(job_app, name="job")

_CACHE_ENV = "VALIDSIM_CACHE_FILE"
_RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


def _cache_path() -> Path:
    """Location of the local scorecard cache (JSON file)."""
    return Path(os.environ.get(_CACHE_ENV, Path(".validsim") / "scorecards.json"))


def _load_cache() -> dict[str, Any]:
    """Read the scorecard cache; empty dict when absent."""
    path = _cache_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_to_cache(scorecard: dict[str, Any]) -> None:
    """Insert one scorecard dict into the cache, keyed by run id."""
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_cache()
    cache[scorecard["run_id"]] = scorecard
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _require_cached(run_id: str) -> dict[str, Any]:
    """Fetch a cached scorecard or abort with exit code 2."""
    scorecard = _load_cache().get(run_id)
    if scorecard is None:
        typer.echo(f"error: no cached run '{run_id}' (run `validsim run` first)", err=True)
        raise typer.Exit(code=2)
    return scorecard


def _newest_run_id(cache: dict[str, Any]) -> str:
    """Return the run id of the newest cached scorecard.

    Ordering is by ``created_at`` — an ISO-8601 string, so lexicographic
    comparison matches chronological order. Ties are broken by insertion
    order: the last-inserted key wins.
    """
    best_run_id = ""
    best_key: tuple[str, int] | None = None
    for index, (run_id, entry) in enumerate(cache.items()):
        created_at = str(entry.get("created_at", "")) if isinstance(entry, dict) else ""
        key = (created_at, index)
        if best_key is None or key > best_key:
            best_key = key
            best_run_id = run_id
    return best_run_id


def _resolve_run_id(run_id: str | None, latest: bool) -> str:
    """Return the run id to act on from the ``--run-id`` / ``--latest`` pair.

    Exactly one of the two must be supplied; otherwise a
    :class:`typer.BadParameter` (exit code 2) is raised. ``--latest`` resolves
    the newest cached run and aborts with exit code 2 when the cache is empty.
    """
    if run_id and latest:
        raise typer.BadParameter("provide exactly one of --run-id or --latest, not both")
    if not run_id and not latest:
        raise typer.BadParameter("one of --run-id or --latest is required")
    if latest:
        cache = _load_cache()
        if not cache:
            typer.echo("error: no cached runs (run `validsim run` first)", err=True)
            raise typer.Exit(code=2)
        return _newest_run_id(cache)
    assert run_id is not None
    return run_id


def _require_stored(run_id: str) -> StoredRun:
    """Fetch a run from the configured store or abort with exit code 2."""
    store = create_store()
    try:
        run = store.get(run_id)
        if run is None:
            typer.echo(
                f"error: no stored run '{run_id}' (run `validsim run` first)",
                err=True,
            )
            raise typer.Exit(code=2)
        return run
    finally:
        store.close()


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a compact, whitespace-aligned plain-text table.

    Columns are left-justified with a two-space gutter; callers pre-format
    every cell as a string so numeric alignment stays a presentation concern.
    """
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def _line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    lines = [_line(headers), _line(["-" * width for width in widths])]
    lines.extend(_line(row) for row in rows)
    return "\n".join(lines)


def _run_impl(
    task: str,
    robot: str,
    episodes: int,
    adversarial: int,
    checkpoint: str,
    environment: str,
    threshold: float,
) -> None:
    """Shared body for the ``run`` and ``validate`` commands.

    Delegates the seed -> scenario -> simulate -> evaluate -> score -> persist
    sequence to :func:`validsim.engine.pipeline.run_and_score` (the same engine
    path the API and job worker use), then caches the resulting scorecard as
    JSON and prints the human-readable summary. ``validate`` is a pure
    discoverability alias of ``run`` (see :func:`validate`); both delegate here
    so their behaviour can never drift apart.
    """
    config = TaskConfig(
        task_id=task,
        robot=RobotSpec(name=robot),
        environment=EnvironmentSpec(name=environment),
        episodes=episodes,
        adversarial_count=adversarial,
    )
    store = create_store()
    try:
        run = run_and_score(
            config,
            checkpoint,
            store,
            threshold=threshold,
            run_validation=run_validation,
            create_backend=create_backend,
        )
    finally:
        store.close()
    scorecard = run.scorecard
    _save_to_cache(scorecard.to_dict())

    top_failures = ", ".join(
        f"{mode}={count}" for mode, count in list(scorecard.failure_taxonomy.items())[:3]
    ) or "none"
    typer.echo("ValidSim validation complete")
    typer.echo(f"  Version:       {__version__}")
    typer.echo(f"  Run ID:        {scorecard.run_id}")
    typer.echo(f"  Checkpoint:    {scorecard.checkpoint_id}")
    typer.echo(
        f"  Task:          {scorecard.task_id} ({episodes} nominal + {adversarial} adversarial)"
    )
    typer.echo(f"  Success rate:  {scorecard.success_rate:.1%}")
    typer.echo(f"  Safety score:  {scorecard.safety_score:.1f}")
    typer.echo(f"  Robustness:    {scorecard.robustness_score:.1f}")
    typer.echo(f"  Composite:     {scorecard.composite_score:.1f} (threshold {threshold:.1f})")
    typer.echo(f"  Decision:      {scorecard.deploy_decision}")
    typer.echo(f"  Top failures:  {top_failures}")


@app.command()
def run(
    task: str = typer.Option("pick-place", "--task", "-t", help="Task identifier."),
    robot: str = typer.Option("franka_panda", "--robot", "-r", help="Robot name."),
    episodes: int = typer.Option(500, "--episodes", "-e", min=1, help="Nominal episodes."),
    adversarial: int = typer.Option(
        24, "--adversarial", "-a", min=0, help="Adversarial scenarios."
    ),
    checkpoint: str = typer.Option("local-checkpoint", "--checkpoint", "-c", help="Checkpoint id."),
    environment: str = typer.Option(
        "mock-scene", "--environment", "-E", help="Environment/scene name."
    ),
    threshold: float = typer.Option(85.0, "--threshold", help="Composite score to approve."),
) -> None:
    """Run a local validation against the configured backend and print a summary."""
    _run_impl(task, robot, episodes, adversarial, checkpoint, environment, threshold)


@app.command()
def validate(
    task: str = typer.Option("pick-place", "--task", "-t", help="Task identifier."),
    robot: str = typer.Option("franka_panda", "--robot", "-r", help="Robot name."),
    episodes: int = typer.Option(500, "--episodes", "-e", min=1, help="Nominal episodes."),
    adversarial: int = typer.Option(
        24, "--adversarial", "-a", min=0, help="Adversarial scenarios."
    ),
    checkpoint: str = typer.Option("local-checkpoint", "--checkpoint", "-c", help="Checkpoint id."),
    environment: str = typer.Option(
        "mock-scene", "--environment", "-E", help="Environment/scene name."
    ),
    threshold: float = typer.Option(85.0, "--threshold", help="Composite score to approve."),
) -> None:
    """Alias for ``run``: validate a checkpoint and print a summary.

    Accepts exactly the same options as :command:`run` and delegates to the
    identical implementation; provided for discoverability.
    """
    _run_impl(task, robot, episodes, adversarial, checkpoint, environment, threshold)


@app.command()
def status(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run id returned by `validsim run`."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Use the newest cached run instead of --run-id."
    ),
) -> None:
    """Show cached status for a previous run."""
    run_id = _resolve_run_id(run_id, latest)
    scorecard = _require_cached(run_id)
    typer.echo(f"Run ID:       {run_id}")
    typer.echo(f"Checkpoint:   {scorecard['checkpoint_id']}")
    typer.echo(f"Created:      {scorecard['created_at']}")
    typer.echo(f"Composite:    {scorecard['composite_score']}")
    typer.echo(f"Decision:     {scorecard['deploy_decision']}")


@app.command()
def scorecard(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run id returned by `validsim run`."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Use the newest cached run instead of --run-id."
    ),
) -> None:
    """Print the full cached scorecard as JSON."""
    resolved = _resolve_run_id(run_id, latest)
    typer.echo(json.dumps(_require_cached(resolved), indent=2))


@app.command()
def report(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run id returned by `validsim run`."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Use the newest cached run instead of --run-id."
    ),
    format: str = typer.Option(
        "markdown", "--format", help="Report format: markdown or html."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the cached scorecard as raw JSON instead of Markdown/HTML.",
    ),
) -> None:
    """Print a human-readable scorecard report (Markdown or HTML).

    With ``--json`` the cached scorecard dict is emitted verbatim as JSON,
    bypassing the Markdown/HTML rendering (useful for machine consumers).
    """
    resolved = _resolve_run_id(run_id, latest)
    scorecard_dict = _require_cached(resolved)
    if json_output:
        typer.echo(json.dumps(scorecard_dict, indent=2))
        return
    ci = scorecard_dict.get("confidence_interval")
    if ci is not None:
        scorecard_dict = {**scorecard_dict, "confidence_interval": (ci[0], ci[1])}
    scorecard = Scorecard(**scorecard_dict)
    if format == "markdown":
        typer.echo(scorecard_to_markdown(scorecard))
    elif format == "html":
        typer.echo(scorecard_to_html(scorecard))
    else:
        raise typer.BadParameter(
            f"invalid --format '{format}': choose 'markdown' or 'html'"
        )


@app.command()
def gate(
    run_id: str | None = typer.Option(None, "--run-id", help="Run id to gate on."),
    latest: bool = typer.Option(
        False, "--latest", help="Use the newest cached run instead of --run-id."
    ),
    threshold: float = typer.Option(
        -1.0, "--threshold", help="Override the stored approval threshold."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the decision as JSON on stdout for machine consumption.",
    ),
) -> None:
    """Exit non-zero (1) when the run's composite score is below threshold.

    With ``--json`` the decision is printed to stdout as a JSON object
    ``{run_id, composite_score, threshold, decision}`` for CI consumers; the
    0/1 exit-code contract is preserved either way.
    """
    resolved = _resolve_run_id(run_id, latest)
    scorecard_dict = _require_cached(resolved)
    effective = scorecard_dict["threshold"] if threshold < 0 else threshold
    approved = float(scorecard_dict["composite_score"]) >= float(effective)
    verdict = "APPROVE" if approved else "BLOCK"
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run_id": resolved,
                    "composite_score": scorecard_dict["composite_score"],
                    "threshold": effective,
                    "decision": verdict,
                }
            )
        )
    else:
        typer.echo(
            f"gate: {resolved} composite={scorecard_dict['composite_score']} "
            f"threshold={effective} -> {verdict}"
        )
    if not approved:
        raise typer.Exit(code=1)


@app.command()
def delete(
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run id returned by `validsim run`."
    ),
    latest: bool = typer.Option(
        False, "--latest", help="Delete the newest cached run instead of --run-id."
    ),
) -> None:
    """Delete a stored run (CLI parity for the API-only run-deletion).

    Targets the run via ``--run-id`` or ``--latest`` — resolved exactly like
    :command:`status` / :command:`scorecard` / :command:`gate` (via
    :func:`_resolve_run_id`), so a missing/ambiguous flag pair or an empty
    cache aborts with exit code 2. The run is removed from the configured
    store (``VALIDSIM_STORE``); on success a confirmation is printed and the
    command exits 0, while an unknown run exits 2.
    """
    resolved = _resolve_run_id(run_id, latest)
    store = create_store()
    try:
        removed = store.delete(resolved)
    finally:
        store.close()
    if not removed:
        typer.echo(f"error: no stored run '{resolved}' to delete", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"Deleted run {resolved}")


@app.command()
def models() -> None:
    """List the checkpoint registry aggregated from the configured store."""
    store = create_store()
    try:
        runs = store.history()
        total = store.count()
    finally:
        store.close()

    aggregated: dict[str, list[StoredRun]] = {}
    for run in runs:
        aggregated.setdefault(run.checkpoint_id, []).append(run)

    rows: list[list[str]] = []
    for checkpoint_id, checkpoint_runs in aggregated.items():
        latest = checkpoint_runs[-1]  # history() is oldest-first -> last is newest
        rows.append(
            [
                checkpoint_id,
                str(len(checkpoint_runs)),
                f"{latest.scorecard.composite_score:.1f}",
                latest.scorecard.deploy_decision,
                latest.created_at,
            ]
        )

    # Newest activity first (created_at is ISO-8601, so lexicographic == chronological).
    rows.sort(key=lambda row: row[-1], reverse=True)
    if not rows:
        typer.echo("no checkpoints registered")
        return

    typer.echo(
        _render_table(
            ["CHECKPOINT", "RUNS", "COMPOSITE", "DECISION", "LAST VALIDATED"], rows
        )
    )
    typer.echo(f"{total} total runs stored")


@app.command()
def version() -> None:
    """Print the ValidSim version and a one-line feature summary.

    Always exits 0; intended for quick environment/CI sanity checks so an
    operator can confirm which build is installed without running a
    validation.
    """
    typer.echo(f"ValidSim {__version__}")
    typer.echo(
        "Sim-to-Real CI/CD validation for robot foundation models: "
        "run validations, gate deploys on scorecards, inspect model history."
    )


#: Class-name suffix shared by every validation store, stripped to derive a
#: short backend label for the ``health`` command (mirrors the API's
#: ``_store_backend_name`` in :mod:`validsim.api.main`).
_STORE_CLASS_SUFFIX = "validationstore"
#: Class-name suffix shared by every job queue, stripped by
#: :func:`_backend_label` for the same reason.
_JOB_QUEUE_CLASS_SUFFIX = "jobqueue"


def _backend_label(obj: Any, suffix: str, default: str) -> str:
    """Derive a short backend label from ``obj``'s class name.

    Mirrors :func:`validsim.api.main._backend_label` so the CLI ``health``
    command reports the same labels as ``/api/v1/health`` without making an
    HTTP call. The concrete class name is lower-cased and the shared
    ``suffix`` (e.g. ``"validationstore"``) is dropped; an empty remainder
    means the object is the base in-memory implementation and maps to
    ``default``. ``"unknown"`` is returned when ``obj`` is ``None``.
    """
    if obj is None:
        return "unknown"
    name = type(obj).__name__.lower()
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return name or default


@app.command()
def health() -> None:
    """Print a local readiness summary mirroring the ``/api/v1/health`` payload.

    Reads the configured validation store and job queue *directly* — via
    :func:`validsim.store.create_store` and
    :func:`validsim.jobs.queue.create_job_queue`, exactly as the other
    commands do — rather than issuing an HTTP request to a running API. It
    reports the package ``version``, the store/queue backend labels (derived
    from the concrete ``create_store`` / ``create_job_queue`` class names, so
    they match the API's ``store_backend`` / ``job_queue_backend`` fields),
    and the number of runs currently stored (``store.count()``).

    Always exits 0: this is a status read, not a CI gate, so an unreachable or
    misconfigured backend degrades to an ``error`` label instead of failing.
    """
    store_backend = "unknown"
    runs = 0
    try:
        store = create_store()
        try:
            store_backend = _backend_label(store, _STORE_CLASS_SUFFIX, "memory")
            runs = store.count()
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a status read must never fail
        store_backend = "error"
        typer.echo(f"  Store backend:     error ({exc})", err=True)

    queue_backend = "unknown"
    try:
        queue = create_job_queue()
        try:
            queue_backend = _backend_label(queue, _JOB_QUEUE_CLASS_SUFFIX, "memory")
        finally:
            queue.close()
    except Exception as exc:  # noqa: BLE001 - a status read must never fail
        queue_backend = "error"
        typer.echo(f"  Job queue backend: error ({exc})", err=True)

    typer.echo("ValidSim health: ok")
    typer.echo(f"  Version:           {__version__}")
    typer.echo(f"  Store backend:     {store_backend}")
    typer.echo(f"  Job queue backend: {queue_backend}")
    typer.echo(f"  Runs stored:       {runs}")


@app.command()
def compare(
    baseline: str | None = typer.Option(None, "--baseline", help="Baseline run id."),
    baseline_latest: bool = typer.Option(
        False, "--baseline-latest", help="Use the newest cached run as the baseline."
    ),
    candidate: str | None = typer.Option(None, "--candidate", help="Candidate run id."),
    candidate_latest: bool = typer.Option(
        False, "--candidate-latest", help="Use the newest cached run as the candidate."
    ),
) -> None:
    """Compare two stored runs and print their regression report."""
    baseline_id = _resolve_run_id(baseline, baseline_latest)
    candidate_id = _resolve_run_id(candidate, candidate_latest)
    baseline_run = _require_stored(baseline_id)
    candidate_run = _require_stored(candidate_id)

    report = compare_regression(
        candidate_run.evaluation,
        baseline_run.evaluation,
        seed=stable_seed(candidate_id, baseline_id),
    )

    rows: list[list[str]] = []
    for item in report.items:
        rows.append(
            [
                item.metric,
                f"{item.before:.4f}",
                f"{item.after:.4f}",
                f"{item.delta:+.4f}",
                "—" if item.p_value is None else f"{item.p_value:.3f}",
                item.severity,
            ]
        )

    typer.echo(f"Regression: {candidate_id} vs baseline {baseline_id}")
    typer.echo(f"  Significant regressions: {len(report.significant_regressions)}")
    typer.echo(f"  Worst severity:          {report.worst_severity}")
    typer.echo()
    typer.echo(_render_table(["METRIC", "BEFORE", "AFTER", "DELTA", "P-VALUE", "SEVERITY"], rows))


@app.command()
def jobs() -> None:
    """List queued validation jobs from the configured job queue.

    Reads the queue selected by ``VALIDSIM_JOB_QUEUE`` (memory by default,
    see :func:`validsim.jobs.queue.create_job_queue`) and prints one row per
    job: id, status, checkpoint and creation timestamp. Always exits 0; an
    empty queue prints a friendly notice rather than failing.
    """
    queue = create_job_queue()
    try:
        records = queue.list()
    finally:
        queue.close()

    if not records:
        typer.echo("no jobs queued")
        return

    rows = [
        [
            record.job_id,
            record.status.value,
            record.spec.checkpoint_id,
            record.created_at or "",
        ]
        for record in records
    ]
    typer.echo(_render_table(["JOB ID", "STATUS", "CHECKPOINT", "CREATED"], rows))


@job_app.command("enqueue")
def job_enqueue(
    checkpoint: str = typer.Option(
        ..., "--checkpoint", "-c", help="Checkpoint id to validate."
    ),
    task: str = typer.Option("pick-place", "--task", "-t", help="Task identifier."),
    episodes: int = typer.Option(
        1000, "--episodes", "-e", min=1, help="Nominal episodes."
    ),
    adversarial: int = typer.Option(
        0, "--adversarial", "-a", min=0, help="Adversarial scenarios."
    ),
) -> None:
    """Enqueue one asynchronous validation job and print its id and status.

    The job is added to the configured queue (``VALIDSIM_JOB_QUEUE``) in the
    ``queued`` state; a :command:`validsim worker` later claims and runs it.
    Exits 0 on success.
    """
    queue = create_job_queue()
    try:
        spec = JobSpec(
            run_id=queue.new_run_id(),
            checkpoint_id=checkpoint,
            task_id=task,
            episodes=episodes,
            adversarial=adversarial,
        )
        record = queue.enqueue(spec)
    finally:
        queue.close()

    typer.echo(f"Enqueued job {record.job_id} (status: {record.status.value})")


def _drain_worker(
    job_worker: JobWorker,
    *,
    poll_seconds: float,
    max_jobs: int,
    echo: bool,
) -> None:
    """Process queued jobs in a loop, optionally printing each transition.

    This is the ``--watch`` / ``--max-jobs`` driver. It repeatedly calls
    :meth:`validsim.jobs.worker.JobWorker.run_once` rather than threading a
    callback into :meth:`~validsim.jobs.worker.JobWorker.run_forever`, so the
    CLI stays decoupled from the worker's internals. Behaviour:

    * ``echo`` prints ``worker: job <id> -> <status>`` for every processed job,
      as soon as its terminal transition is known.
    * ``max_jobs`` (> 0) bounds the run: stop once that many jobs are handled,
      or immediately when the queue runs dry — so a bounded run never hangs.
    * ``max_jobs == 0`` (unlimited) idles ``poll_seconds`` between empty polls,
      waiting for new jobs — like ``run_forever`` but with per-job output.
    """
    processed = 0
    while True:
        record = job_worker.run_once()
        if record is not None:
            if echo:
                typer.echo(f"worker: job {record.job_id} -> {record.status.value}")
            processed += 1
            if max_jobs and processed >= max_jobs:
                return
        elif max_jobs:
            # Bounded run with nothing left to process: stop instead of idling.
            return
        else:
            # Unlimited watch: idle briefly, waiting for new jobs to arrive.
            time.sleep(poll_seconds)


@app.command()
def worker(
    once: bool = typer.Option(
        False, "--once", help="Process a single queued job, then exit."
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        help="Print each processed job's transition (job_id -> status) as it happens.",
    ),
    max_jobs: int = typer.Option(
        0,
        "--max-jobs",
        min=0,
        help="Stop after processing this many jobs (0 = unlimited).",
    ),
    poll_seconds: float = typer.Option(
        1.0, "--poll-seconds", min=0.0, help="Idle interval when looping."
    ),
) -> None:
    """Run a job worker against the configured queue and validation store.

    Wires a :class:`~validsim.jobs.worker.JobWorker` to the queue from
    :func:`validsim.jobs.queue.create_job_queue`, the store from
    :func:`validsim.store.create_store`, and the simulation backend from
    :func:`validsim.sim.create_backend` (the deterministic mock by default,
    the GPU worker when ``VALIDSIM_BACKEND=isaac``). With ``--once`` it claims
    and executes a single job (printing the resulting status) and exits 0;
    otherwise it loops until interrupted (Ctrl-C).

    With ``--watch`` it loops like the default worker but prints each processed
    job's transition (``job_id -> status``) as a single line as it happens.
    ``--max-jobs N`` bounds any looping run to N jobs (0 = unlimited), which
    makes ``worker --watch --max-jobs N`` terminate on its own (and thus be
    testable). ``--once`` behaviour is unchanged and takes precedence.
    """
    queue = create_job_queue()
    store = create_store()
    job_worker = JobWorker(queue, store, create_backend())
    try:
        if once:
            record = job_worker.run_once()
            if record is None:
                typer.echo("worker: no queued jobs")
                return
            typer.echo(f"worker: job {record.job_id} -> {record.status.value}")
        elif watch or max_jobs > 0:
            try:
                _drain_worker(
                    job_worker,
                    poll_seconds=poll_seconds,
                    max_jobs=max_jobs,
                    echo=watch,
                )
            except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl-C
                typer.echo("worker: stopped")
        else:
            job_worker.run_forever(poll_seconds=poll_seconds)
    finally:
        queue.close()
        store.close()


def main() -> None:  # pragma: no cover - console entry point
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["app", "main"]
