"""Persistent validation store backed by PostgreSQL via the psycopg 3 driver.

:class:`PostgresValidationStore` mirrors the public interface of the
in-memory :class:`~validsim.store.memory.ValidationStore` and the
:class:`~validsim.store.sqlite.SqliteValidationStore`
(save/get/list_for_checkpoint/history/new_run_id/__len__/close) so callers
can be swapped between backends without change. Following the SQLite
pattern, each run is persisted as one row: the composite
:class:`~validsim.engine.scorecard.Scorecard` is stored as a JSONB blob
while the query-hot fields (checkpoint, score, decision, timestamp) are
promoted to indexed columns.

Two properties make this backend safe to select in environments that do
not have the driver installed:

1. **Lazy driver import.** ``psycopg`` is imported only inside
   :func:`_import_psycopg`, never at module level, so importing this
   module always succeeds; a missing driver surfaces as a clear
   ``RuntimeError`` ("pip install 'psycopg[binary]'") at first use.
2. **Deferred connection + schema setup (design choice).** ``__init__``
   performs pure configuration validation only — whitelist-checking the
   table name and resolving the DSN (fail-fast ``ValueError`` when no
   DSN is available *and* the driver is present). The single connection
   is opened, and ``CREATE TABLE/INDEX IF NOT EXISTS`` executed, lazily
   on the first query (``_ensure_ready``). This lets
   ``create_store()`` return a configured store without touching the
   network. For tests the private ``_conn_factory`` parameter injects a
   stand-in connection object, so SQL construction (placeholder usage,
   conflict clause, identifier interpolation) can be asserted without a
   live server. Both mechanisms are provided; the fake-factory is what
   the unit tests use.

Security rules baked into the SQL layer:

* The table name is a SQL *identifier* and cannot be parameterized, so it
  is validated against ``^[a-z_][a-z0-9_]*$`` and anything else is
  rejected — interpolation only ever happens after that check.
* All *values* travel exclusively through ``%s`` placeholders; no value
  is ever formatted into a SQL string.

``save`` uses ``ON CONFLICT (run_id) DO NOTHING``, so the first write for a run
id wins: the verdict log is **append-only**. That is the contract declared once
on :class:`~validsim.store.memory.ValidationStore` and now implemented
identically by every backend -- the SQLite backend previously used ``INSERT OR
REPLACE`` (last-write-wins), which made the meaning of a stored verdict depend
on the configured backend. See that class docstring for why a duplicate must
never rewrite a signed-off verdict, and why the sanctioned correction is
``delete`` followed by a fresh ``save``. The full run detail (evaluation,
safety, raw episodes, baseline id and regression report) is persisted
alongside the scorecard in JSONB detail columns that are ``ALTER``-ed onto the
schema on first use, so rows written before those columns existed remain
readable through the same fallback the SQLite backend applies to legacy rows.
Scorecard reconstruction is shared with SQLite via
:func:`~validsim.store.memory.reconstruct_kwargs`, which derives its fields from
the dataclass so a newly added :class:`Scorecard` field is covered without
editing this module, and which falls back to dataclass defaults for rows
written before that field existed.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict
from typing import Any, Callable

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import (
    StoredRun,
    ValidationStore,
    rebind_row_identity,
    reconstruct_kwargs,
)

__all__ = ["PostgresValidationStore"]

#: Env var supplying the default PostgreSQL DSN (libpq connection string).
_PG_URL_ENV = "VALIDSIM_PG_URL"

#: Whitelist for the table identifier. SQL identifiers cannot be sent as
#: query parameters, so the name is validated against this strict pattern
#: before ever being interpolated into a statement (security rule).
_TABLE_NAME_RE = re.compile(r"\A[a-z_][a-z0-9_]*\Z")

#: Schema DDL templates; ``{table}`` is substituted only after the
#: whitelist check above. Statement list (not a script) because psycopg 3
#: executes one command per ``execute()`` call.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS {table} (
    run_id           TEXT PRIMARY KEY,
    checkpoint_id    TEXT NOT NULL,
    task_id          TEXT,
    composite_score  DOUBLE PRECISION,
    deploy_decision  TEXT,
    created_at       TEXT,
    scorecard        JSONB
)
""",
    "CREATE INDEX IF NOT EXISTS idx_{table}_checkpoint ON {table} (checkpoint_id)",
    "CREATE INDEX IF NOT EXISTS idx_{table}_created ON {table} (created_at)",
)

