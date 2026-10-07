"""Hardening tests for the async job router (audited findings M1 and L1).

L1 — ``job_id`` is interpolated straight into Redis keys by the queue backend
(``validsim:jobs:<job_id>``), so a malformed path parameter (e.g. ``index``, or
an id carrying ``:`` separators) could collide with the queue's own keys. The
router must reject any id that does not match the run-id shape
(``^vrun-[0-9a-f]{8}$``) with **400** *before* it reaches the queue, while a
well-formed-but-unknown id must still produce **404** (not 500, and not a
collision).

M1 — the ``/events`` SSE stream previously looped until the job reached a
terminal state, so a job that never finished pinned a worker thread for the
whole life of the request. The stream now has a bounded lifetime and ends with
an ``event: timeout`` terminator instead.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from validsim.jobs import JobQueue, JobRecord, JobSpec, JobStatus, router
from validsim.jobs.router import (
    _DEFAULT_STREAM_TIMEOUT,
    EnqueueJobRequest,
    _event_stream,
    _validate_job_id,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Standalone app with the jobs router and an injected in-memory queue."""
    app = FastAPI()
    app.state.job_queue = JobQueue()
    app.include_router(router)
    return TestClient(app)


def _queued_record(run_id: str = "vrun-cafe1234") -> JobRecord:
    """A non-terminal (queued) record used to drive the stub queue."""
    spec = JobSpec(
        run_id=run_id,
        checkpoint_id="ckpt-alpha",
        task_id="pick-place",
        episodes=10,
        adversarial=0,
    )
    return JobRecord(
        spec=spec, status=JobStatus.QUEUED, created_at="2020-01-01T00:00:00+00:00"
    )


class _StubQueue:
    """JobQueue stand-in whose ``get`` always returns one non-terminal record.

    Models a job that never reaches ``done``/``failed`` — the exact condition
    that, before the M1 fix, kept the SSE worker spinning forever. Only ``get``
    is exercised by the router/stream.
    """

    def __init__(self, record: JobRecord) -> None:
        self._record = record
        self.gets = 0

    def get(self, job_id: str) -> JobRecord:  # noqa: ARG002 - signature parity
        self.gets += 1
        return self._record


# ---------------------------------------------------------------------------
# L1 — malformed job_id must be rejected with 400 (unit level)
# ---------------------------------------------------------------------------

MALFORMED_IDS = [
    "not-a-valid-id",
    "index",              # would collide with the Redis index key
    "vrun-ZZZZZZZZ",      # uppercase hex is never produced by new_run_id()
    "vrun-1234567",       # too short
    "vrun-123456789",     # too long
    "vrun-cafe1234extra", # trailing junk
    "vrun-deadbeef:x",    # separator could forge a nested key
    "vrun-cafe1234\n",    # trailing newline must not sneak past the anchor
    "",                   # empty
    "notvrun-cafe1234",   # wrong prefix
]


@pytest.mark.parametrize("job_id", MALFORMED_IDS)
def test_validate_job_id_raises_400(job_id: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_job_id(job_id)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "job_id", ["vrun-cafe1234", "vrun-00000000", "vrun-ffffffff", "vrun-abcdef01"]
)
def test_validate_job_id_accepts_wellformed(job_id: str) -> None:
    assert _validate_job_id(job_id) == job_id


# ---------------------------------------------------------------------------
# L1 — malformed job_id must be rejected with 400 (HTTP level)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template", ["/jobs/{jid}", "/jobs/{jid}/status", "/jobs/{jid}/events"]
)
def test_malformed_job_id_returns_400_over_http(
    client: TestClient, template: str
) -> None:
    """All three id-bearing routes reject a bad id with 400, not 500."""
    response = client.get(template.format(jid="index"))
    assert response.status_code == 400


def test_malformed_id_does_not_reach_queue(client: TestClient) -> None:
    """A bad id is rejected before ``queue.get`` — never interpolated into a key."""
    queue: JobQueue = client.app.state.job_queue  # type: ignore[assignment]
    queue.enqueue(_queued_record().spec)  # seed one real job so list() is non-empty
    snapshot = [r.job_id for r in queue.list()]
    assert client.get("/jobs/index/status").status_code == 400
    assert [r.job_id for r in queue.list()] == snapshot


