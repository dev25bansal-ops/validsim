"""The ``close()`` contract must be identical on all three store backends.

:func:`validsim.store.memory.ValidationStore.close` documents the contract
explicitly: *"Release backend resources (no-op for the in-memory store). Present
so callers can treat every* ``create_store`` *backend uniformly."* That makes
"uniformly" a stated promise rather than an open design question, and
``SqliteValidationStore`` was the sole violator: it closed its connection
without any way back, so **every** later operation raised
``sqlite3.ProgrammingError: Cannot operate on a closed database`` while the
in-memory store answered normally and the PostgreSQL store transparently
reopened.

The practical consequence is that a caller could not write the obvious
``try: ... finally: store.close()`` and keep using the store afterwards. On two
backends that is fine; on SQLite it bricked the object permanently.

What is pinned here
-------------------
1. **Parity** -- the same operation sequence after ``close()`` produces the same
   answers on memory and SQLite (the two runnable backends).
2. **Transparent reopen** -- each operation works again after ``close()``, and
   the reopened connection is genuinely configured, not merely open. A reopen
   that skipped ``row_factory`` would raise ``IndexError``/``KeyError`` inside
   :func:`_run_from_row`; one that skipped the schema/migration pass would fail
   on a legacy database whose detail columns were never ``ALTER``ed on. Both
   are asserted directly.
3. **Idempotence** -- ``close()`` is safe to repeat, and safe on a store whose
   connection was never opened.

PostgreSQL
----------
There is no live PostgreSQL server in this environment (no reachable server, no
``psycopg`` driver), so nothing here claims execution coverage of that backend.
It is driven through its private ``_conn_factory`` -- the same injection point
``tests/test_store_postgres.py`` uses -- and every such case is labelled
structural. Structural verification is not execution verification.

Deliberately NOT asserted: that any backend holds an OS-level file handle
after ``close()``. On Windows a still-open handle makes ``tmp_path`` cleanup
raise ``PermissionError``, which is a filesystem-locking fact rather than a
contract, and asserting it would make this suite platform-dependent.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore

RUN_ID = "vrun-close0001"
CHECKPOINT = "ckpt-close"
CREATED_AT = "2026-05-01T12:00:00+00:00"


def _run(run_id: str = RUN_ID, created_at: str = CREATED_AT) -> StoredRun:
    """A minimal but fully-populated run (every stored field non-default)."""
    scorecard = Scorecard(
        run_id=run_id,
        checkpoint_id=CHECKPOINT,
        task_id="pick-place",
        composite_score=88.5,
        success_rate=0.9,
        safety_score=97.5,
        robustness_score=0.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=40,
        failure_taxonomy={"collision": 3},
        adversarial_episode_count=0,
        adversarial_success_rate=None,
        block_reasons=(),
    )
    evaluation = EvaluationResult(
        total_episodes=40,
        success_count=36,
        success_rate=0.9,
        per_task_success={"pick-place": 0.9},
        failure_taxonomy={"collision": 3},
        mean_duration_s=12.5,
    )
    safety = SafetyResult(
        collisions_per_episode=0.1,
        max_force_exceeded_rate=0.0,
        min_human_proximity_m=0.42,
        proximity_violation_rate=0.0,
        safety_score=97.5,
    )
    return StoredRun(
        run_id=run_id,
        checkpoint_id=CHECKPOINT,
        task_id="pick-place",
        created_at=created_at,
        scorecard=scorecard,
        evaluation=evaluation,
        safety=safety,
    )


@pytest.fixture
def sqlite_store(tmp_path: Path) -> Any:
    """A SQLite store on a temp file, seeded with one run, always closed."""
    store = SqliteValidationStore(tmp_path / "close.db")
    store.save(_run())
    try:
        yield store
    finally:
        # Closed here as well as by the tests: on Windows a live handle makes
        # tmp_path cleanup raise PermissionError, which would mask real results.
        store.close()


# ---------------------------------------------------------------------------
# 1. Parity: the documented uniform contract
# ---------------------------------------------------------------------------


def _probe_every_method(store: Any) -> list[tuple[str, str]]:
    """Call every public operation; return ``(name, ok:<type>|raise:<type>)``.

    The label deliberately records only success-vs-exception and the returned
    type, never a value: the two backends legitimately hold different data
    (``delete`` then ``save`` yields different counts), and the point of the
    comparison is *behavioural shape*, not equality of results.
    """
    out: list[tuple[str, str]] = []
    for label, call in (
        ("get", lambda: store.get(RUN_ID)),
        ("count", lambda: store.count()),
        ("len", lambda: len(store)),
        ("history", lambda: store.history()),
        ("list_for_checkpoint", lambda: store.list_for_checkpoint(CHECKPOINT)),
        ("delete", lambda: store.delete(RUN_ID)),
        ("save", lambda: store.save(_run("vrun-close0002"))),
        ("save_exists", lambda: store.save_exists(_run("vrun-close0003"))),
        ("close_again", lambda: store.close()),
    ):
        try:
            out.append((label, f"ok:{type(call()).__name__}"))
        except Exception as exc:  # noqa: BLE001 - the failure mode is the result
            out.append((label, f"raise:{type(exc).__name__}"))
    return out


class TestThreeBackendCloseParity:
    """The same operation sequence must behave alike on every backend."""

    def test_memory_and_sqlite_answer_alike_after_close(self, sqlite_store: Any) -> None:
        """The assertion the old divergence raised: no backend may raise here.

        Before the fix SQLite raised ``ProgrammingError: Cannot operate on a
        closed database`` for every method while memory answered normally.
        """
        mem = ValidationStore()
        mem.save(_run())
        mem.close()
        sqlite_store.close()

        assert _probe_every_method(mem) == _probe_every_method(sqlite_store)

    def test_postgres_is_structurally_equivalent(self) -> None:
        """PostgreSQL, driven through ``_conn_factory`` -- STRUCTURAL ONLY.

        No live server exists here, so this asserts the *shape* of the contract
        (a later query reopens and gets a new connection) rather than executing
        against PostgreSQL. It cannot and does not replace execution coverage.
        """
        opened: list[_FakeConnection] = []

        def factory() -> _FakeConnection:
            conn = _FakeConnection(select_rows=[(len(opened) + 1,)])
            opened.append(conn)
            return conn

        store = PostgresValidationStore(_conn_factory=factory)  # type: ignore[arg-type]
        try:
            assert store.count() == 1
            first = opened[0]
            store.close()
            assert first.closed
            assert store._conn is None

            # A later query reopens rather than raising.
            assert store.count() == 2
            assert len(opened) == 2
            assert opened[1] is not first
            # The reopened connection is re-initialised (schema + migration).
            joined = " ".join(opened[1].statements())
            assert "CREATE TABLE IF NOT EXISTS" in joined
        finally:
            store.close()

    def test_close_is_a_noop_on_memory(self) -> None:
        """The documented reference behaviour the other two must match."""
        mem = ValidationStore()
        mem.save(_run())
        mem.close()
        assert len(mem) == 1
        assert mem.get(RUN_ID) is not None
        mem.close()  # repeatable
        assert len(mem) == 1


# ---------------------------------------------------------------------------
# 2. Transparent reopen, with the connection setup actually re-established
# ---------------------------------------------------------------------------


class TestSqliteReopensAfterClose:
    def test_every_read_and_write_works_after_close(self, sqlite_store: Any) -> None:
        sqlite_store.close()

        assert sqlite_store.get(RUN_ID) is not None
        assert sqlite_store.count() == 1
        assert len(sqlite_store) == 1
        assert [r.run_id for r in sqlite_store.history()] == [RUN_ID]
        assert [r.run_id for r in sqlite_store.list_for_checkpoint(CHECKPOINT)] == [RUN_ID]

        sqlite_store.save(_run("vrun-close0004"))
        assert sqlite_store.count() == 2
        assert sqlite_store.delete(RUN_ID) is True
        assert sqlite_store.delete(RUN_ID) is False

    def test_reopened_connection_has_row_factory_restored(self, sqlite_store: Any) -> None:
        """A bare reconnect would break every row reconstruction.

        ``_run_from_row`` indexes rows by column *name*, so a reopened
        connection without ``sqlite3.Row`` would fail with ``IndexError`` on a
        perfectly good database. Asserting the round-trip *value* is what makes
        this a real check rather than a proxy for "the connection is open".
        """
        sqlite_store.close()
        assert sqlite_store._conn is None

        got = sqlite_store.get(RUN_ID)

        assert got is not None
        # These require name-based row access -> require row_factory.
        assert got.scorecard.composite_score == 88.5
        assert got.scorecard.failure_taxonomy == {"collision": 3}
        assert got.evaluation.total_episodes == 40
        assert got.evaluation.per_task_success == {"pick-place": 0.9}
        assert got.safety.safety_score == 97.5

    def test_reopen_reexecutes_schema_and_migration(self, tmp_path: Path) -> None:
        """Reopen must re-run the whole open path, not just ``sqlite3.connect``.

        A legacy database created before the detail columns existed is migrated
        by ``__init__``. If reopen skipped that, a store closed and reused on a
        legacy file would come back with a table missing every detail column --
        a corruption that looks fine until a ``SELECT *`` row is rebuilt.
        """
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE validations (run_id TEXT PRIMARY KEY,"
            " checkpoint_id TEXT NOT NULL, task_id TEXT, composite_score REAL,"
            " deploy_decision TEXT, created_at TEXT, scorecard_json TEXT)"
        )
        conn.commit()
        conn.close()

        store = SqliteValidationStore(db)
        store.close()  # drop the connection that __init__ opened
        store.count()  # reopen: must re-run _SCHEMA and _migrate

        probe = sqlite3.connect(db)
        try:
            names = [r[1] for r in probe.execute("PRAGMA table_info(validations)")]
        finally:
            probe.close()

        for column in (
            "baseline_run_id",
            "evaluation_json",
            "safety_json",
            "episodes_json",
            "regression_json",
        ):
            assert column in names, f"reopen did not re-migrate: {column} absent"
        store.close()

    def test_reopen_recreates_a_dropped_table(self, sqlite_store: Any) -> None:
        """``_SCHEMA`` is ``CREATE TABLE IF NOT EXISTS``; reopen must re-run it."""
        sqlite_store.close()
        raw = sqlite3.connect(sqlite_store.db_path)
        try:
            raw.execute("DROP TABLE validations")
            raw.commit()
        finally:
            raw.close()

        assert sqlite_store.count() == 0

    def test_repeated_close_is_idempotent(self, sqlite_store: Any) -> None:
        """``close(); close()`` must not raise -- memory and postgres allow it."""
        sqlite_store.close()
        sqlite_store.close()
        sqlite_store.close()
        assert len(sqlite_store) == 1

    def test_close_before_any_connection_exists(self, tmp_path: Path) -> None:
        """A never-opened store closes cleanly (no ``AttributeError``)."""
        store = SqliteValidationStore.__new__(SqliteValidationStore)
        store._db_path = str(tmp_path / "never.db")
        store._lock = threading.Lock()
        store._conn = None

        store.close()  # must not raise

        assert store._conn is None

    def test_failed_setup_leaves_no_connection_adopted(self, tmp_path: Path) -> None:
        """A reopen that fails mid-setup must not leave a broken connection.

        The connection is adopted before setup runs (so ``_migrate`` can use
        ``self._conn``). If setup then raises and the half-open connection were
        left adopted, every later call would take the "already ready" fast path
        and query a database whose schema was never created.
        """
        store = SqliteValidationStore.__new__(SqliteValidationStore)
        # Parent directories do not exist -> sqlite3.connect fails.
        store._db_path = str(tmp_path / "missing" / "nested" / "x.db")
        store._lock = threading.Lock()
        store._conn = None

        with pytest.raises(sqlite3.Error):
            store._ensure_ready()  # noqa: SLF001 - the reopen path under test

        assert store._conn is None

    def test_reopen_is_thread_safe(self, sqlite_store: Any) -> None:
        """Concurrent readers plus a concurrent ``close()`` must not error.

        Every method takes ``self._lock`` and so does the reopen, so the two
        serialise; the failure this guards against is a second thread adopting a
        connection while another is mid-setup.
        """
        sqlite_store.close()
        errors: list[str] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            try:
                barrier.wait(timeout=10)
                for _ in range(20):
                    assert sqlite_store.count() == 1
                    assert len(sqlite_store.history()) == 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors[:3]


# ---------------------------------------------------------------------------
# 3. The normal (never-closed) path must be untouched
# ---------------------------------------------------------------------------


class TestNormalPathUnchanged:
    def test_first_write_wins_is_preserved(self, tmp_path: Path) -> None:
        store = SqliteValidationStore(tmp_path / "append.db")
        try:
            store.save(_run())
            store.save(_run())
            assert store.count() == 1
            assert store.get(RUN_ID).scorecard.composite_score == 88.5  # type: ignore[union-attr]
        finally:
            store.close()

    def test_history_ordering_and_filters_survive_a_close_cycle(
        self, tmp_path: Path
    ) -> None:
        store = SqliteValidationStore(tmp_path / "hist.db")
        try:
            for i, stamp in enumerate(
                ["2026-01-03T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                 "2026-01-02T00:00:00+00:00"]
            ):
                store.save(_run(f"vrun-order{i:03d}", created_at=stamp))
            store.close()

            assert [r.run_id for r in store.history()] == [
                "vrun-order001",
                "vrun-order002",
                "vrun-order000",
            ]
            assert [
                r.run_id for r in store.history(since="2026-01-02T00:00:00+00:00")
            ] == ["vrun-order002", "vrun-order000"]
        finally:
            store.close()

    def test_data_is_durable_across_close_and_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "durable.db"
        first = SqliteValidationStore(db)
        first.save(_run())
        first.close()

        second = SqliteValidationStore(db)
        try:
            assert second.count() == 1
            assert second.get(RUN_ID) is not None
        finally:
            second.close()


# ---------------------------------------------------------------------------
# Minimal stand-in for a psycopg 3 connection (structural checks only)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Records executed SQL; satisfies the surface the store uses.

    ``closed`` mirrors psycopg 3's connection attribute, which is what
    ``PostgresValidationStore._ensure_ready`` inspects to decide whether to
    reopen.
    """

    def __init__(self, select_rows: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._select_rows = list(select_rows or [])
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _FakeCursor:
        if self.closed:
            raise RuntimeError("connection is closed")
        self.calls.append((sql, params or ()))
        if sql.strip().upper().startswith("SELECT COUNT"):
            return _FakeCursor(self._select_rows)
        return _FakeCursor([])

    def close(self) -> None:
        self.closed = True

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]