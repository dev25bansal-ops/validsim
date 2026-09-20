"""Tests for the environment-driven ``create_backend()`` factory and its wiring.

Two guarantees are pinned here:

1. The factory itself maps ``VALIDSIM_BACKEND`` onto a concrete backend:
   ``mock``/unset/unknown -> :class:`MockIsaacBackend` (the CPU-only default),
   ``isaac`` -> :class:`IsaacWorkerBackend`. This mirrors
   ``tests/test_store_factory.py`` for the store factory.
2. The three entry points that used to hard-code ``MockIsaacBackend()`` —
   :func:`validsim.api.main.create_app`, the :mod:`validsim.cli` ``run`` and
   ``worker`` commands, and :class:`validsim.jobs.worker.JobWorker`'s default
   backend — now route through ``create_backend()``, so
   ``VALIDSIM_BACKEND=isaac`` is honored end-to-end instead of being silently
   ignored. Each wiring test captures the backend the pipeline actually selects
   (by stubbing ``run_validation``) or asserts ``create_backend`` was called,
   using the same monkeypatching style as the existing suite.

All tests keep the mock default working (env unset -> mock), which is what the
rest of the suite relies on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from validsim import cli
from validsim.api import main as api_main
from validsim.config import TaskConfig
from validsim.jobs import JobQueue, JobSpec, JobStatus, JobWorker
from validsim.jobs import worker as worker_mod
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim import IsaacWorkerBackend, MockIsaacBackend, create_backend
from validsim.sim.runner import EpisodeResult
from validsim.sim.runner import run_validation as _real_run_validation
from validsim.store.memory import ValidationStore

runner = CliRunner()

#: A worker URL used only so ``IsaacWorkerBackend`` reports a configured root.
#: No network is ever touched: construction is side-effect free and the
#: pipeline tests stub ``run_validation`` before any episode is dispatched.
_WORKER_URL = "http://worker-gpu:8090"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic environment for every test in this module."""
    monkeypatch.delenv("VALIDSIM_BACKEND", raising=False)
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_URL", raising=False)
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_KEY", raising=False)


@pytest.fixture
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI scorecard cache at a temp file (mirrors tests/test_cli.py)."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


def _capturing_run_validation(record: dict[str, Any]):
    """Return a ``run_validation`` replacement that records the backend.

    The captured ``backend`` is whatever the pipeline built from the
    environment; the episodes are then produced by a throwaway
    :class:`MockIsaacBackend` so downstream scoring sees valid data and the
    real Isaac client is never exercised (no network).
    """

    def _fake(
        task: TaskConfig,
        backend: Any,
        scenarios: Sequence[AdversarialScenario],
        seed: int = 42,
    ) -> list[EpisodeResult]:
        record["backend"] = backend
        return _real_run_validation(task, MockIsaacBackend(), scenarios, seed=seed)

    return _fake


def _request_body(episodes: int = 20, adversarial: int = 4) -> dict[str, Any]:
    return {
        "checkpoint_id": "ckpt-factory",
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": episodes,
            "adversarial_count": adversarial,
        },
    }


def _spec(run_id: str = "vrun-fac00001") -> JobSpec:
    return JobSpec(
        run_id=run_id,
        checkpoint_id="ckpt-factory",
        task_id="pick-place",
        episodes=20,
        adversarial=0,
    )


# ---------------------------------------------------------------------------
# 1. The factory itself
# ---------------------------------------------------------------------------


class TestCreateBackendFactory:
    def test_default_is_mock(self) -> None:
        assert type(create_backend()) is MockIsaacBackend

    def test_explicit_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "mock")
        assert type(create_backend()) is MockIsaacBackend

    def test_isaac_selected_with_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)
        backend = create_backend()
        assert isinstance(backend, IsaacWorkerBackend)
        assert backend.base_url == _WORKER_URL

    def test_isaac_is_case_insensitive_and_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "  Isaac  ")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)
        assert isinstance(create_backend(), IsaacWorkerBackend)

    def test_unknown_value_defaults_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Existing contract: only "isaac" selects the worker; anything else
        # (including a typo) falls back to the deterministic mock.
        monkeypatch.setenv("VALIDSIM_BACKEND", "quantum-flux")
        assert type(create_backend()) is MockIsaacBackend

    def test_empty_value_defaults_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "")
        assert type(create_backend()) is MockIsaacBackend


