"""ValidSim command-line interface.

``run`` executes a validation locally against the mock backend, persists the
full run to the configured validation store (``VALIDSIM_STORE``; see
:func:`validsim.store.create_store`), and caches the scorecard as JSON (path
overridable via ``VALIDSIM_CACHE_FILE``) so follow-up commands ``status`` /
``scorecard`` / ``gate`` can inspect it across processes. Those three commands
target a run via either ``--run-id`` or ``--latest`` (the newest cached run);
exactly one of the two is required.
``gate`` exits non-zero on ``BLOCK``, making it directly usable in CI.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import typer

from validsim import __version__
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
from validsim.scenarios.generator import ScenarioGenerator
from validsim.sim.runner import MockIsaacBackend, run_validation, stable_seed
from validsim.store import create_store
from validsim.store.memory import StoredRun, ValidationStore

app = typer.Typer(
    name="validsim",
    help="ValidSim — Sim-to-Real CI/CD validation for robot foundation models.",
    no_args_is_help=True,
    add_completion=False,
)

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


def _persist_to_store(run: StoredRun) -> None:
    """Save the finished run to the configured validation store.

    Uses :func:`validsim.store.create_store`, so ``VALIDSIM_STORE=sqlite``
    makes CLI runs visible to the API/dashboard; the default in-memory
    backend keeps local runs side-effect free.
    """
    store = create_store()
    try:
        store.save(run)
    finally:
        store.close()


@app.command()
def run(
    task: str = typer.Option("pick-place", "--task", "-t", help="Task identifier."),
    robot: str = typer.Option("franka_panda", "--robot", "-r", help="Robot name."),
    episodes: int = typer.Option(500, "--episodes", "-e", min=1, help="Nominal episodes."),
    adversarial: int = typer.Option(24, "--adversarial", "-a", min=0, help="Adversarial scenarios."),
    checkpoint: str = typer.Option("local-checkpoint", "--checkpoint", "-c", help="Checkpoint id."),
    threshold: float = typer.Option(85.0, "--threshold", help="Composite score to approve."),
) -> None:
    """Run a local validation against the mock backend and print a summary."""
    config = TaskConfig(
        task_id=task,
        robot=RobotSpec(name=robot),
        environment=EnvironmentSpec(name="mock-scene"),
        episodes=episodes,
        adversarial_count=adversarial,
    )
    seed = stable_seed(checkpoint, task)
    scenarios = ScenarioGenerator(seed=seed).generate(task, adversarial)
    episodes_out = run_validation(config, MockIsaacBackend(), scenarios, seed=seed)

    evaluation = evaluate(episodes_out)
    safety = compute_safety(episodes_out)
    run_id = ValidationStore.new_run_id()
    scorecard = build_scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint,
        task=config,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes_out,
        threshold=threshold,
    )
    _save_to_cache(scorecard.to_dict())
    _persist_to_store(
        StoredRun(
            run_id=run_id,
            checkpoint_id=checkpoint,
            task_id=config.task_id,
            created_at=scorecard.created_at,
            scorecard=scorecard,
            evaluation=evaluation,
            safety=safety,
            episodes=episodes_out,
        )
    )

    top_failures = ", ".join(
        f"{mode}={count}" for mode, count in list(scorecard.failure_taxonomy.items())[:3]
    ) or "none"
    typer.echo("ValidSim validation complete")
    typer.echo(f"  Version:       {__version__}")
    typer.echo(f"  Run ID:        {scorecard.run_id}")
    typer.echo(f"  Checkpoint:    {scorecard.checkpoint_id}")
    typer.echo(f"  Task:          {scorecard.task_id} ({episodes} nominal + {adversarial} adversarial)")
    typer.echo(f"  Success rate:  {scorecard.success_rate:.1%}")
    typer.echo(f"  Safety score:  {scorecard.safety_score:.1f}")
    typer.echo(f"  Robustness:    {scorecard.robustness_score:.1f}")
    typer.echo(f"  Composite:     {scorecard.composite_score:.1f} (threshold {threshold:.1f})")
    typer.echo(f"  Decision:      {scorecard.deploy_decision}")
    typer.echo(f"  Top failures:  {top_failures}")


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
def gate(
    run_id: str | None = typer.Option(None, "--run-id", help="Run id to gate on."),
    latest: bool = typer.Option(
        False, "--latest", help="Use the newest cached run instead of --run-id."
    ),
    threshold: float = typer.Option(
        -1.0, "--threshold", help="Override the stored approval threshold."
    ),
) -> None:
    """Exit non-zero (1) when the run's composite score is below threshold."""
    resolved = _resolve_run_id(run_id, latest)
    scorecard_dict = _require_cached(resolved)
    effective = scorecard_dict["threshold"] if threshold < 0 else threshold
    approved = float(scorecard_dict["composite_score"]) >= float(effective)
    verdict = "APPROVE" if approved else "BLOCK"
    typer.echo(
        f"gate: {resolved} composite={scorecard_dict['composite_score']} "
        f"threshold={effective} -> {verdict}"
    )
    if not approved:
        raise typer.Exit(code=1)


def main() -> None:  # pragma: no cover - console entry point
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["app", "main", "_RUN_ID_RE"]
