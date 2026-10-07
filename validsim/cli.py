"""ValidSim command-line interface.

``run`` executes a validation locally against the configured backend (see
:func:`validsim.sim.create_backend`; the mock backend by default), persists the
full run to the configured validation store (``VALIDSIM_STORE``; see
:func:`validsim.store.create_store`), and caches the scorecard as JSON (path
overridable via ``VALIDSIM_CACHE_FILE``) so follow-up commands ``status`` /
``scorecard`` / ``report`` can inspect it across processes. Those three target a
run via either ``--run-id`` or ``--latest`` (the newest *cached* run); exactly
one of the two is required.

``gate`` is the exception: it decides from the durable store, never the cache,
and resolves ``--latest`` against ``store.history()``. A cache file is writable
by any process that can reach it, so it cannot carry a deploy decision; with the
ephemeral in-memory store it exits 2 rather than guessing. On a ``BLOCK`` it
exits 1, which makes it directly usable in CI. Because exit 2 ("no verdict
obtained") and exit 1 ("verdict obtained, and it is BLOCK") are different
failures, ``gate --json`` always emits one JSON object naming which occurred
via ``outcome``/``reason`` — see :func:`gate`.
``delete`` removes a run from the store — CLI parity for the previously
API-only run-deletion — targeting it the same way (``--run-id`` or
``--latest``); it exits 0 on success and 2 when the run is absent.
``health`` reads the local store and job queue directly (no HTTP call) and
probes them for real, reporting a liveness/readiness summary that mirrors the
``/api/v1/health`` payload — version, store/job-queue backend labels and the
stored-run count. It exits 0 when every configured dependency is reachable and
1 when one is not, so it can actually fail; ``--json`` emits the same result as
one machine-parseable object.
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
from validsim.store import create_store, store_backend
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
_APPROVED = "APPROVE"
_BLOCKED = "BLOCK"
#: Backends that survive the process which wrote them, so `gate` can trust them.
_DURABLE_STORE_BACKENDS = frozenset({"sqlite", "postgres"})

# ---------------------------------------------------------------------------
# `gate` outcome vocabulary
# ---------------------------------------------------------------------------
# `gate` has three *meanings* to express and the process exit code can only
# carry two non-zero values, so the meaning travels in the payload instead.
# Without it, "the model is not good enough" and "I could not find out" both
# arrived as `decision: "BLOCK"`, and a deploy pipeline learned that BLOCK is
# noise.
#: The gate obtained a verdict and it was APPROVE.
_OUTCOME_APPROVED = "approved"
#: The gate obtained a verdict and it was BLOCK (the expected negative result).
_OUTCOME_BLOCKED = "blocked"
#: No verdict was obtained: bad flags, unparseable id, run absent, or no
#: durable store to read. Never a statement about the model.
_OUTCOME_USAGE = "usage_error"

#: Exit code for "no verdict obtained" (Typer's convention for usage errors).
_GATE_USAGE_EXIT = 2
#: Exit code for "verdict obtained, and it is BLOCK".
_GATE_BLOCKED_EXIT = 1


def _gate_usage_error(
    json_output: bool,
    reason: str,
    message: str,
    *,
    run_id: str | None = None,
) -> None:
    """Report a gate usage/lookup failure and exit :data:`_GATE_USAGE_EXIT`.

    With ``--json`` this emits a complete payload — identical in *shape* to the
    verdict payload, with ``decision: null`` and ``outcome: "usage_error"`` —
    before exiting. That is the whole point: a consumer running
    ``validsim gate --json`` previously got an **empty string** on exit 2 and
    had to scrape human-readable stderr to tell a usage mistake from a missing
    run from an unreachable store. A single non-zero code cannot carry three
    meanings, so the meaning is in the payload and the code is also reported
    there as ``exit_code``.

    Without ``--json`` the same sentence goes to stderr, unchanged from before.

    Args:
        json_output: Whether the caller asked for the machine payload.
        reason: A stable machine token, e.g. ``"not_found"``.
        message: The human-readable sentence, also exposed as ``message`` so a
            consumer that only greps stdout still sees it.
        run_id: The run id that was asked for, when one is known.
    """
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "composite_score": None,
                    "threshold": None,
                    "decision": None,
                    "engine_decision": None,
                    "outcome": _OUTCOME_USAGE,
                    "reason": reason,
                    "detail": message,
                    "message": message,
                    "exit_code": _GATE_USAGE_EXIT,
                }
            )
        )
    else:
        typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=_GATE_USAGE_EXIT)


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


def _require_single_selector(run_id: str | None, latest: bool) -> None:
    """Enforce that exactly one of ``--run-id`` / ``--latest`` was supplied.

    Split out of :func:`_resolve_run_id` because that function *also* resolves
    ``--latest`` against the local JSON cache, and a caller resolving against
    the **store** (``gate``) must not consult the cache at all — calling it just
    to run the exclusivity check made an empty cache abort a store-backed
    ``--latest`` query.
    """
    if run_id and latest:
        raise typer.BadParameter("provide exactly one of --run-id or --latest, not both")
    if not run_id and not latest:
        raise typer.BadParameter("one of --run-id or --latest is required")


def _resolve_run_id(run_id: str | None, latest: bool) -> str:
    """Return the run id to act on from the ``--run-id`` / ``--latest`` pair.

    Exactly one of the two must be supplied; otherwise a
    :class:`typer.BadParameter` (exit code 2) is raised. ``--latest`` resolves
    the newest cached run and aborts with exit code 2 when the cache is empty.
    """
    _require_single_selector(run_id, latest)
    if latest:
        cache = _load_cache()
        if not cache:
            typer.echo("error: no cached runs (run `validsim run` first)", err=True)
            raise typer.Exit(code=2)
        return _newest_run_id(cache)
    assert run_id is not None
    return run_id


def _require_stored(run_id: str, *, json_output: bool = False) -> StoredRun:
    """Fetch a run from the configured store or abort with exit code 2.

    Args:
        run_id: The run id to fetch.
        json_output: Forwarded to :func:`_gate_usage_error` so ``gate --json``
            can emit a diagnosable payload for a missing run instead of an
            empty stdout. Other callers keep the plain stderr behaviour.
    """
    store = create_store()
    try:
        run = store.get(run_id)
    finally:
        store.close()
    if run is None:
        _gate_usage_error(
            json_output,
            "not_found",
            f"no stored run '{run_id}' (run `validsim run` first)",
            run_id=run_id,
        )
    return run


def _require_durable_store(json_output: bool = False) -> str:
    """Return the backend name, or abort with exit code 2 if it is ephemeral.

    The in-memory default lives and dies with its process, so a separate
    ``validsim gate`` invocation could never observe the verdict it is asked to
    enforce. Gating a deploy on a store that cannot be shared is not a safety
    control, so this fails closed rather than falling back to the JSON cache —
    which any process able to write one file could forge (see the gate tests).

    This is a *configuration* failure, not a verdict about the model, so it is
    reported as ``reason="ephemeral_store"`` with ``decision: null``: a consumer
    must not read it as a BLOCK.
    """
    backend = store_backend()
    if backend not in _DURABLE_STORE_BACKENDS:
        _gate_usage_error(
            json_output,
            "ephemeral_store",
            f"gate needs a durable store, but VALIDSIM_STORE="
            f"{backend or 'unset'!r}; set it to one of "
            f"{', '.join(sorted(_DURABLE_STORE_BACKENDS))} when running "
            "`validsim run` so the verdict outlives that process",
        )
    return backend


def _newest_stored_run_id(json_output: bool = False) -> str:
    """Return the run id of the newest stored run, or abort with exit code 2."""
    store = create_store()
    try:
        runs = store.history()
    finally:
        store.close()
    if not runs:
        _gate_usage_error(
            json_output, "no_runs", "no stored runs (run `validsim run` first)"
        )
    return runs[-1].run_id  # history() is oldest-first


def _resolve_deletable_run_id(run_id: str | None, latest: bool) -> str:
    """Resolve ``delete``'s target from the store, falling back to the cache.

    ``delete`` mutates the durable record, so the store — not the local JSON
    cache — is authoritative for ``--latest``. Resolving from the cache made the
    command fail whenever the cache was absent (a normal state for a durable
    deployment) or stale (its newest entry naming a run the store never saw), and
    it would then try to delete a run id the store does not contain.

    Unlike :func:`_resolve_stored_run_id` (used by ``gate``) this does **not**
    require a durable backend: deleting from the default in-memory store is a
    legitimate, if short-lived, operation, and the previous behavior did not
    impose that requirement either. Only the flag-pair validation is shared.
    """
    if run_id and latest:
        raise typer.BadParameter("provide exactly one of --run-id or --latest, not both")
    if not run_id and not latest:
        raise typer.BadParameter("one of --run-id or --latest is required")
    if run_id:
        return run_id
    # --latest: prefer the store, since that is what delete actually mutates.
    store = create_store()
    try:
        runs = store.history()
    finally:
        store.close()
    if runs:
        return runs[-1].run_id  # history() is oldest-first
    # Empty store: the cache is the only remaining hint, so honor it rather than
    # failing outright (preserves the pre-existing in-memory ergonomics).
    cache = _load_cache()
    if cache:
        return _newest_run_id(cache)
    typer.echo("error: no stored or cached runs (run `validsim run` first)", err=True)
    raise typer.Exit(code=2)


def _resolve_stored_run_id(
    run_id: str | None, latest: bool, *, json_output: bool = False
) -> str:
    """Resolve the ``--run-id`` / ``--latest`` pair against the store.

    Unlike :func:`_resolve_run_id`, which answers from the local JSON cache, this
    refuses an ephemeral backend, validates the id shape, and resolves ``--latest``
    from the store. Used by ``gate``, the one command whose answer gates a deploy.

    ``json_output`` is forwarded so each abort here carries a machine-readable
    ``reason`` rather than only a human sentence — see :func:`_gate_usage_error`.

    The flag-pair check is delegated to :func:`_resolve_run_id` and therefore
    runs **before** ``--latest`` is honoured. It used to short-circuit to
    ``--latest`` first, so ``--run-id <stale-id> --latest`` passed the
    mutual-exclusion check, reported success, and then gated the newest run
    instead of the one the caller named. A deploy gate that can be pointed at
    an arbitrary run without saying so is not a safety control.
    """
    _require_single_selector(run_id, latest)
    _require_durable_store(json_output)
    if latest:
        return _newest_stored_run_id(json_output)
    resolved = _resolve_run_id(run_id, latest)
    if not _RUN_ID_RE.fullmatch(resolved):
        _gate_usage_error(
            json_output,
            "invalid_run_id",
            f"'{resolved}' is not a run id (expected vrun-<8 hex>, "
            "e.g. vrun-1a2b3c4d)",
            run_id=resolved,
        )
    return resolved


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
    # The composite pools nominal and adversarial episodes, so it cannot show
    # which segment failed. Report the adversarial segment on its own line --
    # it is gated separately and is frequently the deciding factor.
    if scorecard.adversarial_episode_count:
        adv_rate = scorecard.adversarial_success_rate or 0.0
        typer.echo(
            f"  Adversarial:   {adv_rate:.1%} "
            f"({scorecard.adversarial_episode_count} episodes)"
        )
    typer.echo(f"  Safety score:  {scorecard.safety_score:.1f}")
    typer.echo(f"  Robustness:    {scorecard.robustness_score:.1f}")
    typer.echo(f"  Composite:     {scorecard.composite_score:.1f} (threshold {threshold:.1f})")
    typer.echo(f"  Decision:      {scorecard.deploy_decision}")
    # A BLOCK with no stated reason is unactionable, so print every reason the
    # gate recorded rather than making the user re-derive it.
    for reason in scorecard.block_reasons:
        typer.echo(f"    - {reason}")
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
        False, "--latest", help="Use the newest run in the store instead of --run-id."
    ),
    threshold: float = typer.Option(
        -1.0,
        "--threshold",
        help="Raise the stored approval threshold; a lower value is ignored.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the decision as JSON on stdout for machine consumption.",
    ),
) -> None:
    """Exit non-zero unless the stored verdict approves the run.

    Reads the **durable store**, never the local JSON cache: a cache file can be
    written by any process with access to it, so it cannot be the basis for a
    deploy decision. With the default in-memory store nothing survives the run
    that produced it, so this exits 2 and says so instead of guessing.

    The verdict the engine recorded is authoritative. It already encodes the
    score threshold *and* every non-score block reason (an under-delivered run
    proves nothing), so this command never recomputes it from the composite, and
    only the exact string ``APPROVE`` approves. ``--threshold`` may only raise
    the bar above the stored one; it can never buy back an approval.

    Exit codes
    ----------
    * ``0`` — **APPROVE**. The stored verdict approved and the composite meets
      the effective threshold.
    * ``1`` — **BLOCK**. A verdict *was* obtained and it is a negative one.
    * ``2`` — **no verdict obtained** (a usage/configuration/lookup error):
      neither ``--run-id`` nor ``--latest``, both at once, a malformed run id,
      a run absent from the store, an empty store, or a non-durable store.

    These are two distinct failure *meanings* that a single non-zero code cannot
    separate, so the meaning is carried in the payload.

    ``--json`` payload
    ------------------
    Exactly one JSON object is written to stdout for **every** outcome,
    including the exit-2 ones (which previously printed nothing at all):

    * ``decision`` — ``APPROVE`` / ``BLOCK``, or ``null`` when no verdict was
      obtained. Never ``"BLOCK"`` for a usage error.
    * ``outcome`` — ``approved`` / ``blocked`` / ``usage_error``.
    * ``reason`` — a stable machine token, ``null`` when approved:
      ``below_threshold``, ``engine_blocked``, ``not_found``,
      ``invalid_run_id``, ``no_runs``, ``ephemeral_store``.
    * ``engine_decision`` — the verdict the engine itself recorded, so a BLOCK
      the gate applied on top of an engine APPROVE is distinguishable.
    * ``detail`` / ``message`` — the human sentence; the engine's own
      ``block_reasons`` are reproduced verbatim so a BLOCK is actionable.
    * ``exit_code`` — the code this process will exit with, so a consumer can
      branch on ``outcome`` without re-deriving it.

    Branch on ``outcome``, not on the exit code alone: a CI pipeline that treats
    exit 2 as "the model failed" will block good deploys on a misconfiguration.
    """
    resolved = _resolve_stored_run_id(run_id, latest, json_output=json_output)
    card = _require_stored(resolved, json_output=json_output).scorecard
    stored_threshold = float(card.threshold)
    effective = max(stored_threshold, threshold) if threshold >= 0 else stored_threshold
    engine_approved = card.deploy_decision == _APPROVED
    meets_threshold = float(card.composite_score) >= effective
    approved = engine_approved and meets_threshold

    if approved:
        outcome = _OUTCOME_APPROVED
        reason: str | None = None
    elif not engine_approved:
        # The engine refused on a recorded ground (insufficient evidence, an
        # adversarial-segment failure, ...). Reproduce those reasons so the
        # block is actionable rather than a bare "BLOCK".
        outcome = _OUTCOME_BLOCKED
        reason = "engine_blocked"
    else:
        # The engine approved but the operator raised --threshold above the
        # composite: a plain score miss.
        outcome = _OUTCOME_BLOCKED
        reason = "below_threshold"

    if not approved:
        if reason == "engine_blocked" and card.block_reasons:
            detail = "; ".join(card.block_reasons)
        elif reason == "below_threshold":
            detail = (
                f"composite {card.composite_score} is below the effective "
                f"threshold {effective}"
            )
        else:
            detail = f"stored verdict is {card.deploy_decision!r}"
    else:
        detail = f"composite {card.composite_score} meets threshold {effective}"

    verdict = _APPROVED if approved else _BLOCKED
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run_id": resolved,
                    "composite_score": card.composite_score,
                    "threshold": effective,
                    "decision": verdict,
                    "engine_decision": card.deploy_decision,
                    "outcome": outcome,
                    "reason": reason,
                    "detail": detail,
                    "message": detail,
                    "exit_code": 0 if approved else _GATE_BLOCKED_EXIT,
                }
            )
        )
    else:
        typer.echo(
            f"gate: {resolved} composite={card.composite_score} "
            f"threshold={effective} -> {verdict}"
        )
        if not approved:
            # A BLOCK with no stated reason is unactionable.
            typer.echo(f"  Reason: {reason}")
            typer.echo(f"  {detail}")
    if not approved:
        raise typer.Exit(code=_GATE_BLOCKED_EXIT)


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

    Targets the run via ``--run-id`` or ``--latest``. For ``--latest`` the
    target is resolved from the **store** (what delete actually mutates) rather
    than the local scorecard cache, so it keeps working when the cache is
    absent or stale. A missing/ambiguous flag pair, or an empty store and cache,
    aborts with exit code 2. The run is removed from the configured store
    (``VALIDSIM_STORE``); on success a confirmation is printed and the command
    exits 0, while an unknown run exits 2.
    """
    resolved = _resolve_deletable_run_id(run_id, latest)
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


