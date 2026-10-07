"""Pin the observable backend resolution of ``create_store()``.

Each case catches a wrong branch in the environment-driven factory, including a
silent switch away from the zero-config in-memory default. SQLite and
PostgreSQL construction are side-effect free here; SQLite is additionally
pointed at ``tmp_path`` so this test never creates a database in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from validsim.store import (
    PostgresValidationStore,
    SqliteValidationStore,
    ValidationStore,
    create_store,
)


@pytest.mark.parametrize(
    ("configured_backend", "expected_type"),
    [
        (None, ValidationStore),
        ("sqlite", SqliteValidationStore),
        ("postgres", PostgresValidationStore),
        ("not-a-store", ValidationStore),
    ],
)
def test_create_store_resolves_configured_backend(
    configured_backend: str | None,
    expected_type: type[ValidationStore],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unset uses memory; explicit and unknown values resolve deterministically."""
    if configured_backend is None:
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
    else:
        monkeypatch.setenv("VALIDSIM_STORE", configured_backend)

    monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(tmp_path / "factory.db"))
    monkeypatch.setenv("VALIDSIM_PG_URL", "postgresql://user@localhost:5432/valid")
    store = create_store()
    try:
        assert type(store) is expected_type
    finally:
        store.close()
