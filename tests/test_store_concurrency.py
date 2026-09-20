"""Concurrency tests for the in-memory and SQLite validation stores.

Both backends promise thread-safe writes, so these tests hammer ``save`` from
many worker threads (released together by a :class:`threading.Barrier`) and then
assert that no write was lost and that every worker completed without raising.
The SQLite cases additionally verify that checkpoint queries stay correct under
contention and that concurrently written rows survive a close/reopen cycle.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore


def _iso(ordinal: int) -> str:
    """Return a unique, monotonic ISO-8601 timestamp for ``ordinal``.

    Uniqueness keeps ``ORDER BY created_at`` deterministic even when many rows
    are inserted out of order by contending threads.
    """
    return f"2026-01-01T00:{ordinal // 60:02d}:{ordinal % 60:02d}+00:00"


def _scorecard(
    run_id: str,
    checkpoint_id: str = "ckpt-1",
    created_at: str = "2026-01-01T00:00:00+00:00",
    **overrides: object,
) -> Scorecard:
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": checkpoint_id,
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


def _run(
    run_id: str,
    checkpoint_id: str = "ckpt-1",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> StoredRun:
    sc = _scorecard(
        run_id=run_id, checkpoint_id=checkpoint_id, created_at=created_at
    )
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=sc.episode_count,
            success_count=90,
            success_rate=sc.success_rate,
            failure_taxonomy=dict(sc.failure_taxonomy),
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, sc.safety_score),
    )


def _submit_all(worker: Callable[[int], None], n_threads: int) -> list[Exception]:
    """Run ``worker(idx)`` on ``n_threads`` pool threads released by a barrier.

    A barrier guarantees the workers start simultaneously, producing maximal
    contention on the shared store. Exceptions raised inside a worker are
    collected (rather than propagating from ``future.result``) so the test can
    report every failure instead of stopping at the first one.
    """
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def wrapped(idx: int) -> None:
        try:
            barrier.wait(timeout=30)
            worker(idx)
        except Exception as exc:  # pragma: no cover - failure reporting
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(wrapped, t) for t in range(n_threads)]
        for future in futures:
            future.result(timeout=120)
    return errors


class TestMemoryStoreConcurrency:
    def test_16_threads_x_50_unique_saves_no_lost_updates(self) -> None:
        store = ValidationStore()
        n_threads, per_thread = 16, 50
        total = n_threads * per_thread

        def worker(t: int) -> None:
            for i in range(per_thread):
                idx = t * per_thread + i
                run_id = f"vrun-{idx:08x}"
                store.save(_run(run_id, created_at=_iso(idx)))
                assert store.get(run_id) is not None

        errors = _submit_all(worker, n_threads)

        assert not errors
        assert len(store) == total
        history = store.history()
        assert len(history) == total
        # No lost updates: every unique id is present and correct.
        assert {r.run_id for r in history} == {
            f"vrun-{idx:08x}" for idx in range(total)
        }


class TestSqliteStoreConcurrency:
    def test_contention_between_two_checkpoints(self, tmp_path: Path) -> None:
        store = SqliteValidationStore(tmp_path / "contention.db")
        n_threads, per_thread = 8, 20
        total = n_threads * per_thread
        checkpoints = ("ckpt-A", "ckpt-B")

        def worker(t: int) -> None:
            for i in range(per_thread):
                idx = t * per_thread + i
                ckpt = checkpoints[i % 2]
                store.save(
                    _run(
                        f"vrun-{idx:08x}",
                        checkpoint_id=ckpt,
                        created_at=_iso(idx),
                    )
                )

        try:
            errors = _submit_all(worker, n_threads)
            assert not errors
            assert len(store) == total

            for ckpt in checkpoints:
                expected = {
                    f"vrun-{t * per_thread + i:08x}"
                    for t in range(n_threads)
                    for i in range(per_thread)
                    if checkpoints[i % 2] == ckpt
                }
                runs = store.list_for_checkpoint(ckpt)
                # Each bucket has half the total, all correctly filtered.
                assert len(expected) == total // 2
                assert len(runs) == len(expected)
                assert {r.run_id for r in runs} == expected
                assert all(r.checkpoint_id == ckpt for r in runs)
                # Deterministic oldest-first despite out-of-order inserts.
                created = [r.created_at for r in runs]
                assert created == sorted(created)
        finally:
            store.close()

    def test_concurrent_write_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist-concurrent.db"
        n_threads, per_thread = 8, 20
        total = n_threads * per_thread

        first = SqliteValidationStore(path)

        def writer(t: int) -> None:
            for i in range(per_thread):
                idx = t * per_thread + i
                first.save(
                    _run(f"vrun-{idx:08x}", checkpoint_id="ckpt-1",
                         created_at=_iso(idx))
                )

        try:
            errors = _submit_all(writer, n_threads)
            assert not errors
            assert len(first) == total
        finally:
            first.close()

        # A fresh connection on the same file must observe every write.
        second = SqliteValidationStore(path)
        try:
            assert len(second) == total
            history = second.history()
            assert len(history) == total
            assert {r.run_id for r in history} == {
                f"vrun-{idx:08x}" for idx in range(total)
            }
            for idx in range(total):
                got = second.get(f"vrun-{idx:08x}")
                assert got is not None
                assert got.run_id == f"vrun-{idx:08x}"
                assert got.created_at == _iso(idx)
                assert got.checkpoint_id == "ckpt-1"
            # Spot-check full scorecard round-trip on the persisted rows.
            probe = second.get("vrun-00000000")
            assert probe is not None
            assert probe.scorecard == _scorecard("vrun-00000000", created_at=_iso(0))
        finally:
            second.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))