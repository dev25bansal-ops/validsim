"""Overload behaviour: the sliding-window rate limiter and the queue depth cap.

These are assertions, not timings - the point is that the 429/503 contract
holds under a burst and that backpressure is observable. They live in the
benchmark suite because load-shedding is half the performance story and should
run whenever the benchmark gate runs.
"""

from __future__ import annotations

import os

import pytest

from validsim.api.main import _SlidingWindowRateLimiter
from validsim.jobs.models import JobSpec, JobStatus
from validsim.jobs.queue import JobQueue, QueueFullError



# --------------------------------------------------------------------------
# rate limiter
# --------------------------------------------------------------------------
def test_rate_limiter_burst_returns_429_after_limit() -> None:
    """A burst beyond the window limit is denied with a Retry-After hint."""
    limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=60.0)
    for i in range(5):
        assert limiter.check("1.2.3.4") is None, f"request {i} should be allowed"
    retry = limiter.check("1.2.3.4")
    assert retry is not None, "the 6th request in the window must be denied"
    assert retry >= 1


def test_rate_limiter_window_slides() -> None:
    """After the window elapses the client is allowed again."""
    limiter = _SlidingWindowRateLimiter(limit=5, window_seconds=10.0)
    for i in range(5):
        assert limiter.check("1.2.3.4", now=0.0) is None, f"request {i} should fit"
    # The window is now full, so the next one is denied even though it is only
    # half a second later.
    assert limiter.check("1.2.3.4", now=0.5) is not None
    # Once the window has slid past, the same client is allowed again.
    assert limiter.check("1.2.3.4", now=11.0) is None


def test_rate_limiter_working_set_is_bounded() -> None:
    """A flood of one-shot client IPs cannot grow the table without limit."""
    limiter = _SlidingWindowRateLimiter(limit=60, window_seconds=60.0, max_buckets=1_000)
    for i in range(50_000):
        limiter.check(f"10.0.{i // 65536}.{i % 256}")
    assert len(limiter._hits) <= limiter.max_buckets


# --------------------------------------------------------------------------
# queue depth cap / 503
# --------------------------------------------------------------------------
def test_queue_rejects_beyond_max_depth() -> None:
    """Enqueue past max_depth raises QueueFullError (the router maps it to 503)."""
    queue = JobQueue(max_depth=3, lease_seconds=300)
    for i in range(3):
        queue.enqueue(
            JobSpec(
                run_id=f"vrun-{i:08x}", checkpoint_id="c", task_id="t",
                episodes=1, adversarial=0,
            )
        )
    with pytest.raises(QueueFullError):
        queue.enqueue(
            JobSpec(
                run_id="vrun-99999999", checkpoint_id="c", task_id="t",
                episodes=1, adversarial=0,
            )
        )


# The depth cap is measured against *pending* work, not retained records, so a
def test_queue_accepts_work_after_full_drain() -> None:
    """A fully-drained queue must accept new work.

    Records are retained after a job finishes, so a depth cap counted over all
    of them would leave the queue permanently full once the backlog drains.
    """
    queue = JobQueue(max_depth=3, lease_seconds=300)
    for i in range(3):
        queue.enqueue(
            JobSpec(
                run_id=f"vrun-{i:08x}", checkpoint_id="c", task_id="t",
                episodes=1, adversarial=0,
            )
        )
    for record in queue.list():
        queue.update_status(record.job_id, JobStatus.DONE, result=record.job_id)
    queue.enqueue(
        JobSpec(run_id="vrun-000000ff", checkpoint_id="c", task_id="t", episodes=1, adversarial=0)
    )


def test_queue_503_served_by_http_route() -> None:
    """The HTTP surface returns 503 (not 500) with Retry-After when full."""
    from fastapi.testclient import TestClient

    from validsim.api.main import create_app
    from validsim.store.memory import ValidationStore

    os.environ["VALIDSIM_RATE_LIMIT"] = "0"
    try:
        app = create_app(store=ValidationStore())
        app.state.job_queue = JobQueue(max_depth=2, lease_seconds=300)
        with TestClient(app) as client:
            body = {"checkpoint_id": "ck", "task_id": "tk", "episodes": 1}
            assert client.post("/api/v1/jobs", json=body).status_code == 202
            assert client.post("/api/v1/jobs", json=body).status_code == 202
            full = client.post("/api/v1/jobs", json=body)
            assert full.status_code == 503
            assert full.headers.get("Retry-After")
    finally:
        os.environ.pop("VALIDSIM_RATE_LIMIT", None)
