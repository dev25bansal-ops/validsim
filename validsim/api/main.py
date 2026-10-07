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

* ``VALIDSIM_API_KEY`` — when set to a non-blank value, every ``/api/v1`` route
  requires an ``X-API-Key`` header matching it (401 otherwise), except the
  healthcheck path in ``PUBLIC_PATHS``. A blank or whitespace-only value counts
  as *not configured* rather than as a key to compare against, and with no key
  configured auth is disabled entirely. **Destructive operations are a special
  case:** ``DELETE`` carries its own auth gate
  (``require_api_key_for_destructive``), so any deployment that exposes
  ``DELETE`` over a network MUST configure ``VALIDSIM_API_KEY`` — with no key
  set the destructive endpoint runs unauthenticated, which is acceptable only
  for throwaway local/dev instances.
* ``VALIDSIM_ENV`` — deployment mode. Defaults to ``development``;
  ``production`` (or ``prod``) refuses to start unless ``VALIDSIM_API_KEY`` is
  configured, so an unprotected API fails closed at boot instead of serving
  traffic.
* ``VALIDSIM_CORS_ORIGINS`` — comma-separated CORS origin allow-list
  (default ``"*"``, i.e. the previous allow-all behavior).
* ``VALIDSIM_RATE_LIMIT`` — requests per ``VALIDSIM_RATE_WINDOW_SECONDS``
  window for write/sensitive routes. Protection is **on by default**
  (``60`` per ``60``s); an absent, unparsable, or negative value falls back to
  that default, and only an explicit ``0`` disables it. The limited
  routes are ``POST /api/v1/validations``, ``POST /api/v1/jobs``,
  ``POST /api/v1/validations/{run_id}/compare`` and the destructive
  ``DELETE /api/v1/validations/{run_id}``. Buckets are keyed on the client IP
  alone (never on a caller-supplied header) so the limit cannot be bypassed by
  rotating ``X-API-Key``; the working set is bounded by a max-bucket cap plus a
  periodic sweep of stale buckets.
* ``VALIDSIM_RATE_WINDOW_SECONDS`` — sliding-window length in seconds
  (default ``60``).
* ``VALIDSIM_PIPELINE_CONCURRENCY`` — how many validation pipelines may run
  concurrently (default: see :data:`DEFAULT_PIPELINE_CONCURRENCY`). Excess
  requests are refused immediately with ``503`` + ``Retry-After`` instead of
  queueing on the threadpool.
* ``VALIDSIM_THREAD_LIMITER_TOKENS`` — size of the worker-thread pool backing
  every synchronous route (default: see :data:`DEFAULT_THREAD_LIMITER_TOKENS`,
  which reserves headroom above the pipeline budget).

Availability model
------------------

Every route here is declared ``def``, not ``async def``, so FastAPI dispatches
it to :func:`starlette.concurrency.run_in_threadpool` ->
``anyio.to_thread.run_sync``, which is gated by anyio's *per-event-loop*
default :class:`~anyio.CapacityLimiter` — **40 tokens** as measured at runtime.
A validation pipeline is CPU-bound and holds its thread for its whole run: 20 000
episodes measured **6.9 s**, and concurrent ``GET /api/v1/health`` probes went
from ~3 ms to **2.7 s** in that window. With 45 concurrent 20 k-episode POSTs
the pool was fully drained and the liveness probe became **unreachable**; the
same starvation reproduces on a 20-line control app with two sync routes and no
ValidSim code, so this is a property of the deployment shape, not of one
handler. The Docker ``HEALTHCHECK`` is ``--timeout=5s --retries=3``, so three
consecutive time-outs mark a healthy container unhealthy and get it killed
mid-validation.

Two mechanisms keep that from happening, rather than "make the route async"
(which would only block the event loop instead):

* **Admission control** — :func:`_run_pipeline_admitted` bounds concurrent
  pipelines with a semaphore *before* a worker thread is claimed, so overload
  is refused fast and observably (``503``) instead of queued invisibly.
* **Reserved headroom** — :func:`_apply_thread_limiter_headroom` raises the
  thread pool above the pipeline budget on server start, so the cheap routes
  (above all the liveness probe) keep worker threads even at full saturation.
  They are unchanged (``def``) because blocking the event loop is worse.
