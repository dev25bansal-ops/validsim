"""Pagination tests for the jobs list endpoint (``GET /jobs``).

The endpoint keeps its historical *bare array* contract when neither ``limit``
nor ``offset`` is supplied, and only switches to the paginated, newest-first
``{total, limit, offset, items}`` envelope (the same shape used by
``GET /api/v1/validations``) when at least one of those query params is given.

These cases pin both behaviors plus the slicing, defaults, and range
validation. They drive the standalone router (prefix ``/jobs``) — the same
``list_jobs`` handler that ``create_app`` mounts under ``/api/v1`` — and add a
couple of wired checks against the real app to prove the envelope survives the
``/api/v1`` prefix.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.jobs import JobQueue, router
from validsim.store.memory import ValidationStore

#: Env vars cleared so the wired app always uses an in-memory queue (never Redis).
_ENV_JOB_QUEUE = "VALIDSIM_JOB_QUEUE"
_ENV_REDIS_URL = "VALIDSIM_REDIS_URL"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic queue/redis environment for every test in this module."""
    monkeypatch.delenv(_ENV_JOB_QUEUE, raising=False)
    monkeypatch.delenv(_ENV_REDIS_URL, raising=False)


@pytest.fixture()
def client() -> TestClient:
    """Standalone router over a fresh in-memory queue (prefix ``/jobs``)."""
    app = FastAPI()
    app.state.job_queue = JobQueue()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def wired_client() -> TestClient:
    """Full app via ``create_app`` exposing the router under ``/api/v1/jobs``."""
    return TestClient(create_app(ValidationStore()))


def _enqueue(client: TestClient, path: str = "/jobs", **overrides: Any) -> str:
    """Enqueue one job and return its ``job_id`` (insertion order)."""
    body: dict[str, Any] = {"checkpoint_id": "ckpt-1", "task_id": "pick-place"}
    body.update(overrides)
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def _enqueue_many(client: TestClient, n: int, path: str = "/jobs") -> list[str]:
    """Enqueue ``n`` jobs, returning their ids in insertion (FIFO) order."""
    return [_enqueue(client, path) for _ in range(n)]


# ---------------------------------------------------------------------------
# Backwards-compatible bare array (no query params)
# ---------------------------------------------------------------------------


class TestBareArrayPreserved:
    def test_empty_queue_is_bare_array(self, client: TestClient) -> None:
        response = client.get("/jobs")
        assert response.status_code == 200
        assert response.json() == []

    def test_no_params_returns_fifo_array(self, client: TestClient) -> None:
        ids = _enqueue_many(client, 3)
        jobs = client.get("/jobs").json()
        assert isinstance(jobs, list)
        assert [job["job_id"] for job in jobs] == ids  # insertion order preserved


# ---------------------------------------------------------------------------
# Envelope activation & shape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    def test_limit_only_switches_to_envelope(self, client: TestClient) -> None:
        _enqueue_many(client, 2)
        body = client.get("/jobs", params={"limit": 10}).json()
        assert set(body) == {"total", "limit", "offset", "items"}
        assert body["total"] == 2
        assert body["limit"] == 10
        assert body["offset"] == 0  # default offset when only limit supplied
        assert isinstance(body["items"], list)

    def test_offset_only_switches_to_envelope(self, client: TestClient) -> None:
        _enqueue_many(client, 2)
        body = client.get("/jobs", params={"offset": 1}).json()
        assert set(body) == {"total", "limit", "offset", "items"}
        assert body["offset"] == 1
        assert body["limit"] == 100  # default limit when only offset supplied

    def test_items_are_record_dicts(self, client: TestClient) -> None:
        jid = _enqueue(client)
        body = client.get("/jobs", params={"limit": 5}).json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["job_id"] == jid
        assert item["status"] == "queued"
        assert item["spec"]["checkpoint_id"] == "ckpt-1"


# ---------------------------------------------------------------------------
# Ordering & slicing (newest-first)
# ---------------------------------------------------------------------------


class TestOrderingAndSlicing:
    def test_items_newest_first(self, client: TestClient) -> None:
        ids = _enqueue_many(client, 3)
        body = client.get("/jobs", params={"limit": 10}).json()
        assert [job["job_id"] for job in body["items"]] == list(reversed(ids))

    def test_limit_pages_from_newest(self, client: TestClient) -> None:
        ids = _enqueue_many(client, 5)
        newest_first = list(reversed(ids))
        body = client.get("/jobs", params={"limit": 2}).json()
        assert [job["job_id"] for job in body["items"]] == newest_first[:2]
        assert body["total"] == 5  # total independent of page size

    def test_offset_skips_newest(self, client: TestClient) -> None:
        ids = _enqueue_many(client, 5)
        newest_first = list(reversed(ids))
        body = client.get("/jobs", params={"limit": 2, "offset": 2}).json()
        assert [job["job_id"] for job in body["items"]] == newest_first[2:4]
        assert body["offset"] == 2

    def test_offset_beyond_total_is_empty_page(self, client: TestClient) -> None:
        _enqueue_many(client, 2)
        body = client.get("/jobs", params={"limit": 10, "offset": 50}).json()
        assert body["items"] == []
        assert body["total"] == 2

    def test_empty_queue_envelope(self, client: TestClient) -> None:
        body = client.get("/jobs", params={"limit": 10}).json()
        assert body == {"total": 0, "limit": 10, "offset": 0, "items": []}


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_limit_above_max_rejected(self, client: TestClient) -> None:
        assert client.get("/jobs", params={"limit": 501}).status_code == 422

    def test_limit_below_min_rejected(self, client: TestClient) -> None:
        assert client.get("/jobs", params={"limit": 0}).status_code == 422

    def test_limit_max_boundary_ok(self, client: TestClient) -> None:
        assert client.get("/jobs", params={"limit": 500}).status_code == 200

    def test_offset_negative_rejected(self, client: TestClient) -> None:
        assert client.get("/jobs", params={"offset": -1}).status_code == 422

    def test_offset_zero_boundary_ok(self, client: TestClient) -> None:
        assert client.get("/jobs", params={"offset": 0}).status_code == 200


# ---------------------------------------------------------------------------
# Wired under /api/v1 (proves the envelope survives the app prefix)
# ---------------------------------------------------------------------------


class TestWiredEnvelope:
    def test_wired_bare_array_without_params(self, wired_client: TestClient) -> None:
        assert wired_client.get("/api/v1/jobs").json() == []

    def test_wired_envelope_with_limit(self, wired_client: TestClient) -> None:
        ids = _enqueue_many(wired_client, 3, path="/api/v1/jobs")
        body = wired_client.get("/api/v1/jobs", params={"limit": 2}).json()
        assert set(body) == {"total", "limit", "offset", "items"}
        assert body["total"] == 3
        assert [job["job_id"] for job in body["items"]] == list(reversed(ids))[:2]
