"""End-to-end test for the asynchronous validation pipeline.

This exercises the *whole* async path through real HTTP handlers — no route
functions are called directly:

1. ``POST /api/v1/jobs`` enqueues a validation on a memory
   :class:`~validsim.jobs.queue.JobQueue` injected via ``app.state.job_queue``.
2. :class:`~validsim.jobs.worker.JobWorker` (sharing that exact queue and a
   memory :class:`~validsim.store.memory.ValidationStore` injected via
   ``app.state.store``) drains the queue with :meth:`run_once`, running the
   same engine pipeline the synchronous API uses and persisting a
   :class:`~validsim.store.memory.StoredRun`.
3. ``GET /api/v1/validations/{job_id}`` and ``.../scorecard`` confirm the run
   was persisted to the shared store and is fetchable over HTTP.
4. ``GET /api/v1/jobs/{job_id}`` confirms the job reached the terminal
   ``done`` state with ``result`` pointing at the persisted ``run_id``.

The queue and the store are the *same* objects handed to both the app (via
``app.state``) and the worker, which is the contract that makes the async
pipeline coherent: the job's ``run_id`` is the store key the worker writes to,
so the result pointer resolves through the read endpoints.

Nothing here mutates source: the app is built with ``create_app()`` and the
``store``/``job_queue`` are swapped onto ``app.state`` afterwards (the documented
injection seam). The default backend is pinned to the deterministic mock by
injecting :class:`~validsim.sim.runner.MockIsaacBackend` explicitly and by
clearing ``VALIDSIM_BACKEND`` so a GPU worker is never selected.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.jobs import JobQueue, JobStatus, JobWorker
from validsim.sim.runner import MockIsaacBackend
from validsim.store.memory import ValidationStore

#: Env vars this suite owns; cleared before each test and restored afterwards so
#: no leftover deployment config (Redis queue, GPU backend, API-key auth) can
#: leak into the happy path.
_MANAGED_ENV = (
    "VALIDSIM_API_KEY",
    "VALIDSIM_JOB_QUEUE",
    "VALIDSIM_REDIS_URL",
    "VALIDSIM_BACKEND",
)


@pytest.fixture()
def clean_env() -> Iterator[None]:
    """Isolate the async pipeline's env so every run uses memory + mock."""
    saved = {name: os.environ.get(name) for name in _MANAGED_ENV}
    for name in _MANAGED_ENV:
        os.environ.pop(name, None)
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _job_body(**overrides: Any) -> dict[str, Any]:
    """Minimal valid body for POST /api/v1/jobs (small episode count = fast)."""
    body: dict[str, Any] = {
        "checkpoint_id": "ckpt-e2e",
        "task_id": "pick-place",
        "episodes": 20,
        "adversarial": 0,
    }
    body.update(overrides)
    return body


class _Harness:
    """Bundles a freshly-wired app, its TestClient, and the shared queue/store.

    The queue and store are constructed here and injected onto ``app.state`` so
    the HTTP handlers and the worker operate on the *same* instances — the
    essential property the end-to-end flow depends on.
    """

    def __init__(self) -> None:
        self.queue = JobQueue()
        self.store = ValidationStore()
        self.app: FastAPI = create_app()
        # Inject the shared collaborators via the documented app.state seam.
        self.app.state.store = self.store
        self.app.state.job_queue = self.queue
        self.client = TestClient(self.app)

    def worker(self) -> JobWorker:
        """A worker wired to this harness's queue/store on the mock backend."""
        return JobWorker(self.queue, self.store, backend=MockIsaacBackend())


@pytest.fixture()
def harness(clean_env: None) -> _Harness:
    """Build the app + queue + store + client for one test."""
    return _Harness()


def _enqueue(client: TestClient, **overrides: Any) -> str:
    """POST a job through HTTP and return its ``job_id`` (asserting 202)."""
    response = client.post("/api/v1/jobs", json=_job_body(**overrides))
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    job_id = payload["job_id"]
    assert job_id.startswith("vrun-")
    return job_id


class TestAsyncValidationEndToEnd:
    """The full enqueue -> worker -> fetch happy path through real handlers."""

    def test_enqueue_run_and_fetch(self, harness: _Harness) -> None:
        client = harness.client

        # 1. Enqueue a validation via the HTTP router.
        job_id = _enqueue(client)

        # Before the worker runs, the read endpoints know nothing about it: the
        # job is queued but no run has been persisted yet.
        assert client.get(f"/api/v1/validations/{job_id}").status_code == 404
        pre = client.get(f"/api/v1/jobs/{job_id}")
        assert pre.status_code == 200
        assert pre.json()["status"] == "queued"
        assert pre.json()["result"] is None

        # 2. Drain the queue with the worker (one job -> run_once).
        done = harness.worker().run_once()
        assert done is not None
        assert done.status is JobStatus.DONE
        # The result pointer is the persisted run id, shared with the job id.
        assert done.result == job_id

        # 3. The finished run is persisted in the shared store and fetchable.
        summary_resp = client.get(f"/api/v1/validations/{job_id}")
        assert summary_resp.status_code == 200
        summary = summary_resp.json()
        assert summary["run_id"] == job_id
        assert summary["checkpoint_id"] == "ckpt-e2e"
        assert summary["task_id"] == "pick-place"
        assert summary["episode_count"] == 20
        assert isinstance(summary["composite_score"], (int, float))
        assert summary["deploy_decision"]

        scorecard_resp = client.get(f"/api/v1/validations/{job_id}/scorecard")
        assert scorecard_resp.status_code == 200
        scorecard = scorecard_resp.json()
        assert scorecard["run_id"] == job_id
        assert scorecard["checkpoint_id"] == "ckpt-e2e"
        assert scorecard["composite_score"] == summary["composite_score"]
        assert scorecard["deploy_decision"] == summary["deploy_decision"]

        # 4. The job record now reports the terminal state and result pointer.
        job_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert job_resp.status_code == 200
        record = job_resp.json()
        assert record["job_id"] == job_id
        assert record["status"] == "done"
        assert record["result"] == job_id
        assert record["error"] is None
        assert record["started_at"] is not None
        assert record["finished_at"] is not None

    def test_worker_leaves_nothing_more_to_run(self, harness: _Harness) -> None:
        # After the single queued job is drained, a second run_once is a no-op
        # and the store holds exactly the one persisted run.
        job_id = _enqueue(harness.client)
        assert harness.worker().run_once() is not None
        assert harness.worker().run_once() is None
        assert len(harness.store) == 1
        assert harness.store.get(job_id) is not None

    def test_multiple_jobs_drain_in_fifo_order(self, harness: _Harness) -> None:
        # Two enqueued jobs are processed one-per-run_once, oldest first, and
        # each persists a run fetchable by its own id.
        first = _enqueue(harness.client, checkpoint_id="ckpt-first")
        second = _enqueue(harness.client, checkpoint_id="ckpt-second")

        assert harness.worker().run_once().job_id == first  # type: ignore[union-attr]
        assert harness.worker().run_once().job_id == second  # type: ignore[union-attr]

        for job_id, checkpoint in ((first, "ckpt-first"), (second, "ckpt-second")):
            record = harness.client.get(f"/api/v1/jobs/{job_id}").json()
            assert record["status"] == "done"
            assert record["result"] == job_id
            summary = harness.client.get(f"/api/v1/validations/{job_id}").json()
            assert summary["run_id"] == job_id
            assert summary["checkpoint_id"] == checkpoint
        assert len(harness.store) == 2