"""

from __future__ import annotations

import math
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, AsyncIterator, Callable, TypeVar
from uuid import uuid4

import anyio.to_thread
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders

from validsim import __version__
from validsim.api.metrics import Metrics, metrics_router
from validsim.config import ValidationRequest
from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
from validsim.engine.pdf import scorecard_pdf_bytes
from validsim.engine.pipeline import DEFAULT_THRESHOLD, BaselineNotFoundError, run_and_score
from validsim.engine.regression import compare
from validsim.logging import configure_logging, get_logger
from validsim.sim import create_backend
from validsim.sim.runner import run_validation, stable_seed
from validsim.store import create_store
from validsim.store.memory import StoredRun, ValidationStore


#: Upper bound accepted for an id-like path parameter or request field
#: (``run_id``, ``checkpoint_id``, ``baseline_id``). Run ids the store issues
#: are ``vrun-`` + 8 hex chars, so this is ~30x headroom: generous enough that
#: no legitimate caller is rejected, tight enough that a caller cannot push
#: megabytes through a field that is only ever used as a lookup key.
_MAX_ID_LENGTH = 256


class CompareRequest(BaseModel):
    """Body for POST /validations/{run_id}/compare.

    ``baseline_id`` is a run id, so it is bounded: a multi-kilobyte string in
    this field costs a full dictionary lookup and an error string for nothing,
    and the value is echoed back in the response body. The bound is
    deliberately generous relative to the ids the store actually issues
    (``vrun-`` + 8 hex chars) so no legitimate caller can trip it.
    """

    baseline_id: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_ID_LENGTH,
        description="Run id of the baseline to compare against.",
    )


#: Header carrying the caller's API key when ``VALIDSIM_API_KEY`` is enforced.
API_KEY_HEADER = "X-API-Key"

#: Paths reachable without an API key. ``/api/v1/health`` backs the container
#: healthcheck, which has no credential to present. The exemption is a path
#: check inside the dependency rather than a registration-order trick, because
#: FastAPI snapshots router-level dependencies per route at include time — any
#: route defined later silently inherits (or escapes) the gate.
PUBLIC_PATHS = frozenset({"/api/v1/health"})

#: Deployment modes that refuse to start with no API key configured.
AUTH_REQUIRED_ENVS = frozenset({"production", "prod"})

#: Header carrying the per-request correlation id (echoed on every response).
REQUEST_ID_HEADER = "X-Request-ID"

#: Env var controlling the per-client request budget over a window.
RATE_LIMIT_ENV = "VALIDSIM_RATE_LIMIT"
#: Env var controlling the sliding-window length in seconds.
RATE_WINDOW_ENV = "VALIDSIM_RATE_WINDOW_SECONDS"
#: ``VALIDSIM_RATE_LIMIT`` value that disables rate limiting entirely.
RATE_LIMIT_DISABLED = 0
#: Requests per window allowed per client when ``VALIDSIM_RATE_LIMIT`` is unset.
#: Generous enough that ordinary interactive and CI use never trips it,
#: while still bounding a runaway loop on the write/sensitive routes.
DEFAULT_RATE_LIMIT = 60
DEFAULT_RATE_WINDOW_SECONDS = 60.0

#: Write/sensitive route prefix used by the default rate-limiting scope.
RATE_LIMITED_PATH_PREFIX = "/api/v1/validations/"
#: Suffix of the ad-hoc compare endpoint, also covered by the default scope.
COMPARE_PATH_SUFFIX = "/compare"
#: Async job-queue enqueue route, also covered by the default scope.
JOBS_PATH = "/api/v1/jobs"
#: HTTP methods the rate-limit scope meters. ``DELETE`` is destructive and
#: irreversible, so it is metered alongside the writes; reads are never metered.
RATE_LIMITED_METHODS = frozenset({"POST", "DELETE"})

#: Upper bound on distinct client buckets held by the limiter. Once reached a
#: newly-seen client evicts the least-recently-touched bucket, so a flood of
#: never-revisited IPs cannot grow the table without limit (memory guard).
DEFAULT_MAX_BUCKETS = 10_000
#: Number of limiter checks served between full stale-bucket sweeps.
_SWEEP_INTERVAL = 1024

#: Env var bounding how many validation pipelines run at once.
PIPELINE_CONCURRENCY_ENV = "VALIDSIM_PIPELINE_CONCURRENCY"
#: Env var sizing the worker-thread pool that serves every sync route.
THREAD_LIMITER_ENV = "VALIDSIM_THREAD_LIMITER_TOKENS"

#: Default concurrent-pipeline budget. Matches the anyio thread limiter's own
#: 40-token default, so on a stock deployment the admission gate starts out
#: matching the pool it protects (and an operator who raises one without the
#: other gets the headroom rule below rather than a surprise).
DEFAULT_PIPELINE_CONCURRENCY = 40
#: Worker threads kept in reserve *above* the pipeline budget so the cheap
#: routes — above all ``/api/v1/health`` — stay answerable at full saturation.
#: 8 is enough for a liveness probe, a Prometheus scrape, a dashboard read and
#: a burst of auth rejections, and small enough to be negligible next to 48
#: idle threads.
THREAD_LIMITER_HEADROOM = 8
#: Floor for the thread pool, applied when the pipeline budget is tiny. Below
#: this, reserving headroom would exceed anyio's own 40-token default and the
#: "reserve 8" rule would silently become "multiply by 5".
DEFAULT_THREAD_LIMITER_TOKENS = 48
#: Number of seconds a client is asked to wait after a 503 overload refusal.
_OVERLOAD_RETRY_AFTER_SECONDS = 5

class _AdmissionGate:
    """Counting semaphore whose budget can be changed after construction.

    ``threading.Semaphore`` cannot be resized and ``threading.BoundedSemaphore``
    has no public way to do it, yet the budget genuinely has to be changeable:
    it is read from the environment at :func:`create_app` time, and the test
    suite builds dozens of apps in one process. Hand-rolled rather than reused so
    the invariant is explicit and assertable: ``in_flight`` never exceeds
    ``limit``, and a release can never drive it negative.

    The lock is held for two integer operations, so contention is irrelevant
    next to the pipeline work it guards.
    """

    def __init__(self, limit: int) -> None:
        self._lock = threading.Lock()
        self._limit = max(1, int(limit))
        self._in_flight = 0

    @property
    def limit(self) -> int:
        """Current admission budget (always ``>= 1``)."""
        return self._limit

    @property
    def in_flight(self) -> int:
        """How many callers currently hold a slot."""
        return self._in_flight

    def resize(self, limit: int) -> None:
        """Set a new budget. Shrinking never cancels work already admitted."""
        with self._lock:
            self._limit = max(1, int(limit))

    def try_acquire(self, limit: int | None = None) -> bool:
        """Take a slot if the budget allows; never blocks.

        ``limit`` overrides the gate's own budget for this call only, so a
        caller holding its budget on ``app.state`` (as the route does) is
        authoritative rather than silently governed by whatever value the
        process-wide default was last resized to. Omit it to use the gate's
        configured limit.
        """
        with self._lock:
            cap = self._limit if limit is None else max(1, int(limit))
            if self._in_flight >= cap:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        """Give a slot back. Clamped at zero so it is safe to over-call."""
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1


#: Process-wide admission gate, resized once per :func:`create_app`. Module-level
#: because the pipeline is handed to the threadpool as a bare callable, which
#: runs with no request or app in scope, so it reads the budget from here.
_PIPELINE_GATE = _AdmissionGate(DEFAULT_PIPELINE_CONCURRENCY)


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
        # Every route is a sync `def`, so FastAPI dispatches it to a threadpool
        # and `check` is called concurrently by construction. Without this lock
        # two threads could interleave between the capacity check and the
        # append and both be admitted past the limit — the limit would then be a
        # suggestion under exactly the bursty load it exists to stop. The lock is
        # held for a handful of O(1) deque operations, so contention is
        # irrelevant next to the work it guards.
        self._lock = threading.Lock()

    def _sweep(self, current: float) -> None:
        """Drop buckets that are empty or whose newest hit has left the window.

        A bucket whose most-recent *recorded hit* is at least ``window_seconds``
        older than ``current`` holds no in-window hits, so removing it frees the
        slot without changing any client's effective allowance. Note this keys
        off ``hits[-1]`` — the newest recorded hit — and not off how recently the
        bucket was *touched*: a denied request marks its bucket most-recently-used
        without adding a hit, and reclaiming on that basis would hand a locked
        out client a fresh allowance mid-window.
        """
        stale = [
            key
            for key, hits in self._hits.items()
            if not hits or current - hits[-1] >= self.window_seconds
        ]
        for key in stale:
            del self._hits[key]

    def sweep_stale(self, now: float | None = None) -> int:
        """Reclaim every stale bucket; return how many were freed.

        The public, callable form of the periodic reclaim, so an instance can be
        swept off a clock as well as off request traffic. ``check`` calls it
        every :data:`_SWEEP_INTERVAL` checks; the sweep itself is a full pass
        over the table, so driving it from a timer is the only way it happens at
        all on an instance that never reaches that many checks — which is exactly
        the low-traffic case where the table is smallest and staleness matters
        least, but also the case where nothing else would ever run it.

        Args:
            now: Injectable clock for deterministic tests; defaults to
                :func:`time.monotonic`.
        """
        current = time.monotonic() if now is None else now
        with self._lock:
            before = len(self._hits)
            self._sweep(current)
            return before - len(self._hits)

    def check(self, key: str, now: float | None = None) -> int | None:
        """Record a hit for ``key``; return seconds to wait, or ``None`` if allowed.

        ``now`` is injectable for deterministic tests; callers normally omit it
        so the limiter uses :func:`time.monotonic`.

        Every call counts toward the periodic sweep, **including denied ones**.
        The sweep used to be advanced only on the allowed path, so the requests
        that most needed it — a flood from one client whose budget is spent —
        advanced nothing: 3 000 denied checks ran the sweep zero times, and the
        memory cap degraded into evicting whichever live bucket came next.
        """
        current = time.monotonic() if now is None else now
        retry_after: int | None = None
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                # New client: enforce the memory cap by evicting the coldest
                # bucket. The sweep runs first so a table full of stale buckets
                # reclaims its own slots instead of evicting a live one.
                if self._operations >= _SWEEP_INTERVAL:
                    self._sweep(current)
                    self._operations = 0
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
                    # Denied: no hit is appended, so a client that keeps retrying
                    # cannot push its own window forward and lock itself out
                    # permanently.
                    retry_after = max(1, math.ceil(hits[0] + self.window_seconds - current))
                else:
                    hits.append(current)

            self._operations += 1
            if self._operations >= _SWEEP_INTERVAL:
                self._sweep(current)
                self._operations = 0
        return retry_after


def _read_rate_limit_config() -> tuple[int, float]:
    """Read and validate the rate-limit env vars.

    Write-route protection ships on. An unset, unparsable, or negative
    ``VALIDSIM_RATE_LIMIT`` falls back to :data:`DEFAULT_RATE_LIMIT` rather than
    to disabled, so a typo in deployment config cannot silently remove the
    guard. Only an explicit ``0`` turns rate limiting off.
    """
    raw_limit = os.environ.get(RATE_LIMIT_ENV, str(DEFAULT_RATE_LIMIT))
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = DEFAULT_RATE_LIMIT
    if limit < 0:
        limit = DEFAULT_RATE_LIMIT

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
    ``POST /api/v1/jobs`` (async enqueue),
    ``POST /api/v1/validations/{run_id}/compare``, and the destructive
    ``DELETE /api/v1/validations/{run_id}``. Every read endpoint stays unlimited
    so heavy read tests keep their current behavior.

    ``DELETE`` was previously unmetered because the scope matched ``POST`` only:
    six consecutive deletes returned ``[204, 404, 404, 404, 404, 404]`` where
    four posts returned ``[201, 429, 429, 429]``. That is the one verb that
    destroys data irreversibly, it is the route that carries its own dedicated
    auth gate precisely because it is dangerous, and leaving it unbounded made a
    runaway delete loop the cheapest possible abuse of the API.
    """
    if scope.get("type") != "http":
        return False
    method = scope.get("method")
    path = scope.get("path", "")
    if method not in RATE_LIMITED_METHODS:
        return False
    if path == "/api/v1/validations":
        return method == "POST"
    if path == JOBS_PATH:
        return method == "POST"
    if path.startswith(RATE_LIMITED_PATH_PREFIX):
        middle = path[len(RATE_LIMITED_PATH_PREFIX) :]
        if middle.endswith(COMPARE_PATH_SUFFIX):
            run_id = middle[: -len(COMPARE_PATH_SUFFIX)]
            return method == "POST" and "/" not in run_id and run_id != ""
        # The bare ``DELETE /api/v1/validations/{run_id}`` collection item.
        return method == "DELETE" and "/" not in middle and middle != ""
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


