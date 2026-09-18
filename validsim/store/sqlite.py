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
is upgraded in place on open.
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
from validsim.store.memory import StoredRun, ValidationStore

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

    JSON has no tuple type, so ``confidence_interval`` is restored from the
    serialized list back to a ``(low, high)`` tuple to preserve equality with
    the originally stored object.
    """
    ci = data.get("confidence_interval")
    return Scorecard(
        run_id=data["run_id"],
        checkpoint_id=data["checkpoint_id"],
        task_id=data["task_id"],
        composite_score=data["composite_score"],
        success_rate=data["success_rate"],
        safety_score=data["safety_score"],
        robustness_score=data["robustness_score"],
        regression_delta=data["regression_delta"],
        confidence_interval=tuple(ci) if ci is not None else None,
        deploy_decision=data["deploy_decision"],
        threshold=data["threshold"],
        created_at=data["created_at"],
        episode_count=data["episode_count"],
        failure_taxonomy=dict(data.get("failure_taxonomy", {})),
    )


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
    empty episodes and no regression — matching the original MVP behaviour.
    """
    scorecard = _scorecard_from_dict(json.loads(row["scorecard_json"]))
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
    """

    def __init__(self, db_path: str | Path = "validsim.db") -> None:
        """Open (creating if needed) the database at ``db_path``."""
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """ALTER detail columns onto databases created before they existed."""
        existing = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(validations)").fetchall()
        }
        for column, sql_type in _MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE validations ADD COLUMN {column} {sql_type}"
                )

    @property
    def db_path(self) -> str:
        """Filesystem path of the backing database file."""
        return self._db_path

    def save(self, run: StoredRun) -> StoredRun:
        """Insert or replace ``run``'s row, returning the run unchanged."""
        card = run.scorecard
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO validations "
                "(run_id, checkpoint_id, task_id, composite_score, "
                " deploy_decision, created_at, scorecard_json, "
                " baseline_run_id, evaluation_json, safety_json, "
                " episodes_json, regression_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            self._conn.commit()
        return run

    def get(self, run_id: str) -> StoredRun | None:
        """Return the reconstructed run for ``run_id`` or ``None`` if unknown."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM validations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _run_from_row(row) if row is not None else None

    def list_for_checkpoint(self, checkpoint_id: str) -> list[StoredRun]:
        """All runs for a checkpoint, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM validations WHERE checkpoint_id = ? "
                "ORDER BY created_at ASC",
                (checkpoint_id,),
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def history(self) -> list[StoredRun]:
        """Every stored run, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM validations ORDER BY created_at ASC"
            ).fetchall()
        return [_run_from_row(r) for r in rows]

    def __len__(self) -> int:
        """Number of persisted runs."""
        with self._lock:
            (count,) = self._conn.execute(
                "SELECT COUNT(*) FROM validations"
            ).fetchone()
        return int(count)

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()
