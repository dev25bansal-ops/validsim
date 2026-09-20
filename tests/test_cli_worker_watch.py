"""Tests for the ``worker --watch`` / ``--max-jobs`` CLI behaviour.

``--watch`` turns the worker into a per-job transition printer: each processed
job emits a single ``worker: job <id> -> <status>`` line as its terminal
transition happens. ``--max-jobs N`` bounds the loop so a run terminates on its
own (0 = unlimited), which is what makes the watch mode testable without a
long-running process.

The memory job queue and in-memory store are process-local, so — exactly like
``tests/test_cli_jobs.py`` — these tests monkeypatch ``create_job_queue`` and
``create_store`` on :mod:`validsim.cli` to hand the worker the *same* queue
(pre-seeded with two jobs) and *same* store, then drive it via the CliRunner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim import cli
from validsim.cli import app
from validsim.jobs.models import JobSpec, JobStatus
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


def _spec(run_id: str) -> JobSpec:
    """A small, fast job spec (few episodes) so the pipeline stays quick."""
    return JobSpec(
        run_id=run_id,
        checkpoint_id="ckpt-watch",
        task_id="pick-place",
        episodes=20,
        adversarial=0,
    )


@pytest.fixture
def two_job_queue(monkeypatch: pytest.MonkeyPatch) -> JobQueue:
    """A shared memory queue pre-seeded with exactly two queued jobs."""
    queue = JobQueue()
    queue.enqueue(_spec("vrun-aaaa1111"))
    queue.enqueue(_spec("vrun-bbbb2222"))
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


def _transition_lines(output: str) -> list[str]:
    """The worker's per-job transition lines, in order."""
    return [
        line
        for line in output.splitlines()
        if line.startswith("worker: job ") and " -> " in line
    ]


class TestWorkerWatch:
    def test_watch_processes_two_jobs_and_prints_two_lines(
        self, two_job_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        result = _invoke("worker", "--watch", "--max-jobs", "2")

        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]

        lines = _transition_lines(output)
        # One line per processed job, and nothing extra.
        assert len(lines) == 2, output

        # Both freshly-seeded jobs ran to completion (the mock backend never
        # fails), so each line reads "<job_id> -> done" and both ids appear.
        for line in lines:
            assert " -> done" in line, line
        assert "vrun-aaaa1111" in output
        assert "vrun-bbbb2222" in output

        # The queue reflects both terminal transitions and the store persisted
        # both runs (result pointer == the job's own run id).
        for job_id in ("vrun-aaaa1111", "vrun-bbbb2222"):
            record = two_job_queue.get(job_id)
            assert record is not None
            assert record.status is JobStatus.DONE
            assert record.result == job_id
            assert shared_store.get(job_id) is not None

    def test_watch_lines_match_job_ids_in_order(
        self, two_job_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        result = _invoke("worker", "--watch", "--max-jobs", "2")
        assert result.exit_code == 0, result.output

        lines = _transition_lines(result.output)  # type: ignore[attr-defined]
        ids = [JOB_ID_RE.search(line).group(0) for line in lines]  # type: ignore[union-attr]
        # FIFO claim order preserved by the worker loop.
        assert ids == ["vrun-aaaa1111", "vrun-bbbb2222"]


class TestWorkerMaxJobs:
    def test_max_jobs_stops_after_n_even_without_watch(
        self, two_job_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        # --max-jobs 1 bounds the loop without --watch: it must not print
        # transition lines (that is --watch's job) and must exit 0 after one.
        result = _invoke("worker", "--max-jobs", "1")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]

        assert _transition_lines(output) == []
        # Exactly one job drained; the other is still queued.
        assert two_job_queue.get("vrun-aaaa1111").status is JobStatus.DONE  # type: ignore[union-attr]
        assert two_job_queue.get("vrun-bbbb2222").status is JobStatus.QUEUED  # type: ignore[union-attr]

    def test_watch_stops_when_queue_runs_dry_before_max(
        self, monkeypatch: pytest.MonkeyPatch, shared_store: ValidationStore
    ) -> None:
        # Only one job present but --max-jobs 2: the bounded run must stop when
        # the queue is empty (no hang) after printing the single transition.
        queue = JobQueue()
        queue.enqueue(_spec("vrun-cccc3333"))
        monkeypatch.setattr(cli, "create_job_queue", lambda: queue)

        result = _invoke("worker", "--watch", "--max-jobs", "2")
        assert result.exit_code == 0, result.output
        lines = _transition_lines(result.output)  # type: ignore[attr-defined]
        assert len(lines) == 1
        assert "vrun-cccc3333" in lines[0]
        assert queue.get("vrun-cccc3333").status is JobStatus.DONE  # type: ignore[union-attr]


class TestWorkerOnceUnchanged:
    def test_once_still_processes_a_single_job(
        self, two_job_queue: JobQueue, shared_store: ValidationStore
    ) -> None:
        # --once keeps its exact behaviour: one job, one line, exit 0; the
        # second job stays queued and --watch/--max-jobs do not interfere.
        result = _invoke("worker", "--once")
        assert result.exit_code == 0, result.output
        lines = _transition_lines(result.output)  # type: ignore[attr-defined]
        assert len(lines) == 1
        assert " -> done" in lines[0]
        assert two_job_queue.get("vrun-aaaa1111").status is JobStatus.DONE  # type: ignore[union-attr]
        assert two_job_queue.get("vrun-bbbb2222").status is JobStatus.QUEUED  # type: ignore[union-attr]
