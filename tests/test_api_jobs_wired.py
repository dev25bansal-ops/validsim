"""Tests that the async jobs router is correctly wired into the main API app.

``create_app`` installs the app-level ``X-API-Key`` dependency on the router
and *then* includes :data:`validsim.jobs.router` under the ``/api/v1`` prefix
(the router's own prefix is ``/jobs``), so ``/api/v1/jobs`` must:

* behave like the standalone router (202 enqueue, list, get, 404 for unknown);
* inherit the same optional auth gate as every other ``/api/v1`` route, which
  proves the app-level dependency covers the included router.

Each case builds a fresh app via ``create_app`` (its own in-memory job queue
and validation store), reusing the env-fixture pattern from
``tests/test_api_auth.py``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import API_KEY_HEADER, create_app
from validsim.store.memory import ValidationStore

ENV_KEY = "VALIDSIM_API_KEY"
#: Cleared so ``create_job_queue`` always builds the in-memory backend (never Redis).
ENV_JOB_QUEUE = "VALIDSIM_JOB_QUEUE"
ENV_REDIS_URL = "VALIDSIM_REDIS_URL"

#: Env vars this suite owns; popped before each test and restored afterwards.
_MANAGED_ENV = (ENV_KEY, ENV_JOB_QUEUE, ENV_REDIS_URL)


@pytest.fixture()
def clean_env() -> None:
    """Isolate auth + job-queue env so no value leaks between tests."""
    saved = {name: os.environ.get(name) for name in _MANAGED_ENV}
    for name in _MANAGED_ENV:
        os.environ.pop(name, None)
    yield  # type: ignore[misc]
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _client() -> TestClient:
    """Fresh TestClient over an app with its own store and in-memory queue."""
    return TestClient(create_app(ValidationStore()))


def _job_body(**overrides: Any) -> dict[str, Any]:
    """Minimal valid body for POST /api/v1/jobs, with optional overrides."""
    body: dict[str, Any] = {"checkpoint_id": "ckpt-wired", "task_id": "pick-place"}
    body.update(overrides)
    return body


class TestJobsWiredOpen:
    """With auth disabled the wired router behaves like the standalone one."""

    def test_enqueue_returns_202_queued(self, clean_env: None) -> None:
        client = _client()
        response = client.post("/api/v1/jobs", json=_job_body())
        assert response.status_code == 202
        payload = response.json()
        assert payload["job_id"].startswith("vrun-")
        assert payload["status"] == "queued"

    def test_list_includes_enqueued(self, clean_env: None) -> None:
        client = _client()
        job_id = client.post("/api/v1/jobs", json=_job_body()).json()["job_id"]
        response = client.get("/api/v1/jobs")
        assert response.status_code == 200
        listed = response.json()
        assert any(job["job_id"] == job_id for job in listed)

    def test_get_returns_200(self, clean_env: None) -> None:
        client = _client()
        job_id = client.post("/api/v1/jobs", json=_job_body()).json()["job_id"]
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        record = response.json()
        assert record["job_id"] == job_id
        assert record["status"] == "queued"
        assert record["spec"]["checkpoint_id"] == "ckpt-wired"

    def test_get_unknown_returns_404(self, clean_env: None) -> None:
        client = _client()
        assert client.get("/api/v1/jobs/vrun-ffffffff").status_code == 404


class TestJobsWiredAuth:
    """The app-level X-API-Key gate covers the included jobs router."""

    def test_missing_key_401_on_post(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.post("/api/v1/jobs", json=_job_body()).status_code == 401

    def test_missing_key_401_on_list(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get("/api/v1/jobs").status_code == 401

    def test_valid_key_202_on_post(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.post(
            "/api/v1/jobs", json=_job_body(), headers={API_KEY_HEADER: "secret-key"}
        )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"

    def test_valid_key_enables_readback(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        headers = {API_KEY_HEADER: "secret-key"}
        client = _client()
        job_id = client.post(
            "/api/v1/jobs", json=_job_body(), headers=headers
        ).json()["job_id"]
        assert client.get(f"/api/v1/jobs/{job_id}", headers=headers).status_code == 200
