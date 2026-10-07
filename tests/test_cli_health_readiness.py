"""Readiness vs liveness in the ``validsim health`` command.

Motivation
----------
``validsim health`` printed ``ValidSim health: ok`` and exited 0 **whatever
happened**. A deployment configured with ``VALIDSIM_STORE=postgres`` and a
deadline-reachable database but an unreachable host, a missing API key in
production, or a job queue that cannot connect, all produced the same green
"ok" as a perfectly healthy install. A probe that cannot fail is not a probe:
it is decoration, and a deployment guide that says "check ``validsim health``
before you start" is sending operators to a light that is wired to the
battery.

The distinction being encoded
----------------------------
* **Liveness** — is the process up and the CLI able to answer at all? Implies
  the version banner and a completed summary.
* **Readiness** — is the *configured* durable infrastructure actually
  reachable? A ``postgres``/``sqlite`` store or a ``redis`` queue that cannot
  be reached makes the process useless for its purpose, so readiness fails
  and the exit code is non-zero.

The rules, precisely:

* Exit ``0`` requires **every** checked dependency to be reachable. Anything
  less exits ``1``.
* The **ephemeral, in-process defaults are not a readiness failure**. The
  in-memory store and queue are the documented, supported default and are
  always reachable by construction; failing them would make ``validsim
  health`` fail for every default install and every local test, which is how
  probes get ignored. This mirrors the deliberate policy already applied in
  ``tests/test_cli_health.py``: "an untouched store is a perfectly healthy (if
  empty) state."
* Readiness is **configuration-aware, not a blanket I/O probe**: only the
  backends the operator actually selected can fail.

The probe is still a pure status read. It issues no HTTP request (there may be
no server running), it writes nothing, and it never raises.
"""

from __future__ import annotations

import json
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


class _BrokenStore:
    """A store whose every I/O operation fails, as an unreachable host would.

    ``count()`` is what the probe calls, so failing it is enough to model a
    configured-but-unreachable durable store. ``close()`` succeeds, as a
    connection that failed to open would.
    """

    def __init__(self, label: str = "postgres") -> None:
        self._label = label

    def count(self) -> int:
        raise OSError(f"could not connect to {self._label} server at db.internal:5432")

    def close(self) -> None:
        return None


class _BrokenQueue:
    """A job queue that cannot reach its backend."""

    def __init__(self, label: str = "redis") -> None:
        self._label = label

    def close(self) -> None:
        return None

    def ping(self) -> None:
        raise OSError(f"could not connect to {self._label} at cache.internal:6379")


class _FailingFactory:
    """A queue whose construction itself raises, as a missing DSN would."""

    def close(self) -> None:
        return None

    def __getattr__(self, name: str):  # pragma: no cover - not reached
        raise OSError(f"queue unavailable: {name}")


