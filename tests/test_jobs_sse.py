"""Tests for the compact status + Server-Sent-Events endpoints on the router.

The status endpoint is exercised through the ordinary TestClient request path.
The events endpoint streams, so every test pre-seeds the queue with a job that
is already terminal (``done``) — the generator emits one ``data:`` frame and an
``event: end`` terminator on its very first poll and returns before any sleep,
so the response body is fully consumed quickly without a live worker.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim.jobs import JobQueue, JobStatus, router


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Standalone app with the jobs router and an injected in-memory queue."""
    app = FastAPI()
    app.state.job_queue = JobQueue()
    app.include_router(router)
    return TestClient(app)


def _enqueue(client: TestClient, **overrides: Any) -> str:
    """Enqueue a job via POST /jobs and return its id."""
    body: dict[str, Any] = {
        "checkpoint_id": "ckpt-alpha",
        "task_id": "pick-place",
        "episodes": 60,
        "adversarial": 12,
    }
    body.update(overrides)
    response = client.post("/jobs", json=body)
    assert response.status_code == 202
    return str(response.json()["job_id"])


def _queue(client: TestClient) -> JobQueue:
    return client.app.state.job_queue  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/status
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    def test_returns_compact_payload(self, client: TestClient) -> None:
        jid = _enqueue(client)
        response = client.get(f"/jobs/{jid}/status")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"job_id", "status", "updated_at"}
        assert body["job_id"] == jid
        assert body["status"] == "queued"
        # A freshly enqueued job has created_at -> surfaced as updated_at.
        assert body["updated_at"] is not None

    def test_reflects_terminal_transition(self, client: TestClient) -> None:
        jid = _enqueue(client)
        _queue(client).update_status(jid, JobStatus.DONE, result=jid)
        body = client.get(f"/jobs/{jid}/status").json()
        assert body["status"] == "done"
        assert body["updated_at"] is not None

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/vrun-ffffffff/status").status_code == 404


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/events (SSE)
# ---------------------------------------------------------------------------


class TestEventsEndpoint:
    def test_stream_terminates_on_done_job(self, client: TestClient) -> None:
        jid = _enqueue(client)
        _queue(client).update_status(jid, JobStatus.DONE, result=jid)

        response = client.get(f"/jobs/{jid}/events")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        body = response.text
        # One data frame describing the terminal status, then the terminator.
        assert body.startswith("data: ")
        assert '"status": "done"' in body
        assert jid in body
        assert "event: end" in body
        # The end event is the final frame — the stream terminated.
        assert body.rstrip().endswith("event: end")

    def test_stream_terminates_on_failed_job(self, client: TestClient) -> None:
        jid = _enqueue(client)
        _queue(client).update_status(jid, JobStatus.FAILED, error="boom")

        response = client.get(f"/jobs/{jid}/events")
        assert response.status_code == 200
        body = response.text
        assert '"status": "failed"' in body
        assert body.rstrip().endswith("event: end")

    def test_unknown_job_is_404_before_streaming(self, client: TestClient) -> None:
        assert client.get("/jobs/vrun-ffffffff/events").status_code == 404
