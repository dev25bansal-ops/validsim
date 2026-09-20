"""Tests for the asynchronous job CLI: ``jobs`` / ``job enqueue`` / ``worker``.

The memory job queue and the in-memory validation store are process-local, so
a fresh ``create_job_queue()`` / ``create_store()`` call would not be shared
across separate ``runner.invoke`` calls. These tests therefore monkeypatch the
two factories on :mod:`validsim.cli` to hand every command the *same* memory
queue and *same* injected store, which is exactly what lets an enqueued job be
observed by ``jobs`` and later drained to ``done`` by ``worker --once``.

The CliRunner + autouse ``cache_file`` fixtures mirror ``tests/test_cli.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim import cli
from validsim.cli import app
from validsim.jobs.models import JobStatus
from validsim.jobs.queue import JobQueue
from validsim.store.memory import ValidationStore

runner = CliRunner()
JOB_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


@pytest.fixture
def shared_queue(monkeypatch: pytest.MonkeyPatch) -> JobQueue:
    """A single memory queue shared by every CLI call in the test."""
    queue = JobQueue()
    monkeypatch.setattr(cli, "create_job_queue", lambda: queue)
    return queue


@pytest.fixture
def shared_store(monkeypatch: pytest.MonkeyPatch) -> ValidationStore:
    """A single in-memory store injected into the worker command."""
    store = ValidationStore()
    monkeypatch.setattr(cli, "create_store", lambda: store)
    return store


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _enqueue() -> str:
    """Enqueue a small job and return its freshly-generated job id."""
    result = _invoke("job", "enqueue", "-c", "ckpt-jobs", "-t", "pick-place",
                     "-e", "20", "-a", "4")
    assert result.exit_code == 0, result.output
    match = JOB_ID_RE.search(result.output)  # type: ignore[attr-defined]
    assert match is not None, result.output
    return match.group(0)


class TestJobEnqueue:
    def test_enqueue_prints_id_and_queued_status(self, shared_queue: JobQueue) -> None:
        result = _invoke("job", "enqueue", "-c", "ckpt-a", "-t", "pick-place",
                         "-e", "10", "-a", "0")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]
        match = JOB_ID_RE.search(output)
        assert match is not None, output
        assert "queued" in output
        # The job really landed in the (shared) queue.
        assert len(shared_queue) == 1
        assert shared_queue.get(match.group(0)) is not None

    def test_enqueue_requires_checkpoint(self, shared_queue: JobQueue) -> None:
        result = _invoke("job", "enqueue", "-t", "pick-place")
        assert result.exit_code == 2  # usage error -> missing required -c


class TestJobsList:
    def test_empty_queue_exits_zero(self, shared_queue: JobQueue) -> None:
        result = _invoke("jobs")
        assert result.exit_code == 0, result.output
        assert "no jobs queued" in result.output  # type: ignore[attr-defined]

    def test_enqueue_then_list_shows_it(self, shared_queue: JobQueue) -> None:
        job_id = _enqueue()
        result = _invoke("jobs")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]
        assert job_id in output
        assert "ckpt-jobs" in output
        assert "queued" in output
        # Header row is rendered for a non-empty table.
        assert "JOB ID" in output and "STATUS" in output and "CHECKPOINT" in output


class TestWorker:
    def test_once_processes_job_to_done(
        self, shared_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        job_id = _enqueue()
        assert shared_queue.get(job_id).status is JobStatus.QUEUED  # type: ignore[union-attr]

        result = _invoke("worker", "--once")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]
        assert job_id in output
        assert "done" in output

        # Terminal state recorded on the queue and the run persisted to store.
        record = shared_queue.get(job_id)
        assert record is not None
        assert record.status is JobStatus.DONE
        assert record.result == job_id
        assert shared_store.get(job_id) is not None

    def test_once_with_empty_queue_is_a_noop(
        self, shared_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        result = _invoke("worker", "--once")
        assert result.exit_code == 0, result.output
        assert "no queued jobs" in result.output  # type: ignore[attr-defined]
        assert len(shared_store) == 0