@pytest.mark.parametrize(
    "template", ["/jobs/{jid}", "/jobs/{jid}/status", "/jobs/{jid}/events"]
)
def test_wellformed_unknown_id_is_404(client: TestClient, template: str) -> None:
    """The 400 guard must not shadow the existing 404 for a valid-but-unknown id."""
    response = client.get(template.format(jid="vrun-ffffffff"))
    assert response.status_code == 404


def test_wellformed_known_id_is_200(client: TestClient) -> None:
    """The happy path is unaffected by the new validation."""
    response = client.post(
        "/jobs",
        json={"checkpoint_id": "ckpt-a", "task_id": "pick", "episodes": 5},
    )
    assert response.status_code == 202
    jid = response.json()["job_id"]
    assert client.get(f"/jobs/{jid}").status_code == 200
    assert client.get(f"/jobs/{jid}/status").status_code == 200


# ---------------------------------------------------------------------------
# M1 — the SSE stream has a bounded lifetime
# ---------------------------------------------------------------------------


def test_event_stream_terminates_on_max_polls() -> None:
    frames = list(
        _event_stream(
            _StubQueue(_queued_record()),
            "vrun-cafe1234",
            poll_interval=0.0,
            max_polls=3,
        )
    )
    assert frames[-1] == "event: timeout\n\n"
    assert sum(1 for f in frames if f.startswith("data: ")) == 3


def test_event_stream_terminates_on_deadline() -> None:
    start = time.monotonic()
    frames = list(
        _event_stream(
            _StubQueue(_queued_record()),
            "vrun-cafe1234",
            poll_interval=0.01,
            deadline=0.05,
        )
    )
    elapsed = time.monotonic() - start
    assert frames[-1] == "event: timeout\n\n"
    assert elapsed < 1.0  # terminated far below the production default


def test_event_stream_still_ends_cleanly_on_terminal_job() -> None:
    """A done job must end with ``event: end`` and never emit a timeout."""
    queue = JobQueue()
    jid = queue.enqueue(_queued_record().spec).job_id
    queue.update_status(jid, JobStatus.DONE, result=jid)
    frames = list(_event_stream(queue, jid, poll_interval=0.0, deadline=5.0))
    assert frames[-1] == "event: end\n\n"
    assert not any(f.startswith("event: timeout") for f in frames)


def test_events_route_times_out_for_never_finishing_job() -> None:
    """End-to-end: the route stops streaming within the configured deadline.

    A stub queue that never finishes is exactly the M1 scenario; the app.state
    override shrinks the bound so the assertion runs in milliseconds.
    """
    app = FastAPI()
    app.state.job_queue = _StubQueue(_queued_record())
    app.state.sse_poll_interval = 0.01
    app.state.sse_stream_deadline = 0.05
    app.include_router(router)
    client = TestClient(app)

    start = time.monotonic()
    response = client.get("/jobs/vrun-cafe1234/events")
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert '"status": "queued"' in body
    assert body.rstrip().endswith("event: timeout")
    assert elapsed < 1.0


def test_events_route_default_deadline_is_bounded() -> None:
    """The production default is a finite bound (never an infinite stream)."""
    assert _DEFAULT_STREAM_TIMEOUT > 0


# ---------------------------------------------------------------------------
# Terminal-state completeness: a dead job must terminate the SSE stream too
# ---------------------------------------------------------------------------


def test_event_stream_ends_cleanly_on_a_dead_job() -> None:
    """``dead`` is terminal, so a client must not be left polling forever.

    The stream only checked ``done``/``failed``. A job that exhausted its retry
    budget would therefore stream until the request deadline and then report
    ``timeout`` — indistinguishable, to a client, from a job that was merely
    slow, which is precisely the signal an operator needs.
    """
    queue = JobQueue()
    jid = queue.enqueue(_queued_record().spec).job_id
    queue.update_status(jid, JobStatus.DEAD, error="retry budget exhausted")

    frames = list(_event_stream(queue, jid, poll_interval=0.0, deadline=5.0))

    assert frames[-1] == "event: end\n\n"
    assert not any(frame.startswith("event: timeout") for frame in frames)
    assert '"status": "dead"' in frames[0]


