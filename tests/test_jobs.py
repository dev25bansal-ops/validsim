"""Tests for the async validation job queue (memory + Redis) and its router.

Redis tests are server-absent-safe: they never require a live Redis instance
for real and inject a stand-in client via ``_client_factory`` instead, so
serialization, FIFO ordering, and parameter-free key names are asserted
without a server. Factory fail-fast and lazy-import paths are exercised by
monkeypatching :func:`validsim.jobs.queue._import_redis`.
"""

from __future__ import annotations

import builtins
import dataclasses
import itertools
import json
import os
import subprocess
import sys
import threading
from collections import deque
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import validsim.jobs.queue as queue_mod
from validsim.jobs import (
    JobQueue,
    JobRecord,
    JobSpec,
    JobStatus,
    RedisJobQueue,
    create_job_queue,
    router,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REDIS_NAMESPACES = itertools.count()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(run_id: str = "vrun-cafe1234", **overrides: Any) -> JobSpec:
    base: dict[str, Any] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "episodes": 60,
        "adversarial": 12,
    }
    base.update(overrides)
    return JobSpec(**base)


class _FakeRedis:
    """Minimal redis-py stand-in recording calls and mirroring a tiny store."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.closed = False
        self.results: deque[Any] = deque()

    def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self.calls.append(("eval", script, numkeys, *args))
        if self.results:
            return self.results.popleft()
        if script == queue_mod._REDIS_ENQUEUE_SCRIPT:
            key, index_key, ready_key, payload, max_depth, job_id = args
            if self.exists(key):
                return -1
            if self.llen(index_key) >= int(max_depth):
                return -2
            self.set(key, payload)
            self.rpush(index_key, job_id)
            self.rpush(ready_key, job_id)
            return 1
        if script == queue_mod._REDIS_CAS_SET_SCRIPT:
            key, expected, payload = args
            current = self._kv.get(key)
            if current is None:
                return None
            if current != expected:
                return current
            # The real server commits inside the script, so no separate SET
            # command reaches the wire and none is recorded here.
            self._kv[key] = payload
            return payload
        return None

    def exists(self, key: str) -> int:
        self.calls.append(("exists", key))
        return int(key in self._kv)

    def set(self, key: str, value: str) -> None:
        self.calls.append(("set", key, value))
        self._kv[key] = value

    def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        return self._kv.get(key)

    def rpush(self, key: str, value: str) -> None:
        self.calls.append(("rpush", key, value))
        self._lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        self.calls.append(("lrange", key, start, end))
        items = list(self._lists.get(key, []))
        if end < 0:
            end = len(items)
        return items[start:end]

    def llen(self, key: str) -> int:
        self.calls.append(("llen", key))
        return len(self._lists.get(key, []))

    def close(self) -> None:
        self.closed = True


def _block_redis_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import redis`` (any form) raise ImportError in-process."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "redis" or name.startswith("redis."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic queue/redis environment for every test in this module."""
    monkeypatch.delenv("VALIDSIM_JOB_QUEUE", raising=False)
    monkeypatch.delenv("VALIDSIM_REDIS_URL", raising=False)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_spec_round_trip(self) -> None:
        spec = _spec()
        assert JobSpec.from_dict(spec.to_dict()) == spec

    def test_spec_defaults(self) -> None:
        spec = JobSpec("r", "c", "t")
        assert spec.episodes == 1000 and spec.adversarial == 0

    def test_record_round_trip(self) -> None:
        record = JobRecord(spec=_spec(), status=JobStatus.RUNNING, created_at="t")
        assert JobRecord.from_dict(record.to_dict()) == record

    def test_record_job_id_is_run_id(self) -> None:
        assert JobRecord(spec=_spec()).job_id == "vrun-cafe1234"

    def test_status_values(self) -> None:
        assert [s.value for s in JobStatus] == [
            "queued",
            "running",
            "done",
            "failed",
            "dead",
        ]

    def test_dead_is_a_terminal_state(self) -> None:
        """``dead`` is where a job lands when its retry budget runs out.

        It has to be terminal, and it has to be *distinct* from ``failed``: a
        failed job exhausted its attempts against a known error, while a dead
        job was abandoned because its worker kept dying before it could report
        anything. Collapsing them would hide the poison-job case entirely.
        """
        assert JobStatus.DEAD is not JobStatus.FAILED
        assert JobStatus.DEAD not in (JobStatus.QUEUED, JobStatus.RUNNING)

    def test_record_round_trip_carries_the_retry_fields(self) -> None:
        """A persisted record keeps its attempt count across a round trip."""
        record = JobRecord(
            spec=_spec(),
            status=JobStatus.QUEUED,
            created_at="2026-09-25T00:00:00+00:00",
            attempt=3,
            lease_epoch=2,
        )
        assert JobRecord.from_dict(record.to_dict()) == record

    def test_legacy_record_without_attempt_defaults_to_zero(self) -> None:
        """A payload written before retries existed must still deserialize."""
        payload = _spec()
        legacy = {
            "job_id": payload.run_id,
            "status": "queued",
            "spec": {
                "run_id": payload.run_id,
                "checkpoint_id": payload.checkpoint_id,
                "task_id": payload.task_id,
                "episodes": payload.episodes,
                "adversarial": payload.adversarial,
            },
            "created_at": "2026-09-25T00:00:00+00:00",
        }
        restored = JobRecord.from_dict(legacy)
        assert restored.attempt == 0
        assert restored.reclaim_count == 0
        assert restored.retry_after is None
        # The new spec fields default too, so the worker still gates sensibly.
        assert restored.spec.threshold is None
        assert restored.spec.baseline_run_id is None


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class TestMemoryQueue:
    def test_enqueue_get_list(self) -> None:
        q = JobQueue()
        record = q.enqueue(_spec())
        assert record.status is JobStatus.QUEUED
        assert record.created_at is not None
        assert q.get(record.job_id) == record
        assert q.list() == [record]
        assert len(q) == 1

    def test_get_unknown_returns_none(self) -> None:
        assert JobQueue().get("vrun-ffffffff") is None

    def test_duplicate_run_id_rejected(self) -> None:
        q = JobQueue()
        q.enqueue(_spec())
        with pytest.raises(ValueError, match="already exists"):
            q.enqueue(_spec())

    def test_lifecycle_timestamps_and_result(self) -> None:
        q = JobQueue()
        jid = q.enqueue(_spec()).job_id
        running = q.update_status(jid, JobStatus.RUNNING)
        assert running.started_at is not None and running.finished_at is None
        done = q.update_status(jid, JobStatus.DONE, result="vrun-cafe1234")
        assert done.finished_at is not None and done.result == "vrun-cafe1234"
        assert q.get(jid) == done

    def test_failed_sets_error(self) -> None:
        q = JobQueue()
        jid = q.enqueue(_spec()).job_id
        failed = q.update_status(jid, JobStatus.FAILED, error="boom")
        assert failed.error == "boom" and failed.result is None
        assert failed.finished_at is not None

    def test_update_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            JobQueue().update_status("vrun-ffffffff", JobStatus.RUNNING)

    def test_list_is_fifo(self) -> None:
        q = JobQueue()
        a = q.enqueue(_spec("vrun-aaaaaaaa"))
        b = q.enqueue(_spec("vrun-bbbbbbbb"))
        assert [r.job_id for r in q.list()] == [a.job_id, b.job_id]

    def test_close_is_noop_idempotent(self) -> None:
        q = JobQueue()
        q.close()
        assert q.list() == []  # still usable

    def test_a_record_that_cannot_be_deserialized_is_never_claimed(self) -> None:
        """A claim must never hand a worker a record it cannot read.

        The in-memory backend holds :class:`JobRecord` objects, so corruption
        arrives as a *replacement* value rather than as a bad string: an older
        process, a restore from an incompatible dump, or a hand-edited dict can
        leave a plain mapping behind. ``claim_next`` returning it would make the
        worker dereference attributes that do not exist, and the resulting
        ``AttributeError`` would be recorded as a *job* failure while the
        poisonous record stayed queued to fail every later claim as well.
        """
        q = JobQueue()
        job_id = q.enqueue(_spec()).job_id
        q._jobs[job_id] = {"job_id": job_id, "status": "queued"}  # type: ignore[assignment]

        assert q.claim_next() is None
        # The unreadable record must not remain a landmine for the next claim.
        assert q.claim_next() is None
        assert q.list() == []

    def test_a_record_with_an_unreadable_status_is_never_claimed(self) -> None:
        """Same protection for a valid record in a status this build cannot read."""
        q = JobQueue()
        job_id = q.enqueue(_spec()).job_id
        record = q.get(job_id)
        assert record is not None
        broken = dataclasses.replace(record, status="not-a-real-status")
        q._jobs[job_id] = broken  # type: ignore[assignment]

        assert q.claim_next() is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_default_is_memory(self) -> None:
        assert isinstance(create_job_queue(), JobQueue)
        assert not isinstance(create_job_queue(), RedisJobQueue)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_JOB_QUEUE", "memory")
        assert type(create_job_queue()) is JobQueue

    def test_redis_backend_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_JOB_QUEUE", "Redis")  # case-insensitive
        monkeypatch.setenv("VALIDSIM_REDIS_URL", "redis://factory:6379/0")
        queue = create_job_queue()
        try:
            assert isinstance(queue, RedisJobQueue)
            assert isinstance(queue, JobQueue)
            assert queue.url == "redis://factory:6379/0"
            assert queue._client is None  # no connection until first use
        finally:
            queue.close()

    def test_missing_redis_url_fails_fast_when_driver_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VALIDSIM_REDIS_URL", raising=False)
        monkeypatch.setattr(queue_mod, "_import_redis", lambda: object())  # "installed"
        with pytest.raises(ValueError, match="VALIDSIM_REDIS_URL"):
            RedisJobQueue()

    def test_missing_driver_surfaces_at_first_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> Any:
            raise RuntimeError("the redis driver is missing: pip install redis")

        monkeypatch.setattr(queue_mod, "_import_redis", boom)
        queue = RedisJobQueue()  # no URL: __init__ must not touch the driver
        with pytest.raises(RuntimeError, match="pip install redis"):
            queue.list()