class _DocsAuthMiddleware:
    """Gate the auto-generated API schema/docs when an API key is configured.

    FastAPI mounts ``/openapi.json``, ``/docs``, ``/docs/oauth2-redirect`` and
    ``/redoc`` on the application itself, *outside* the ``/api/v1`` router that
    carries the ``X-API-Key`` dependency. With a key configured they therefore
    answered ``200`` to a fully anonymous caller, disclosing the complete route
    surface, every parameter name and every response schema of an otherwise
    authenticated API — enough to plan an attack without holding a credential.

    Only installed when a key is configured, so local development and the
    open test-suite keep their interactive docs. The comparison is on UTF-8
    bytes for the same reason as :func:`require_api_key` (a non-ASCII key would
    make ``secrets.compare_digest`` raise ``TypeError``).

    The path set is matched as a *prefix*, not by equality. Enumerating
    ``/openapi.json``, ``/docs`` and ``/redoc`` and calling the gate complete
    left ``/docs/oauth2-redirect`` — a fourth route FastAPI registers alongside
    them, serving the Swagger UI's OAuth2 helper page — answering ``200``
    anonymously. The disclosure there is minor, but the lesson is not: an
    exhaustive-looking literal list is exactly the construct that lets the next
    generated route slip through, so a test now enumerates the app's real route
    table instead of trusting this set.
    """

    #: Path prefixes FastAPI serves itself; each discloses the API's full shape.
    #: Matched as prefixes so a route generated under one of them
    #: (``/docs/oauth2-redirect``) cannot escape the gate.
    _SCHEMA_PATH_PREFIXES: tuple[str, ...] = ("/openapi.json", "/docs", "/redoc")

    def __init__(self, app: Any, api_key: str) -> None:
        self.app = app
        self._api_key = api_key.encode("utf-8")

    @classmethod
    def _is_schema_path(cls, path: str) -> bool:
        """True for a path (or sub-path) served by FastAPI's own docs machinery."""
        return any(path.startswith(prefix) for prefix in cls._SCHEMA_PATH_PREFIXES)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self._is_schema_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        supplied = headers.get(API_KEY_HEADER.lower(), "").encode("utf-8")
        if not secrets.compare_digest(supplied, self._api_key):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key"},
                headers={"WWW-Authenticate": "ApiKey"},
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


