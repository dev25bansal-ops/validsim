"""Async validation job queue backed by memory or Redis.

:class:`JobQueue` is the thread-safe in-memory default; :class:`RedisJobQueue`
subclasses it and persists the same records to Redis so multiple processes (the
API and a future GPU worker) can share one queue. The two backends expose an
identical interface (enqueue/get/list/update_status/__len__/close) so callers
are backend-agnostic.

Redis is treated exactly like the optional PostgreSQL store: the driver is
imported lazily, never at module level, so importing this module always
succeeds and a missing driver surfaces as an actionable ``RuntimeError``
("pip install redis") at first use. The client connection is also opened
lazily, so :func:`create_job_queue` returns a configured queue without
touching the network.

Redis serialization rules:

* Every job is a single JSON string under a plain, parameter-free key
  (``validsim:jobs:<run_id>``) — no URL/query parameters are ever baked into
  keys, and job payloads never travel through Redis hashes or pickles.
* An insertion-order index is kept as a Redis list (``validsim:jobs:index``)
  of run ids, preserving FIFO semantics for :meth:`RedisJobQueue.list`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from validsim.jobs.models import JobRecord, JobSpec, JobStatus

__all__ = ["JobQueue", "RedisJobQueue", "QueueFullError", "create_job_queue"]

#: Env var selecting the backend ("memory" | "redis"); defaults to memory.
_QUEUE_ENV = "VALIDSIM_JOB_QUEUE"
#: Env var supplying the Redis URL (e.g. ``redis://localhost:6379/0``).
_REDIS_URL_ENV = "VALIDSIM_REDIS_URL"
#: Env var overriding the default queue depth cap (a positive integer).
_MAX_DEPTH_ENV = "VALIDSIM_JOB_QUEUE_MAX_DEPTH"

#: Default maximum number of jobs the queue holds before rejecting new work.
#: Bounds the otherwise-unbounded growth flagged by audit H3. Overridable
#: per-instance via the ``max_depth`` argument or process-wide via
#: ``VALIDSIM_JOB_QUEUE_MAX_DEPTH``.
_DEFAULT_MAX_DEPTH = 1000

#: Namespace prefix for all Redis keys owned by this queue.
_KEY_PREFIX = "validsim:jobs"


class QueueFullError(Exception):
    """Raised by :meth:`JobQueue.enqueue` when the queue is at ``max_depth``.

    A distinct exception type so callers (e.g. the HTTP router) can map a full
    queue to a specific response (such as ``503 Service Unavailable``) without
    confusing it with the ``ValueError`` raised for a duplicate ``run_id``.
    """


def _utc_now() -> str:
    """Current UTC time as a second-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_max_depth(explicit: int | None) -> int:
    """Resolve the effective queue depth cap for a freshly built queue.

    Precedence: an explicit ``max_depth`` argument wins; otherwise the
    ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` environment variable is used when set;
    otherwise :data:`_DEFAULT_MAX_DEPTH` applies.

    Args:
        explicit: Caller-supplied cap, or ``None`` to defer to env/default.

    Returns:
        A positive integer depth cap.

    Raises:
        ValueError: If the resolved value is not a positive integer (an
            explicit non-numeric argument, or a malformed env var).
    """
    if explicit is None:
        raw = (os.environ.get(_MAX_DEPTH_ENV) or "").strip()
        if not raw:
            return _DEFAULT_MAX_DEPTH
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{_MAX_DEPTH_ENV} must be a positive integer, got {raw!r}"
            ) from exc
    else:
        try:
            value = int(explicit)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"max_depth must be a positive integer, got {explicit!r}"
            ) from exc
    if value < 1:
        raise ValueError(f"max_depth must be a positive integer, got {value!r}")
    return value


def _import_redis() -> Any:
    """Import and return the redis-py driver module.

    The call is deliberately lazy (never at module import), mirroring the
    psycopg pattern used by the PostgreSQL store.

    Raises:
        RuntimeError: If the driver is not installed, with an actionable
            install hint. Importing this module never triggers the failure.
    """
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError(
            "The Redis job queue requires the redis-py driver, which is not "
            "installed. Install it with: pip install redis"
        ) from exc
    return redis


def _apply_status(
    record: JobRecord,
    status: JobStatus,
    error: str | None,
    result: str | None,
) -> JobRecord:
    """Return a new record reflecting the transition to ``status``.

    Timestamps follow lifecycle rules: ``started_at`` is set the first time a
    job becomes running, and ``finished_at`` is set on any terminal state.
    ``error``/``result`` are recorded only for failed/done respectively.
    """
    now = _utc_now()
    changes: dict[str, Any] = {"status": status}
    if status is JobStatus.RUNNING and record.started_at is None:
        changes["started_at"] = now
    if status in (JobStatus.DONE, JobStatus.FAILED):
        changes["finished_at"] = now
    if status is JobStatus.FAILED:
        changes["error"] = error
    if status is JobStatus.DONE:
        changes["result"] = result
    return dataclasses.replace(record, **changes)