# ---------------------------------------------------------------------------
# 2a. The synchronous API pipeline honors the env
# ---------------------------------------------------------------------------


class TestApiHonorsBackendEnv:
    def test_defaults_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(api_main, "run_validation", _capturing_run_validation(record))
        client = TestClient(api_main.create_app(ValidationStore()))

        response = client.post("/api/v1/validations", json=_request_body())

        assert response.status_code == 201
        assert isinstance(record["backend"], MockIsaacBackend)
        assert not isinstance(record["backend"], IsaacWorkerBackend)

    def test_selects_isaac_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(api_main, "run_validation", _capturing_run_validation(record))
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)
        client = TestClient(api_main.create_app(ValidationStore()))

        response = client.post("/api/v1/validations", json=_request_body())

        assert response.status_code == 201
        assert isinstance(record["backend"], IsaacWorkerBackend)

    def test_create_backend_is_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def _spy() -> MockIsaacBackend:
            calls.append(1)
            return MockIsaacBackend()

        monkeypatch.setattr(api_main, "create_backend", _spy)
        client = TestClient(api_main.create_app(ValidationStore()))

        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        assert calls == [1]


# ---------------------------------------------------------------------------
# 2b. The CLI ``run`` command honors the env
# ---------------------------------------------------------------------------


class TestCliRunHonorsBackendEnv:
    def test_defaults_to_mock(self, monkeypatch: pytest.MonkeyPatch, cache_file: Path) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(cli, "run_validation", _capturing_run_validation(record))

        result = runner.invoke(cli.app, ["run", "-e", "20", "-a", "4"])

        assert result.exit_code == 0, result.output
        assert isinstance(record["backend"], MockIsaacBackend)

    def test_selects_isaac_when_configured(
        self, monkeypatch: pytest.MonkeyPatch, cache_file: Path
    ) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(cli, "run_validation", _capturing_run_validation(record))
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)

        result = runner.invoke(cli.app, ["run", "-e", "20", "-a", "4"])

        assert result.exit_code == 0, result.output
        assert isinstance(record["backend"], IsaacWorkerBackend)


# ---------------------------------------------------------------------------
# 2c. The CLI ``worker`` command routes through the factory
# ---------------------------------------------------------------------------


class TestCliWorkerHonorsBackendEnv:
    def test_calls_create_backend(
        self, monkeypatch: pytest.MonkeyPatch, cache_file: Path
    ) -> None:
        queue = JobQueue()
        store = ValidationStore()
        monkeypatch.setattr(cli, "create_job_queue", lambda: queue)
        monkeypatch.setattr(cli, "create_store", lambda: store)

        calls: list[int] = []

        def _spy() -> MockIsaacBackend:
            calls.append(1)
            return MockIsaacBackend()

        monkeypatch.setattr(cli, "create_backend", _spy)
        job_id = queue.enqueue(_spec()).job_id

        result = runner.invoke(cli.app, ["worker", "--once"])

        assert result.exit_code == 0, result.output
        assert calls == [1]
        # The job really ran end-to-end with the factory-supplied backend.
        assert queue.get(job_id).status is JobStatus.DONE  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 2d. JobWorker's default backend honors the env
# ---------------------------------------------------------------------------


class TestJobWorkerDefaultBackend:
    def test_default_backend_is_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(worker_mod, "run_validation", _capturing_run_validation(record))
        queue = JobQueue()
        queue.enqueue(_spec("vrun-fac00002"))

        JobWorker(queue, ValidationStore()).run_once()

        assert isinstance(record["backend"], MockIsaacBackend)

    def test_default_backend_is_isaac_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record: dict[str, Any] = {}
        monkeypatch.setattr(worker_mod, "run_validation", _capturing_run_validation(record))
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)
        queue = JobQueue()
        queue.enqueue(_spec("vrun-fac00003"))

        JobWorker(queue, ValidationStore()).run_once()

        assert isinstance(record["backend"], IsaacWorkerBackend)

    def test_explicit_backend_overrides_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An injected backend must win over the env-selected default.
        record: dict[str, Any] = {}
        monkeypatch.setattr(worker_mod, "run_validation", _capturing_run_validation(record))
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", _WORKER_URL)
        queue = JobQueue()
        queue.enqueue(_spec("vrun-fac00004"))
        injected = MockIsaacBackend()

        JobWorker(queue, ValidationStore(), backend=injected).run_once()

        assert record["backend"] is injected
