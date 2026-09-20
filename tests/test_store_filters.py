"""Tests for optional date-range filtering on the stores' ``history()``.

Every backend persists ``created_at`` as ISO-8601 UTC text in a fixed
``+00:00``-offset format, so the ``since``/``until`` bounds are compared
lexicographically — which equals chronological order for these stamps, and
makes both bounds inclusive.

The in-memory and SQLite backends are exercised through *real* filtering.
The PostgreSQL backend is checked through an injected fake connection that
captures the generated SQL and bound parameters (no live server, the psycopg
driver is never imported): we assert the ``WHERE`` clauses use placeholders
and that the bound values are never interpolated into the statement.
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


def _scorecard(run_id: str, created_at: str) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )


def _run(run_id: str, created_at: str) -> StoredRun:
    sc = _scorecard(run_id, created_at)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9,
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


#: Three runs a day apart, ordered oldest -> newest.
_STAMPS: list[tuple[str, str]] = [
    ("vrun-00000001", "2026-01-01T00:00:00+00:00"),
    ("vrun-00000002", "2026-01-02T00:00:00+00:00"),
    ("vrun-00000003", "2026-01-03T00:00:00+00:00"),
]


def _ids(runs: list[StoredRun]) -> list[str]:
    return [r.run_id for r in runs]


# Shared expectation table for the two real-filtering backends:
# (history kwargs, expected run ids oldest-first).
_CASES: list[tuple[dict[str, str], list[str]]] = [
    ({}, ["vrun-00000001", "vrun-00000002", "vrun-00000003"]),
    ({"since": "2026-01-02T00:00:00+00:00"}, ["vrun-00000002", "vrun-00000003"]),
    ({"until": "2026-01-02T00:00:00+00:00"}, ["vrun-00000001", "vrun-00000002"]),
    (
        {"since": "2026-01-02T00:00:00+00:00", "until": "2026-01-02T00:00:00+00:00"},
        ["vrun-00000002"],
    ),
    # A mid-day lower bound excludes the first run's 00:00:00 stamp.
    ({"since": "2026-01-01T12:00:00+00:00"}, ["vrun-00000002", "vrun-00000003"]),
    # Bounds that match nothing yield an empty history.
    ({"since": "2026-02-01T00:00:00+00:00"}, []),
    ({"until": "2025-12-31T23:59:59+00:00"}, []),
]


class TestMemoryHistoryFilters:
    def _store(self) -> ValidationStore:
        store = ValidationStore()
        for run_id, ts in _STAMPS:
            store.save(_run(run_id, ts))
        return store

    @pytest.mark.parametrize("kwargs,expected", _CASES)
    def test_filters(
        self, kwargs: dict[str, str], expected: list[str]
    ) -> None:
        assert _ids(self._store().history(**kwargs)) == expected

    def test_default_matches_explicit_none_bounds(self) -> None:
        store = self._store()
        assert store.history() == store.history(None, None)
        assert _ids(store.history()) == [rid for rid, _ in _STAMPS]

    def test_bounds_inclusive_on_exact_match(self) -> None:
        store = self._store()
        exact = store.history(
            since="2026-01-01T00:00:00+00:00", until="2026-01-03T00:00:00+00:00"
        )
        assert _ids(exact) == [rid for rid, _ in _STAMPS]


@pytest.fixture()
def make_store(tmp_path: Path) -> Iterator[Callable[[str], SqliteValidationStore]]:
    """Factory opening named SQLite stores under ``tmp_path``; closes on exit."""
    created: list[SqliteValidationStore] = []

    def _make(name: str = "filters.db") -> SqliteValidationStore:
        store = SqliteValidationStore(tmp_path / name)
        created.append(store)
        return store

    yield _make
    for store in created:
        store.close()


class TestSqliteHistoryFilters:
    def _store(self, make_store: Callable[[str], SqliteValidationStore]) -> SqliteValidationStore:
        store = make_store()
        for run_id, ts in _STAMPS:
            store.save(_run(run_id, ts))
        return store

    @pytest.mark.parametrize("kwargs,expected", _CASES)
    def test_filters(
        self,
        make_store: Callable[[str], SqliteValidationStore],
        kwargs: dict[str, str],
        expected: list[str],
    ) -> None:
        store = self._store(make_store)
        assert _ids(store.history(**kwargs)) == expected

    def test_default_matches_full_history(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = self._store(make_store)
        assert store.history() == store.history(None, None)
        assert _ids(store.history()) == [rid for rid, _ in _STAMPS]


# ---------------------------------------------------------------------------
# PostgreSQL: SQL construction asserted through an injected fake connection.
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor exposing fetchone/fetchall over queued rows."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Stand-in for a psycopg 3 connection recording every executed query."""

    def __init__(self, select_rows: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._select_rows = list(select_rows or [])
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _FakeCursor:
        self.calls.append((sql, tuple(params or ())))
        rows = self._select_rows if sql.strip().upper().startswith("SELECT") else []
        return _FakeCursor(rows)

    def close(self) -> None:
        self.closed = True

    def history_query(self) -> tuple[str, tuple[Any, ...]]:
        """Return the captured ``history()`` SELECT (scorecard projection)."""
        for sql, params in self.calls:
            head = sql.strip().upper()
            if head.startswith("SELECT SCORECARD") and "ORDER BY CREATED_AT ASC" in head:
                return sql, params
        raise AssertionError(f"no history SELECT captured in {[s for s, _ in self.calls]}")


def _store_and_conn() -> tuple[PostgresValidationStore, _FakeConnection]:
    conn = _FakeConnection()
    store = PostgresValidationStore(_conn_factory=lambda: conn)
    return store, conn


class TestPostgresHistorySql:
    def test_no_filters_omits_where(self) -> None:
        store, conn = _store_and_conn()
        store.history()
        sql, params = conn.history_query()
        assert "WHERE" not in sql
        assert params == ()
        assert "ORDER BY created_at ASC" in sql

    def test_since_uses_placeholder_not_interpolation(self) -> None:
        store, conn = _store_and_conn()
        store.history(since="2026-01-02T00:00:00+00:00")
        sql, params = conn.history_query()
        assert "created_at >= %s" in sql
        assert "created_at <= %s" not in sql
        assert params == ("2026-01-02T00:00:00+00:00",)
        # The value must travel only through the placeholder, never the SQL.
        assert "2026-01-02" not in sql

    def test_until_uses_placeholder(self) -> None:
        store, conn = _store_and_conn()
        store.history(until="2026-01-02T00:00:00+00:00")
        sql, params = conn.history_query()
        assert "created_at <= %s" in sql
        assert "created_at >= %s" not in sql
        assert params == ("2026-01-02T00:00:00+00:00",)
        assert "2026-01-02" not in sql

    def test_both_bounds_combined_with_and(self) -> None:
        store, conn = _store_and_conn()
        store.history(
            since="2026-01-01T00:00:00+00:00", until="2026-01-03T00:00:00+00:00"
        )
        sql, params = conn.history_query()
        assert "WHERE created_at >= %s AND created_at <= %s" in sql
        assert params == (
            "2026-01-01T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
        )

    def test_filtered_history_restores_rows(self) -> None:
        """A non-empty result set is still reconstructed via _run_from_row."""
        sc = _scorecard("vrun-00000002", "2026-01-02T00:00:00+00:00")
        conn = _FakeConnection(select_rows=[(sc.to_dict(),)])
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        runs = store.history(since="2026-01-02T00:00:00+00:00")
        assert [r.run_id for r in runs] == ["vrun-00000002"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
