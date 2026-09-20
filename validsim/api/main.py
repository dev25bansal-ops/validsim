"""ValidSim REST API.

The API exposes two validation paths:

* ``POST /api/v1/validations`` runs a validation synchronously against the
  configured backend (see :func:`validsim.sim.create_backend`; the mock backend
  by default) and returns its scorecard — appropriate for the platform's
  interactive/CI episode counts.
* ``POST /api/v1/jobs`` enqueues a validation on an asynchronous job queue
  (in-memory or Redis-backed, see :mod:`validsim.jobs`) for long-running or
  batched work, with status/SSE endpoints under ``/api/v1/jobs/{job_id}``.

The store and job queue are injected via ``app.state`` so tests (and a
deployment's worker pool) can supply their own instances.

Deployment hardening is env-driven and read once at :func:`create_app` time:

* ``VALIDSIM_API_KEY`` — when set, every ``/api/v1`` route requires an
  ``X-API-Key`` header matching it (401 otherwise). When unset, auth is
  disabled entirely, keeping local development and the test suite open.
  **Destructive operations are a special case:** ``DELETE`` carries its own
  auth gate (``require_api_key_for_destructive``), so any deployment that
  exposes ``DELETE`` MUST configure ``VALIDSIM_API_KEY`` — with no key set the
  destructive endpoint runs unauthenticated, which is acceptable only for
  throwaway local/dev instances.
* ``VALIDSIM_CORS_ORIGINS`` — comma-separated CORS origin allow-list
  (default ``"*"``, i.e. the previous allow-all behavior).
* ``VALIDSIM_RATE_LIMIT`` — requests per ``VALIDSIM_RATE_WINDOW_SECONDS``
  window for write/sensitive routes (``0`` disables rate limiting entirely,
  which is also the default and preserves the existing behavior). The limited
  routes are ``POST /api/v1/validations``, ``POST /api/v1/jobs`` and
  ``POST /api/v1/validations/{run_id}/compare``. Buckets are keyed on the
  client IP alone (never on a caller-supplied header) so the limit cannot be
  bypassed by rotating ``X-API-Key``; the working set is bounded by a
  max-bucket cap plus a periodic sweep of stale buckets.
* ``VALIDSIM_RATE_WINDOW_SECONDS`` — sliding-window length in seconds
  (default ``60``).
"""

from __future__ import annotations

import math
import os
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders

from validsim import __version__
from validsim.api.metrics import Metrics, metrics_router
from validsim.config import ValidationRequest
from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
from validsim.engine.pdf import scorecard_pdf_bytes
from validsim.engine.pipeline import BaselineNotFoundError, run_and_score
from validsim.engine.regression import compare
from validsim.logging import configure_logging, get_logger
from validsim.sim import create_backend
from validsim.sim.runner import run_validation, stable_seed
from validsim.store import create_store
from validsim.store.memory import StoredRun, ValidationStore


class CompareRequest(BaseModel):
    """Body for POST /validations/{run_id}/compare."""

    baseline_id: str


#: Header carrying the caller's API key when ``VALIDSIM_API_KEY`` is enforced.
API_KEY_HEADER = "X-API-Key"

#: Header carrying the per-request correlation id (echoed on every response).
REQUEST_ID_HEADER = "X-Request-ID"

#: Env var controlling the per-client request budget over a window.
RATE_LIMIT_ENV = "VALIDSIM_RATE_LIMIT"
#: Env var controlling the sliding-window length in seconds.
RATE_WINDOW_ENV = "VALIDSIM_RATE_WINDOW_SECONDS"
#: ``VALIDSIM_RATE_LIMIT`` value that disables rate limiting entirely.
RATE_LIMIT_DISABLED = 0
DEFAULT_RATE_WINDOW_SECONDS = 60.0

#: Write/sensitive route prefix used by the default rate-limiting scope.
RATE_LIMITED_PATH_PREFIX = "/api/v1/validations/"
#: Suffix of the ad-hoc compare endpoint, also covered by the default scope.
COMPARE_PATH_SUFFIX = "/compare"
#: Async job-queue enqueue route, also covered by the default scope.
JOBS_PATH = "/api/v1/jobs"