class JobQueue:
    """Thread-safe in-memory job queue (the default backend).

    Jobs are keyed by their spec's ``run_id``; :meth:`list` returns them in
    insertion (FIFO) order. The number of jobs held is bounded by
    ``max_depth`` (default :data:`_DEFAULT_MAX_DEPTH`, env-overridable via
    ``VALIDSIM_JOB_QUEUE_MAX_DEPTH``): once the queue is at capacity,
    :meth:`enqueue` raises :class:`QueueFullError` instead of growing without
    limit (audit H3).
    """

    def __init__(self, *, max_depth: int | None = None) -> None:
        """Create an empty queue.

        Args:
            max_depth: Maximum number of jobs the queue will hold before
                rejecting new work. ``None`` (the default) defers to the
                ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` env var, then to
                :data:`_DEFAULT_MAX_DEPTH`. Must resolve to a positive integer.

        Raises:
            ValueError: If the resolved ``max_depth`` is not a positive integer.
        """
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._max_depth = _resolve_max_depth(max_depth)

    @property
    def max_depth(self) -> int:
        """Maximum number of jobs this queue will hold before rejecting work."""
        return self._max_depth

    @staticmethod
    def new_run_id() -> str:
        """Generate a fresh job/run id like ``"vrun-1a2b3c4d"``."""
        return f"vrun-{uuid.uuid4().hex[:8]}"

    def enqueue(self, spec: JobSpec) -> JobRecord:
        """Add ``spec`` as a queued job and return its record.

        Raises:
            ValueError: If a job with ``spec.run_id`` already exists.
            QueueFullError: If the queue already holds ``max_depth`` jobs.
        """
        with self._lock:
            if spec.run_id in self._jobs:
                raise ValueError(f"job {spec.run_id} already exists")
            if len(self._jobs) >= self._max_depth:
                raise QueueFullError(
                    f"job queue is full ({len(self._jobs)}/{self._max_depth}); "
                    "cannot enqueue more jobs"
                )
            record = JobRecord(
                spec=spec,
                status=JobStatus.QUEUED,
                created_at=_utc_now(),
            )
            self._jobs[spec.run_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job record for ``job_id`` or ``None`` if unknown."""
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobRecord]:
        """All jobs in insertion (FIFO) order."""
        with self._lock:
            return list(self._jobs.values())

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: str | None = None,
    ) -> JobRecord:
        """Transition ``job_id`` to ``status``, returning the updated record.

        Raises:
            KeyError: If ``job_id`` is unknown.
        """
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            updated = _apply_status(record, status, error, result)
            self._jobs[job_id] = updated
            return updated

    def __len__(self) -> int:
        """Number of queued jobs."""
        with self._lock:
            return len(self._jobs)

    def close(self) -> None:
        """Release backend resources (no-op for the in-memory backend).

        Present so callers can treat every :func:`create_job_queue` backend
        uniformly; the Redis backend overrides this to close its client.
        """


class RedisJobQueue(JobQueue):
    """Redis-backed queue satisfying the :class:`JobQueue` interface.

    Subclasses the in-memory queue purely to advertise interface compatibility
    (and reuse :meth:`new_run_id`); every state-touching method is overridden
    to read/write Redis. The client connection is opened lazily on first use
    under a :class:`threading.Lock` (redis-py clients are not safe for
    concurrent use by multiple threads).
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        max_depth: int | None = None,
        _client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Configure the queue without opening a connection.

        Args:
            url: Redis URL (``redis://...``); defaults to the
                ``VALIDSIM_REDIS_URL`` environment variable.
            max_depth: Maximum number of jobs the queue will hold before
                rejecting new work. ``None`` defers to the
                ``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` env var, then to
                :data:`_DEFAULT_MAX_DEPTH`. Must resolve to a positive integer.
            _client_factory: Test seam — a zero-argument callable returning a
                redis-client-like object. When omitted, the queue opens a real
                ``redis.Redis.from_url(url, decode_responses=True)`` client on
                first use.

        Raises:
            ValueError: When no URL is available (argument or env var) and the
                redis-py driver is installed. With the driver missing,
                construction still succeeds and the actionable missing-driver
                ``RuntimeError`` surfaces at first use instead. Also raised if
                the resolved ``max_depth`` is not a positive integer.
        """
        self._lock = threading.Lock()
        self._client: Any | None = None
        self._client_factory = _client_factory
        self._max_depth = _resolve_max_depth(max_depth)

        resolved = (url or "").strip() or (os.environ.get(_REDIS_URL_ENV) or "").strip()
        if not resolved and _client_factory is None:
            # Fail fast only when the driver is present; otherwise the more
            # actionable error (missing driver) surfaces at first use.
            try:
                _import_redis()
            except RuntimeError:
                pass
            else:
                raise ValueError(
                    "No Redis URL configured: pass url= or set the "
                    f"{_REDIS_URL_ENV} environment variable."
                )
        self._url: str | None = resolved or None

    # -- introspection ------------------------------------------------------

    @property
    def url(self) -> str | None:
        """Resolved Redis URL (``None`` when a client factory was given)."""
        return self._url

    @staticmethod
    def _job_key(job_id: str) -> str:
        """Parameter-free Redis key holding one job's JSON payload."""
        return f"{_KEY_PREFIX}:{job_id}"

    @staticmethod
    def _index_key() -> str:
        """Parameter-free Redis key holding the insertion-order index list."""
        return f"{_KEY_PREFIX}:index"

    # -- connection lifecycle ------------------------------------------------

    def _default_client(self) -> Any:
        """Open the real redis-py client on first use (requires the driver)."""
        redis = _import_redis()
        if not self._url:
            raise ValueError(
                "No Redis URL configured: pass url= or set the "
                f"{_REDIS_URL_ENV} environment variable."
            )
        return redis.Redis.from_url(self._url, decode_responses=True)

    def _ensure_client(self) -> Any:
        """Return the live client, opening it on demand; caller holds the lock."""
        if self._client is None:
            factory = self._client_factory or self._default_client
            self._client = factory()
        return self._client

    # -- JobQueue interface ---------------------------------------------------

    def enqueue(self, spec: JobSpec) -> JobRecord:
        """Serialize and store a new queued job, returning its record.

        The depth check reads the insertion-order index length under the same
        lock that performs the ``set``/``rpush`` writes, so a single process
        cannot exceed ``max_depth`` (audit H3).

        Raises:
            ValueError: If a job with ``spec.run_id`` already exists.
            QueueFullError: If the queue already holds ``max_depth`` jobs.
        """
        key = self._job_key(spec.run_id)
        record = JobRecord(
            spec=spec,
            status=JobStatus.QUEUED,
            created_at=_utc_now(),
        )
        payload = json.dumps(record.to_dict())
        with self._lock:
            client = self._ensure_client()
            if client.exists(key):
                raise ValueError(f"job {spec.run_id} already exists")
            depth = int(client.llen(self._index_key()))
            if depth >= self._max_depth:
                raise QueueFullError(
                    f"job queue is full ({depth}/{self._max_depth}); "
                    "cannot enqueue more jobs"
                )
            client.set(key, payload)
            client.rpush(self._index_key(), spec.run_id)
        return record

    def get(self, job_id: str) -> JobRecord | None:
        """Return the deserialized job record or ``None`` if unknown."""
        key = self._job_key(job_id)
        with self._lock:
            raw = self._ensure_client().get(key)
        return JobRecord.from_dict(json.loads(raw)) if raw is not None else None

    def list(self) -> list[JobRecord]:
        """All jobs in insertion (FIFO) order, following the index list."""
        with self._lock:
            client = self._ensure_client()
            job_ids = client.lrange(self._index_key(), 0, -1)
            records: list[JobRecord] = []
            for job_id in job_ids:
                raw = client.get(self._job_key(job_id))
                if raw is not None:
                    records.append(JobRecord.from_dict(json.loads(raw)))
        return records

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: str | None = None,
        result: str | None = None,
    ) -> JobRecord:
        """Transition ``job_id`` to ``status``, persisting the updated record.

        Raises:
            KeyError: If ``job_id`` is unknown.
        """
        key = self._job_key(job_id)
        with self._lock:
            client = self._ensure_client()
            raw = client.get(key)
            if raw is None:
                raise KeyError(job_id)
            record = JobRecord.from_dict(json.loads(raw))
            updated = _apply_status(record, status, error, result)
            client.set(key, json.dumps(updated.to_dict()))
        return updated

    def __len__(self) -> int:
        """Number of queued jobs (length of the insertion-order index)."""
        with self._lock:
            return int(self._ensure_client().llen(self._index_key()))

    def close(self) -> None:
        """Close the underlying client (a later query reopens it)."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None


def create_job_queue() -> JobQueue:
    """Build a job queue from the environment configuration.

    Reads ``VALIDSIM_JOB_QUEUE`` (case-insensitive):

    * ``"redis"`` — :class:`RedisJobQueue` using ``VALIDSIM_REDIS_URL``.
      Construction is side-effect free (no driver import or connection until
      first use), but it fails fast with ``ValueError`` when no URL is
      configured and the redis-py driver is installed.
    * anything else — the default in-memory :class:`JobQueue`, keeping tests
      and local runs side-effect free.
    """
    backend = os.environ.get(_QUEUE_ENV, "memory").strip().lower()
    if backend == "redis":
        return RedisJobQueue()
    return JobQueue()