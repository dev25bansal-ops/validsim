"""Tests for run deletion across all three validation-store backends.

Each backend must satisfy the same contract: :meth:`delete` removes the row
for a known ``run_id`` and returns ``True``, and for an unknown id removes
nothing and returns ``False`` (idempotent double-delete included). The
in-memory and SQLite backends are exercised against real state; the
PostgreSQL backend is exercised through the injected ``_conn_factory`` fake
(the same seam the existing Postgres tests use), so SQL construction and the
``rowcount``-derived return value are asserted without a live server.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.postgres import PostgresValidationStore
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
        "regression_delta": None,
        "confidence_interval": None,
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "episode_count": 100,
        "failure_taxonomy": {},
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


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class TestMemoryDelete:
    def test_delete_existing_returns_true_and_removes(self) -> None:
        store = ValidationStore()
        run = _run(_scorecard())
        store.save(run)
        assert store.delete(run.run_id) is True
        assert store.get(run.run_id) is None
        assert len(store) == 0

    def test_delete_unknown_returns_false(self) -> None:
        store = ValidationStore()
        assert store.delete("vrun-nope0001") is False

    def test_double_delete_second_is_false(self) -> None:
        store = ValidationStore()
        run = _run(_scorecard())
        store.save(run)
        assert store.delete(run.run_id) is True
        assert store.delete(run.run_id) is False

    def test_delete_only_removes_target(self) -> None:
        store = ValidationStore()
        a = _run(_scorecard("vrun-aaaaaaaa"))
        b = _run(_scorecard("vrun-bbbbbbbb"))
        store.save(a)
        store.save(b)
        assert store.delete(a.run_id) is True
        assert store.get(a.run_id) is None
        assert store.get(b.run_id) is b
        assert len(store) == 1


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_store(tmp_path: Path) -> Iterator[Callable[[str], SqliteValidationStore]]:
    """Factory opening named SQLite stores under ``tmp_path``; closes on exit."""
    created: list[SqliteValidationStore] = []

    def _make(name: str = "delete.db") -> SqliteValidationStore:
        store = SqliteValidationStore(tmp_path / name)
        created.append(store)
        return store

    yield _make
    for store in created:
        store.close()


class TestSqliteDelete:
    def test_delete_existing_returns_true_and_removes(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = _run(_scorecard())
        store.save(run)
        assert store.delete(run.run_id) is True
        assert store.get(run.run_id) is None
        assert len(store) == 0

    def test_delete_unknown_returns_false(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        assert make_store().delete("vrun-deadbeef") is False

    def test_double_delete_second_is_false(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = _run(_scorecard())
        store.save(run)
        assert store.delete(run.run_id) is True
        assert store.delete(run.run_id) is False

    def test_delete_only_removes_target(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        a = _run(_scorecard("vrun-aaaaaaaa"))
        b = _run(_scorecard("vrun-bbbbbbbb"))
        store.save(a)
        store.save(b)
        assert store.delete(a.run_id) is True
        assert store.get(b.run_id) is not None
        assert len(store) == 1

    def test_deletion_persists_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "delete_persist.db"
        run = _run(_scorecard())
        first = SqliteValidationStore(path)
        first.save(run)
        assert first.delete(run.run_id) is True
        first.close()
        second = SqliteValidationStore(path)
        try:
            assert second.get(run.run_id) is None
            assert len(second) == 0
        finally:
            second.close()


# ---------------------------------------------------------------------------
# PostgreSQL backend (via the injected fake-connection seam)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor exposing ``rowcount`` plus fetch helpers."""

    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Stand-in for a psycopg 3 connection recording every executed query.

    ``DELETE`` statements return a cursor whose ``rowcount`` is the configured
    ``delete_rowcount``; everything else (schema DDL, SELECTs) returns an empty
    cursor. All statements are captured in ``calls`` for assertions.
    """

    def __init__(self, delete_rowcount: int = 0) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._delete_rowcount = delete_rowcount
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _FakeCursor:
        self.calls.append((sql, tuple(params or ())))
        if sql.strip().upper().startswith("DELETE"):
            return _FakeCursor(rowcount=self._delete_rowcount)
        return _FakeCursor()

    def close(self) -> None:
        self.closed = True

    # -- assertion helpers ------------------------------------------------

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    def find(self, prefix: str) -> tuple[str, tuple[Any, ...]]:
        want = prefix.upper()
        for sql, params in self.calls:
            if sql.strip().upper().startswith(want):
                return sql, params
        raise AssertionError(f"no statement starting with {prefix!r} in {self.statements()}")


class TestPostgresDelete:
    def _store(
        self, delete_rowcount: int = 0
    ) -> tuple[PostgresValidationStore, _FakeConnection]:
        conn = _FakeConnection(delete_rowcount)
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        return store, conn

    def test_delete_uses_parameterized_placeholder(self) -> None:
        store, conn = self._store(delete_rowcount=1)
        assert store.delete("vrun-cafe1234") is True
        sql, params = conn.find("DELETE")
        assert "DELETE FROM validations WHERE run_id = %s" in sql
        # The value travels only via params — never interpolated into the SQL.
        assert "vrun-cafe1234" not in sql
        assert params == ("vrun-cafe1234",)

    def test_delete_returns_true_when_row_removed(self) -> None:
        store, _ = self._store(delete_rowcount=1)
        assert store.delete("vrun-abc00001") is True

    def test_delete_returns_false_when_no_row_matched(self) -> None:
        store, _ = self._store(delete_rowcount=0)
        assert store.delete("vrun-abc00001") is False

    def test_delete_interpolates_validated_table_identifier(self) -> None:
        conn = _FakeConnection(delete_rowcount=1)
        store = PostgresValidationStore(
            table="my_validations", _conn_factory=lambda: conn
        )
        assert store.delete("vrun-abc00001") is True
        sql, _ = conn.find("DELETE")
        assert "FROM my_validations" in sql

    def test_delete_does_not_open_real_connection(self) -> None:
        """The seam is used; no psycopg driver import or network is required."""
        store, conn = self._store(delete_rowcount=1)
        assert store._conn is None  # deferred until first use
        store.delete("vrun-cafe1234")
        assert store._conn is conn  # fake was adopted, not a real connection


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