def _failure_text(exc: BaseException) -> str:
    """Render an exception as one operator-readable line.

    The exception *type* is kept alongside the message because "could not
    connect" and "no such table" and "permission denied" are three different
    incidents with three different fixes, and ``str(exc)`` alone is often just
    a bare errno. Secrets are not a concern here: these are backend/transport
    errors raised by the driver, not credential values.
    """
    return f"{type(exc).__name__}: {exc}"


def _probe_store() -> tuple[str, int, str | None]:
    """Reach the configured validation store and count its runs.

    This is a real I/O probe, not a label guess: ``store.count()`` is the
    cheapest operation that actually touches the backend, so it is exactly
    where a dead ``postgres``/``sqlite`` store surfaces.

    Returns ``(backend_label, runs_stored, error_text)``. On failure the label
    is ``"error"``, the count is ``0`` and *error_text* carries the cause — the
    probe must never raise, because a probe that crashes reports nothing at all.
    """
    try:
        store = create_store()
        try:
            backend = _backend_label(store, _STORE_CLASS_SUFFIX, "memory")
            runs = store.count()
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        return "error", 0, _failure_text(exc)
    return backend, runs, None


def _probe_queue() -> tuple[str, str | None]:
    """Reach the configured job queue and return ``(backend_label, error_text)``.

    ``len(queue)`` is the minimal read that forces a real connection: for
    :class:`~validsim.jobs.queue.JobQueue` it is an in-process ``dict`` length
    (so the default backend is reachable by construction and can never fail the
    probe), while for
    :class:`~validsim.jobs.queue.RedisJobQueue` it issues an ``LLEN`` — a
    read-only command, so probing has no side effect on queue state.
    """
    try:
        queue = create_job_queue()
        try:
            backend = _backend_label(queue, _JOB_QUEUE_CLASS_SUFFIX, "memory")
            len(queue)
        finally:
            queue.close()
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        return "error", _failure_text(exc)
    return backend, None


