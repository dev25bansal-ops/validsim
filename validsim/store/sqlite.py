"""Persistent, thread-safe validation store backed by stdlib :mod:`sqlite3`.

:class:`SqliteValidationStore` mirrors the public interface of the in-memory
:class:`~validsim.store.memory.ValidationStore` (save/get/list/history/len) so
callers can be swapped between backends without change. Each run is persisted
as a single row in the ``validations`` table: the composite :class:`Scorecard`
and the full run detail (evaluation, safety, raw episodes, baseline id and
regression report) are stored as JSON TEXT blobs, while a handful of query-hot
fields are promoted to indexed columns. This keeps every API endpoint —
including ``/failures`` and ``/regressions`` — behaviourally identical across
backends.

Rows written before the detail columns existed remain readable: missing blobs
fall back to an approximate reconstruction from the scorecard, and the schema
is upgraded in place on open. The same holds for the scorecard blob itself --
a field added to :class:`Scorecard` after a row was written is absent from that
row's JSON, and is restored from its dataclass default rather than filtered out
(see :func:`~validsim.store.memory.reconstruct_kwargs`).

Writes are **append-only**: the first row written for a ``run_id`` is the
record and a later write of the same id is rejected by the database itself. See
:class:`ValidationStore` for why a trustworthy evidence record must never be
silently rewritten, and why the sanctioned way to correct a stored run is
``delete`` followed by a fresh ``save``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence, Type, TypeVar

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

__all__ = ["SqliteValidationStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS validations (
    run_id           TEXT PRIMARY KEY,
    checkpoint_id    TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    composite_score  REAL NOT NULL,
    deploy_decision  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    scorecard_json   TEXT NOT NULL,
    baseline_run_id  TEXT,
    evaluation_json  TEXT,
    safety_json      TEXT,
    episodes_json    TEXT,
    regression_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_validations_checkpoint
    ON validations (checkpoint_id, created_at);
CREATE INDEX IF NOT EXISTS idx_validations_created
    ON validations (created_at);
"""

#: Detail columns added after the initial release; ALTERed onto legacy
#: databases at open time (names/types are fixed constants, never user input).
_MIGRATION_COLUMNS: dict[str, str] = {
    "baseline_run_id": "TEXT",
    "evaluation_json": "TEXT",
    "safety_json": "TEXT",
    "episodes_json": "TEXT",
    "regression_json": "TEXT",
}

_T = TypeVar("_T")


def _scorecard_from_dict(data: dict[str, Any]) -> Scorecard:
    """Rebuild a frozen :class:`Scorecard` from its ``to_dict`` mapping.

    Delegates to the shared :func:`~validsim.store.memory.reconstruct_kwargs`
    so this backend and PostgreSQL cannot drift, and so a field added to
    :class:`Scorecard` is covered without anyone editing this function: it used
    to enumerate fields by hand, and the three measurement-provenance fields
    (``robustness_measured``, ``randomization_group_count``,
    ``regression_baseline_available``) were written to the blob but silently
    reset to their defaults on reload -- a stored robustness score whose own
    "was this even measured?" flag came back claiming it was never measured.

    Rows written before a field existed still load: a missing key falls back to
    the field's default rather than being filtered out. See
    :func:`~validsim.store.memory.reconstruct_kwargs` for the full rationale.
    """
    return Scorecard(**reconstruct_kwargs(Scorecard, data))


def _loads(row: sqlite3.Row, key: str) -> Any:
    """Parse a JSON TEXT column, returning ``None`` when NULL/absent."""
    raw = row[key]
    return None if raw is None else json.loads(raw)


def _rebuild(row: sqlite3.Row, key: str, cls: Type[_T]) -> _T | None:
    """Reconstruct a dataclass from its JSON column via keyword fields."""
    data = _loads(row, key)
    return None if data is None else cls(**data)  # type: ignore[call-arg]


