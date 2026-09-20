"""Tests for the queue-full (503) path of ``POST /jobs``.

The async job queue is bounded by ``max_depth`` (audit H3). Once it is full,
:meth:`~validsim.jobs.queue.JobQueue.enqueue` raises
:class:`~validsim.jobs.queue.QueueFullError`. Before the router fix that
exception bubbled up as an unhandled ``500``; a saturated queue is an expected
overload condition, so ``POST /jobs`` must instead answer ``503 Service
Unavailable`` carrying a JSON body ``{"error": "queue_full", "max_depth": N}``
and a ``Retry-After`` header.

These tests drive the router over the real in-memory backend with a tiny cap so
the boundary (last accepted job vs. the first rejected one) is exercised
end-to-end.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim.jobs import JobQueue, router

# Cap used by the shared ``client`` fixture: small enough that "one past the
# limit" is reached in two requests, large enough to prove the boundary.
_CAP = 2


def _app(max_depth: int) -> FastAPI:
    """Standalone app with the jobs router and a bounded in-memory queue."""
    app = FastAPI()
    app.state.job_queue = JobQueue(max_depth=max_depth)
    app.include_router(router)
    return app


@pytest.fixture()
def client() -> TestClient:
    """TestClient over a memory-backend queue capped at :data:`_CAP`."""
    return TestClient(_app(max_depth=_CAP))


def _post(client: TestClient):
    """One enqueue request with a minimal valid body."""
    return client.post(
        "/jobs",
        json={"checkpoint_id": "ckpt-a", "task_id": "pick", "episodes": 5},
    )


def _fill_to_cap(client: TestClient) -> None:
    """Enqueue exactly ``max_depth`` jobs so the queue is full."""
    for _ in range(_CAP):
        assert _post(client).status_code == 202


# ---------------------------------------------------------------------------
# Happy path: enqueue up to the cap is accepted (202), not rejected.
# ---------------------------------------------------------------------------


def test_enqueue_up_to_cap_is_accepted(client: TestClient) -> None:
    """Every job up to ``max_depth`` returns 202 with an id and status."""
    for _ in range(_CAP):
        response = _post(client)
        assert response.status_code == 202
        body = response.json()
        assert body["job_id"].startswith("vrun-")
        assert body["status"] == "queued"


# ---------------------------------------------------------------------------
# Boundary: one past the cap is 503, never 500.
# ---------------------------------------------------------------------------


def test_one_past_cap_is_503_not_500(client: TestClient) -> None:
    """The first request beyond ``max_depth`` is a 503, not an unhandled 500."""
    _fill_to_cap(client)
    response = _post(client)
    assert response.status_code == 503
    assert response.status_code != 500


def test_queue_full_body_reports_error_and_max_depth(client: TestClient) -> None:
    """The 503 body is the documented ``{"error", "max_depth"}`` shape."""
    _fill_to_cap(client)
    response = _post(client)
    assert response.status_code == 503
    assert response.json() == {"error": "queue_full", "max_depth": _CAP}


def test_retry_after_header_present(client: TestClient) -> None:
    """The 503 carries a parseable, positive ``Retry-After`` hint."""
    _fill_to_cap(client)
    response = _post(client)
    assert response.status_code == 503
    header_names = {name.lower() for name in response.headers}
    assert "retry-after" in header_names
    assert int(response.headers["retry-after"]) >= 1


def test_rejected_request_does_not_grow_queue(client: TestClient) -> None:
    """A rejected enqueue is side-effect free: the queue stays at the cap."""
    queue: JobQueue = client.app.state.job_queue  # type: ignore[assignment]
    _fill_to_cap(client)
    assert len(queue) == _CAP
    assert _post(client).status_code == 503
    assert len(queue) == _CAP
    assert len(client.get("/jobs").json()) == _CAP


# ---------------------------------------------------------------------------
# A tiny memory backend (cap == 1) rejects the very second request.
# ---------------------------------------------------------------------------


def test_tiny_memory_backend_cap_one() -> None:
    """With ``max_depth=1`` the first job is accepted, the second is 503."""
    client = TestClient(_app(max_depth=1))
    assert _post(client).status_code == 202
    response = _post(client)
    assert response.status_code == 503
    assert response.json() == {"error": "queue_full", "max_depth": 1}
    assert "retry-after" in {name.lower() for name in response.headers}
