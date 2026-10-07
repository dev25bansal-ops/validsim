"""ValidSim Dashboard API.

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

    # The site root must be reachable WITHOUT an API key, and it is the only
    # unauthenticated surface that can explain *why* a key is needed: the SPA
    # boots, reads the 401 from /api/v1/summary, and renders its auth-required
    # panel. If the gate is inherited here, a browser hitting "/" gets a bare
    # 401 JSON body, no page loads, and that panel can never execute -- the
    # operator is left with no way to authenticate at all.
    #
    # The gate lives in ``application.router.dependencies`` and FastAPI merges
    # router-level dependencies into each route when it is registered, so the
    # only way to serve "/" unauthenticated is to register it while that list
    # is empty and restore it immediately. This mirrors exactly what
    # ``create_app`` already does for the un-gated ``/metrics`` mount.
    saved_dependencies = application.router.dependencies
    application.router.dependencies = []
    try:

        @application.get("/", include_in_schema=False)
        def dashboard_root() -> FileResponse:
            """Site root serves the dashboard single-page app.

            Deliberately outside the ``X-API-Key`` gate, and this is a
            *reviewed* exemption rather than an accident. It is the only
            unauthenticated surface that can explain **why** a key is needed:
            the SPA boots, reads the 401 from its first ``/api/v1`` call, and
            renders its auth-required panel. Gate this route and a browser
            receives bare 401 JSON, no page loads, and the panel can never
            execute -- leaving the operator no way to authenticate at all.

            The exemption discloses **no run data**: it is a static HTML shell.
            Every ``/api/v1`` route, ``/api/v1/dashboard/summary`` and the run
            history included, still requires the key, and ``validsim/web/static``
            is presentation code only. This is the same trade already accepted
            for ``/metrics``, on the same reasoning: an unauthenticated surface
            that carries no data, reachable so the authenticated one can be used.

            Do not add endpoints here. A route that reaches the store belongs
            behind the gate.
            """
            return FileResponse(INDEX_PATH, media_type="text/html")

    finally:
        application.router.dependencies = saved_dependencies


__all__ = ["router", "mount_dashboard", "get_store", "WEB_DIR", "INDEX_PATH"]