#: Upper bound on distinct client buckets held by the limiter. Once reached a
#: newly-seen client evicts the least-recently-touched bucket, so a flood of
#: never-revisited IPs cannot grow the table without limit (memory guard).
DEFAULT_MAX_BUCKETS = 10_000
#: Number of limiter checks served between full stale-bucket sweeps.
_SWEEP_INTERVAL = 1024


class _SlidingWindowRateLimiter:
    """Process-local sliding-window rate limiter with a bounded working set.

    Each client (identified by IP address — see :func:`_rate_limit_key`) keeps
    a deque of hit timestamps. On every check, timestamps older than the window
    are dropped; if the number of remaining hits already equals ``limit``, the
    request is denied with a ``Retry-After`` hint derived from the oldest
    in-window hit.

    Two mechanisms bound memory so that clients which visit once and never
    return cannot grow the table without limit:

    * ``max_buckets`` caps the number of distinct client buckets; once full, a
      newly-seen client evicts the least-recently-touched bucket (the buckets
      are kept in an :class:`~collections.OrderedDict` ordered by last touch).
    * A periodic sweep (:meth:`_sweep`, run every ``_SWEEP_INTERVAL`` checks)
      drops buckets whose *newest* hit has aged out of the window — such a
      bucket holds no in-window hits and is equivalent to one never created.

    The limiter is intentionally process-local: it blunts abuse against a
    single uvicorn worker without requiring a shared store.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
    ) -> None:
        if limit < 1:
            raise ValueError("rate limit must be a positive integer")
        if max_buckets < 1:
            raise ValueError("max_buckets must be a positive integer")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self.max_buckets = int(max_buckets)
        # Ordered by recency: least-recently-touched at the head, most-recent at
        # the tail, so eviction pops the coldest bucket in O(1).
        self._hits: "OrderedDict[str, deque[float]]" = OrderedDict()
        self._operations = 0

    def _sweep(self, current: float) -> None:
        """Drop buckets that are empty or whose newest hit has left the window.

        A bucket whose most-recent hit is at least ``window_seconds`` older than
        ``current`` holds no in-window hits, so removing it frees the slot
        without changing any client's effective allowance.
        """
        stale = [
            key
            for key, hits in self._hits.items()
            if not hits or current - hits[-1] >= self.window_seconds
        ]
        for key in stale:
            del self._hits[key]

    def check(self, key: str, now: float | None = None) -> int | None:
        """Record a hit for ``key``; return seconds to wait, or ``None`` if allowed.

        ``now`` is injectable for deterministic tests; callers normally omit it
        so the limiter uses :func:`time.monotonic`.
        """
        current = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            # New client: enforce the memory cap by evicting the coldest bucket.
            while len(self._hits) >= self.max_buckets:
                self._hits.popitem(last=False)
            self._hits[key] = deque([current])
        else:
            while hits and current - hits[0] >= self.window_seconds:
                hits.popleft()
            # Mark as most-recently-used before deciding, so a hot (even if
            # currently-denied) client is evicted last.
            self._hits.move_to_end(key)
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window_seconds - current))
            hits.append(current)

        self._operations += 1
        if self._operations >= _SWEEP_INTERVAL:
            self._sweep(current)
            self._operations = 0
        return None


def _read_rate_limit_config() -> tuple[int, float]:
    """Read and validate the rate-limit env vars, degrading gracefully on bad input."""
    raw_limit = os.environ.get(RATE_LIMIT_ENV, str(RATE_LIMIT_DISABLED))
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = RATE_LIMIT_DISABLED
    if limit < 0:
        limit = RATE_LIMIT_DISABLED

    raw_window = os.environ.get(RATE_WINDOW_ENV, str(DEFAULT_RATE_WINDOW_SECONDS))
    try:
        window = float(raw_window)
    except (TypeError, ValueError):
        window = DEFAULT_RATE_WINDOW_SECONDS
    if window <= 0:
        window = DEFAULT_RATE_WINDOW_SECONDS

    return limit, window


def _is_rate_limited_request(scope: dict[str, Any]) -> bool:
    """True for the write/sensitive routes covered by the default scope.

    Limited routes are ``POST /api/v1/validations`` (create),
    ``POST /api/v1/jobs`` (async enqueue) and
    ``POST /api/v1/validations/{run_id}/compare``. Every read endpoint stays
    unlimited so heavy read tests keep their current behavior.
    """
    if scope.get("type") != "http":
        return False
    method = scope.get("method")
    path = scope.get("path", "")
    if method != "POST":
        return False
    if path == "/api/v1/validations":
        return True
    if path == JOBS_PATH:
        return True
    if path.startswith(RATE_LIMITED_PATH_PREFIX) and path.endswith(COMPARE_PATH_SUFFIX):
        middle = path[len(RATE_LIMITED_PATH_PREFIX) : -len(COMPARE_PATH_SUFFIX)]
        return "/" not in middle and middle != ""
    return False


def _rate_limit_key(scope: dict[str, Any]) -> str:
    """Bucket key: the client IP address only.

    Keying on the ``X-API-Key`` header (the previous behavior) let an attacker
    mint a fresh bucket per request simply by rotating the caller-supplied
    header, defeating the limit entirely. The header is therefore ignored here;
    the bucket is identified solely by the connection's client IP, which the
    caller cannot forge over a real TCP connection.
    """
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return "unknown"


class _RateLimitMiddleware:
    """ASGI middleware enforcing the sliding-window limit on write routes."""

    def __init__(self, app: Any, limiter: _SlidingWindowRateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if not _is_rate_limited_request(scope):
            await self.app(scope, receive, send)
            return

        retry_after = self.limiter.check(_rate_limit_key(scope))
        if retry_after is not None:
            response = JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class _ObservabilityMiddleware:
    """Assign/echo ``X-Request-ID``, log one line per request, count status.

    A raw ASGI middleware (no per-request thread hand-off, negligible overhead)
    that:

    * reuses an inbound ``X-Request-ID`` or mints a fresh ``uuid4`` and echoes
      it on the response so callers can correlate their logs;
    * captures the response status by intercepting the ``http.response.start``
      message (so 401/429 produced by inner auth/rate-limit layers are seen);
    * on completion emits one structured log line (method, path, status,
      duration_ms, request_id) and bumps the shared :class:`Metrics` counter for
      the response's status class.

    It is registered as the outermost user middleware so the recorded duration
    and status reflect the whole request lifecycle.
    """

    def __init__(self, app: Any, *, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics
        self.logger = get_logger("api.access")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(REQUEST_ID_HEADER) or str(uuid4())
        method = scope.get("method", "")
        path = scope.get("path", "")
        # Default to 500 so an exception that prevents any response from
        # starting is still counted/logged as a server error.
        status_holder = {"status": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            status = status_holder["status"]
            self.metrics.observe_status(status)
            self.logger.info(
                "http_request",
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(duration_ms, 3),
                    "request_id": request_id,
                },
            )


def _execute_validation(request: ValidationRequest, store: ValidationStore) -> StoredRun:
    """Run the full validation pipeline for one request and persist the result.

    The shared engine sequence lives in :func:`validsim.engine.pipeline.run_and_score`;
    this adapter supplies the request's inputs and translates the domain-level
    :class:`~validsim.engine.pipeline.BaselineNotFoundError` into an HTTP 404 —
    the only HTTP concern kept here. The simulation backend is selected by
    :func:`validsim.sim.create_backend`, so ``VALIDSIM_BACKEND=isaac`` routes
    episodes to the GPU worker; the default (env unset) keeps the deterministic
    mock, preserving CPU-only/CI behavior. ``run_validation`` and
    ``create_backend`` are passed through from this module's namespace so the
    wiring tests can keep intercepting them here.
    """
    try:
        return run_and_score(
            request.task,
            request.checkpoint_id,
            store,
            baseline_run_id=request.baseline_run_id,
            run_validation=run_validation,
            create_backend=create_backend,
        )
    except BaselineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_run(store: ValidationStore, run_id: str) -> StoredRun:
    """Fetch a run or raise 404."""
    run = store.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


def _validate_iso_filter(value: str | None, *, field: str) -> str | None:
    """Validate an optional ISO-8601 date-range query filter.

    ``None`` and blank/whitespace-only strings mean "no filter" and are
    returned as ``None``. Any other value must parse as an ISO-8601
    timestamp or a 422 is raised.

    The store keeps ``created_at`` as ISO-8601 UTC text in a fixed
    ``+00:00``-offset format and filters it by lexicographic comparison, so
    the returned string is kept in that same shape: a trailing ``Z`` (either
    case) is rewritten to the equivalent ``+00:00`` offset before parsing and
    returned, which keeps a ``Z``-style bound aligned with the stored format.
    The value is otherwise passed through unchanged (never reformatted), so
    callers should supply full-precision timestamps for exact boundaries.
    """
    if value is None or value.strip() == "":
        return None
    normalized = value[:-1] + "+00:00" if value[-1:] in ("Z", "z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid {field}: {value!r} is not a valid ISO-8601 timestamp",
        ) from exc
    return normalized


def _parse_cors_origins(raw: str) -> list[str]:
    """Split a comma-separated ``VALIDSIM_CORS_ORIGINS`` value into origins.

    Whitespace around entries is stripped; empty entries are dropped so that
    trailing commas and ``"*"`` both behave sensibly (``"*"`` keeps the
    allow-all semantics of :class:`CORSMiddleware`).
    """
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


#: Class-name suffix shared by every validation store, stripped to derive a
#: short backend label for the health probe (see :func:`_store_backend_name`).
_STORE_CLASS_SUFFIX = "validationstore"
#: Class-name suffix shared by every job queue, stripped by
#: :func:`_job_queue_backend_name` for the same reason.
_JOB_QUEUE_CLASS_SUFFIX = "jobqueue"


def _backend_label(obj: Any, suffix: str, default: str) -> str:
    """Derive a short backend label from ``obj``'s class name.

    The concrete class name is lower-cased and the shared ``suffix`` (e.g.
    ``"validationstore"``) is dropped; an empty remainder means the object is
    the base in-memory implementation and maps to ``default``. This keeps the
    label in sync with the actual injected instance without any registry or
    I/O. ``"unknown"`` is returned when ``obj`` is ``None``.
    """
    if obj is None:
        return "unknown"
    name = type(obj).__name__.lower()
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return name or default


def _store_backend_name(store: Any) -> str:
    """Backend label for a validation store instance.

    ``ValidationStore`` -> ``"memory"``, ``SqliteValidationStore`` ->
    ``"sqlite"``, ``PostgresValidationStore`` -> ``"postgres"``.
    """
    return _backend_label(store, _STORE_CLASS_SUFFIX, "memory")


def _job_queue_backend_name(queue: Any) -> str:
    """Backend label for a job-queue instance.

    ``JobQueue`` -> ``"memory"``, ``RedisJobQueue`` -> ``"redis"``.
    """
    return _backend_label(queue, _JOB_QUEUE_CLASS_SUFFIX, "memory")


def create_app(store: ValidationStore | None = None) -> FastAPI:
    """Build the FastAPI app, optionally with an injected store (for tests).

    Reads ``VALIDSIM_API_KEY``, ``VALIDSIM_CORS_ORIGINS``,
    ``VALIDSIM_RATE_LIMIT``, and ``VALIDSIM_RATE_WINDOW_SECONDS`` from the
    environment at call time (not import time), so tests and deployments can
    reconfigure per :func:`create_app` invocation.
    """
    application = FastAPI(
        title="ValidSim API",
        version=__version__,
        description="Sim-to-Real CI/CD validation for robot foundation models.",
    )
    # Structured JSON logging is idempotent, so calling it on every create_app
    # invocation (tests build one app each) leaves exactly one handler.
    configure_logging()
    rate_limit, rate_window = _read_rate_limit_config()
    # Publish the effective rate-limit config on state so the health probe can
    # report it without re-reading the environment. ``None`` means disabled.
    application.state.rate_limit_config = (
        {"requests": rate_limit, "window_seconds": rate_window} if rate_limit > 0 else None
    )
    if rate_limit > 0:
        application.add_middleware(
            _RateLimitMiddleware,
            limiter=_SlidingWindowRateLimiter(rate_limit, rate_window),
        )
    allow_origins = _parse_cors_origins(os.environ.get("VALIDSIM_CORS_ORIGINS", "*"))
    application.state.cors_wildcard = "*" in allow_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.store = store if store is not None else create_store()

    # Observability: one process-local metrics collector shared between the
    # request middleware (which bumps the HTTP request counters) and the
    # /metrics route (which renders them alongside store-derived gauges).
    # Registered last so it is the outermost user middleware and therefore
    # observes the final status of every response, including 401/429.
    application.state.metrics = Metrics()
    application.add_middleware(
        _ObservabilityMiddleware, metrics=application.state.metrics
    )

    def get_store() -> ValidationStore:
        """Dependency providing the app's validation store."""
        return application.state.store  # type: ignore[return-value]

    api_key = os.environ.get("VALIDSIM_API_KEY")

    def require_api_key(request: Request) -> None:
        """Dependency enforcing ``X-API-Key`` auth on all /api/v1 routes.

        Enabled only when ``VALIDSIM_API_KEY`` was set when the app was built.
        Compares the header to the configured key in constant time; missing,
        empty, or wrong values all raise a uniform 401 that does not reveal
        whether the key exists.
        """
        if api_key is None:
            return
        supplied = request.headers.get(API_KEY_HEADER, "")
        if not secrets.compare_digest(supplied, api_key):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    def require_api_key_for_destructive(request: Request) -> None:
        """Dependency enforcing ``X-API-Key`` auth on destructive routes.

        ``DELETE`` permanently removes stored runs, so it carries its own gate
        attached directly to the route rather than relying only on the global
        ``VALIDSIM_API_KEY`` toggle wired on the app router. Whenever a key is
        configured the header is required and validated exactly like
        :func:`require_api_key` (constant-time compare, uniform 401 that never
        reveals key state). When no key is configured it is a deliberate no-op
        so local development and the open test-suite keep working — but any
        deployment that exposes ``DELETE`` over a network MUST set
        ``VALIDSIM_API_KEY``, otherwise destructive operations run
        unauthenticated (see the module docstring).
        """
        if api_key is None:
            return
        supplied = request.headers.get(API_KEY_HEADER, "")
        if not secrets.compare_digest(supplied, api_key):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    application.state.api_key_enabled = api_key is not None
    application.router.dependencies = [Depends(require_api_key)]

    @application.get("/api/v1/health")
    def health(request: Request) -> dict[str, Any]:
        """Liveness/readiness probe with the effective runtime configuration.

        Cheap by design: every field is read from ``app.state`` (populated once
        at :func:`create_app` time) or derived from the injected store/queue
        class names, so the route performs no I/O and is safe to back a
        container healthcheck. ``status`` and ``version`` are stable contract
        keys; the rest describe how the instance is configured:

        * ``auth_enabled`` — whether ``X-API-Key`` auth is enforced.
        * ``cors_wildcard`` — whether CORS allows all origins (``"*"``).
        * ``rate_limit`` — ``{"requests", "window_seconds"}`` when enabled,
          else ``None``.
        * ``store_backend`` / ``job_queue_backend`` — backend labels derived
          from the concrete store/queue classes (e.g. ``"memory"``,
          ``"sqlite"``, ``"postgres"``, ``"redis"``).
        """
        state = request.app.state
        return {
            "status": "ok",
            "version": __version__,
            "auth_enabled": bool(getattr(state, "api_key_enabled", False)),
            "cors_wildcard": bool(getattr(state, "cors_wildcard", False)),
            "rate_limit": getattr(state, "rate_limit_config", None),
            "store_backend": _store_backend_name(getattr(state, "store", None)),
            "job_queue_backend": _job_queue_backend_name(getattr(state, "job_queue", None)),
        }

    @application.post("/api/v1/validations", status_code=201)
    def create_validation(
        request: ValidationRequest, st: ValidationStore = Depends(get_store)
    ) -> dict[str, Any]:
        """Run a validation synchronously and return its scorecard."""
        return _execute_validation(request, st).scorecard.to_dict()

    @application.get("/api/v1/validations")
    def list_validations(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        st: ValidationStore = Depends(get_store),
    ) -> dict[str, Any]:
        """Paginated list of validation run summaries, newest first.

        Returns ``{"total", "limit", "offset", "items"}`` where ``items`` are
        compact :meth:`StoredRun.summary` dicts. ``total`` is the count of
        stored runs matching the active filters, independent of pagination.

        Optional ``since``/``until`` are inclusive ISO-8601 date-range bounds
        on each run's ``created_at``; a malformed value yields 422. With
        neither supplied the listing is the full history (unchanged default).
        """
        since = _validate_iso_filter(since, field="since")
        until = _validate_iso_filter(until, field="until")
        history = st.history(since=since, until=until)  # oldest first
        total = len(history)
        newest_first = list(reversed(history))
        page = newest_first[offset : offset + limit]
        items = [r.summary() for r in page]
        return {"total": total, "limit": limit, "offset": offset, "items": items}

    @application.get("/api/v1/validations/{run_id}")
    def get_validation(run_id: str, st: ValidationStore = Depends(get_store)) -> dict[str, Any]:
        """Run summary (no episode payloads)."""
        return _require_run(st, run_id).summary()

    @application.delete(
        "/api/v1/validations/{run_id}",
        status_code=204,
        dependencies=[Depends(require_api_key_for_destructive)],
    )
    def delete_validation(
        run_id: str, st: ValidationStore = Depends(get_store)
    ) -> Response:
        """Delete a stored validation run.

        Returns 204 (no body) on success and 404 when the run is unknown. The
        store's :meth:`~ValidationStore.delete` doubles as the existence
        lookup: it removes the record and reports whether it existed in a
        single step, so there is no check-then-delete race (unlike reusing
        :func:`_require_run` followed by a separate delete).

        Because this is destructive, it carries the dedicated
        ``require_api_key_for_destructive`` gate in addition to the global
        ``/api/v1`` auth: when ``VALIDSIM_API_KEY`` is configured a valid
        ``X-API-Key`` is mandatory (401 otherwise, checked before the 404
        lookup). Deployments exposing ``DELETE`` must therefore configure
        ``VALIDSIM_API_KEY`` — with no key set the endpoint runs
        unauthenticated, which is intended only for throwaway local/dev use.
        """
        if not st.delete(run_id):
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return Response(status_code=204)

    @application.get("/api/v1/validations/{run_id}/scorecard")
    def get_scorecard(run_id: str, st: ValidationStore = Depends(get_store)) -> JSONResponse:
        """Full scorecard JSON for a run."""
        run = _require_run(st, run_id)
        return JSONResponse(content=run.scorecard.to_dict())

    @application.get("/api/v1/validations/{run_id}/failures")
    def get_failures(run_id: str, st: ValidationStore = Depends(get_store)) -> dict[str, Any]:
        """Failure taxonomy plus per-failure episode details."""
        run = _require_run(st, run_id)
        failed = [asdict(e) for e in run.episodes if not e.success]
        return {
            "run_id": run.run_id,
            "failure_taxonomy": run.evaluation.failure_taxonomy,
            "episodes": failed,
        }

    @application.post("/api/v1/validations/{run_id}/compare")
    def compare_runs(
        run_id: str,
        body: CompareRequest,
        st: ValidationStore = Depends(get_store),
    ) -> dict[str, Any]:
        """Ad-hoc regression comparison of two stored runs.

        The permutation-test seed is derived from the run/baseline id pair
        (``stable_seed``), so the same comparison is reproducible across
        calls and processes.
        """
        current = _require_run(st, run_id)
        baseline = _require_run(st, body.baseline_id)
        report = compare(
            current.evaluation,
            baseline.evaluation,
            seed=stable_seed(run_id, body.baseline_id),
        )
        return {
            "run_id": current.run_id,
            "baseline_id": baseline.run_id,
            **report.to_dict(),
        }

    @application.get("/api/v1/regressions")
    def list_regressions(st: ValidationStore = Depends(get_store)) -> list[dict[str, Any]]:
        """Runs whose stored comparison flagged significant regressions."""
        out: list[dict[str, Any]] = []
        for run in st.history():
            if run.regression is not None and run.regression.has_regressions:
                out.append({**run.summary(), "regression": run.regression.to_dict()})
        return out

    @application.get("/api/v1/validations/{run_id}/scorecard.pdf")
    def get_scorecard_pdf(run_id: str, st: ValidationStore = Depends(get_store)) -> Response:
        """One-page branded PDF scorecard, served as a file attachment.

        Returns 404 for an unknown run and 501 when the optional ``reportlab``
        dependency is not installed (the rest of the platform runs fine without
        it; only PDF export is gated).
        """
        run = _require_run(st, run_id)
        try:
            data = scorecard_pdf_bytes(run.scorecard.to_dict())
        except RuntimeError as exc:  # reportlab missing -> friendly 501
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        filename = f"{run.run_id}-scorecard.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @application.get("/api/v1/validations/{run_id}/scorecard.md")
    def get_scorecard_markdown(run_id: str, st: ValidationStore = Depends(get_store)) -> Response:
        """Markdown validation report for a run (CI job summaries).

        Returns 404 for an unknown run; otherwise the rendered Markdown with
        ``text/markdown`` content type.
        """
        run = _require_run(st, run_id)
        return Response(
            content=scorecard_to_markdown(run.scorecard),
            media_type="text/markdown",
        )

    @application.get("/api/v1/validations/{run_id}/scorecard.html")
    def get_scorecard_html(run_id: str, st: ValidationStore = Depends(get_store)) -> Response:
        """Self-contained HTML scorecard for a run (email/PDF distribution).

        Returns 404 for an unknown run; otherwise the rendered HTML document
        with ``text/html`` content type.
        """
        run = _require_run(st, run_id)
        return Response(
            content=scorecard_to_html(run.scorecard),
            media_type="text/html",
        )

    @application.get("/api/v1/models")
    def list_models(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        st: ValidationStore = Depends(get_store),
    ) -> list[dict[str, Any]]:
        """Model-registry view: one row per validated checkpoint.

        Aggregated from the store's chronological history (oldest first), so the
        "latest" fields reflect each checkpoint's most recent run. Checkpoints
        appear in order of first validation. Optional ``limit``/``offset``
        paginate the (unchanged) full ordering.
        """
        runs_by_checkpoint: dict[str, list[StoredRun]] = {}
        for run in st.history():
            runs_by_checkpoint.setdefault(run.checkpoint_id, []).append(run)
        models: list[dict[str, Any]] = []
        for checkpoint_id, runs in runs_by_checkpoint.items():
            latest = runs[-1]  # history() is oldest-first -> last is newest
            models.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "runs": len(runs),
                    "latest_composite": latest.scorecard.composite_score,
                    "latest_decision": latest.scorecard.deploy_decision,
                    "last_validated": latest.created_at,
                }
            )
        return models[offset : offset + limit]

    @application.get("/api/v1/models/{checkpoint_id}/history")
    def get_model_history(
        checkpoint_id: str, st: ValidationStore = Depends(get_store)
    ) -> list[dict[str, Any]]:
        """Chronological (oldest-first) compact scorecard summaries for a checkpoint.

        Reuses the same compact dict shape as ``/api/v1/dashboard/history``
        (:meth:`StoredRun.summary`). Returns 404 for an unknown checkpoint.
        """
        runs = st.list_for_checkpoint(checkpoint_id)
        if not runs:
            raise HTTPException(status_code=404, detail=f"checkpoint {checkpoint_id} not found")
        return [run.summary() for run in runs]

    # dashboard: router + /static + /
    from validsim.api.dashboard import mount_dashboard
    mount_dashboard(application)

    # async job queue: router reads app.state.job_queue (Redis-backed or
    # in-memory). Included after the auth dependency is set on the app router,
    # so /api/v1/jobs inherits the same X-API-Key gate as the rest of the API.
    from validsim.jobs.queue import create_job_queue
    from validsim.jobs.router import router as jobs_router

    application.state.job_queue = create_job_queue()
    application.include_router(jobs_router, prefix="/api/v1")

    # Metrics: the scrape endpoint is exposed twice.
    #   * /api/v1/metrics — included normally, so it inherits the same
    #     X-API-Key gate as the rest of the API.
    #   * /metrics — the path Prometheus scrapes by convention, mounted so it
    #     is reachable WITHOUT auth. The gate lives on the app router's
    #     dependencies (merged into each route at include time), so we drop it
    #     just for this include and restore it immediately after.
    application.include_router(metrics_router, prefix="/api/v1")
    saved_dependencies = application.router.dependencies
    application.router.dependencies = []
    try:
        application.include_router(metrics_router)
    finally:
        application.router.dependencies = saved_dependencies

    return application


app = create_app()

__all__ = ["app", "create_app", "CompareRequest"]
