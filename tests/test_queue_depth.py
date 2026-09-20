"""Tests for the bounded job-queue depth (audit H3: unbounded queue growth).

Covers the new ``max_depth`` cap on both backends — the in-memory
:class:`~validsim.jobs.queue.JobQueue` dict path and the Redis
:class:`~validsim.jobs.queue.RedisJobQueue` index path — including the distinct
:class:`~validsim.jobs.queue.QueueFullError`, the
``VALIDSIM_JOB_QUEUE_MAX_DEPTH`` env override, and the guarantee that a
rejected enqueue leaves the queue unchanged. Redis is exercised with the same
server-absent fake-client pattern used by ``test_jobs.py`` (injected via
``_client_factory``), so no live Redis is required.
"""

from __future__ import annotations

from typing import Any

import pytest

from validsim.jobs.queue import (
    JobQueue,
    JobSpec,
    QueueFullError,
    RedisJobQueue,
)

_MAX_DEPTH_ENV = "VALIDSIM_JOB_QUEUE_MAX_DEPTH"
_INDEX_KEY = "validsim:jobs:index"


def _spec(run_id: str) -> JobSpec:
    return JobSpec(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        episodes=10,
        adversarial=0,
    )


def _fill(queue: JobQueue, n: int) -> None:
    """Enqueue ``n`` jobs with unique run ids (assumes the cap allows it)."""
    for i in range(n):
        queue.enqueue(_spec(f"vrun-{i:08d}"))


class _FakeRedis:
    """Minimal redis-py stand-in mirroring the tiny store used in test_jobs."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    def exists(self, key: str) -> int:
        return int(key in self._kv)

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = list(self._lists.get(key, []))
        if end < 0:
            end = len(items)
        return items[start:end]

    def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic depth-cap environment for every test in this module."""
    monkeypatch.delenv(_MAX_DEPTH_ENV, raising=False)
    monkeypatch.delenv("VALIDSIM_JOB_QUEUE", raising=False)
    monkeypatch.delenv("VALIDSIM_REDIS_URL", raising=False)


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class TestMemoryDepthCap:
    def test_enqueue_up_to_cap_is_ok(self) -> None:
        q = JobQueue(max_depth=3)
        _fill(q, 3)
        assert len(q) == 3
        assert q.max_depth == 3

    def test_cap_plus_one_raises_queue_full(self) -> None:
        q = JobQueue(max_depth=2)
        _fill(q, 2)
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-overflow"))

    def test_rejected_enqueue_leaves_queue_unchanged(self) -> None:
        q = JobQueue(max_depth=1)
        first = q.enqueue(_spec("vrun-aaaaaaaa"))
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-bbbbbbbb"))
        assert len(q) == 1
        assert [r.job_id for r in q.list()] == [first.job_id]

    def test_default_cap_is_1000(self) -> None:
        assert JobQueue().max_depth == 1000

    def test_duplicate_still_value_error_before_depth(self) -> None:
        # A duplicate on a full queue keeps the more specific ValueError.
        q = JobQueue(max_depth=1)
        q.enqueue(_spec("vrun-aaaaaaaa"))
        with pytest.raises(ValueError, match="already exists"):
            q.enqueue(_spec("vrun-aaaaaaaa"))

    def test_non_positive_max_depth_rejected(self) -> None:
        with pytest.raises(ValueError):
            JobQueue(max_depth=0)
        with pytest.raises(ValueError):
            JobQueue(max_depth=-5)


# ---------------------------------------------------------------------------
# Redis-backed (fake client — no server)
# ---------------------------------------------------------------------------


class TestRedisDepthCap:
    def _queue(self, **kwargs: Any) -> tuple[RedisJobQueue, _FakeRedis]:
        client = _FakeRedis()
        queue = RedisJobQueue(_client_factory=lambda: client, **kwargs)
        return queue, client

    def test_enqueue_up_to_cap_is_ok(self) -> None:
        queue, client = self._queue(max_depth=3)
        _fill(queue, 3)
        assert len(queue) == 3
        assert client.llen(_INDEX_KEY) == 3

    def test_cap_plus_one_raises_queue_full(self) -> None:
        queue, _ = self._queue(max_depth=2)
        _fill(queue, 2)
        with pytest.raises(QueueFullError):
            queue.enqueue(_spec("vrun-overflow"))

    def test_rejected_enqueue_does_not_touch_index(self) -> None:
        queue, client = self._queue(max_depth=1)
        queue.enqueue(_spec("vrun-aaaaaaaa"))
        before = list(client._lists[_INDEX_KEY])
        with pytest.raises(QueueFullError):
            queue.enqueue(_spec("vrun-bbbbbbbb"))
        # No partial write: index unchanged, overflow job key absent.
        assert client._lists[_INDEX_KEY] == before
        assert client.exists("validsim:jobs:vrun-bbbbbbbb") == 0
        assert len(queue) == 1

    def test_default_cap_is_1000(self) -> None:
        queue, _ = self._queue()
        assert queue.max_depth == 1000


# ---------------------------------------------------------------------------
# Environment override
# ---------------------------------------------------------------------------


class TestEnvOverride:
    def test_memory_env_override_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_MAX_DEPTH_ENV, "2")
        q = JobQueue()  # no explicit max_depth -> env
        assert q.max_depth == 2
        _fill(q, 2)
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-overflow"))

    def test_redis_env_override_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_MAX_DEPTH_ENV, "1")
        client = _FakeRedis()
        q = RedisJobQueue(_client_factory=lambda: client)
        assert q.max_depth == 1
        q.enqueue(_spec("vrun-aaaaaaaa"))
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-bbbbbbbb"))

    def test_explicit_argument_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_MAX_DEPTH_ENV, "5")
        assert JobQueue(max_depth=2).max_depth == 2

    def test_env_with_whitespace_is_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_MAX_DEPTH_ENV, "  4  ")
        assert JobQueue().max_depth == 4

    def test_malformed_env_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_MAX_DEPTH_ENV, "not-a-number")
        with pytest.raises(ValueError):
            JobQueue()