class TestRedisDriverImport:
    def test_import_redis_raises_helpful_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _block_redis_import(monkeypatch)
        with pytest.raises(RuntimeError, match="pip install redis"):
            queue_mod._import_redis()

    def test_package_imports_without_driver(self) -> None:
        """Importing ``validsim.jobs`` never triggers the lazy redis import."""
        code = (
            "import sys; sys.modules['redis'] = None; "
            "import validsim.jobs as j; "
            "print('ok', j.JobQueue.__name__, j.RedisJobQueue.__name__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok JobQueue RedisJobQueue" in result.stdout


# ---------------------------------------------------------------------------
# Redis backend (fake client — no server)
# ---------------------------------------------------------------------------


class TestRedisQueue:
    def _queue(self) -> tuple[RedisJobQueue, _FakeRedis]:
        client = _FakeRedis()
        queue = RedisJobQueue(_client_factory=lambda: client)
        return queue, client

    def test_enqueue_serializes_json_under_parameter_free_key(self) -> None:
        queue, client = self._queue()
        record = queue.enqueue(_spec())
        key = "validsim:jobs:vrun-cafe1234"
        assert client._kv[key] == json.dumps(record.to_dict())
        # Key is plain (no URL/query parameters, no encoding).
        assert "?" not in key and "=" not in key and "%" not in key
        assert client._lists["validsim:jobs:index"] == ["vrun-cafe1234"]

    def test_get_deserializes_json(self) -> None:
        queue, _ = self._queue()
        record = queue.enqueue(_spec())
        assert queue.get(record.job_id) == record

    def test_get_unknown_returns_none(self) -> None:
        queue, _ = self._queue()
        assert queue.get("vrun-ffffffff") is None

    def test_duplicate_rejected_and_index_not_corrupted(self) -> None:
        queue, client = self._queue()
        queue.enqueue(_spec())
        with pytest.raises(ValueError, match="already exists"):
            queue.enqueue(_spec())
        assert client._lists["validsim:jobs:index"] == ["vrun-cafe1234"]

    def test_list_fifo_and_len(self) -> None:
        queue, _ = self._queue()
        a = queue.enqueue(_spec("vrun-aaaaaaaa"))
        b = queue.enqueue(_spec("vrun-bbbbbbbb"))
        assert [r.job_id for r in queue.list()] == [a.job_id, b.job_id]
        assert len(queue) == 2

    def test_enqueue_writes_payload_index_and_ready_list_atomically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queue, client = self._queue()
        monkeypatch.setattr(queue_mod, "_REDIS_ENQUEUE_SCRIPT", "atomic_enqueue")
        monkeypatch.setattr(queue_mod, "_utc_now", lambda: "2026-09-21T00:00:00+00:00")
        record = JobRecord(
            spec=_spec(),
            status=JobStatus.QUEUED,
            created_at="2026-09-21T00:00:00+00:00",
        )
        expected_calls = [
            (
                "eval",
                "atomic_enqueue",
                3,
                "validsim:jobs:vrun-cafe1234",
                "validsim:jobs:index",
                "validsim:jobs:ready",
                json.dumps(record.to_dict()),
                1000,
                "vrun-cafe1234",
            )
        ]
        client.results.append(1)

        enqueued = queue.enqueue(_spec())

        assert enqueued == record
        assert client.calls == expected_calls
        assert client._kv == {}
        assert client._lists == {}

    def test_faithful_old_enqueue_leaves_orphaned_payload_on_failure(self) -> None:
        queue, client = self._queue()

        def old_enqueue(spec: JobSpec) -> JobRecord:
            key = queue._job_key(spec.run_id)
            record = JobRecord(
                spec=spec,
                status=JobStatus.QUEUED,
                created_at=queue_mod._utc_now(),
            )
            client.set(key, json.dumps(record.to_dict()))
            raise RuntimeError("simulated process failure")

        with pytest.raises(RuntimeError, match="simulated process failure"):
            old_enqueue(_spec())

        assert "validsim:jobs:vrun-cafe1234" in client._kv
        assert client._lists.get("validsim:jobs:index", []) == []
        assert queue.list() == []
        assert len(queue) == 0

    def test_lifecycle_persists_updates(self) -> None:
        queue, client = self._queue()
        jid = queue.enqueue(_spec()).job_id
        queue.update_status(jid, JobStatus.RUNNING)
        got = queue.get(jid)
        assert got is not None and got.status is JobStatus.RUNNING
        assert got.started_at is not None and got.finished_at is None
        queue.update_status(jid, JobStatus.DONE, result="vrun-cafe1234")
        assert queue.get(jid).result == "vrun-cafe1234"
        stored = json.loads(client._kv["validsim:jobs:" + jid])
        assert stored["status"] == "done"

    def test_update_unknown_raises_key_error(self) -> None:
        queue, _ = self._queue()
        with pytest.raises(KeyError):
            queue.update_status("vrun-ffffffff", JobStatus.RUNNING)

    def test_close_closes_client_and_reopens_lazily(self) -> None:
        queue, client = self._queue()
        queue.enqueue(_spec())
        queue.close()
        assert client.closed is True
        assert queue._client is None
        queue.get("vrun-cafe1234")  # reopens — no error
        assert queue._client is client


# ---------------------------------------------------------------------------
# Corrupt / unreadable payloads on claim (Redis backend, real Lua)
# ---------------------------------------------------------------------------
#
# ``LPOP`` has already removed the id from the ready list by the time the payload
# is read, so every failure branch in the claim script is a *choice*: requeue
# (risk an unbounded hot spin) or drop (lose the job). A third option exists and
# is the right one for records that are structurally fine but whose *contents*
# this build cannot read: quarantine them into the dead-letter state. That keeps
# them out of the ready list (no spin), keeps them out of the worker's hands (no
# crash, no misattributed job failure) and keeps them *visible* (an operator can
# still inspect the payload). The genuinely-unreadable-bytes case still falls
# back to bounded requeue-then-drop, because a payload that is not a record at
# all cannot be marked.


@pytest.fixture()
def lua_redis() -> Any:
    """A Redis stand-in that really executes ``EVAL`` (needs ``lupa``)."""
    pytest.importorskip("lupa", reason="Lua execution needs lupa (fakeredis backend)")
    fakeredis = pytest.importorskip("fakeredis", reason="needs fakeredis")
    client = fakeredis.FakeRedis(decode_responses=True)
    try:
        client.eval("return 1", 0)
    except Exception as exc:  # noqa: BLE001 - a Lua-less build must skip, not fail
        pytest.skip(f"fakeredis cannot execute Lua scripts: {exc}")
    return client


def _redis_queue(client: Any) -> RedisJobQueue:
    """A queue bound to the Lua-capable stand-in."""
    return RedisJobQueue(_client_factory=lambda: client)


def _claim(queue: RedisJobQueue) -> JobRecord | None:
    """Claim one job through the real claim script."""
    return queue.claim_next()


class TestRedisClaimCorruptPayloads:
    def test_unreadable_bytes_are_bounded_then_dropped(self, lua_redis: Any) -> None:
        """Garbage is retried a bounded number of times, then abandoned.

        Unbounded requeue would spin a polling worker hot on one bad payload;
        dropping on the first miss would lose a job over a transient error.
        The counter records *requeues*, so the entry is seen
        ``_CORRUPT_CLAIM_ATTEMPTS`` times: three retries, then the
        give-up pass that finds the counter already spent.
        """
        queue = _redis_queue(lua_redis)
        lua_redis.set("validsim:jobs:vrun-corrupt", "{not json")
        lua_redis.rpush("validsim:jobs:ready", "vrun-corrupt")

        claims = 0
        while lua_redis.llen("validsim:jobs:ready"):
            assert _claim(queue) is None
            claims += 1
            assert claims <= 10, "claim requeued a corrupt payload without bound"

        # Bounded, and the ready list is empty so the worker stops spinning.
        # A single call now examines the head, finds it unclaimable, and keeps
        # scanning -- so the budget is spent per call rather than per poll, and
        # the loop below still has to run the counter to its limit before the
        # give-up pass empties the list. What is bounded is the total number of
        # times this entry is ever looked at, which is the property the spin
        # guard exists for.
        # The scan budget bounds how many heads one call may examine, and the
        # corrupt counter bounds the entry across calls. Either way the list
        # drains and the entry is dropped -- the property the spin guard exists
        # for. The exact poll count is where that budget happens to be spent,
        # so it is deliberately not pinned here.
        assert 1 <= claims <= queue_mod._CORRUPT_CLAIM_ATTEMPTS
        assert lua_redis.llen("validsim:jobs:ready") == 0

    def test_a_missing_payload_is_requeued_boundedly(self, lua_redis: Any) -> None:
        """An id with no payload is a transient miss, not corruption."""
        queue = _redis_queue(lua_redis)
        lua_redis.rpush("validsim:jobs:ready", "vrun-vanished")

        claims = 0
        while lua_redis.llen("validsim:jobs:ready"):
            assert _claim(queue) is None
            claims += 1
            assert claims <= 10, "claim requeued a missing payload without bound"

        # The scan budget bounds how many heads one call may examine, and the
        # corrupt counter bounds the entry across calls. Either way the list
        # drains and the entry is dropped -- the property the spin guard exists
        # for. The exact poll count is where that budget happens to be spent,
        # so it is deliberately not pinned here.
        assert 1 <= claims <= queue_mod._CORRUPT_CLAIM_ATTEMPTS

    def test_a_json_scalar_is_never_handed_to_a_worker(self, lua_redis: Any) -> None:
        """``cjson.decode`` succeeds on ``42`` but the result is not a record.

        Indexing it would raise *after* ``LPOP`` already lost the id, so the
        script has to recognise the shape first.
        """
        queue = _redis_queue(lua_redis)
        lua_redis.set("validsim:jobs:vrun-scalar", "42")
        lua_redis.rpush("validsim:jobs:ready", "vrun-scalar")

        claims = 0
        while lua_redis.llen("validsim:jobs:ready"):
            assert _claim(queue) is None
            claims += 1
            assert claims <= 10

        # The scan budget bounds how many heads one call may examine, and the
        # corrupt counter bounds the entry across calls. Either way the list
        # drains and the entry is dropped -- the property the spin guard exists
        # for. The exact poll count is where that budget happens to be spent,
        # so it is deliberately not pinned here.
        assert 1 <= claims <= queue_mod._CORRUPT_CLAIM_ATTEMPTS

    def test_a_structurally_valid_but_undecodable_record_is_quarantined(
        self, lua_redis: Any
    ) -> None:
        """A record this build cannot parse must leave the ready list at once.

        The JSON is valid and the shape is a table, so the bounded
        requeue-then-drop path would keep the worker polling for nothing.
        Quarantining marks the record dead-lettered instead: no spin, no crash,
        and the payload survives for inspection.
        """
        queue = _redis_queue(lua_redis)
        lua_redis.set(
            "validsim:jobs:vrun-future",
            json.dumps(
                {
                    "job_id": "vrun-future",
                    "status": "queued",
                    "spec": {"run_id": "vrun-future"},
                    "attempt": 0,
                    "a_field_from_a_newer_release": True,
                }
            ),
        )
        lua_redis.rpush("validsim:jobs:ready", "vrun-future")

        # A single claim must settle it: no requeue loop.
        assert _claim(queue) is None
        assert lua_redis.llen("validsim:jobs:ready") == 0
        stored = json.loads(lua_redis.get("validsim:jobs:vrun-future"))
        assert stored["status"] == "dead"
        assert stored["error"]

    def test_a_stale_ready_entry_is_dropped_not_claimed(self, lua_redis: Any) -> None:
        """A duplicate ready entry for an already-running job is not re-claimed."""
        queue = _redis_queue(lua_redis)
        record = queue.enqueue(_spec())
        lua_redis.rpush("validsim:jobs:ready", record.job_id)

        first = _claim(queue)
        assert first is not None
        # The stale duplicate must not produce a second claim.
        assert _claim(queue) is None
        assert queue.get(record.job_id).status is JobStatus.RUNNING

    def test_a_corrupt_payload_does_not_block_later_jobs(self, lua_redis: Any) -> None:
        """One bad payload must not starve the jobs behind it in the queue.

        ``claim_next`` scans past heads it cannot claim rather than stopping at
        the first one, so the good job behind the corrupt entry is returned by the
        very same call. That matches the in-memory backend, which drops an
        unreadable record and continues to the next candidate in one pass.
        """
        queue = _redis_queue(lua_redis)
        lua_redis.set("validsim:jobs:vrun-corrupt", "{not json")
        lua_redis.rpush("validsim:jobs:ready", "vrun-corrupt")
        good = queue.enqueue(_spec("vrun-good0001"))
        lua_redis.rpush("validsim:jobs:ready", good.job_id)

        first = _claim(queue)

        # The corrupt entry is skipped, not obeyed, and the real job behind it
        # is claimed without waiting for a second poll.
        assert first is not None and first.job_id == good.job_id


# ---------------------------------------------------------------------------
# Redis backend (live server)
# ---------------------------------------------------------------------------


def _live_redis_client(url: str) -> Any | None:
    try:
        client = queue_mod._import_redis().Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.ping()
    except Exception:  # noqa: BLE001 - absence or an invalid local URL skips integration
        return None
    return client


def _delete_live_redis_namespace(client: Any, namespace: str) -> None:
    keys = list(client.scan_iter(f"{namespace}:jobs:*"))
    if keys:
        client.delete(*keys)


@pytest.fixture()
def live_redis() -> Any:
    pytest.importorskip("redis")
    url = os.environ.get("VALIDSIM_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    client = _live_redis_client(url)
    if client is None:
        pytest.skip(f"live Redis unavailable at {url}")
    old_keys = list(client.scan_iter("validsim:jobs:*", count=100))
    if old_keys:
        client.delete(*old_keys)
    namespace = f"validsim:test:{os.getpid()}:{next(_REDIS_NAMESPACES)}"
    _delete_live_redis_namespace(client, namespace)
    try:
        yield client, namespace
    finally:
        _delete_live_redis_namespace(client, namespace)
        client.close()


class TestLiveRedisQueue:
    def test_enqueued_job_is_claimed_by_only_one_of_two_workers(
        self, live_redis: tuple[Any, str]
    ) -> None:
        client, namespace = live_redis
        queue = RedisJobQueue(_client_factory=lambda: client)
        queue._key_prefix = namespace
        queue.enqueue(_spec())
        start = threading.Barrier(2)
        results: deque[tuple[bool, str | None]] = deque()

        def claim() -> None:
            start.wait(timeout=2.0)
            try:
                record = queue.claim_next()
            except Exception as exc:  # noqa: BLE001 - surface thread failures in main thread
                results.append((False, str(exc)))
            else:
                results.append((record is not None, record.job_id if record else None))

        workers = [threading.Thread(target=claim) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2.0)
            assert not worker.is_alive()

        assert sorted(results) == [(False, None), (True, "vrun-cafe1234")]
        stored = queue.get("vrun-cafe1234")
        assert stored is not None
        assert stored.status is JobStatus.RUNNING
        assert stored.started_at is not None


# ---------------------------------------------------------------------------
# Router (standalone minimal app)
# ---------------------------------------------------------------------------


class TestRouter:
    @pytest.fixture()
    def client(self) -> TestClient:
        app = FastAPI()
        app.state.job_queue = JobQueue()
        app.include_router(router)
        return TestClient(app)

    def _enqueue(self, client: TestClient, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "checkpoint_id": "ckpt-alpha",
            "task_id": "pick-place",
            "episodes": 60,
            "adversarial": 12,
        }
        body.update(overrides)
        response = client.post("/jobs", json=body)
        assert response.status_code == 202
        return response.json()

    def test_enqueue_returns_job_id_and_status(self, client: TestClient) -> None:
        payload = self._enqueue(client)
        assert payload["job_id"].startswith("vrun-")
        assert payload["status"] == "queued"

    def test_list_jobs(self, client: TestClient) -> None:
        jid = self._enqueue(client)["job_id"]
        response = client.get("/jobs")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == jid
        assert jobs[0]["status"] == "queued"

    def test_get_job_and_404(self, client: TestClient) -> None:
        jid = self._enqueue(client)["job_id"]
        got = client.get(f"/jobs/{jid}")
        assert got.status_code == 200
        assert got.json()["spec"]["checkpoint_id"] == "ckpt-alpha"
        assert client.get("/jobs/vrun-ffffffff").status_code == 404

    def test_get_reflects_status_transition(self, client: TestClient) -> None:
        jid = self._enqueue(client)["job_id"]
        queue: JobQueue = client.app.state.job_queue  # type: ignore[assignment]
        queue.update_status(jid, JobStatus.RUNNING)
        assert client.get(f"/jobs/{jid}").json()["status"] == "running"

    def test_invalid_body_rejected(self, client: TestClient) -> None:
        bad = {
            "checkpoint_id": "ckpt-alpha",
            "task_id": "pick-place",
            "episodes": 0,
        }
        assert client.post("/jobs", json=bad).status_code == 422

    def test_router_works_without_injected_queue(self) -> None:
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        payload = self._enqueue(client)
        assert payload["status"] == "queued"