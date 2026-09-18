"""Tests for the Typer CLI: run/status/scorecard/gate and CI exit codes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim.cli import app
from validsim.store.sqlite import SqliteValidationStore

runner = CliRunner()
RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _run_and_extract_id() -> tuple[object, str]:
    result = _invoke("run", "--episodes", "40", "--adversarial", "8",
                     "--checkpoint", "ckpt-cli")
    assert result.exit_code == 0, result.output
    match = RUN_ID_RE.search(result.output)
    assert match is not None, result.output
    return result, match.group(0)


class TestRun:
    def test_run_prints_summary_and_caches(self) -> None:
        result, run_id = _run_and_extract_id()
        output = result.output  # type: ignore[attr-defined]
        assert f"Run ID:        {run_id}" in output
        assert "Decision:" in output
        assert "Composite:" in output
        assert "Success rate:" in output

    def test_cache_written(self, cache_file: Path) -> None:
        _, run_id = _run_and_extract_id()
        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert run_id in data
        assert data[run_id]["checkpoint_id"] == "ckpt-cli"


class TestStatusAndScorecard:
    def test_status(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("status", "--run-id", run_id)
        assert result.exit_code == 0
        assert run_id in result.output  # type: ignore[attr-defined]

    def test_scorecard_json(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("scorecard", "--run-id", run_id)
        assert result.exit_code == 0
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["run_id"] == run_id
        assert payload["episode_count"] == 48  # 40 nominal + 8 adversarial

    def test_missing_run_errors(self) -> None:
        result = _invoke("status", "--run-id", "vrun-deadbeef")
        assert result.exit_code == 2


class TestGate:
    def test_gate_approves_with_low_threshold(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("gate", "--run-id", run_id, "--threshold", "0")
        assert result.exit_code == 0
        assert "APPROVE" in result.output  # type: ignore[attr-defined]

    def test_gate_blocks_with_impossible_threshold(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("gate", "--run-id", run_id, "--threshold", "200")
        assert result.exit_code == 1  # CI-friendly non-zero exit
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_gate_uses_stored_threshold_by_default(self) -> None:
        _, run_id = _run_and_extract_id()
        scorecard = json.loads(
            _invoke("scorecard", "--run-id", run_id).output  # type: ignore[attr-defined]
        )
        result = _invoke("gate", "--run-id", run_id)
        expected_code = 0 if scorecard["deploy_decision"] == "APPROVE" else 1
        assert result.exit_code == expected_code


class TestStorePersistence:
    """`run` must also persist the full StoredRun to the configured store."""

    def test_run_persists_to_sqlite_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "cli-store.db"
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
        result = _invoke("run", "--episodes", "20", "--adversarial", "4",
                         "--checkpoint", "ckpt-store")
        assert result.exit_code == 0, result.output
        match = RUN_ID_RE.search(result.output)
        assert match is not None, result.output

        store = SqliteValidationStore(db)
        try:
            run = store.get(match.group(0))
            assert run is not None
            assert run.checkpoint_id == "ckpt-store"
            assert run.task_id == "pick-place"
            assert run.scorecard.episode_count == 24  # 20 nominal + 4 adversarial
            assert len(run.episodes) == 24            # raw detail persisted too
        finally:
            store.close()

    def test_default_memory_store_leaves_no_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without VALIDSIM_STORE the run only touches the JSON cache."""
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
        monkeypatch.delenv("VALIDSIM_SQLITE_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("run", "--episodes", "10", "--adversarial", "0",
                         "--checkpoint", "ckpt-mem")
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "validsim.db").exists()