#: Detail columns added after the initial release; ALTERed onto existing
#: databases inside :meth:`PostgresValidationStore._ensure_ready`. Names and
#: types are fixed constants (never user input), so interpolating them as
#: identifiers is safe under the same rule applied to the table name.
_MIGRATION_COLUMNS: dict[str, str] = {
    "baseline_run_id": "TEXT",
    "evaluation_json": "JSONB",
    "safety_json": "JSONB",
    "episodes_json": "JSONB",
    "regression_json": "JSONB",
}

#: Projection used by every read path; ``scorecard`` always comes first,
#: followed by the detail columns in the fixed order :func:`_run_from_row`
#: expects, then the indexed identity columns that outrank the blob's.
_DETAIL_COLUMNS: tuple[str, ...] = (
    "scorecard",
    "baseline_run_id",
    "evaluation_json",
    "safety_json",
    "episodes_json",
    "regression_json",
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
)
_DETAIL_SELECT: str = ", ".join(_DETAIL_COLUMNS)


def _import_psycopg() -> Any:
    """Import and return the psycopg 3 driver module.

    Raises:
        RuntimeError: If the driver is not installed (or is not psycopg 3),
            with an actionable install hint. Importing this module never
            triggers this failure: the call is deliberately lazy.
    """
    try:
        import psycopg
        from psycopg import Connection  # noqa: F401  (v3-only symbol)
    except ImportError as exc:
        raise RuntimeError(
            "The PostgreSQL validation store requires the psycopg 3 driver, "
            "which is not installed. Install it with: "
            "pip install 'psycopg[binary]'"
        ) from exc
    version = str(getattr(psycopg, "__version__", ""))
    if version and not version.startswith("3"):
        raise RuntimeError(
            f"psycopg 3.x is required by PostgresValidationStore, found "
            f"{version!r}. Install it with: pip install 'psycopg[binary]'"
        )
    return psycopg


def _validate_table(table: str) -> str:
    """Return ``table`` if it is a safe bare identifier, else raise.

    Raises:
        ValueError: If ``table`` does not match ``^[a-z_][a-z0-9_]*$``.
            Identifiers cannot be parameterized, so anything else (quotes,
            semicolons, schema qualification, injection attempts) is
            rejected outright.
    """
    if not isinstance(table, str) or not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"Invalid PostgreSQL table name {table!r}: must match "
            f"{_TABLE_NAME_RE.pattern!r} (a plain lowercase identifier — "
            "SQL identifiers cannot be parameterized, so anything else is "
            "rejected to prevent injection)."
        )
    return table


def _scorecard_from_dict(data: dict[str, Any]) -> Scorecard:
    """Rebuild a frozen :class:`Scorecard` from its ``to_dict`` mapping.

    Delegates to the shared :func:`~validsim.store.memory.reconstruct_kwargs`
    -- the same call the SQLite backend makes -- so the two read paths cannot
    drift and a field added to :class:`Scorecard` needs no edit here. This
    function used to enumerate fields by hand, which is how the
    measurement-provenance fields came to be written to JSONB but silently reset
    to their defaults on every read. Rows written before a field existed still
    load via the dataclass default; see
    :func:`~validsim.store.memory.reconstruct_kwargs` for the rationale.
    """
    return Scorecard(**reconstruct_kwargs(Scorecard, data))


