"""Tests for bounded job-queue depth on both queue backends.

These tests drive the in-memory backend and the Redis Lua enqueue operation
through small stand-ins. The Redis depth test uses a live local Redis and skips
cleanly when unavailable; its assertions cover the capacity boundary and that a
rejected enqueue leaves all queue state untouched.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from validsim.jobs import queue as queue_mod
from validsim.jobs.models import JobStatus
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


@pytest.fixture()
def live_redis_depth() -> Any:
    pytest.importorskip("redis")
    url = os.environ.get("VALIDSIM_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    try:
        client = queue_mod._import_redis().Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.ping()
    except Exception:  # noqa: BLE001 - skip when local Redis is unavailable
        pytest.skip(f"live Redis unavailable at {url}")
    queue = RedisJobQueue(_client_factory=lambda: client)
    try:
        client.delete(queue._index_key(), queue._ready_key())
        yield queue, client
    finally:
        keys = list(client.scan_iter("validsim:jobs:vrun-*"))
        if keys:
            client.delete(*keys)
        client.delete(queue._index_key(), queue._ready_key())
        client.close()


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


class TestMemoryCapCountsPendingWorkOnly:
    """The cap must bound *pending work*, not all-time retained history.

    Counting terminal records meant a queue that had already processed every job
    it was ever handed still refused further work, so a long-lived queue
    dead-locked itself after ``max_depth`` lifetime jobs (default 1000) and
    could only be revived by manual key surgery. Capacity limits are meant to
    bound outstanding work, so draining the backlog must free capacity.
    """

    @staticmethod
    def _drain(queue: JobQueue) -> None:
        while True:
            record = queue.claim_next()
            if record is None:
                return
            queue.update_status(record.job_id, JobStatus.DONE)

    def test_drained_queue_accepts_new_work(self) -> None:
        q = JobQueue(max_depth=2)
        _fill(q, 2)
        self._drain(q)
        assert q.claim_next() is None  # backlog genuinely empty
        assert q.enqueue(_spec("vrun-after-drain")) is not None

    def test_many_lifetime_jobs_do_not_exhaust_the_cap(self) -> None:
        q = JobQueue(max_depth=2)
        for cycle in range(5):
            for i in range(2):
                q.enqueue(_spec(f"vrun-c{cycle}i{i:04d}"))
            self._drain(q)
        # 10 lifetime jobs processed, yet the queue still takes work.
        assert len(q.list()) == 10  # history retained
        q.enqueue(_spec("vrun-still-open"))

    def test_pending_still_blocks_beyond_the_cap(self) -> None:
        """The cap must still bite while work is genuinely outstanding."""
        q = JobQueue(max_depth=2)
        _fill(q, 2)
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-third"))

    def test_running_jobs_still_count_against_the_cap(self) -> None:
        """A claimed-but-unfinished job occupies capacity, so it must count."""
        q = JobQueue(max_depth=2)
        _fill(q, 2)
        claimed = q.claim_next()
        assert claimed is not None
        with pytest.raises(QueueFullError):
            q.enqueue(_spec("vrun-while-running"))


# ---------------------------------------------------------------------------
# Redis-backed (fake client — no server)
# ---------------------------------------------------------------------------


class TestRedisDepthCap:
    def test_enqueue_up_to_cap_is_ok(self, live_redis_depth: tuple[Any, Any]) -> None:
        live_queue, client = live_redis_depth
        queue = RedisJobQueue(
            _client_factory=lambda: client,
            max_depth=live_queue.max_depth,
        )
        queue._key_prefix = live_queue._key_prefix
        queue._ready_key_override = live_queue._ready_key()
        _fill(queue, 3)
        assert len(queue) == 3
        assert client.llen(_INDEX_KEY) == 3

    def test_cap_plus_one_raises_queue_full(
        self, live_redis_depth: tuple[Any, Any]
    ) -> None:
        live_queue, client = live_redis_depth
        queue = RedisJobQueue(_client_factory=lambda: client, max_depth=2)
        queue._key_prefix = live_queue._key_prefix
        queue._ready_key_override = live_queue._ready_key()
        _fill(queue, 2)
        with pytest.raises(QueueFullError):
            queue.enqueue(_spec("vrun-overflow"))

    def test_rejected_enqueue_does_not_touch_index(
        self, live_redis_depth: tuple[Any, Any]
    ) -> None:
        live_queue, client = live_redis_depth
        queue = RedisJobQueue(_client_factory=lambda: client, max_depth=1)
        queue._key_prefix = live_queue._key_prefix
        queue._ready_key_override = live_queue._ready_key()
        queue.enqueue(_spec("vrun-aaaaaaaa"))
        before = list(client.lrange(_INDEX_KEY, 0, -1))
        with pytest.raises(QueueFullError):
            queue.enqueue(_spec("vrun-bbbbbbbb"))
        assert client.lrange(_INDEX_KEY, 0, -1) == before
        assert client.exists("validsim:jobs:vrun-bbbbbbbb") == 0
        assert len(queue) == 1

    def test_default_cap_is_1000(self) -> None:
        assert RedisJobQueue(_client_factory=object).max_depth == 1000


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
        assert RedisJobQueue(_client_factory=object).max_depth == 1

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