class TestHealthyDefaultsStillPass:
    """The regression guard: fixing the probe must not break the green case.

    A readiness probe that fails for a healthy default install gets
    disregarded, which is strictly worse than the probe it replaced.
    """

    def test_in_memory_defaults_are_ready(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        result = _invoke("health")
        assert result.exit_code == 0, result.output
        assert "ValidSim health: ok" in result.output  # type: ignore[attr-defined]

    def test_reachable_sqlite_store_is_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        store = SqliteValidationStore(str(tmp_path / "ok.db"))
        monkeypatch.setattr(cli, "create_store", lambda: store)
        result = _invoke("health")
        assert result.exit_code == 0, result.output
        assert _STORE_BACKEND_RE.search(result.output).group(1) == "sqlite"  # type: ignore[union-attr]

    def test_version_and_count_still_reported(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        result = _invoke("health")
        assert __version__ in result.output  # type: ignore[attr-defined]
        assert _runs_stored(result.output) == 0  # type: ignore[arg-type]


class TestUnreachableDurableStoreFailsReadiness:
    """The core defect: a configured, unreachable store must not print 'ok'."""

    def test_unreachable_store_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        monkeypatch.setattr(cli, "create_store", lambda: _BrokenStore())
        result = _invoke("health")
        assert result.exit_code == 1, (
            "health exited 0 with an unreachable durable store — the probe "
            "cannot fail, so it is not a probe"
        )

    def test_unreachable_store_does_not_claim_ok(
        self, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        monkeypatch.setattr(cli, "create_store", lambda: _BrokenStore())
        output = _invoke("health").output  # type: ignore[attr-defined]
        assert "ValidSim health: ok" not in output
        assert "not ready" in output.lower()

    def test_unreachable_store_is_named_in_the_output(
        self, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        """A failure an operator cannot identify is a failure they cannot fix."""
        monkeypatch.setattr(cli, "create_store", lambda: _BrokenStore())
        output = _invoke("health").output  # type: ignore[attr-defined]
        assert "error" in output
        # The transport-level cause must survive to the operator.
        assert "db.internal" in output

    def test_json_mode_reports_not_ready_and_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        monkeypatch.setattr(cli, "create_store", lambda: _BrokenStore())
        result = _invoke("health", "--json")
        assert result.exit_code == 1
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["status"] == "not ready"
        assert payload["ready"] is False
        # Which dependency failed, not merely that something did.
        assert "store" in payload["unready"]


class TestUnreachableJobQueueFailsReadiness:
    def test_unreachable_redis_queue_exits_non_zero(
        self, monkeypatch: pytest.MonkeyPatch, shared_store: ValidationStore
    ) -> None:
        monkeypatch.setattr(cli, "create_job_queue", lambda: _BrokenQueue())
        result = _invoke("health")
        assert result.exit_code == 1, result.output
        assert "not ready" in result.output.lower()  # type: ignore[attr-defined]

    def test_json_mode_names_the_queue(
        self, monkeypatch: pytest.MonkeyPatch, shared_store: ValidationStore
    ) -> None:
        monkeypatch.setattr(cli, "create_job_queue", lambda: _BrokenQueue())
        result = _invoke("health", "--json")
        assert result.exit_code == 1
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["unready"] == ["job_queue"]


class TestLivenessIsAlwaysReported:
    """Liveness is orthogonal: the process answered, so the probe must say so.

    A probe that reports *only* "not ready" is indistinguishable from a
    process that hung, which sends an operator to restart a perfectly healthy
    process.
    """

    def test_failing_readiness_still_reports_liveness(
        self, monkeypatch: pytest.MonkeyPatch, shared_queue: JobQueue
    ) -> None:
        monkeypatch.setattr(cli, "create_store", lambda: _BrokenStore())
        payload = json.loads(_invoke("health", "--json").output)  # type: ignore[arg-type]
        assert payload["live"] is True
        assert payload["version"] == __version__

    def test_liveness_is_json_consumable_when_ready(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        payload = json.loads(_invoke("health", "--json").output)  # type: ignore[arg-type]
        assert payload["live"] is True
        assert payload["ready"] is True
        assert payload["status"] == "ok"
        assert payload["unready"] == []


class TestJsonContract:
    """``--json`` is what an orchestrator or CI step will actually parse."""

    def test_healthy_json_is_a_single_object(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        result = _invoke("health", "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["store_backend"] == "memory"
        assert payload["job_queue_backend"] == "memory"
        assert payload["runs_stored"] == 0

    def test_json_mirrors_the_api_health_field_names(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        """The CLI probe mirrors ``/api/v1/health``; the field names must match
        so one consumer can read either without a translation layer."""
        payload = json.loads(_invoke("health", "--json").output)  # type: ignore[arg-type]
        for field in ("status", "version", "store_backend", "job_queue_backend"):
            assert field in payload, f"{field} missing from the CLI health payload"

    def test_human_output_still_exists_for_interactive_use(
        self, shared_store: ValidationStore, shared_queue: JobQueue
    ) -> None:
        """Adding --json must not remove the readable summary."""
        output = _invoke("health").output  # type: ignore[attr-defined]
        assert "Version:" in output
        assert "Store backend:" in output
        assert "Job queue backend:" in output

    def test_help_documents_the_exit_contract(self) -> None:
        """The contract must be discoverable, not folklore."""
        result = runner.invoke(app, ["health", "--help"])
        assert result.exit_code == 0, result.output
        assert "--json" in result.output
        assert "1" in result.output  # the non-zero readiness code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