def _run_from_row(row: sqlite3.Row) -> StoredRun:
    """Reconstruct a :class:`StoredRun` from a persisted ``validations`` row.

    Full detail (episodes, evaluation, safety, regression, baseline id) is
    restored exactly when present. Legacy rows lacking the detail columns fall
    back to an approximate evaluation/safety rebuilt from the scorecard, with
    empty episodes and no regression — the documented legacy-row fallback.
    """
    scorecard = rebind_row_identity(
        _scorecard_from_dict(json.loads(row["scorecard_json"])),
        run_id=row["run_id"],
        checkpoint_id=row["checkpoint_id"],
        task_id=row["task_id"],
        created_at=row["created_at"],
    )
    episodes = [EpisodeResult(**e) for e in _loads(row, "episodes_json") or []]
    evaluation = _rebuild(row, "evaluation_json", EvaluationResult)
    if evaluation is None:
        total = scorecard.episode_count
        evaluation = EvaluationResult(
            total_episodes=total,
            success_count=round(scorecard.success_rate * total),
            success_rate=scorecard.success_rate,
            failure_taxonomy=dict(scorecard.failure_taxonomy),
        )
    safety = _rebuild(row, "safety_json", SafetyResult)
    if safety is None:
        safety = SafetyResult(0.0, 0.0, None, 0.0, scorecard.safety_score)
    regression_items = _loads(row, "regression_json")
    regression = (
        RegressionReport(items=[RegressionItem(**i) for i in regression_items])
        if regression_items is not None
        else None
    )
    return StoredRun(
        run_id=scorecard.run_id,
        checkpoint_id=scorecard.checkpoint_id,
        task_id=scorecard.task_id,
        created_at=scorecard.created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
        episodes=episodes,
        baseline_run_id=row["baseline_run_id"],
        regression=regression,
    )


def _episodes_to_json(episodes: Sequence[EpisodeResult]) -> str:
    """Serialize raw episode results to a JSON array string."""
    return json.dumps([asdict(e) for e in episodes])


def _regression_to_json(report: RegressionReport | None) -> str | None:
    """Serialize a regression report's items (``None`` passes through)."""
    if report is None:
        return None
    return json.dumps([asdict(i) for i in report.items])


