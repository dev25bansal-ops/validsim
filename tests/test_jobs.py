"""Tests for the async validation job queue (memory + Redis) and its router.

Redis tests are server-absent-safe: they never require a live Redis instance
for real and inject a stand-in client via ``_client_factory`` instead, so
serialization, FIFO ordering, and parameter-free key names are asserted
without a server. Factory fail-fast and lazy-import paths are exercised by
monkeypatching :func:`validsim.jobs.queue._import_redis`.
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
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
        assert [s.value for s in JobStatus] == ["queued", "running", "done", "failed"]


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