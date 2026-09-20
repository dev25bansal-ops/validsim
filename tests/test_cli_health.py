"""Tests for the ``validsim health`` command.

The command is a side-effect-free *local* status read: it inspects the
configured validation store and job queue (via ``create_store`` /
``create_job_queue``) and prints a readiness summary mirroring the
``/api/v1/health`` payload — package version, backend labels derived from the
concrete store/queue class names, and the number of stored runs — without
making any HTTP call. It always exits 0, even against an empty store.

These tests monkeypatch the two factories on :mod:`validsim.cli` so they can
assert exact backend labels and run counts against a *shared* store/queue
(process-local in-memory backends are not shared across separate
``runner.invoke`` calls otherwise). The CliRunner + autouse ``cache_file``
fixtures mirror ``tests/test_cli.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim import __version__
from validsim import cli
from validsim.cli import app
from validsim.jobs.queue import JobQueue
from validsim.store.memory import ValidationStore
from validsim.store.sqlite import SqliteValidationStore

runner = CliRunner()
_RUNS_RE = re.compile(r"Runs stored:\s+(\d+)")
_STORE_BACKEND_RE = re.compile(r"Store backend:\s+(\S+)")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


@pytest.fixture
def shared_store(monkeypatch: pytest.MonkeyPatch) -> ValidationStore:
    """A single in-memory store handed to every CLI call in the test."""
    store = ValidationStore()
    monkeypatch.setattr(cli, "create_store", lambda: store)
    return store


@pytest.fixture
def shared_queue(monkeypatch: pytest.MonkeyPatch) -> JobQueue:
    """A single in-memory job queue handed to every CLI call in the test."""
    queue = JobQueue()
    monkeypatch.setattr(cli, "create_job_queue", lambda: queue)
    return queue


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _runs_stored(output: str) -> int:
    """Parse the integer reported on the ``Runs stored:`` line."""
    match = _RUNS_RE.search(output)
    assert match is not None, output
    return int(match.group(1))


class TestHealthCommand:
    def test_health_exits_zero(self, shared_store: ValidationStore, shared_queue: JobQueue) -> None:
        result = _invoke("health")
        assert result.exit_code == 0, result.output

    def test_health_empty_store_ok(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        # An untouched store is a perfectly healthy (if empty) state.
        result = _invoke("health")
        assert result.exit_code == 0, result.output
        assert _runs_stored(result.output) == 0  # type: ignore[attr-defined]

    def test_health_prints_version_and_backends(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        result = _invoke("health")
        output = result.output  # type: ignore[attr-defined]
        assert "ValidSim health: ok" in output
        assert __version__ in output
        assert "Store backend" in output
        assert "Job queue backend" in output
        # Both default backends are the in-memory implementations.
        assert "memory" in output

    def test_health_prints_run_count(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        # Persist one real run through the engine, then confirm health reflects it.
        run_result = _invoke("run", "--episodes", "20", "--adversarial", "4",
                             "--checkpoint", "ckpt-health")
        assert run_result.exit_code == 0, run_result.output
        assert shared_store.count() == 1

        result = _invoke("health")
        assert result.exit_code == 0, result.output
        assert _runs_stored(result.output) == 1  # type: ignore[attr-defined]

    def test_health_reports_non_memory_store_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        # The backend label is derived from the concrete store class name, so a
        # SQLite store reports "sqlite" (mirroring /api/v1/health), not "memory".
        store = SqliteValidationStore(str(tmp_path / "health.db"))
        monkeypatch.setattr(cli, "create_store", lambda: store)
        result = _invoke("health")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]
        # The store line reflects the concrete class name, not the default.
        match = _STORE_BACKEND_RE.search(output)
        assert match is not None, output
        assert match.group(1) == "sqlite"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