class SqliteValidationStore(ValidationStore):
    """Disk-backed store satisfying the :class:`ValidationStore` interface.

    Subclasses the in-memory store purely to advertise interface compatibility
    (and reuse :meth:`new_run_id`); every state-touching method is overridden
    to read/write SQLite instead of the process dictionary.

    **Write semantics are append-only**, exactly as the base contract on
    :class:`ValidationStore` requires -- ``INSERT ... ON CONFLICT (run_id) DO
    NOTHING``, the same clause the PostgreSQL backend uses. This backend is the
    one ``actions/validate/action.yml`` selects by default, and it is where the
    first-write-wins / last-write-wins divergence was last observed: it used to
    ``INSERT OR REPLACE`` while PostgreSQL used ``DO NOTHING``, so the meaning
    of "the stored verdict" depended on which backend a deployment happened to
    configure. The base class docstring carries the full rationale and the
    delete-then-save escape hatch; ``tests/test_store_parity.py`` asserts the
    shared behaviour on every backend.
    """

    def __init__(self, db_path: str | Path = "validsim.db") -> None:
        """Open (creating if needed) the database at ``db_path``."""
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        with self._lock:
            self._ensure_ready()

    def _ensure_ready(self) -> sqlite3.Connection:
        """Return a live connection, opening and configuring it on demand.

        Callers must hold ``self._lock``. This mirrors
        :meth:`PostgresValidationStore._ensure_ready` and is what makes
        :meth:`close` non-terminal: an operation after ``close()`` transparently
        reopens instead of raising ``ProgrammingError: Cannot operate on a
        closed database`. :meth:`ValidationStore.close` documents that uniform
        contract ("present so callers can treat every ``create_store`` backend
        uniformly"), so a caller must be able to close a store in a ``finally``
        and keep using it -- which this backend alone used to forbid.

        **The whole open path lives here, not just ``sqlite3.connect``.** A
        reopened connection without ``row_factory`` would break every
        ``_run_from_row`` (which indexes rows by column name), and one without
        the schema/migration pass would fail on a legacy database whose detail
        columns were never ALTERed on. So the reopen re-runs exactly what
        ``__init__`` runs, in the same order: ``row_factory``, ``_SCHEMA``,
        ``_migrate``, ``commit``. This backend sets no ``PRAGMA``
        (``foreign_keys``/``journal_mode``) and opens no explicit transaction,
        so there is no other connection state to restore.
        """
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Adopted before _migrate() runs, because _migrate reads self._conn and
        # its zero-argument signature is what tests drive directly.
        self._conn = conn
        try:
            conn.executescript(_SCHEMA)
            self._migrate()
            conn.commit()
        except Exception:
            # Never leave a half-configured connection adopted: the next call
            # would take the fast path above and query a database whose schema
            # was never created.
            self._conn = None
            conn.close()
            raise
        return conn

    def _migrate(self) -> None:
        """ALTER detail columns onto databases created before they existed.

        ``self._lock`` only serialises this instance, so two processes opening the
        same pre-detail database both read ``PRAGMA table_info``, both conclude the
        column is missing, and both issue the ALTER -- the second dying with
        ``OperationalError: duplicate column name``. That is reachable on the
        default SQLite path with a cached database and parallel jobs, and it takes
        the worker down at construction rather than degrading anything.

        The read is therefore only an optimisation. Each ALTER is issued inside a
        ``try``, and a duplicate-column error is the expected outcome for the loser
        of that race, so it is swallowed; anything else still propagates. The
        PostgreSQL backend faces the same problem and solves it with
        ``ADD COLUMN IF NOT EXISTS``, which SQLite has no equivalent for.
        """
        existing = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(validations)").fetchall()
        }
        for column, sql_type in _MIGRATION_COLUMNS.items():
            if column not in existing:
                try:
                    self._conn.execute(
                        f"ALTER TABLE validations ADD COLUMN {column} {sql_type}"
                    )
                except sqlite3.OperationalError as exc:
                    # Another process added it between the PRAGMA above and here;
                    # "duplicate column name" is that race resolving in its favour.
                    if "duplicate column name" not in str(exc).lower():
                        raise

    @property
    def db_path(self) -> str:
        """Filesystem path of the backing database file."""
        return self._db_path

    def save(self, run: StoredRun) -> StoredRun:
        """Record ``run``'s row; a duplicate ``run_id`` is a no-op.

        Append-only, per the contract on :class:`ValidationStore`. The write is
        ``INSERT ... ON CONFLICT (run_id) DO NOTHING`` -- byte-for-byte the same
        conflict clause :mod:`validsim.store.postgres` emits. It used to be
        ``INSERT OR REPLACE``, which made this backend the *last-write-wins*
        outlier against PostgreSQL's first-write-wins, on the backend
        ``actions/validate/action.yml`` selects by default. Combined with
        ``jobs/worker.py`` reusing ``spec.run_id`` on every retry, a
        crash-and-retry silently rewrote a recorded 95.0/APPROVE verdict to
        20.0/BLOCK here, with no error and no log line. A duplicate now costs a
        rejected row instead of a rewritten verdict.

        ``DO NOTHING`` requires SQLite 3.24+ (2018); the module targets the
        stdlib :mod:`sqlite3` that ships with CPython 3.9+.

        Returns the ``run`` argument unchanged, exactly as the base contract
        specifies -- including after a rejected duplicate, where the returned
        object is *not* what is stored. Use :meth:`save_exists` to detect that.
        """
        card = run.scorecard
        with self._lock:
            conn = self._ensure_ready()
            conn.execute(
                "INSERT INTO validations "
                "(run_id, checkpoint_id, task_id, composite_score, "
                " deploy_decision, created_at, scorecard_json, "
                " baseline_run_id, evaluation_json, safety_json, "
                " episodes_json, regression_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id) DO NOTHING",
                (
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
                ),
            )
            conn.commit()
        return run

    def save_exists(self, run: StoredRun) -> bool:
        """Record ``run`` unless its id is taken; return whether it was new.

        Uses ``ON CONFLICT (run_id) DO NOTHING RETURNING run_id``, which yields
        a row exactly when the insert happened. The test is a plain ``fetchone``
        on the statement the base contract already requires, so no separate
        ``SELECT`` is issued and no TOCTOU window is introduced.

        Returns:
            ``True`` if ``run`` was recorded, ``False`` if the id was already
            taken and the stored record is unchanged.
        """
        card = run.scorecard
        with self._lock:
            conn = self._ensure_ready()
            row = conn.execute(
                "INSERT INTO validations "
                "(run_id, checkpoint_id, task_id, composite_score, "
                " deploy_decision, created_at, scorecard_json, "
                " baseline_run_id, evaluation_json, safety_json, "
                " episodes_json, regression_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id) DO NOTHING RETURNING run_id",
                (
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
                ),
            ).fetchone()
            conn.commit()
        return row is not None

    def get(self, run_id: str) -> StoredRun | None:
        """Return the reconstructed run for ``run_id`` or ``None`` if unknown."""
        with self._lock:
            row = self._ensure_ready().execute(
                "SELECT * FROM validations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def delete(self, run_id: str) -> bool:
        """Delete ``run_id``'s row; return ``True`` if a row was removed.

        Mirrors the in-memory store's contract: an unknown id deletes nothing
        and returns ``False``. The id travels through a ``?`` placeholder
        (never interpolated); ``sqlite3`` reports ``rowcount`` for ``DELETE``,
        so a no-match delete yields ``0``.
        """
        with self._lock:
            conn = self._ensure_ready()
            cur = conn.execute(
                "DELETE FROM validations WHERE run_id = ?", (run_id,)
            )
            conn.commit()
        return cur.rowcount > 0

    def list_for_checkpoint(self, checkpoint_id: str) -> list[StoredRun]:
        """All runs for a checkpoint, oldest first."""
        with self._lock:
            rows = self._ensure_ready().execute(
                "SELECT * FROM validations WHERE checkpoint_id = ? "
                "ORDER BY created_at ASC",
                (checkpoint_id,),
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def history(
        self, since: str | None = None, until: str | None = None
    ) -> list[StoredRun]:
        """Every stored run, oldest first, optionally bounded by a date range.

        ``created_at`` is persisted as ISO-8601 TEXT, so SQLite's lexical
        ``>=``/``<=`` comparison equals chronological comparison for these
        fixed-format UTC stamps; both bounds are inclusive. Each bound travels
        through a ``?`` placeholder (never interpolated into the SQL). With
        both ``None`` the statement is the original unfiltered history.
        """
        clauses: list[str] = []
        params: list[str] = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._ensure_ready().execute(
                f"SELECT * FROM validations{where} ORDER BY created_at ASC",
                params,
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def count(self) -> int:
        """Number of persisted runs.

        Runs ``SELECT COUNT(*) FROM validations`` — a fixed statement with no
        user-supplied values, so there is nothing to interpolate and the query
        is injection-free by construction (values would travel through ``?``
        placeholders, as elsewhere in this backend). Equivalent to
        :meth:`__len__`; exposed as a method so every backend offers the same
        ``count()`` surface for callers such as the CLI ``models`` footer.
        """
        with self._lock:
            (count,) = self._ensure_ready().execute(
                "SELECT COUNT(*) FROM validations"
            ).fetchone()
        return int(count)

    def __len__(self) -> int:
        """Number of persisted runs."""
        with self._lock:
            (count,) = self._ensure_ready().execute(
                "SELECT COUNT(*) FROM validations"
            ).fetchone()
        return int(count)

    def close(self) -> None:
        """Release the connection; a later operation transparently reopens it.

        Honours the same contract the other two backends already honour --
        :meth:`ValidationStore.close` documents that ``close`` exists "so
        callers can treat every ``create_store`` backend uniformly", and the
        PostgreSQL backend meets that by reopening on next use. ``close()`` is
        therefore **not terminal**: an operation issued after it transparently
        reopens (see :meth:`_ensure_ready`) rather than raising
        ``ProgrammingError: Cannot operate on a closed database``.

        Idempotent (a second ``close()`` is a no-op) and safe on a never-opened
        store, because it only releases what :meth:`_ensure_ready` opened.
        """
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
