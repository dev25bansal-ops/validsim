"""Tests for the in-memory, thread-safe validation store."""

from __future__ import annotations

import re
import threading

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore


def _scorecard(run_id: str = "vrun-cafe1234", created_at: str = "2026-01-01T00:00:00+00:00",
               **overrides: object) -> Scorecard:
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "composite_score": 90.0,
        "success_rate": 0.9,
        "safety_score": 80.0,
        "robustness_score": 100.0,
        "regression_delta": None,
        "confidence_interval": None,
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": created_at,
        "episode_count": 100,
        "failure_taxonomy": {},
    }
    base.update(overrides)
    return Scorecard(**base)  # type: ignore[arg-type]


def _run(run_id: str = "vrun-cafe1234", checkpoint_id: str = "ckpt-1",
         created_at: str = "2026-01-01T00:00:00+00:00") -> StoredRun:
    sc = _scorecard(run_id=run_id, checkpoint_id=checkpoint_id, created_at=created_at)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9,
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


class TestRunIdFormat:
    def test_new_run_id_has_prefix_and_8_chars(self) -> None:
        run_id = ValidationStore.new_run_id()
        assert run_id.startswith("vrun-")
        suffix = run_id.removeprefix("vrun-")
        assert len(suffix) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", suffix)

    def test_new_run_ids_are_unique(self) -> None:
        ids = {ValidationStore.new_run_id() for _ in range(200)}
        assert len(ids) == 200


class TestSaveGetRoundTrip:
    def test_save_then_get_returns_same_object(self) -> None:
        store = ValidationStore()
        run = _run()
        saved = store.save(run)
        assert saved is run
        assert store.get(run.run_id) is run

    def test_get_unknown_id_returns_none(self) -> None:
        store = ValidationStore()
        assert store.get("vrun-doesnot1") is None

    def test_save_overwrites_existing_id(self) -> None:
        store = ValidationStore()
        first = _run(run_id="vrun-00000001")
        second = _run(run_id="vrun-00000001", created_at="2026-01-02T00:00:00+00:00")
        store.save(first)
        store.save(second)
        assert len(store) == 1
        assert store.get("vrun-00000001") is second


class TestOrdering:
    def test_list_for_checkpoint_filters_and_orders_oldest_first(self) -> None:
        store = ValidationStore()
        store.save(_run(run_id="vrun-a0000001", checkpoint_id="ckpt-1",
                        created_at="2026-01-03T00:00:00+00:00"))
        store.save(_run(run_id="vrun-b0000002", checkpoint_id="ckpt-2",
                        created_at="2026-01-01T00:00:00+00:00"))
        store.save(_run(run_id="vrun-c0000003", checkpoint_id="ckpt-1",
                        created_at="2026-01-02T00:00:00+00:00"))
        runs = store.list_for_checkpoint("ckpt-1")
        assert [r.run_id for r in runs] == ["vrun-c0000003", "vrun-a0000001"]
        assert all(r.checkpoint_id == "ckpt-1" for r in runs)

    def test_list_for_checkpoint_empty_result(self) -> None:
        store = ValidationStore()
        assert store.list_for_checkpoint("ckpt-nope") == []

    def test_history_orders_oldest_first_across_checkpoints(self) -> None:
        store = ValidationStore()
        stamps = ["2026-03-01", "2026-01-01", "2026-02-01"]
        for i, ts in enumerate(stamps):
            store.save(_run(run_id=f"vrun-{i:08x}", checkpoint_id=f"ckpt-{i}",
                            created_at=ts))
        runs = store.history()
        assert [r.created_at for r in runs] == sorted(stamps)


class TestThreadSafety:
    def test_concurrent_saves_from_8_threads(self) -> None:
        store = ValidationStore()
        n_threads, per_thread = 8, 25
        errors: list[Exception] = []

        def worker(offset: int) -> None:
            try:
                for i in range(per_thread):
                    run = _run(run_id=f"vrun-{offset * per_thread + i:08x}")
                    store.save(run)
                    assert store.get(run.run_id) is not None
            except Exception as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        assert len(store) == n_threads * per_thread
        assert len(store.history()) == n_threads * per_thread


class TestLenAndClose:
    def test_len_reflects_store_contents(self) -> None:
        store = ValidationStore()
        assert len(store) == 0
        store.save(_run(run_id="vrun-00000001"))
        store.save(_run(run_id="vrun-00000002"))
        assert len(store) == 2

    def test_close_is_noop_and_store_remains_usable(self) -> None:
        store = ValidationStore()
        run = _run()
        store.save(run)
        store.close()  # must not raise
        assert store.get(run.run_id) is run
        store.close()
        assert len(store) == 1


class TestSummary:
    def test_summary_shape(self) -> None:
        run = _run(run_id="vrun-cafe1234")
        summary = run.summary()
        assert set(summary) == {
            "run_id",
            "checkpoint_id",
            "task_id",
            "created_at",
            "baseline_run_id",
            "composite_score",
            "deploy_decision",
            "episode_count",
        }
        assert summary["run_id"] == "vrun-cafe1234"
        assert summary["checkpoint_id"] == "ckpt-1"
        assert summary["task_id"] == "pick-place"
        assert summary["created_at"] == "2026-01-01T00:00:00+00:00"
        assert summary["baseline_run_id"] is None
        assert summary["composite_score"] == 90.0
        assert summary["deploy_decision"] == "APPROVE"
        assert summary["episode_count"] == 100

    def test_summary_values_follow_scorecard(self) -> None:
        sc = _scorecard(
            run_id="vrun-deadbeef",
            composite_score=70.0,
            deploy_decision="BLOCK",
            episode_count=42,
        )
        run = StoredRun(
            run_id="vrun-deadbeef",
            checkpoint_id=sc.checkpoint_id,
            task_id=sc.task_id,
            created_at=sc.created_at,
            scorecard=sc,
            evaluation=EvaluationResult(total_episodes=42, success_count=30,
                                        success_rate=0.714),
            safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
            baseline_run_id="vrun-baseline1",
        )
        summary = run.summary()
        assert summary["composite_score"] == 70.0
        assert summary["deploy_decision"] == "BLOCK"
        assert summary["episode_count"] == 42
        assert summary["baseline_run_id"] == "vrun-baseline1"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
