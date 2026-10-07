"""Validation result storage backends and backend-selection factory."""

from __future__ import annotations

import os

from validsim.store.memory import (
    DuplicateRunError,
    StoredRun,
    ValidationStore,
    reconstruct_kwargs,
)
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore

__all__ = [
    "DuplicateRunError",
    "PostgresValidationStore",
    "SqliteValidationStore",
    "StoredRun",
    "ValidationStore",
    "create_store",
    "reconstruct_kwargs",
    "store_backend",
]

#: Env var selecting the backend ("memory" | "sqlite" | "postgres").
_STORE_ENV = "VALIDSIM_STORE"
#: Backend used when ``VALIDSIM_STORE`` is unset or blank.
_DEFAULT_BACKEND = "memory"
#: Env var overriding the SQLite database path (default ``validsim.db``).
_SQLITE_PATH_ENV = "VALIDSIM_SQLITE_PATH"
_DEFAULT_SQLITE_PATH = "validsim.db"


def store_backend() -> str:
    """Return the normalised backend name selected by the environment.

    Single source of truth for the ``memory`` default: callers that must refuse
    an ephemeral store (``validsim gate``) ask this instead of re-reading the
    variable, so the two can never disagree about what the default is.
    """
    return os.environ.get(_STORE_ENV, _DEFAULT_BACKEND).strip().lower() or _DEFAULT_BACKEND


def create_store() -> ValidationStore:
    """Build a validation store from the environment configuration.

    Reads ``VALIDSIM_STORE`` (case-insensitive) via :func:`store_backend`:

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
    backend = store_backend()
    if backend == "sqlite":
        # A blank/whitespace-only ``VALIDSIM_SQLITE_PATH`` means "unset", not
        # "the current directory". ``os.environ.get`` returns "" for an empty
        # value, and ``sqlite3.connect("")`` opens a *private temporary*
        # database that is discarded when the connection closes -- so a
        # deployment with an empty env var silently lost every run while
        # appearing to work. Fall back to the documented default instead.
        path = os.environ.get(_SQLITE_PATH_ENV, "").strip() or _DEFAULT_SQLITE_PATH
        return SqliteValidationStore(path)
    if backend == "postgres":
        return PostgresValidationStore()
    return ValidationStore()
