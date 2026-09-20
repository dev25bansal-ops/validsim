"""Tests for the `validsim models` and `validsim compare` CLI commands.

Both commands read from the configured *store* (unlike ``status``/``gate``,
which read the JSON cache), so these tests select the SQLite backend via
``VALIDSIM_STORE``/``VALIDSIM_SQLITE_PATH`` and seed ``StoredRun`` records
directly for deterministic ordering and scores. The ``--latest`` resolution
flags still read the cache, mirroring the existing ``_resolve_run_id`` helper.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim.cli import app
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun
from validsim.store.sqlite import SqliteValidationStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


@pytest.fixture
def sqlite_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Select the SQLite backend and return the database path."""
    db = tmp_path / "store.db"
    monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
    monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
    return db


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _stored_run(
    run_id: str,
    checkpoint_id: str,
    created_at: str,
    composite: float = 90.0,
    success_rate: float = 0.9,
    decision: str = "APPROVE",
    total: int = 100,
    mean_duration_s: float = 1.0,
) -> StoredRun:
    """Assemble a realistic :class:`StoredRun` with fully-known engine fields."""
    scorecard = Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id="pick-place",
        composite_score=composite,
        success_rate=success_rate,
        safety_score=95.0,
        robustness_score=90.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at=created_at,
        episode_count=total,
        failure_taxonomy={},
    )
    evaluation = EvaluationResult(
        total_episodes=total,
        success_count=round(success_rate * total),
        success_rate=success_rate,
        mean_duration_s=mean_duration_s,
    )
    safety = SafetyResult(0.0, 0.0, None, 0.0, 95.0)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id="pick-place",
        created_at=created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
        episodes=[],
    )


def _seed_store(db: Path, runs: list[StoredRun]) -> None:
    store = SqliteValidationStore(db)
    try:
        for run in runs:
            store.save(run)
    finally:
        store.close()


def _seed_cache(cache_file: Path, entries: dict[str, dict]) -> None:
    cache_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")


class TestModels:
    def test_lists_checkpoints_newest_activity_first(self, sqlite_store: Path) -> None:
        _seed_store(
            sqlite_store,
            [
                _stored_run("vrun-aaaaaaaa", "ckpt-a", "2024-01-01T00:00:00+00:00",
                            composite=80.0, decision="BLOCK"),
                _stored_run("vrun-bbbbbbbb", "ckpt-a", "2024-03-01T00:00:00+00:00",
                            composite=90.0),
                _stored_run("vrun-cccccccc", "ckpt-b", "2024-06-01T00:00:00+00:00",
                            composite=95.0),
            ],
        )
        result = _invoke("models")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]

        # Newest activity ("ckpt-b" @ 2024-06-01) must precede "ckpt-a".
        assert output.index("ckpt-b") < output.index("ckpt-a")

        # ckpt-a aggregates two runs and reports its *latest* run's score.
        a_line = next(line for line in output.splitlines() if "ckpt-a" in line)
        assert "2" in a_line and "90.0" in a_line and "APPROVE" in a_line

        # ckpt-b has a single run.
        b_line = next(line for line in output.splitlines() if "ckpt-b" in line)
        assert "1" in b_line and "95.0" in b_line and "APPROVE" in b_line

    def test_empty_store_exits_zero(self, sqlite_store: Path) -> None:
        result = _invoke("models")
        assert result.exit_code == 0, result.output
        assert "no checkpoints registered" in result.output  # type: ignore[attr-defined]