#: Result type of a value-producing callable the admission gate may run.
_PipelineResult = TypeVar("_PipelineResult")


def _parse_positive_int(raw: str | int | None, default: int) -> int:
    """Coerce ``raw`` to an int ``>= 1``, falling back to ``default``.

    Applies the rule the rate limiter already uses: an absent, unparsable, zero
    or negative value degrades to the shipped default rather than to whatever was
    literally typed, so a typo in deployment config cannot quietly disable a
    protection.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _apply_thread_limiter_headroom(
    limiter: Any | None, pipeline_concurrency: int
) -> Any | None:
    """Reserve worker threads above the pipeline budget on ``limiter``.

    Starlette dispatches every sync route through
    ``anyio.to_thread.run_sync``, which draws a token from anyio's *per-event-loop*
    default :class:`~anyio.CapacityLimiter` (40 tokens, measured at runtime).
    Without reserved headroom, a burst of pipelines that fills the pool makes
    *every* sync route unserviceable — including ``/api/v1/health``, so the
    container healthcheck times out three times and a healthy instance is killed
    mid-validation.

    The pool is therefore sized to ``pipeline_concurrency + THREAD_LIMITER_HEADROOM``
    (floored at :data:`DEFAULT_THREAD_LIMITER_TOKENS`, so a tiny pipeline budget
    does not make the pool smaller than anyio's own default). The value is only
    ever **raised**: an operator who deliberately set a larger pool keeps it, and
    restarting the process never silently undoes that capacity decision.

    Returns ``None`` when called outside a running event loop (no limiter to
    configure — e.g. from a plain unit test), so callers never have to guard.
    ``pipeline_concurrency`` is coerced through :func:`_parse_positive_int`, so a
    garbage value degrades to the shipped default rather than raising on the
    startup path.
    """
    try:
        target = limiter or anyio.to_thread.current_default_thread_limiter()
    except RuntimeError:
        return None
    budget = _parse_positive_int(pipeline_concurrency, DEFAULT_PIPELINE_CONCURRENCY)  # type: ignore[arg-type]
    floor = max(DEFAULT_THREAD_LIMITER_TOKENS, budget + THREAD_LIMITER_HEADROOM)
    if target.total_tokens < floor:
        target.total_tokens = floor
    return target


def _sync_routes_are_admitted(
    limit: int, run: Callable[[], _PipelineResult]
) -> tuple[bool, _PipelineResult | None]:
    """Run ``run`` if fewer than ``limit`` such calls are in flight.

    Bounded admission for the CPU-bound pipelines, applied **before** a worker
    thread is claimed. Returns ``(True, result)`` when the call was admitted and
    ``(False, None)`` when the budget was already full.

    The bound is what turns threadpool exhaustion from a hang into a fast,
    visible refusal: without it, a burst of CI traffic queues unboundedly and
    drags the liveness probe down with it (measured: 45 concurrent 20 k-episode
    POSTs made ``/api/v1/health`` unreachable for 20 s+).

    A non-positive ``limit`` means "unbounded" and runs the work directly, so a
    misconfigured budget can never turn into a route that refuses everything.
    """
    if limit < 1:
        return True, run()
    if not _PIPELINE_GATE.try_acquire(limit):
        return False, None
    try:
        return True, run()
    finally:
        _PIPELINE_GATE.release()


async def _run_pipeline_admitted(
    limit: int, work: Callable[[], _PipelineResult]
) -> tuple[bool, _PipelineResult | None]:
    """Await ``work`` on a worker thread under bounded admission.

    Awaits :func:`_sync_routes_are_admitted` through the threadpool so the gate is
    held by whichever thread does the work, and never blocks the event loop while
    waiting. Returns ``(admitted, result)``.
    """
    return await run_in_threadpool(_sync_routes_are_admitted, limit, work)


class _OverloadResponse(JSONResponse):
    """``503`` + ``Retry-After`` for a request refused by the admission gate.

    Deliberately not a :class:`HTTPException`: raising one from inside the
    pipeline closure would have to travel out of a worker thread, and
    :class:`HTTPException` is only converted to a response by FastAPI's
    exception handler on the event loop. Returning a ready-made response keeps
    the refusal identical to every other response and lets the observability
    middleware count it normally.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            content={
                "detail": "validation capacity exhausted; retry shortly",
                "error": "overloaded",
            },
            headers={"Retry-After": str(_OVERLOAD_RETRY_AFTER_SECONDS)},
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
            # ``None`` preserves the engine default (85.0); an explicit value
            # from the request is honoured so the dashboard gate knob is live.
            threshold=(
                request.threshold
                if request.threshold is not None
                else DEFAULT_THRESHOLD
            ),
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

    # An empty or whitespace value means "not configured", never "authenticate
    # against the empty string" — the latter made a missing header compare
    # equal to the key and opened every route while health claimed auth was on.
    # Resolved here rather than beside the auth dependency below, because the
    # middleware ordering block that follows needs ``api_key``.
    api_key = (os.environ.get("VALIDSIM_API_KEY") or "").strip() or None
    deployment = (os.environ.get("VALIDSIM_ENV") or "development").strip().lower()

    # Observability: one process-local metrics collector shared between the
    # request middleware (which bumps the HTTP request counters) and the
    # /metrics route (which renders them alongside store-derived gauges).
    # Ordering is load-bearing, and inverted from the usual intuition.
    # ``add_middleware`` inserts at position 0, and Starlette composes so that
    # ``user_middleware[0]`` is the OUTERMOST layer -- meaning the LAST one
    # registered is the first one entered. Two consequences:
    #
    # * _ObservabilityMiddleware must be registered last, so it wraps everything
    #   and records the final status of every response, including 401/429.
    # * _DocsAuthMiddleware must be registered BEFORE observability, so that
    #   observability is outermost. Registering it afterwards made it outermost
    #   and short-circuited the docs gate before observability was ever entered:
    #   an anonymous probe of /docs returned 401 with no X-Request-ID and no
    #   increment to validsim_http_requests_total -- i.e. exactly the requests an
    #   operator most wants to correlate were the only ones that vanished.
    if api_key is not None:
        application.add_middleware(_DocsAuthMiddleware, api_key=api_key)

    # Registered last so it is the outermost user middleware and therefore
    # observes the final status of every response, including 401/429.
    application.state.metrics = Metrics()
    application.add_middleware(
        _ObservabilityMiddleware, metrics=application.state.metrics
    )

    @asynccontextmanager
    async def _tune_worker_pool(application: FastAPI) -> AsyncIterator[None]:
        """Reserve worker-thread headroom for the whole life of the app.

        Runs on server start, when an event loop exists and anyio's per-loop
        thread limiter can actually be resized. Doing it here rather than in
        :func:`create_app` is deliberate: the limiter belongs to the running loop,
        not to the app object, so a test-built app (no server) simply skips it and
        the helper returns ``None`` off-loop.
        """
        _apply_thread_limiter_headroom(None, application.state.pipeline_concurrency)
        yield

    application.router.lifespan_context = _tune_worker_pool

    def get_store() -> ValidationStore:
        """Dependency providing the app's validation store."""
        return application.state.store  # type: ignore[return-value]

    # Availability budget, resolved once here and published on state so the
    # health probe reports it and the wrapper below can read it. The thread
    # limiter is configured on server start (see `_tune_worker_pool`) because
    # it only exists once an event loop is running.
    pipeline_concurrency = _parse_positive_int(
        os.environ.get(PIPELINE_CONCURRENCY_ENV), DEFAULT_PIPELINE_CONCURRENCY
    )
    thread_limiter_tokens = _parse_positive_int(
        os.environ.get(THREAD_LIMITER_ENV),
        max(DEFAULT_THREAD_LIMITER_TOKENS, pipeline_concurrency + THREAD_LIMITER_HEADROOM),
    )
    _PIPELINE_GATE.resize(pipeline_concurrency)
    application.state.pipeline_concurrency = pipeline_concurrency
    application.state.thread_limiter_tokens = thread_limiter_tokens

    # The auth comparison runs on UTF-8 bytes (see require_api_key), which makes
    # a *correctly supplied* non-ASCII key work rather than raising TypeError.
    # But the header itself must still be latin-1 transport-encodable per RFC 7230
    # — and httpx refuses to even send a non-ASCII header value, raising
    # UnicodeEncodeError client-side. A key containing an accent is therefore
    # unusable in practice: every request 401s, indistinguishable from a wrong
    # key, while /api/v1/health still reports status ok. That silent lockout is
    # worse than a boot failure, so reject it here, loudly, with the reason.
    if api_key is not None and not api_key.isascii():
        raise ValueError(
            f"VALIDSIM_API_KEY must contain only ASCII characters; got "
            f"{len(api_key)} chars, some non-ASCII. HTTP header values cannot "
            "carry non-ASCII text (RFC 7230), so such a key can never "
            "authenticate a client — the API would reject every request with a "
            "misleading 401. Use an ASCII-safe key."
        )

    # FastAPI mounts /docs, /redoc and /openapi.json itself, outside the
    # /api/v1 router the X-API-Key dependency is attached to. With a key
    # configured they therefore answered 200 to an anonymous caller, publishing
    # the full route surface, every parameter and every response schema of an
    # otherwise authenticated API. They are schema and documentation endpoints,
    # not data, but they are a reconnaissance gift, so an authenticated
    # deployment must gate them the same way it gates everything else. The
    # health probe stays public (see PUBLIC_PATHS).
    if api_key is None and deployment in AUTH_REQUIRED_ENVS:
        raise RuntimeError(
            f"VALIDSIM_ENV={deployment!r} requires VALIDSIM_API_KEY; refusing to "
            "start an API that would serve every route unauthenticated"
        )
    auth_log = get_logger("api.auth")
    if api_key is None:
        auth_log.warning(
            "auth policy resolved",
            extra={"auth_enabled": False, "deployment": deployment},
        )
    else:
        auth_log.info(
            "auth policy resolved",
            extra={"auth_enabled": True, "deployment": deployment},
        )

    def require_api_key(request: Request) -> None:
        """Dependency enforcing ``X-API-Key`` auth on /api/v1 routes.

        Enabled only when a non-blank ``VALIDSIM_API_KEY`` was set when the app
        was built; :data:`PUBLIC_PATHS` is reachable either way. Compares the
        header to the configured key in constant time; missing, empty, or wrong
        values all raise a uniform 401 that does not reveal whether the key
        exists.

        The comparison runs on UTF-8 *bytes* rather than ``str``: ``str``
        comparison via :func:`secrets.compare_digest` raises ``TypeError`` for
        any non-ASCII input, so a deployment with a non-ASCII key (e.g. one
        containing an accented character) turned every authenticated request
        into an unhandled ``500`` instead of a clean 401/200. Byte comparison
        is also the correct primitive here -- it keeps the constant-time
        property and avoids the implicit encoder entirely.
        """
        if api_key is None or request.url.path in PUBLIC_PATHS:
            return
        supplied = request.headers.get(API_KEY_HEADER, "")
        if not secrets.compare_digest(supplied.encode("utf-8"), api_key.encode("utf-8")):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    def require_api_key_for_destructive(request: Request) -> None:
        """Dependency enforcing ``X-API-Key`` auth on destructive routes.

        ``DELETE`` permanently removes stored runs, so it carries its own gate
        attached directly to the route rather than relying only on the global
        ``VALIDSIM_API_KEY`` toggle wired on the app router. The check is the
        shared :func:`require_api_key`, so a key is validated identically
        wherever it is required; with no key configured it is a deliberate
        no-op so local development and the open test-suite keep working — but
        any deployment that exposes ``DELETE`` over a network MUST set
        ``VALIDSIM_API_KEY`` (or run with ``VALIDSIM_ENV=production``, which
        refuses to start without it), otherwise destructive operations run
        unauthenticated (see the module docstring).
        """
        require_api_key(request)

    application.state.api_key_enabled = api_key is not None
    application.router.dependencies = [Depends(require_api_key)]

    @application.get("/api/v1/health")
    def health(request: Request) -> dict[str, Any]:
        """Liveness/readiness probe with the effective runtime configuration.

        Cheap by design: every field is read from ``app.state`` (populated once
        at :func:`create_app` time) or derived from the injected store/queue
        class names, so the route performs no I/O and is safe to back a
        container healthcheck. This is the one ``/api/v1`` route deliberately
        exempt from the ``X-API-Key`` gate (see :data:`PUBLIC_PATHS`): a probe
        cannot present a credential, and what it discloses is configuration,
        not data. ``status`` and ``version`` are stable contract keys; the rest
        describe how the instance is configured:

        * ``auth_enabled`` — whether ``X-API-Key`` auth is enforced. The probe
          itself is reachable either way, so this is how an operator confirms
          the policy actually in force.
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
    async def create_validation(
        request: ValidationRequest,
        st: ValidationStore = Depends(get_store),
        application: FastAPI = Depends(lambda: application),
    ) -> Any:
        """Run a validation synchronously and return its scorecard.

        The only ``/api/v1`` route that is ``async def``, and it is ``async`` for
        one reason: the CPU-bound pipeline is submitted to the worker pool
        explicitly, under a bounded admission gate, instead of FastAPI submitting
        it implicitly and without a limit. A 20 000-episode run holds a worker
        thread for ~7 s; unbounded, a burst of them drains the pool and the
        liveness probe stops being answerable. So:

        * admission is checked **before** a thread is claimed, and a full budget
          is refused immediately with ``503`` + ``Retry-After`` rather than
          queued invisibly behind other pipelines;
        * the pipeline itself still runs in a thread (``run_in_threadpool``), so
          the event loop is not blocked and the container stays responsive to
          shutdown signals.

        Every other route stays a plain ``def``: they are cheap, and making them
        ``async`` would move blocking work onto the event loop.
        """
        limit = getattr(application.state, "pipeline_concurrency", DEFAULT_PIPELINE_CONCURRENCY)
        admitted, stored = await _run_pipeline_admitted(
            limit, lambda: _execute_validation(request, st)
        )
        if not admitted or stored is None:
            return _OverloadResponse()
        return stored.scorecard.to_dict()

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