def _jsonb_load(blob: Any) -> Any:
    """Normalize a JSONB column value to its decoded Python object.

    psycopg 3 decodes ``jsonb`` to Python objects automatically, but a
    driver configured for raw output (or a test double) may hand back the
    JSON text; accept both. A SQL ``NULL`` (``None``) passes through
    unchanged so callers can apply their own fallback.
    """
    if isinstance(blob, str):
        return json.loads(blob)
    if isinstance(blob, (bytes, bytearray)):
        return json.loads(blob.decode("utf-8"))
    return blob


def _blob_to_dict(blob: Any) -> dict[str, Any]:
    """Normalize a JSONB *object* column to a dict (used for the scorecard)."""
    return dict(_jsonb_load(blob))  # type: ignore[arg-type]


def _episodes_to_json(episodes: list[EpisodeResult]) -> str:
    """Serialize raw episode results to a JSON array string."""
    return json.dumps([asdict(e) for e in episodes])


def _regression_to_json(report: RegressionReport | None) -> str | None:
    """Serialize a regression report's items (``None`` passes through)."""
    if report is None:
        return None
    return json.dumps([asdict(i) for i in report.items])


def card_params(run: StoredRun) -> tuple[Any, ...]:
    """The twelve column values for ``run``, in :data:`_INSERT_COLUMNS` order.

    Shared by :meth:`PostgresValidationStore.save` and
    :meth:`PostgresValidationStore.save_exists` so the two statements cannot
    drift apart in *what* they write -- only in whether they ask for a
    ``RETURNING`` row. Every value travels as a parameter; none is ever
    formatted into the SQL.
    """
    card = run.scorecard
    return (
        run.run_id,
        run.checkpoint_id,
        run.task_id,
        card.composite_score,
        card.deploy_decision,
        run.created_at,
        card.to_json(),
        run.baseline_run_id,
        json.dumps(run.evaluation.to_dict()),
        json.dumps(run.safety.to_dict()),
        _episodes_to_json(run.episodes),
        _regression_to_json(run.regression),
    )


#: Column list of the single INSERT every write path uses. Split out so
#: ``save`` and ``save_exists`` are provably writing the same twelve columns.
_INSERT_COLUMNS: tuple[str, ...] = (
    "run_id",
    "checkpoint_id",
    "task_id",
    "composite_score",
    "deploy_decision",
    "created_at",
    "scorecard",
    "baseline_run_id",
    "evaluation_json",
    "safety_json",
    "episodes_json",
    "regression_json",
)
#: JSONB columns need an explicit cast: psycopg sends the bound text as
#: ``unknown`` and PostgreSQL will not resolve it to ``jsonb`` implicitly.
_JSONB_CAST_COLUMNS: frozenset[str] = frozenset(
    {"scorecard", "evaluation_json", "safety_json", "episodes_json", "regression_json"}
)


def _insert_statement(table: str, returning: bool = False) -> str:
    """Build the append-only INSERT for the whitelisted ``table`` identifier.

    Kept separate from the callers so both write paths share one statement
    shape. The result is ``ON CONFLICT (run_id) DO NOTHING`` -- the contract
    declared on :class:`ValidationStore`; ``DO UPDATE`` here would silently
    reintroduce last-write-wins, and ``tests/test_store_postgres.py`` pins the
    clause against that. ``table`` is interpolated only after the
    :func:`_validate_table` whitelist check; every *value* travels as a
    ``%s`` parameter (see :func:`card_params`).

    Args:
        table: The already-whitelisted table identifier.
        returning: Append ``RETURNING run_id`` so the caller can tell an
            applied insert from a rejected duplicate in the same round trip.

    Returns:
        The SQL text, with ``%s`` markers in :data:`_INSERT_COLUMNS` order.
    """
    placeholders = ", ".join(
        "%s::jsonb" if name in _JSONB_CAST_COLUMNS else "%s" for name in _INSERT_COLUMNS
    )
    tail = " RETURNING run_id" if returning else ""
    return (
        f"INSERT INTO {table} ({', '.join(_INSERT_COLUMNS)})"
        f" VALUES ({placeholders})"
        " ON CONFLICT (run_id) DO NOTHING" + tail
    )