@app.command()
def health(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the probe result as a single JSON object on stdout.",
    ),
) -> None:
    """Probe liveness and readiness of the configured store and job queue.

    Reads both dependencies *directly* — via
    :func:`validsim.store.create_store` and
    :func:`validsim.jobs.queue.create_job_queue`, exactly as the other
    commands do — rather than issuing an HTTP request to a running API. It
    reports the package ``version``, the store/queue backend labels (matching
    the API's ``store_backend`` / ``job_queue_backend`` fields) and the number
    of runs currently stored.

    Liveness vs readiness
    ---------------------
    **Liveness** is "this process is up and answered" — true whenever you are
    reading this output at all. It is reported as ``live`` in ``--json`` mode
    and is never false, because a probe that cannot answer has no way to say
    so.

    **Readiness** is "the configured durable infrastructure is actually
    reachable". Each dependency is genuinely exercised (``store.count()`` and
    ``len(queue)``), so a configured-but-unreachable ``postgres``/``sqlite``
    store or ``redis`` queue is detected.

    Exit codes
    ----------
    * ``0`` — live **and** ready: every configured dependency was reached.
    * ``1`` — live but **not ready**: at least one dependency could not be
      reached. The failing dependency and the transport-level cause are named.

    The ephemeral in-process defaults (in-memory store and queue) are the
    documented default and are reachable by construction, so they never fail
    readiness; an install that is merely *empty* is healthy, not broken.

    With ``--json`` the whole result is one JSON object: ``status``
    (``ok``/``not ready``), ``live``, ``ready``, ``version``,
    ``store_backend``, ``job_queue_backend``, ``runs_stored``, ``unready``
    (the names that failed) and ``errors``.
    """
    store_label, runs, store_error = _probe_store()
    queue_label, queue_error = _probe_queue()
    unready = [
        name
        for name, error in (("store", store_error), ("job_queue", queue_error))
        if error is not None
    ]
    ready = not unready
    errors = {
        name: error
        for name, error in (("store", store_error), ("job_queue", queue_error))
        if error is not None
    }

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "status": "ok" if ready else "not ready",
                    "live": True,
                    "ready": ready,
                    "version": __version__,
                    "store_backend": store_label,
                    "job_queue_backend": queue_label,
                    "runs_stored": runs,
                    "unready": unready,
                    "errors": errors,
                }
            )
        )
    else:
        typer.echo(f"ValidSim health: {'ok' if ready else 'not ready'}")
        typer.echo(f"  Version:           {__version__}")
        typer.echo(f"  Store backend:     {store_label}")
        typer.echo(f"  Job queue backend: {queue_label}")
        typer.echo(f"  Runs stored:       {runs}")
        if ready:
            return
        # Name both the failing dependency and the cause: a not-ready verdict
        # an operator cannot act on is as useless as the "ok" it replaced.
        typer.echo(f"  Unready:           {', '.join(unready)}", err=True)
        for name, error in errors.items():
            typer.echo(f"  {name} error: {error}", err=True)

    if not ready:
        raise typer.Exit(code=1)


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
    # Both resolvers read the same cache, so ``--baseline-latest
    # --candidate-latest`` legitimately resolves both sides to one run. That
    # self-comparison is not a harmless no-op: every delta is exactly 0.0, the
    # test is degenerate, and the command prints "Significant regressions: 0"
    # with p-value 1.000 -- a confident all-clear for a comparison that never
    # happened. Refuse it instead of emitting a fabricated clean bill of health.
    if baseline_id == candidate_id:
        raise typer.BadParameter(
            f"baseline and candidate are the same run ({candidate_id}); "
            "pass --baseline/--candidate with two distinct run ids"
        )
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
