"""Tests for the persistent SQLite validation store (round-trip + ordering)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun
from validsim.store.sqlite import SqliteValidationStore


def _scorecard(run_id: str = "vrun-cafe1234", **overrides: object) -> Scorecard:
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "composite_score": 90.0,
        "success_rate": 0.9,
        "safety_score": 80.0,
        "robustness_score": 100.0,
        "regression_delta": -0.1,
        "confidence_interval": (0.82, 0.95),
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "episode_count": 100,
        "failure_taxonomy": {"collision": 7, "timeout": 3},
    }
    base.update(overrides)
    return Scorecard(**base)  # type: ignore[arg-type]


def _run(sc: Scorecard) -> StoredRun:
    return StoredRun(
        run_id=sc.run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=sc.created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=sc.episode_count,
            success_count=90,
            success_rate=sc.success_rate,
            failure_taxonomy=dict(sc.failure_taxonomy),
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, sc.safety_score),
    )


@pytest.fixture()
def make_store(tmp_path: Path) -> Iterator[Callable[[str], SqliteValidationStore]]:
    """Factory opening named SQLite stores under ``tmp_path``; closes on exit."""
    created: list[SqliteValidationStore] = []

    def _make(name: str = "valid.db") -> SqliteValidationStore:
        store = SqliteValidationStore(tmp_path / name)
        created.append(store)
        return store

    yield _make
    for store in created:
        store.close()


class TestRoundTrip:
    def test_save_and_get_preserves_scorecard(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        store = make_store()
        sc = _scorecard()
        store.save(_run(sc))
        got = store.get(sc.run_id)
        assert got is not None
        assert got.scorecard == sc  # CI tuple + floats restored exactly
        assert got.run_id == sc.run_id
        assert got.checkpoint_id == sc.checkpoint_id
        assert got.task_id == sc.task_id
        assert got.created_at == sc.created_at

    def test_get_unknown_returns_none(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        assert make_store().get("vrun-deadbeef") is None

    def test_repeated_save_overwrites(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        store = make_store()
        store.save(_run(_scorecard(composite_score=50.0, deploy_decision="BLOCK")))
        store.save(_run(_scorecard(composite_score=95.0, deploy_decision="APPROVE")))
        assert len(store) == 1
        assert store.get("vrun-cafe1234").scorecard.composite_score == 95.0  # type: ignore[union-attr]

    def test_summary_reflects_stored_scorecard(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        store = make_store()
        sc = _scorecard()
        store.save(_run(sc))
        summary = store.get(sc.run_id).summary()  # type: ignore[union-attr]
        assert summary["composite_score"] == 90.0
        assert summary["deploy_decision"] == "APPROVE"
        assert summary["episode_count"] == 100


class TestQueries:
    def test_list_for_checkpoint_filters_and_orders(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        store.save(_run(_scorecard("vrun-a0000001", checkpoint_id="ckpt-A", created_at="2026-01-02T00:00:00+00:00")))
        store.save(_run(_scorecard("vrun-a0000002", checkpoint_id="ckpt-A", created_at="2026-01-01T00:00:00+00:00")))
        store.save(_run(_scorecard("vrun-b0000001", checkpoint_id="ckpt-B", created_at="2026-01-03T00:00:00+00:00")))
        runs = store.list_for_checkpoint("ckpt-A")
        assert [r.run_id for r in runs] == ["vrun-a0000002", "vrun-a0000001"]  # oldest first

    def test_history_orders_oldest_first(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        store = make_store()
        store.save(_run(_scorecard("vrun-c0000002", created_at="2026-02-02T00:00:00+00:00")))
        store.save(_run(_scorecard("vrun-c0000001", created_at="2026-02-01T00:00:00+00:00")))
        assert [r.run_id for r in store.history()] == ["vrun-c0000001", "vrun-c0000002"]

    def test_len_tracks_saved_runs(self, make_store: Callable[[str], SqliteValidationStore]) -> None:
        store = make_store()
        assert len(store) == 0
        store.save(_run(_scorecard("vrun-11111111")))
        store.save(_run(_scorecard("vrun-22222222")))
        assert len(store) == 2


class TestPersistence:
    def test_data_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        sc = _scorecard()
        first = SqliteValidationStore(path)
        first.save(_run(sc))
        first.close()
        second = SqliteValidationStore(path)
        try:
            got = second.get(sc.run_id)
            assert got is not None and got.scorecard == sc
        finally:
            second.close()

    def test_creates_schema_on_init(self, tmp_path: Path) -> None:
        path = tmp_path / "fresh.db"
        store = SqliteValidationStore(path)
        try:
            assert path.exists()
            assert store.db_path == str(path)
            assert store.history() == []
        finally:
            store.close()


def _full_run(sc: Scorecard) -> StoredRun:
    """A StoredRun exercising every persisted detail field."""
    episodes = [
        EpisodeResult(
            episode_id="pick-place-seed0000000042",
            task_id="pick-place",
            seed=42,
            success=True,
            collision_count=0,
            max_contact_force_n=12.5,
            min_human_distance_m=1.2,
            failure_mode=None,
            duration_s=8.25,
            joint_states_summary={"position_rms": 0.4, "dof": 7.0},
            randomization_level="full",
        ),
        EpisodeResult(
            episode_id="pick-place-seed0000000043",
            task_id="pick-place",
            seed=43,
            success=False,
            collision_count=2,
            max_contact_force_n=98.75,
            min_human_distance_m=None,
            failure_mode="collision",
            duration_s=15.5,
            joint_states_summary={},
            randomization_level="partial",
        ),
    ]
    return StoredRun(
        run_id=sc.run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=sc.created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=2,
            success_count=1,
            success_rate=0.5,
            per_task_success={"pick-place": 0.5},
            failure_taxonomy={"collision": 1},
            mean_duration_s=11.875,
        ),
        safety=SafetyResult(1.0, 0.5, 1.2, 0.0, 42.5),
        episodes=episodes,
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"),
                RegressionItem("mean_duration_s", 8.0, 11.875, 3.875, None, False, "info"),
            ]
        ),
    )


class TestFullDetailRoundTrip:
    """The SQLite backend must restore every StoredRun field exactly."""

    def test_round_trip_preserves_entire_run(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = _full_run(_scorecard())
        store.save(run)
        assert store.get(run.run_id) == run  # dataclass equality across all fields

    def test_episodes_survive_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "detail.db"
        run = _full_run(_scorecard())
        first = SqliteValidationStore(path)
        first.save(run)
        first.close()
        second = SqliteValidationStore(path)
        try:
            got = second.get(run.run_id)
            assert got is not None
            assert got.episodes == run.episodes
            assert got.evaluation == run.evaluation
            assert got.safety == run.safety
            assert got.baseline_run_id == "vrun-base0001"
            assert got.regression == run.regression
        finally:
            second.close()

    def test_null_regression_round_trips_as_none(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = _run(_scorecard())  # no episodes/regression/baseline
        store.save(run)
        got = store.get(run.run_id)
        assert got is not None
        assert got.regression is None
        assert got.baseline_run_id is None
        assert got.episodes == []
        assert got.evaluation == run.evaluation  # exact, not approximated

    def test_legacy_row_without_detail_columns_is_readable(
        self, tmp_path: Path
    ) -> None:
        """Rows written by the pre-detail schema still load via fallback."""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE validations ("
            " run_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL,"
            " task_id TEXT NOT NULL, composite_score REAL NOT NULL,"
            " deploy_decision TEXT NOT NULL, created_at TEXT NOT NULL,"
            " scorecard_json TEXT NOT NULL)"
        )
        sc = _scorecard()
        conn.execute(
            "INSERT INTO validations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sc.run_id,
                sc.checkpoint_id,
                sc.task_id,
                sc.composite_score,
                sc.deploy_decision,
                sc.created_at,
                sc.to_json(),
            ),
        )
        conn.commit()
        conn.close()

        store = SqliteValidationStore(path)  # migrates columns in place
        try:
            got = store.get(sc.run_id)
            assert got is not None
            assert got.scorecard == sc
            assert got.episodes == []
            assert got.regression is None
            assert got.baseline_run_id is None
            assert got.evaluation.total_episodes == 100  # approximated fallback
            assert got.evaluation.success_count == 90
            # migrated store accepts new full-detail writes
            run = _full_run(_scorecard("vrun-newf0001"))
            store.save(run)
            assert store.get(run.run_id) == run
        finally:
            store.close()

    def test_scorecard_json_remains_queryable(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        """Indexed hot columns stay consistent with the stored blobs."""
        store = make_store()
        run = _full_run(_scorecard())
        store.save(run)
        conn = sqlite3.connect(store.db_path)
        try:
            (card_json, ep_json) = conn.execute(
                "SELECT scorecard_json, episodes_json FROM validations WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert json.loads(card_json)["run_id"] == run.run_id
        assert len(json.loads(ep_json)) == 2
