"""FastAPI router exposing the asynchronous job queue.

The router is deliberately *not* wired into :mod:`validsim.api.main` — a
deployment (or worker pool) that wants the async path includes
:data:`router` itself and supplies a queue via ``app.state.job_queue``
(the same injection pattern the dashboard uses for its store). If no queue
was injected, the dependency lazily builds a default via
:func:`~validsim.jobs.queue.create_job_queue`.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from validsim.jobs.models import TERMINAL_STATUSES, JobRecord, JobSpec
from validsim.jobs.queue import JobQueue, QueueFullError, create_job_queue

__all__ = ["router", "EnqueueJobRequest", "get_queue"]

router = APIRouter(prefix="/jobs", tags=["jobs"])

#: Accepted ``job_id`` path-parameter shape — mirrors the queue's
#: :meth:`~validsim.jobs.queue.JobQueue.new_run_id` output (``vrun-`` + 8 lower
#: hex chars). The Redis backend interpolates the id straight into a key
#: (``validsim:jobs:<job_id>``), so an unvalidated path parameter could collide
#: with the queue's own keys (e.g. ``index``). Validating it up front closes
#: that hole (audited finding L1).
_JOB_ID_RE = re.compile(r"^vrun-[0-9a-f]{8}$")


def _validate_job_id(job_id: str) -> str:
    """Return ``job_id`` unchanged when it matches the run-id format.

    Args:
        job_id: Raw value of the ``{job_id}`` path parameter.

    Returns:
        The validated id, ready to be handed to the queue.

    Raises:
        HTTPException: 400 when ``job_id`` is malformed. Rejecting a bad id
            before it reaches the queue means it is never interpolated into a
            backend key; a well-formed-but-unknown id still falls through to the
            routes' normal 404.
    """
    if not _JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid job id {job_id!r} "
                "(expected format 'vrun-' followed by 8 hex characters)"
            ),
        )
    return job_id


class EnqueueJobRequest(BaseModel):
    """Body for POST /jobs — enqueue one asynchronous validation job.

    ``threshold`` and ``baseline_run_id`` mirror the fields of the synchronous
    :class:`~validsim.config.ValidationRequest`, bounds included, so a client can
    move a validation between the two paths without changing what it asks for.
    Without them the async path had no way to express either, and silently used
    the worker's default gate of 85 with no regression comparison — which made
    the same checkpoint come back ``APPROVE`` here and ``BLOCK`` from
    ``POST /validations``, and (since notification severity is read off the
    scorecard's ``threshold``/``deploy_decision`` pair) fire the wrong hooks.
    ``threshold=None`` keeps the engine default rather than pinning 85.0, so the
    stored spec stays honest about whether a gate was actually requested.
    """

    checkpoint_id: str = Field(..., min_length=1, description="Checkpoint to validate.")
    task_id: str = Field(..., min_length=1, description="Task to execute.")
    episodes: int = Field(1000, ge=1, le=100000, description="Nominal episodes.")
    adversarial: int = Field(0, ge=0, le=1000, description="Adversarial episodes.")
    threshold: float | None = Field(
        None,
        ge=0.0,
        le=100.0,
        description="Composite score (0-100) required to approve; defaults to 85.0.",
    )
    baseline_run_id: str | None = Field(
        None,
        min_length=1,
        description="Run id to compare against for regressions.",
    )


def get_queue(request: Request) -> JobQueue:
    """Dependency resolving the app-level job queue.

    Prefers ``app.state.job_queue`` (injected by tests or a deployment that
    wires the router in); falls back to building a memory queue on demand so
    the router is usable standalone.
    """
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        queue = create_job_queue()
        request.app.state.job_queue = queue
    return queue


#: ``Retry-After`` (seconds) advertised on the ``503`` returned when
#: ``POST /jobs`` finds the queue at its ``max_depth`` cap. The queue drains as
#: workers finish jobs, so a short, fixed hint lets well-behaved clients back
#: off briefly and retry instead of hammering the API.
_QUEUE_FULL_RETRY_AFTER_SECONDS = 5


@router.post("", status_code=202)
def enqueue_job(
    body: EnqueueJobRequest, queue: JobQueue = Depends(get_queue)
) -> dict[str, str]:
    """Enqueue a validation job and return its id and initial status.

    When the queue is already at its ``max_depth`` cap,
    :meth:`~validsim.jobs.queue.JobQueue.enqueue` raises
    :class:`~validsim.jobs.queue.QueueFullError`. Left unhandled that surfaces
    as an opaque ``500``; here it is translated into ``503 Service Unavailable``
    with a JSON body ``{"error": "queue_full", "max_depth": N}`` and a
    ``Retry-After`` header so clients can back off and retry (audit gap: a full
    queue is an expected overload condition, not a server error).
    """
    spec = JobSpec(
        run_id=queue.new_run_id(),
        checkpoint_id=body.checkpoint_id,
        task_id=body.task_id,
        episodes=body.episodes,
        adversarial=body.adversarial,
        threshold=body.threshold,
        baseline_run_id=body.baseline_run_id,
    )
    try:
        record = queue.enqueue(spec)
    except QueueFullError:
        return JSONResponse(
            status_code=503,
            content={"error": "queue_full", "max_depth": queue.max_depth},
            headers={"Retry-After": str(_QUEUE_FULL_RETRY_AFTER_SECONDS)},
        )
    return {"job_id": record.job_id, "status": record.status.value}


#: Default ``limit`` applied to the paginated ``GET /jobs`` envelope when the
#: client supplies ``offset`` but omits ``limit`` — mirrors the default used by
#: ``GET /api/v1/validations`` so the two listings behave identically.
_DEFAULT_JOB_PAGE_LIMIT = 100
#: Default ``offset`` applied to the paginated envelope when only ``limit`` is
#: supplied (i.e. the first page).
_DEFAULT_JOB_PAGE_OFFSET = 0


@router.get("")
def list_jobs(
    queue: JobQueue = Depends(get_queue),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=500,
        description=(
            "Maximum number of jobs to return (1-500). Supplying ``limit`` "
            "and/or ``offset`` switches the response to the paginated "
            "``{total, limit, offset, items}`` envelope (newest-first); with "
            "neither the full list is returned as a bare array."
        ),
    ),
    offset: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Number of newest-first jobs to skip (>=0). See ``limit`` for the "
            "envelope behavior."
        ),
    ),
) -> Any:
    """List jobs, optionally as a paginated, newest-first envelope.

    Backwards-compatible contract:

    * When **neither** ``limit`` nor ``offset`` is supplied the response is the
      historical bare JSON array of every job record in insertion (FIFO) order
      — the shape existing clients and the test suite depend on.
    * When **either** is supplied the response switches to the same paginated
      envelope used by ``GET /api/v1/validations``:
      ``{"total", "limit", "offset", "items"}`` where ``items`` are job-record
      dicts ordered **newest-first**. ``limit`` defaults to
      :data:`_DEFAULT_JOB_PAGE_LIMIT` (1-500) and ``offset`` to
      :data:`_DEFAULT_JOB_PAGE_OFFSET` (>=0) when the other is provided; out of
      range values yield ``422``. ``total`` is the full number of jobs in the
      queue, independent of pagination.
    """
    records = queue.list()
    if limit is None and offset is None:
        return [record.to_dict() for record in records]

    eff_limit = _DEFAULT_JOB_PAGE_LIMIT if limit is None else limit
    eff_offset = _DEFAULT_JOB_PAGE_OFFSET if offset is None else offset
    total = len(records)
    newest_first = list(reversed(records))
    page = newest_first[eff_offset : eff_offset + eff_limit]
    items = [record.to_dict() for record in page]
    return {"total": total, "limit": eff_limit, "offset": eff_offset, "items": items}


@router.get("/{job_id}")
def get_job(job_id: str, queue: JobQueue = Depends(get_queue)) -> dict[str, Any]:
    """Full status record for a single job (400 malformed id, 404 when unknown)."""
    _validate_job_id(job_id)
    record = queue.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return record.to_dict()


def _updated_at(record: JobRecord) -> str | None:
    """Most recent lifecycle timestamp for a record.

    The queue records carry no dedicated ``updated_at`` column, so the freshest
    of ``finished_at`` → ``started_at`` → ``created_at`` is used as the effective
    "last updated" marker (each transition stamps exactly one of them).
    """
    return record.finished_at or record.started_at or record.created_at


@router.get("/{job_id}/status")
def get_job_status(job_id: str, queue: JobQueue = Depends(get_queue)) -> dict[str, Any]:
    """Compact lifecycle snapshot for a single job (400 malformed, 404 unknown).

    Returns only ``{job_id, status, updated_at}`` — the lightweight counterpart
    to the verbose :func:`get_job`, intended for status bars and SSE clients.
    """
    _validate_job_id(job_id)
    record = queue.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return {
        "job_id": record.job_id,
        "status": record.status.value,
        "updated_at": _updated_at(record),
    }


#: Default SSE poll interval (seconds) between status frames.
_DEFAULT_POLL_INTERVAL = 1.0
#: Default upper bound (seconds) on how long a single ``/events`` stream may
#: stay open for a job that has not reached a terminal state. Bounding the
#: stream stops a stuck/never-finishing job from pinning a worker thread for
#: the whole life of the request (audited finding M1).
_DEFAULT_STREAM_TIMEOUT = 60.0


def _event_stream(
    queue: JobQueue,
    job_id: str,
    *,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    deadline: float | None = None,
    max_polls: int | None = None,
) -> Iterator[str]:
    """Yield Server-Sent-Events frames by polling ``job_id`` until terminal.

    Each poll emits one ``data: {json}`` frame carrying the compact status; once
    the job reaches ``done``/``failed`` a final ``data:`` frame is emitted
    followed by an ``event: end`` terminator so well-behaved clients close
    cleanly. If the job vanishes from the queue the stream simply ends.

    The stream is deliberately *bounded* so a job that never reaches a terminal
    state cannot hold a worker thread forever (audited finding M1): it stops
    once either the wall-clock ``deadline`` (seconds since the stream opened) or
    the ``max_polls`` safety valve is reached, emitting a final
    ``event: timeout`` terminator first so clients close cleanly and can choose
    to reconnect.
    """
    started = time.monotonic()
    polls = 0
    while True:
        record = queue.get(job_id)
        if record is None:
            return
        frame = {
            "job_id": record.job_id,
            "status": record.status.value,
            "updated_at": _updated_at(record),
        }
        yield f"data: {json.dumps(frame)}\n\n"
        if record.status in TERMINAL_STATUSES:
            # Keyed off the shared terminal set rather than an explicit
            # done/failed pair: a job dead-lettered by the retry policy is just
            # as final, and reporting it as a stream timeout would tell the
            # client the opposite of what happened.
            yield "event: end\n\n"
            return
        polls += 1
        timed_out = (max_polls is not None and polls >= max_polls) or (
            deadline is not None and (time.monotonic() - started) >= deadline
        )
        if timed_out:
            yield "event: timeout\n\n"
            return
        time.sleep(poll_interval)


@router.get("/{job_id}/events")
def job_events(
    job_id: str,
    request: Request,
    queue: JobQueue = Depends(get_queue),
) -> StreamingResponse:
    """Stream a job's status over Server-Sent Events until it is done/failed.

    Returns 400 for a malformed ``job_id`` and 404 (before opening the stream)
    for an unknown job; otherwise polls the queue and emits ``data:`` frames
    terminated by an ``event: end`` frame once the job reaches a terminal state
    — or an ``event: timeout`` frame once the stream's bounded lifetime elapses,
    so a job that never finishes cannot pin a worker thread forever (M1). The
    bound and poll interval default to :data:`_DEFAULT_STREAM_TIMEOUT` /
    :data:`_DEFAULT_POLL_INTERVAL` and may be overridden per deployment (or
    test) via ``app.state.sse_stream_deadline`` / ``app.state.sse_poll_interval``.
    """
    _validate_job_id(job_id)
    if queue.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    deadline = float(
        getattr(request.app.state, "sse_stream_deadline", _DEFAULT_STREAM_TIMEOUT)
    )
    poll_interval = float(
        getattr(request.app.state, "sse_poll_interval", _DEFAULT_POLL_INTERVAL)
    )
    return StreamingResponse(
        _event_stream(queue, job_id, poll_interval=poll_interval, deadline=deadline),
        media_type="text/event-stream",
    )