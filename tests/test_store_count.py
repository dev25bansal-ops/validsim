"""Tests for the ``count()`` method on all three validation-store backends.

Every backend must expose ``count() -> int`` returning the number of stored
runs, matching ``len(store)``. The in-memory and SQLite backends are exercised
against real state; the PostgreSQL backend is exercised through the injected
``_conn_factory`` fake (the same seam the existing Postgres tests use), so the
``SELECT COUNT(*)`` statement and its decoded result are asserted without a
live server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from conftest import make_scorecard, make_stored_run
from validsim.store.memory import ValidationStore
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class TestMemoryCount:
    def test_empty_store_counts_zero(self) -> None:
        assert ValidationStore().count() == 0

    def test_count_tracks_saves(self) -> None:
        store = ValidationStore()
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa")))
        store.save(make_stored_run(make_scorecard("vrun-bbbbbbbb")))
        assert store.count() == 2

    def test_count_matches_len(self) -> None:
        store = ValidationStore()
        for suffix in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
            store.save(make_stored_run(make_scorecard(f"vrun-{suffix}")))
        assert store.count() == len(store) == 3

    def test_overwrite_does_not_increase_count(self) -> None:
        store = ValidationStore()
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa")))
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa", composite_score=10.0)))
        assert store.count() == 1

    def test_count_after_delete(self) -> None:
        store = ValidationStore()
        run = make_stored_run(make_scorecard("vrun-aaaaaaaa"))
        store.save(run)
        store.save(make_stored_run(make_scorecard("vrun-bbbbbbbb")))
        store.delete(run.run_id)
        assert store.count() == 1


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


class TestSqliteCount:
    def test_empty_store_counts_zero(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        assert make_store().count() == 0

    def test_count_tracks_saves(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa")))
        store.save(make_stored_run(make_scorecard("vrun-bbbbbbbb")))
        assert store.count() == 2
        assert store.count() == len(store)

    def test_overwrite_does_not_increase_count(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa")))
        store.save(make_stored_run(make_scorecard("vrun-aaaaaaaa", composite_score=10.0)))
        assert store.count() == 1

    def test_count_persists_across_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "count_persist.db"
        first = SqliteValidationStore(path)
        first.save(make_stored_run(make_scorecard("vrun-aaaaaaaa")))
        first.save(make_stored_run(make_scorecard("vrun-bbbbbbbb")))
        assert first.count() == 2
        first.close()
        second = SqliteValidationStore(path)
        try:
            assert second.count() == 2
        finally:
            second.close()


# ---------------------------------------------------------------------------
# PostgreSQL backend (via the injected fake-connection seam)
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor exposing fetchone/fetchall over queued rows."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = list(rows or [])

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Stand-in for a psycopg 3 connection recording every executed query.

    SELECTs yield the queued ``select_rows`` (so a ``SELECT COUNT(*)`` reads
    back the prepared scalar); all statements are captured in ``calls``.
    """

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

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    def find(self, prefix: str) -> tuple[str, tuple[Any, ...]]:
        want = prefix.upper()
        for sql, params in self.calls:
            if sql.strip().upper().startswith(want):
                return sql, params
        raise AssertionError(f"no statement starting with {prefix!r} in {self.statements()}")


class TestPostgresCount:
    def _store(
        self, select_rows: list[Any] | None = None
    ) -> tuple[PostgresValidationStore, _FakeConnection]:
        conn = _FakeConnection(select_rows)
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        return store, conn

    def test_count_returns_scalar_from_select(self) -> None:
        store, _ = self._store(select_rows=[(7,)])
        assert store.count() == 7

    def test_count_matches_len(self) -> None:
        store, _ = self._store(select_rows=[(3,)])
        assert store.count() == len(store) == 3

    def test_count_uses_select_count_star(self) -> None:
        store, conn = self._store(select_rows=[(5,)])
        store.count()
        sql, params = conn.find("SELECT COUNT")
        assert sql == "SELECT COUNT(*) FROM validations"
        # No values are interpolated or parameterized for a plain count.
        assert params == ()

    def test_count_interpolates_validated_table_identifier(self) -> None:
        conn = _FakeConnection(select_rows=[(2,)])
        store = PostgresValidationStore(
            table="my_validations", _conn_factory=lambda: conn
        )
        assert store.count() == 2
        sql, _ = conn.find("SELECT COUNT")
        assert sql == "SELECT COUNT(*) FROM my_validations"

    def test_count_does_not_open_real_connection(self) -> None:
        store, conn = self._store(select_rows=[(1,)])
        assert store._conn is None  # deferred until first use
        store.count()
        assert store._conn is conn  # fake adopted, not a real connection


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