class TestCompare:
    def test_compare_prints_regression_table(self, sqlite_store: Path) -> None:
        _seed_store(
            sqlite_store,
            [
                _stored_run("vrun-base", "ckpt-v1", "2024-01-01T00:00:00+00:00",
                            success_rate=0.95, mean_duration_s=1.0),
                _stored_run("vrun-cand", "ckpt-v2", "2024-02-01T00:00:00+00:00",
                            success_rate=0.9, mean_duration_s=1.3),
            ],
        )
        result = _invoke("compare", "--baseline", "vrun-base", "--candidate", "vrun-cand")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]

        assert "Regression: vrun-cand vs baseline vrun-base" in output
        assert "success_rate" in output
        assert "mean_duration_s" in output
        # Success-rate delta (0.90 - 0.95) and duration delta (1.3 - 1.0) are
        # deterministic regardless of the permutation-test p-value.
        assert "-0.0500" in output
        assert "+0.3000" in output

    def test_missing_baseline_exits_two(self, sqlite_store: Path) -> None:
        _seed_store(
            sqlite_store,
            [_stored_run("vrun-cand", "ckpt-v2", "2024-02-01T00:00:00+00:00")],
        )
        result = _invoke("compare", "--baseline", "vrun-none", "--candidate", "vrun-cand")
        assert result.exit_code == 2

    def test_missing_candidate_exits_two(self, sqlite_store: Path) -> None:
        _seed_store(
            sqlite_store,
            [_stored_run("vrun-base", "ckpt-v1", "2024-01-01T00:00:00+00:00")],
        )
        result = _invoke("compare", "--baseline", "vrun-base", "--candidate", "vrun-none")
        assert result.exit_code == 2


class TestCompareResolution:
    """``--baseline``/``--candidate`` each follow the --run-id XOR --latest rule."""

    def _seed(self, sqlite_store: Path, cache_file: Path) -> None:
        _seed_store(
            sqlite_store,
            [
                _stored_run("vrun-aaaa", "ckpt-a", "2024-01-01T00:00:00+00:00",
                            success_rate=0.9),
                _stored_run("vrun-bbbb", "ckpt-b", "2024-06-01T00:00:00+00:00",
                            success_rate=0.95),
            ],
        )
        _seed_cache(
            cache_file,
            {
                "vrun-aaaa": {"run_id": "vrun-aaaa", "created_at": "2024-01-01T00:00:00+00:00"},
                "vrun-bbbb": {"run_id": "vrun-bbbb", "created_at": "2024-06-01T00:00:00+00:00"},
            },
        )

    def test_baseline_latest_resolves_newest(self, sqlite_store: Path, cache_file: Path) -> None:
        self._seed(sqlite_store, cache_file)
        result = _invoke("compare", "--candidate", "vrun-aaaa", "--baseline-latest")
        assert result.exit_code == 0, result.output
        assert "vs baseline vrun-bbbb" in result.output  # type: ignore[attr-defined]

    def test_candidate_latest_resolves_newest(self, sqlite_store: Path, cache_file: Path) -> None:
        self._seed(sqlite_store, cache_file)
        result = _invoke("compare", "--baseline", "vrun-aaaa", "--candidate-latest")
        assert result.exit_code == 0, result.output
        assert "Regression: vrun-bbbb vs baseline vrun-aaaa" in result.output  # type: ignore[attr-defined]

    def test_both_baseline_and_latest_errors(self, sqlite_store: Path, cache_file: Path) -> None:
        self._seed(sqlite_store, cache_file)
        result = _invoke(
            "compare", "--baseline", "vrun-aaaa", "--baseline-latest",
            "--candidate", "vrun-bbbb",
        )
        assert result.exit_code == 2

    def test_missing_candidate_flag_errors(self, sqlite_store: Path) -> None:
        _seed_store(
            sqlite_store,
            [_stored_run("vrun-aaaa", "ckpt-a", "2024-01-01T00:00:00+00:00")],
        )
        result = _invoke("compare", "--baseline", "vrun-aaaa")
        assert result.exit_code == 2

    def test_latest_empty_cache_errors(self, sqlite_store: Path) -> None:
        # cache_file fixture leaves no cache file -> --baseline-latest resolves
        # an empty cache and aborts with exit code 2.
        result = _invoke("compare", "--candidate", "vrun-aaaa", "--baseline-latest")
        assert result.exit_code == 2