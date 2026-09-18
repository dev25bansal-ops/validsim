"""Validation result storage backends and backend-selection factory."""

from __future__ import annotations

import os

from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore

__all__ = [
    "PostgresValidationStore",
    "SqliteValidationStore",
    "StoredRun",
    "ValidationStore",
    "create_store",
]

#: Env var selecting the backend ("memory" | "sqlite" | "postgres"); defaults to memory.
_STORE_ENV = "VALIDSIM_STORE"
#: Env var overriding the SQLite database path (default ``validsim.db``).
_SQLITE_PATH_ENV = "VALIDSIM_SQLITE_PATH"
_DEFAULT_SQLITE_PATH = "validsim.db"


def create_store() -> ValidationStore:
    """Build a validation store from the environment configuration.

    Reads ``VALIDSIM_STORE`` (case-insensitive):

    * ``"sqlite"`` — :class:`SqliteValidationStore` using
      ``VALIDSIM_SQLITE_PATH`` (defaulting to ``validsim.db``).
    * ``"postgres"`` — :class:`PostgresValidationStore` using the DSN from
      ``VALIDSIM_PG_URL``. Construction is side-effect free (no driver
      import or connection until first use), but it fails fast with
      ``ValueError`` when no DSN is configured and the psycopg driver is
      installed.
    * anything else — the default in-memory :class:`ValidationStore`,
      keeping tests and local runs side-effect free.
    """
    backend = os.environ.get(_STORE_ENV, "memory").strip().lower()
    if backend == "sqlite":
        path = os.environ.get(_SQLITE_PATH_ENV, _DEFAULT_SQLITE_PATH)
        return SqliteValidationStore(path)
    if backend == "postgres":
        return PostgresValidationStore()
    return ValidationStore()
