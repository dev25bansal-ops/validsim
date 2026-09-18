"""FastAPI service exposing the ValidSim validation pipeline."""

from __future__ import annotations

from validsim.api.main import app, create_app

__all__ = ["app", "create_app"]