def test_dead_job_status_endpoint_reports_the_terminal_state() -> None:
    app = FastAPI()
    app.state.job_queue = JobQueue()
    app.include_router(router)
    client = TestClient(app)
    jid = client.post(
        "/jobs", json={"checkpoint_id": "ckpt-a", "task_id": "pick", "episodes": 5}
    ).json()["job_id"]
    queue: JobQueue = client.app.state.job_queue  # type: ignore[assignment]
    queue.update_status(jid, JobStatus.DEAD, error="retry budget exhausted")

    body = client.get(f"/jobs/{jid}/status").json()

    assert body["status"] == "dead"
    assert body["updated_at"] is not None


# ---------------------------------------------------------------------------
# The async path must carry the same gate as the synchronous one
# ---------------------------------------------------------------------------
#
# ``EnqueueJobRequest`` had no threshold field, so ``POST /jobs`` could not carry
# one and every async run was gated at the worker's default 85. A checkpoint the
# sync path blocks at 70 was approved on the async path — different verdicts for
# the same checkpoint. Notification severity is derived from the scorecard's own
# ``threshold``/``deploy_decision`` pair, so the wrong hooks fired as well. The
# body now accepts the same knobs the sync endpoint does.


class TestAsyncGateParity:
    @pytest.fixture()
    def client(self) -> TestClient:
        app = FastAPI()
        app.state.job_queue = JobQueue()
        app.include_router(router)
        return TestClient(app)

    def _spec_of(self, client: TestClient, job_id: str) -> dict:
        return client.get(f"/jobs/{job_id}").json()["spec"]

    def test_threshold_is_accepted_and_persisted(self, client: TestClient) -> None:
        response = client.post(
            "/jobs",
            json={
                "checkpoint_id": "ckpt-a",
                "task_id": "pick",
                "episodes": 5,
                "threshold": 70.5,
            },
        )
        assert response.status_code == 202
        spec = self._spec_of(client, response.json()["job_id"])
        assert spec["threshold"] == pytest.approx(70.5)

    def test_threshold_omitted_falls_back_to_the_engine_default(
        self, client: TestClient
    ) -> None:
        """``None`` means "use the default", exactly as the sync path treats it.

        Storing a concrete 85.0 instead would be indistinguishable from a caller
        that explicitly asked for 85.0, and would drift the moment the engine
        default changed.
        """
        response = client.post(
            "/jobs", json={"checkpoint_id": "ckpt-a", "task_id": "pick", "episodes": 5}
        )
        spec = self._spec_of(client, response.json()["job_id"])
        assert spec["threshold"] is None
        assert spec["baseline_run_id"] is None

    @pytest.mark.parametrize("bad", [-0.1, 100.1, "ninety"])
    def test_out_of_range_threshold_rejected(self, client: TestClient, bad: Any) -> None:
        response = client.post(
            "/jobs",
            json={"checkpoint_id": "ckpt-a", "task_id": "pick", "threshold": bad},
        )
        assert response.status_code == 422

    def test_baseline_run_id_is_accepted_and_persisted(self, client: TestClient) -> None:
        response = client.post(
            "/jobs",
            json={
                "checkpoint_id": "ckpt-a",
                "task_id": "pick",
                "episodes": 5,
                "baseline_run_id": "vrun-base0001",
            },
        )
        assert response.status_code == 202
        spec = self._spec_of(client, response.json()["job_id"])
        assert spec["baseline_run_id"] == "vrun-base0001"

    def test_empty_baseline_run_id_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/jobs",
            json={"checkpoint_id": "ckpt-a", "task_id": "pick", "baseline_run_id": ""},
        )
        assert response.status_code == 422

    def test_sync_and_async_bodies_accept_the_same_gate_fields(self) -> None:
        """The two paths must expose the same knobs, or verdicts drift again."""
        from validsim.config import ValidationRequest

        sync_fields = set(ValidationRequest.model_fields)
        job_fields = set(EnqueueJobRequest.model_fields)
        assert {"threshold", "baseline_run_id"} <= sync_fields
        assert {"threshold", "baseline_run_id"} <= job_fields
