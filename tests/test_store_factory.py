"""Tests for the environment-driven ``create_store()`` backend factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from validsim.store import SqliteValidationStore, ValidationStore, create_store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a deterministic environment for every test in this module."""
    monkeypatch.delenv("VALIDSIM_STORE", raising=False)
    monkeypatch.delenv("VALIDSIM_SQLITE_PATH", raising=False)


class TestMemoryDefault:
    def test_default_is_memory(self) -> None:
        store = create_store()
        assert isinstance(store, ValidationStore)
        assert not isinstance(store, SqliteValidationStore)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_STORE", "memory")
        assert type(create_store()) is ValidationStore


class TestSqliteSelection:
    def test_sqlite_backend_with_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db = tmp_path / "factory.db"
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
        store = create_store()
        try:
            assert isinstance(store, SqliteValidationStore)
            assert store.db_path == str(db)
            assert db.exists()
        finally:
            store.close()

    def test_backend_name_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("VALIDSIM_STORE", "SQLite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(tmp_path / "ci.db"))
        store = create_store()
        try:
            assert isinstance(store, SqliteValidationStore)
        finally:
            store.close()

    def test_sqlite_default_path_used(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.chdir(tmp_path)  # default "validsim.db" resolves here
        store = create_store()
        try:
            assert isinstance(store, SqliteValidationStore)
            assert store.db_path == "validsim.db"
        finally:
            store.close()
