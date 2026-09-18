"""ValidSim Dashboard API (Week-7 MVP).

Serves the single-page dashboard (``validsim/web``) from the existing FastAPI
app — no Node/npm build step. The data endpoints resolve the *same* store
instance that ``create_app`` puts on ``app.state`` (via ``Request``), so the
dashboard never creates a second store and stays compatible with the
in-memory and SQLite backends alike.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from validsim.store.memory import ValidationStore

#: SPA assets live next to the package: ``validsim/web``.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
#: Dashboard entry page served at ``/`` and ``/api/v1/dashboard/``.
INDEX_PATH = WEB_DIR / "index.html"

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def get_store(request: Request) -> ValidationStore:
    """Dependency resolving the app-level store (same instance as main.py's)."""
    store: ValidationStore = request.app.state.store
    return store


@router.get("/history")
def dashboard_history(store: ValidationStore = Depends(get_store)) -> list[dict[str, Any]]:
    """All stored runs, newest first, as compact summary dicts."""
    return [run.summary() for run in reversed(store.history())]


@router.get("/summary")
def dashboard_summary(store: ValidationStore = Depends(get_store)) -> dict[str, Any]:
    """Gate-level counts for the KPI strip.

    ``avg_composite`` is ``None`` when no runs have been recorded yet.
    """
    runs = store.history()
    total = len(runs)
    approvals = sum(1 for r in runs if r.scorecard.deploy_decision == "APPROVE")
    composites = [r.scorecard.composite_score for r in runs]
    return {
        "total_runs": total,
        "approvals": approvals,
        "blocks": total - approvals,
        "avg_composite": round(sum(composites) / total, 2) if total else None,
    }


@router.get("/", include_in_schema=False)
def dashboard_page() -> FileResponse:
    """The dashboard SPA, also reachable under the API prefix."""
    return FileResponse(INDEX_PATH, media_type="text/html")


def mount_dashboard(application: FastAPI) -> None:
    """Wire the dashboard into a ValidSim app in a single call.

    Adds the ``/api/v1/dashboard`` API router, a ``/static`` mount for the
    SPA assets, and a ``/`` route serving the dashboard page. Existing API
    endpoints are untouched.
    """
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="validsim-web")

    @application.get("/", include_in_schema=False)
    def dashboard_root() -> FileResponse:
        """Site root serves the dashboard single-page app."""
        return FileResponse(INDEX_PATH, media_type="text/html")


__all__ = ["router", "mount_dashboard", "get_store", "WEB_DIR", "INDEX_PATH"]