def _run_from_row(row: Any) -> StoredRun:
    """Reconstruct a :class:`StoredRun` from a persisted ``validations`` row.

    ``row[0]`` is the scorecard blob; the next elements are nullable and
    follow the :data:`_DETAIL_COLUMNS` order (``baseline_run_id``,
    ``evaluation_json``, ``safety_json``, ``episodes_json``,
    ``regression_json``), then the four indexed identity columns
    (``run_id``, ``checkpoint_id``, ``task_id``, ``created_at``). Full
    detail is restored exactly when present; legacy rows (or a shorter
    test double) fall back to an approximate evaluation/safety rebuilt
    from the scorecard, empty episodes and no regression — the same
    fallback SQLite applies to pre-detail rows.

    The trailing identity columns are what make a read self-consistent:
    a row shorter than ten elements has no columns to prefer, so the
    scorecard's own copy is kept. See
    :func:`~validsim.store.memory.rebind_row_identity`.
    """
    scorecard = _scorecard_from_dict(_blob_to_dict(row[0]))
    if len(row) > 9:
        scorecard = rebind_row_identity(
            scorecard,
            run_id=row[6],
            checkpoint_id=row[7],
            task_id=row[8],
            created_at=row[9],
        )
    baseline_run_id = row[1] if len(row) > 1 else None

    evaluation_data = _jsonb_load(row[2]) if len(row) > 2 else None
    safety_data = _jsonb_load(row[3]) if len(row) > 3 else None
    episodes_data = _jsonb_load(row[4]) if len(row) > 4 else None
    regression_data = _jsonb_load(row[5]) if len(row) > 5 else None

    evaluation = (
        EvaluationResult(**evaluation_data)  # type: ignore[arg-type]
        if evaluation_data is not None
        else None
    )
    safety = (
        SafetyResult(**safety_data)  # type: ignore[arg-type]
        if safety_data is not None
        else None
    )
    episodes = [EpisodeResult(**e) for e in (episodes_data or [])]
    regression = (
        RegressionReport(items=[RegressionItem(**i) for i in regression_data])
        if regression_data is not None
        else None
    )

    if evaluation is None:
        total = scorecard.episode_count
        evaluation = EvaluationResult(
            total_episodes=total,
            success_count=round(scorecard.success_rate * total),
            success_rate=scorecard.success_rate,
            failure_taxonomy=dict(scorecard.failure_taxonomy),
        )
    if safety is None:
        safety = SafetyResult(0.0, 0.0, None, 0.0, scorecard.safety_score)

    return StoredRun(
        run_id=scorecard.run_id,
        checkpoint_id=scorecard.checkpoint_id,
        task_id=scorecard.task_id,
        created_at=scorecard.created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        baseline_run_id=baseline_run_id,
        regression=regression,
    )


