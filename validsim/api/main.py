"""ValidSim REST API (MVP).

Runs validation synchronously against the mock backend — appropriate for the
MVP's episode counts. The store is injected via ``app.state`` so tests (and a
future async worker pool) can supply their own instances.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from validsim import __version__
from validsim.config import ValidationRequest
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import compare
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
from validsim.scenarios.generator import ScenarioGenerator
from validsim.sim.runner import MockIsaacBackend, run_validation, stable_seed
from validsim.store import create_store
from validsim.store.memory import StoredRun, ValidationStore


class CompareRequest(BaseModel):
    """Body for POST /validations/{run_id}/compare."""

    baseline_id: str


def _execute_validation(request: ValidationRequest, store: ValidationStore) -> StoredRun:
    """Run the full mock pipeline for one request and persist the result."""
    task = request.task
    seed = stable_seed(request.checkpoint_id, task.task_id)
    scenarios = ScenarioGenerator(seed=seed).generate(task.task_id, task.adversarial_count)
    backend = MockIsaacBackend()
    episodes = run_validation(task, backend, scenarios, seed=seed)

    evaluation = evaluate(episodes)
    safety = compute_safety(episodes)

    baseline = store.get(request.baseline_run_id) if request.baseline_run_id else None
    if request.baseline_run_id and baseline is None:
        raise HTTPException(status_code=404, detail=f"baseline run {request.baseline_run_id} not found")
    regression = compare(evaluation, baseline.evaluation, seed=seed) if baseline else None

    run_id = ValidationStore.new_run_id()
    scorecard = build_scorecard(
        run_id=run_id,
        checkpoint_id=request.checkpoint_id,
        task=task,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        regression=regression,
    )
    return store.save(
        StoredRun(
            run_id=run_id,
            checkpoint_id=request.checkpoint_id,
            task_id=task.task_id,
            created_at=scorecard.created_at,
            scorecard=scorecard,
            evaluation=evaluation,
            safety=safety,
            episodes=episodes,
            baseline_run_id=request.baseline_run_id,
            regression=regression,
        )
    )


def _require_run(store: ValidationStore, run_id: str) -> StoredRun:
    """Fetch a run or raise 404."""
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


def create_app(store: ValidationStore | None = None) -> FastAPI:
    """Build the FastAPI app, optionally with an injected store (for tests)."""
    application = FastAPI(
        title="ValidSim API",
        version=__version__,
        description="Sim-to-Real CI/CD validation for robot foundation models.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # dev-only: allow all origins
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.store = store if store is not None else create_store()

    def get_store() -> ValidationStore:
        """Dependency providing the app's validation store."""
        return application.state.store  # type: ignore[return-value]

    @application.get("/api/v1/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "version": __version__}

    @application.post("/api/v1/validations", status_code=201)
    def create_validation(
        request: ValidationRequest, st: ValidationStore = Depends(get_store)
    ) -> dict[str, Any]:
        """Run a validation synchronously and return its scorecard."""
        return _execute_validation(request, st).scorecard.to_dict()

    @application.get("/api/v1/validations/{run_id}")
    def get_validation(run_id: str, st: ValidationStore = Depends(get_store)) -> dict[str, Any]:
        """Run summary (no episode payloads)."""
        return _require_run(st, run_id).summary()

    @application.get("/api/v1/validations/{run_id}/scorecard")
    def get_scorecard(run_id: str, st: ValidationStore = Depends(get_store)) -> JSONResponse:
        """Full scorecard JSON for a run."""
        run = _require_run(st, run_id)
        return JSONResponse(content=run.scorecard.to_dict())

    @application.get("/api/v1/validations/{run_id}/failures")
    def get_failures(run_id: str, st: ValidationStore = Depends(get_store)) -> dict[str, Any]:
        """Failure taxonomy plus per-failure episode details."""
        run = _require_run(st, run_id)
        failed = [asdict(e) for e in run.episodes if not e.success]
        return {
            "run_id": run.run_id,
            "failure_taxonomy": run.evaluation.failure_taxonomy,
            "episodes": failed,
        }

    @application.post("/api/v1/validations/{run_id}/compare")
    def compare_runs(
        run_id: str,
        body: CompareRequest,
        st: ValidationStore = Depends(get_store),
    ) -> dict[str, Any]:
        """Ad-hoc regression comparison of two stored runs.

        The permutation-test seed is derived from the run/baseline id pair
        (``stable_seed``), so the same comparison is reproducible across
        calls and processes.
        """
        current = _require_run(st, run_id)
        baseline = _require_run(st, body.baseline_id)
        report = compare(
            current.evaluation,
            baseline.evaluation,
            seed=stable_seed(run_id, body.baseline_id),
        )
        return {
            "run_id": current.run_id,
            "baseline_id": baseline.run_id,
            **report.to_dict(),
        }

    @application.get("/api/v1/regressions")
    def list_regressions(st: ValidationStore = Depends(get_store)) -> list[dict[str, Any]]:
        """Runs whose stored comparison flagged significant regressions."""
        out: list[dict[str, Any]] = []
        for run in st.history():
            if run.regression is not None and run.regression.has_regressions:
                out.append({**run.summary(), "regression": run.regression.to_dict()})
        return out

    from validsim.api.dashboard import mount_dashboard; mount_dashboard(application)  # dashboard: router + /static + /

    return application


app = create_app()

__all__ = ["app", "create_app", "CompareRequest"]