class PostgresValidationStore(ValidationStore):
    """PostgreSQL-backed store satisfying the :class:`ValidationStore` interface.

    Subclasses the in-memory store purely to advertise interface
    compatibility (and reuse :meth:`new_run_id`); every state-touching
    method is overridden to talk to PostgreSQL through a single
    autocommit connection guarded by a :class:`threading.Lock` (psycopg
    connections are not safe for concurrent use by multiple threads).

    Connection and schema setup are deferred to first use — see the
    module docstring for the design rationale.

    **Write semantics are append-only**, as the contract on
    :class:`ValidationStore` requires; this backend is the reference
    implementation of the ``ON CONFLICT (run_id) DO NOTHING`` clause that the
    SQLite backend now matches.
    """

    def __init__(
        self,
        dsn: str | None = None,
        table: str = "validations",
        *,
        _conn_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Configure the store without opening any connection.

        Args:
            dsn: libpq connection string; defaults to the
                ``VALIDSIM_PG_URL`` environment variable.
            table: Target table name; must be a plain lowercase
                identifier (``^[a-z_][a-z0-9_]*$``) or ``ValueError`` is
                raised (identifiers cannot be parameterized).
            _conn_factory: Test seam — a zero-argument callable returning
                a connection-like object. When omitted, the store opens a
                real ``psycopg.connect(dsn, autocommit=True)`` connection
                on first use.

        Raises:
            ValueError: On an invalid ``table``, or when no DSN is
                available (argument or env var) and the psycopg driver is
                installed. With the driver missing, construction still
                succeeds and the actionable missing-driver ``RuntimeError``
                surfaces at first use instead.
        """
        self._table = _validate_table(table)
        self._lock = threading.Lock()
        self._conn: Any | None = None
        self._conn_factory = _conn_factory

        resolved = (dsn or "").strip() or (os.environ.get(_PG_URL_ENV) or "").strip()
        if not resolved and _conn_factory is None:
            # Fail fast only when the driver is present; otherwise the
            # more actionable error (missing driver) surfaces at first use.
            try:
                _import_psycopg()
            except RuntimeError:
                pass
            else:
                raise ValueError(
                    "No PostgreSQL DSN configured: pass dsn= or set the "
                    f"{_PG_URL_ENV} environment variable."
                )
        self._dsn: str | None = resolved or None

    # -- introspection ----------------------------------------------------

    @property
    def dsn(self) -> str | None:
        """Resolved connection string (``None`` when a conn factory was given)."""
        return self._dsn

    @property
    def table(self) -> str:
        """Validated name of the backing table."""
        return self._table

    # -- connection lifecycle ----------------------------------------------

    def _default_connect(self) -> Any:
        """Open the real autocommit psycopg 3 connection (first use)."""
        psycopg = _import_psycopg()
        if not self._dsn:
            raise ValueError(
                "No PostgreSQL DSN configured: pass dsn= or set the "
                f"{_PG_URL_ENV} environment variable."
            )
        return psycopg.connect(self._dsn, autocommit=True)

    def _ensure_ready(self) -> Any:
        """Return a live connection, opening it and creating the schema on demand.

        Callers must hold ``self._lock``.
        """
        if self._conn is not None and not getattr(self._conn, "closed", False):
            return self._conn
        factory = self._conn_factory or self._default_connect
        conn = factory()
        try:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement.format(table=self._table))
            # Upgrade detail columns onto databases created before they
            # existed. Column names/types come from the fixed
            # :data:`_MIGRATION_COLUMNS` constants (never user input), so
            # they are safe to interpolate as identifiers.
            for column, sql_type in _MIGRATION_COLUMNS.items():
                conn.execute(
                    f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS "
                    f"{column} {sql_type}"
                )
        except Exception:
            conn.close()
            raise
        self._conn = conn
        return conn

    # -- ValidationStore interface -------------------------------------------

    def save(self, run: StoredRun) -> StoredRun:
        """Insert ``run``'s full-detail row; the first write for a run id wins.

        Uses ``INSERT ... ON CONFLICT (run_id) DO NOTHING``, the append-only
        verdict log required by the contract on :class:`ValidationStore` and
        matched byte-for-byte by the SQLite backend. (This backend was the only
        one that already behaved this way; the divergence was that the others
        did not.) Returns the ``run`` argument unchanged either way -- including
        after a rejected duplicate, where it is *not* what is stored. Use
        :meth:`save_exists` to detect that case.
        """
        with self._lock:
            self._ensure_ready().execute(
                _insert_statement(self._table), card_params(run)
            )
        return run

    def save_exists(self, run: StoredRun) -> bool:
        """Insert unless the id is taken; return whether the row was new.

        ``ON CONFLICT (run_id) DO NOTHING RETURNING run_id`` yields a row
        exactly when the insert happened, so the check rides on the same
        statement :meth:`save` issues -- no second round trip and no window in
        which another writer could claim the id between the two.

        Returns:
            ``True`` if ``run`` was recorded, ``False`` if the id was already
            taken and the stored record is unchanged.
        """
        with self._lock:
            row = self._ensure_ready().execute(
                _insert_statement(self._table, returning=True), card_params(run)
            ).fetchone()
        return row is not None

    def get(self, run_id: str) -> StoredRun | None:
        """Return the reconstructed run for ``run_id`` or ``None`` if unknown."""
        sql = f"SELECT {_DETAIL_SELECT} FROM {self._table} WHERE run_id = %s"
        with self._lock:
            row = self._ensure_ready().execute(sql, (run_id,)).fetchone()
        return _run_from_row(row) if row is not None else None

    def delete(self, run_id: str) -> bool:
        """Delete ``run_id``'s row; return ``True`` if a row was removed.

        Mirrors the in-memory/SQLite contract: an unknown id deletes nothing
        and returns ``False``. The id travels exclusively through a ``%s``
        placeholder (never interpolated); the table name is the already
        whitelisted identifier. ``rowcount`` reflects the affected rows.
        """
        sql = f"DELETE FROM {self._table} WHERE run_id = %s"
        with self._lock:
            cur = self._ensure_ready().execute(sql, (run_id,))
        return cur.rowcount > 0

    def list_for_checkpoint(self, checkpoint_id: str) -> list[StoredRun]:
        """All runs for a checkpoint, oldest first."""
        sql = (
            f"SELECT {_DETAIL_SELECT} FROM {self._table} "
            "WHERE checkpoint_id = %s ORDER BY created_at ASC"
        )
        with self._lock:
            rows = self._ensure_ready().execute(sql, (checkpoint_id,)).fetchall()
        return [_run_from_row(row) for row in rows]

    def history(
        self, since: str | None = None, until: str | None = None
    ) -> list[StoredRun]:
        """Every stored run, oldest first, optionally bounded by a date range.

        ``created_at`` is persisted as ISO-8601 TEXT, so PostgreSQL's lexical
        ``>=``/``<=`` comparison equals chronological comparison for these
        fixed-format UTC stamps; both bounds are inclusive. Each bound travels
        through a ``%s`` placeholder (never interpolated); the table name is
        the already-whitelisted identifier. With both ``None`` the statement
        is the original unfiltered history.
        """
        clauses: list[str] = []
        params: list[str] = []
        if since is not None:
            clauses.append("created_at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= %s")
            params.append(until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_DETAIL_SELECT} FROM {self._table}{where} "
            "ORDER BY created_at ASC"
        )
        with self._lock:
            rows = self._ensure_ready().execute(sql, tuple(params)).fetchall()
        return [_run_from_row(row) for row in rows]

    def count(self) -> int:
        """Number of persisted runs.

        Runs ``SELECT COUNT(*) FROM <table>``. The table name is the already
        whitelisted identifier (validated in :meth:`__init__`); the statement
        carries no user-supplied values, so there is nothing to interpolate
        and the query is injection-free by construction. Equivalent to
        :meth:`__len__`; exposed as a method so every backend offers the same
        ``count()`` surface for callers such as the CLI ``models`` footer.
        """
        sql = f"SELECT COUNT(*) FROM {self._table}"
        with self._lock:
            (count,) = self._ensure_ready().execute(sql).fetchone()
        return int(count)

    def __len__(self) -> int:
        """Number of persisted runs."""
        sql = f"SELECT COUNT(*) FROM {self._table}"
        with self._lock:
            (count,) = self._ensure_ready().execute(sql).fetchone()
        return int(count)

    def close(self) -> None:
        """Close the underlying connection (a later query reopens it)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
