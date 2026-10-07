"""Tests for the PostgreSQL validation store (Week-5 upgrade).

Unit tests are driver-absent-safe: they never import ``psycopg`` for real
nor contact a database. SQL construction is asserted through an injected
``_conn_factory`` fake, and the lazy-import/fail-fast paths are exercised
by monkeypatching :func:`validsim.store.postgres._import_psycopg` or by
blocking the import in a subprocess. A live-server integration class runs
only when ``VALIDSIM_PG_URL`` points at a reachable PostgreSQL instance.
"""

from __future__ import annotations

import builtins
import inspect
import json
import os
import subprocess
import sys
import uuid
from dataclasses import asdict
from typing import Any

import pytest

import validsim.store.postgres as pg
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store import ValidationStore, create_store
from validsim.store.memory import StoredRun
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARITY_TABLE = "validations"
_PARITY_CHECKPOINT = "ckpt-parity"
_PARITY_START = "2026-01-01T00:00:00+00:00"
_PARITY_MIDDLE = "2026-01-02T00:00:00+00:00"
_PARITY_END = "2026-01-03T00:00:00+00:00"


def _scorecard(run_id: str = "vrun-cafe1234", **overrides: object) -> Scorecard:
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "task_id": "pick-place",
        "composite_score": 90.0,
        "success_rate": 0.9,
        "safety_score": 80.0,
        "robustness_score": 100.0,
        "regression_delta": -0.1,
        "confidence_interval": (0.82, 0.95),
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "episode_count": 100,
        "failure_taxonomy": {"collision": 7, "timeout": 3},
    }
    base.update(overrides)
    return Scorecard(**base)  # type: ignore[arg-type]


def _run(sc: Scorecard) -> StoredRun:
    return StoredRun(
        run_id=sc.run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=sc.created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=sc.episode_count,
            success_count=90,
            success_rate=sc.success_rate,
            failure_taxonomy=dict(sc.failure_taxonomy),
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, sc.safety_score),
    )


def _full_run(sc: Scorecard) -> StoredRun:
    """A StoredRun exercising every persisted detail field."""
    episodes = [
        EpisodeResult(
            episode_id="pick-place-seed0000000042",
            task_id="pick-place",
            seed=42,
            success=True,
            collision_count=0,
            max_contact_force_n=12.5,
            min_human_distance_m=1.2,
            failure_mode=None,
            duration_s=8.25,
            joint_states_summary={"position_rms": 0.4, "dof": 7.0},
            randomization_level="full",
        ),
        EpisodeResult(
            episode_id="pick-place-seed0000000043",
            task_id="pick-place",
            seed=43,
            success=False,
            collision_count=2,
            max_contact_force_n=98.75,
            min_human_distance_m=None,
            failure_mode="collision",
            duration_s=15.5,
            joint_states_summary={},
            randomization_level="partial",
        ),
    ]
    return StoredRun(
        run_id=sc.run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=sc.created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=2,
            success_count=1,
            success_rate=0.5,
            per_task_success={"pick-place": 0.5},
            failure_taxonomy={"collision": 1},
            mean_duration_s=11.875,
        ),
        safety=SafetyResult(1.0, 0.5, 1.2, 0.0, 42.5),
        episodes=episodes,
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"),
                RegressionItem("mean_duration_s", 8.0, 11.875, 3.875, None, False, "info"),
            ]
        ),
    )


def _detail_row(run: StoredRun, *, raw: bool = False) -> tuple[Any, ...]:
    """Build the six-column row shape the store reads back (decoded or raw JSON text)."""

    def enc(obj: Any) -> Any:
        return json.dumps(obj) if raw else obj

    return (
        enc(run.scorecard.to_dict()),
        run.baseline_run_id,
        enc(run.evaluation.to_dict()),
        enc(run.safety.to_dict()),
        enc([asdict(e) for e in run.episodes]),
        enc([asdict(i) for i in run.regression.items])
        if run.regression is not None
        else None,
    )


class _FakeCursor:
    """Minimal cursor exposing fetchone/fetchall over queued rows."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Stand-in for a psycopg 3 connection recording every executed query.

    Mirrors the sqlite-style API the store uses: ``execute(sql, params?)``
    returning a cursor. SELECTs yield the queued ``select_rows``; all
    statements are captured in ``calls`` for assertions.
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

    # -- assertion helpers ------------------------------------------------

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    def find(self, prefix: str) -> tuple[str, tuple[Any, ...]]:
        want = prefix.upper()
        for sql, params in self.calls:
            if sql.strip().upper().startswith(want):
                return sql, params
        raise AssertionError(f"no statement starting with {prefix!r} in {self.statements()}")


def _block_psycopg_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import psycopg`` (any form) raise ImportError in-process."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestTableNameValidation:
    """Identifiers cannot be parameterized, so only whitelisted names pass."""

    @pytest.mark.parametrize("name", ["validations", "my_validations_2", "_private"])
    def test_valid_names_accepted(self, name: str) -> None:
        store = PostgresValidationStore(dsn="postgresql://unused/x", table=name)
        assert store.table == name

    @pytest.mark.parametrize(
        "bad",
        [
            "validations; DROP TABLE x--",  # classic injection attempt
            "Validations",  # uppercase not whitelisted
            "public.validations",  # no schema qualification
            "vali dations",
            "1leading",
            "",
        ],
    )
    def test_injection_and_invalid_names_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Invalid PostgreSQL table name"):
            PostgresValidationStore(dsn="postgresql://unused/x", table=bad)


class TestDsnResolution:
    def test_missing_dsn_fails_fast_when_driver_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VALIDSIM_PG_URL", raising=False)
        monkeypatch.setattr(pg, "_import_psycopg", lambda: object())  # driver "installed"
        with pytest.raises(ValueError, match="VALIDSIM_PG_URL"):
            PostgresValidationStore()

    def test_explicit_dsn_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_PG_URL", "postgresql://env/x")
        store = PostgresValidationStore("postgresql://explicit/y")
        assert store.dsn == "postgresql://explicit/y"

    def test_dsn_defaults_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_PG_URL", "postgresql://env/x")
        assert PostgresValidationStore().dsn == "postgresql://env/x"

    def test_no_connection_attempted_at_init(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Construction is pure config: connection + schema deferred to first use."""
        monkeypatch.setattr(pg, "_import_psycopg", lambda: object())
        store = PostgresValidationStore("postgresql://unreachable:9999/db")
        assert store._conn is None  # deferred setup — nothing opened yet


class TestDriverAbsence:
    """The module must be importable and constructible without psycopg."""

    def test_module_imports_without_driver(self) -> None:
        code = (
            "import sys; sys.modules['psycopg'] = None; "
            "import validsim.store.postgres as p; "
            "print('ok', p.PostgresValidationStore.__name__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok PostgresValidationStore" in result.stdout

    def test_import_psycopg_raises_helpful_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _block_psycopg_import(monkeypatch)
        with pytest.raises(RuntimeError, match=r"pip install 'psycopg\[binary\]'"):
            pg._import_psycopg()

    def test_missing_driver_surfaces_at_first_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        def boom() -> Any:
            raise RuntimeError("psycopg missing: pip install 'psycopg[binary]'")

        monkeypatch.setattr(pg, "_import_psycopg", boom)
        # dsn present → __init__ never touches the driver; store builds fine.
        store = PostgresValidationStore("postgresql://x/y")
        with pytest.raises(RuntimeError, match="pip install"):
            store.history()


class TestSqlConstruction:
    """SQL is asserted against an injected fake connection (no server)."""

    def _store(
        self, select_rows: list[Any] | None = None
    ) -> tuple[PostgresValidationStore, _FakeConnection]:
        conn = _FakeConnection(select_rows)
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        return store, conn

    def test_first_use_creates_schema_with_indexes(self) -> None:
        store, conn = self._store()
        store.history()
        stmts = conn.statements()
        assert stmts[0].strip().upper().startswith("CREATE TABLE IF NOT EXISTS")
        assert "validations" in stmts[0]
        assert any("checkpoint_id" in s and "CREATE INDEX IF NOT EXISTS" in s for s in stmts)
        assert any("created_at" in s and "CREATE INDEX IF NOT EXISTS" in s for s in stmts)

    def test_first_use_migrates_detail_columns(self) -> None:
        store, conn = self._store()
        store.history()  # triggers deferred schema + migration setup
        alters = [s for s in conn.statements() if s.strip().upper().startswith("ALTER TABLE")]
        assert len(alters) == 5
        for column in (
            "baseline_run_id",
            "evaluation_json",
            "safety_json",
            "episodes_json",
            "regression_json",
        ):
            assert any("ADD COLUMN IF NOT EXISTS" in a and column in a for a in alters), column

    def test_save_uses_placeholders_and_conflict_clause(self) -> None:
        store, conn = self._store()
        sc = _scorecard()
        store.save(_run(sc))
        sql, params = conn.find("INSERT")
        assert "ON CONFLICT (run_id) DO NOTHING" in sql
        assert sql.count("%s") == 12
        # Values travel only via params — never interpolated into the SQL.
        assert sc.run_id not in sql
        assert params[0] == sc.run_id
        assert params[1] == sc.checkpoint_id
        assert params[3] == sc.composite_score
        assert json.loads(params[6])["run_id"] == sc.run_id  # scorecard JSONB blob

    def test_save_does_not_use_a_last_write_wins_clause(self) -> None:
        """Append-only is a shared contract, not a Postgres-only accident.

        The divergence this pins (``DO NOTHING`` here vs ``INSERT OR REPLACE``
        in SQLite) shipped for a long time unnoticed because each backend's
        tests only asserted their own behaviour. ``DO UPDATE`` is the clause
        that would reintroduce the bug, so it is rejected explicitly.
        """
        source = inspect.getsource(PostgresValidationStore.save)
        assert "ON CONFLICT (run_id) DO NOTHING" in source
        assert "DO UPDATE" not in source
        assert "OR REPLACE" not in source

    def test_get_reconstructs_scorecard_from_decoded_jsonb(self) -> None:
        sc = _scorecard()
        store, conn = self._store(select_rows=[(sc.to_dict(),)])  # psycopg decodes JSONB to dict
        got = store.get(sc.run_id)
        assert got is not None
        assert got.scorecard == sc  # CI list restored to tuple, floats exact
        assert isinstance(got.scorecard.confidence_interval, tuple)
        sql, params = conn.find("SELECT")
        assert "WHERE run_id = %s" in sql and sc.run_id not in sql
        assert params == (sc.run_id,)

    def test_get_accepts_raw_json_text_blob(self) -> None:
        sc = _scorecard()
        store, _ = self._store(select_rows=[(sc.to_json(),)])  # raw-text driver output
        got = store.get(sc.run_id)
        assert got is not None and got.scorecard == sc

    def test_get_unknown_returns_none(self) -> None:
        store, _ = self._store(select_rows=[])
        assert store.get("vrun-deadbeef") is None

    def test_queries_filter_and_order_via_placeholders(self) -> None:
        sc = _scorecard()
        store, conn = self._store(select_rows=[(sc.to_dict(),), (sc.to_dict(),)])
        runs = store.list_for_checkpoint("ckpt-1")
        sql, params = conn.find("SELECT scorecard, baseline_run_id, evaluation_json")
        assert "WHERE checkpoint_id = %s" in sql and params == ("ckpt-1",)
        assert "ORDER BY created_at ASC" in sql
        assert [r.run_id for r in runs] == [sc.run_id, sc.run_id]

        store2, conn2 = self._store(select_rows=[(5,)])
        assert len(store2) == 5
        assert conn2.find("SELECT COUNT")[0] == "SELECT COUNT(*) FROM validations"


class TestFullDetailRoundTrip:
    """The Postgres backend restores every StoredRun field exactly."""

    def _store(
        self, select_rows: list[Any] | None = None
    ) -> tuple[PostgresValidationStore, _FakeConnection]:
        conn = _FakeConnection(select_rows)
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        return store, conn

    def test_save_persists_full_detail_columns(self) -> None:
        store, conn = self._store()
        run = _full_run(_scorecard())
        store.save(run)
        sql, params = conn.find("INSERT")
        assert sql.count("%s") == 12
        assert "evaluation_json" in sql
        assert "episodes_json" in sql
        assert "baseline_run_id" in sql
        # params 0-6 are the scorecard columns; 7-11 carry the full detail.
        assert params[7] == "vrun-base0001"
        assert json.loads(params[8]) == run.evaluation.to_dict()
        assert json.loads(params[9]) == run.safety.to_dict()
        assert json.loads(params[10]) == [asdict(e) for e in run.episodes]
        assert json.loads(params[11]) == [asdict(i) for i in run.regression.items]  # type: ignore[union-attr]

    def test_save_binds_user_values_and_preserves_first_write_clause(self) -> None:
        """Save keeps run fields out of SQL and pins the append-only contract."""
        store, conn = self._store()
        run = _full_run(_scorecard())
        store.save(run)
        sql, params = conn.find("INSERT")

        assert "ON CONFLICT (run_id) DO NOTHING" in sql
        assert params[0] == run.run_id
        assert params[1] == run.checkpoint_id
        assert params[2] == run.task_id
        assert params[4] == run.scorecard.deploy_decision
        assert params[5] == run.created_at
        assert params[6] == run.scorecard.to_json()
        for value in (
            run.run_id,
            run.checkpoint_id,
            run.task_id,
            run.scorecard.deploy_decision,
            run.created_at,
            run.baseline_run_id,
        ):
            if value is not None:
                assert value not in sql

    def test_close_releases_connection_and_reopens_on_next_query(self) -> None:
        """Closing releases the connection; a later query gets a fresh one."""
        connections = [
            _FakeConnection(select_rows=[(1,)]),
            _FakeConnection(select_rows=[(2,)]),
        ]
        opened: list[_FakeConnection] = []

        def factory() -> _FakeConnection:
            conn = connections.pop(0)
            opened.append(conn)
            return conn

        store = PostgresValidationStore(_conn_factory=factory)
        assert store.count() == 1
        first = opened[0]
        store.close()

        assert first.closed
        assert store._conn is None
        assert store.count() == 2
        assert len(opened) == 2
        assert opened[1] is not first
        store.close()

    def test_round_trip_preserves_entire_run(self) -> None:
        store, conn = self._store()
        run = _full_run(_scorecard())
        store.save(run)
        _sql, params = conn.find("INSERT")
        # Replay the captured write params as the database's stored row. The
        # JSON columns were sent as raw JSON text with a ::jsonb cast.
        conn._select_rows = [tuple(params[6:])]
        assert store.get(run.run_id) == run  # dataclass equality across all fields

    def test_get_restores_detail_from_decoded_jsonb(self) -> None:
        run = _full_run(_scorecard())
        store, _ = self._store(select_rows=[_detail_row(run)])
        got = store.get(run.run_id)
        assert got is not None
        assert got == run
        assert got.episodes == run.episodes
        assert got.evaluation == run.evaluation
        assert got.safety == run.safety
        assert got.baseline_run_id == "vrun-base0001"
        assert got.regression == run.regression

    def test_get_accepts_raw_json_text_for_detail_columns(self) -> None:
        run = _full_run(_scorecard())
        store, _ = self._store(select_rows=[_detail_row(run, raw=True)])
        got = store.get(run.run_id)
        assert got is not None and got == run

    def test_list_and_history_restore_full_detail(self) -> None:
        run = _full_run(_scorecard())
        store, _ = self._store(select_rows=[_detail_row(run), _detail_row(run)])
        assert store.list_for_checkpoint(run.checkpoint_id) == [run, run]
        assert store.history() == [run, run]

    def test_null_regression_round_trips_as_none(self) -> None:
        store, conn = self._store()
        run = _full_run(_scorecard())
        run_none = StoredRun(
            run_id=run.run_id,
            checkpoint_id=run.checkpoint_id,
            task_id=run.task_id,
            created_at=run.created_at,
            scorecard=run.scorecard,
            evaluation=run.evaluation,
            safety=run.safety,
            episodes=run.episodes,
            baseline_run_id=None,
            regression=None,
        )
        store.save(run_none)
        _sql, params = conn.find("INSERT")
        assert params[7] is None  # NULL baseline_run_id
        assert params[11] is None  # NULL regression_json
        conn._select_rows = [tuple(params[6:])]
        got = store.get(run_none.run_id)
        assert got is not None
        assert got.regression is None
        assert got.baseline_run_id is None
        assert got.evaluation == run_none.evaluation  # exact, not approximated
        assert got.episodes == run_none.episodes

    def test_legacy_single_column_row_falls_back(self) -> None:
        """A pre-detail row (scorecard only) is reconstructed via fallback."""
        sc = _scorecard()
        store, _ = self._store(select_rows=[(sc.to_dict(),)])
        got = store.get(sc.run_id)
        assert got is not None
        assert got.scorecard == sc
        assert got.episodes == []
        assert got.regression is None
        assert got.baseline_run_id is None
        assert got.evaluation.total_episodes == 100  # approximated fallback
        assert got.evaluation.success_count == 90


class TestFactorySelection:
    def test_postgres_backend_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_STORE", "Postgres")  # case-insensitive
        monkeypatch.setenv("VALIDSIM_PG_URL", "postgresql://factory/x")
        store = create_store()
        try:
            assert isinstance(store, PostgresValidationStore)
            assert isinstance(store, ValidationStore)
            assert store.dsn == "postgresql://factory/x"
            assert store._conn is None  # no connection attempt until needed
        finally:
            store.close()

    def test_default_remains_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
        store = create_store()
        try:
            assert type(store) is ValidationStore
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Integration: runs only against a reachable server (e.g. docker-compose pg).
# ---------------------------------------------------------------------------


def _cleanup(store: PostgresValidationStore, run_id: str) -> None:
    with store._lock:
        store._ensure_ready().execute(
            f"DELETE FROM {store.table} WHERE run_id = %s", (run_id,)
        )


def _postgres_is_configured() -> bool:
    """Return whether this environment explicitly supplied a PostgreSQL DSN."""
    return bool((os.environ.get("VALIDSIM_PG_URL") or "").strip())


def _parity_run(run_id: str, checkpoint_id: str, created_at: str) -> StoredRun:
    """Create one deliberately unsorted run for store parity checks."""
    card = _scorecard(
        run_id,
        checkpoint_id=checkpoint_id,
        composite_score=91.25,
        deploy_decision="APPROVE",
        created_at=created_at,
    )
    run = _full_run(card)
    return StoredRun(
        run_id=run.run_id,
        checkpoint_id=run.checkpoint_id,
        task_id=run.task_id,
        created_at=run.created_at,
        scorecard=run.scorecard,
        evaluation=run.evaluation,
        safety=run.safety,
        episodes=run.episodes,
        baseline_run_id=run.baseline_run_id,
        regression=run.regression,
    )


class TestSqliteParityOracle:
    """Verify the always-available oracle used by the live parity test."""

    def test_sqlite_sequence_supports_live_comparison(self) -> None:
        sqlite_store = SqliteValidationStore(":memory:")
        run_id = "vrun-a0000001"
        middle = _parity_run(run_id, _PARITY_CHECKPOINT, _PARITY_MIDDLE)
        end = _parity_run("vrun-a0000002", _PARITY_CHECKPOINT, _PARITY_END)
        start = _parity_run("vrun-b0000001", "ckpt-other", _PARITY_START)
        try:
            assert sqlite_store.get(run_id) is None
            assert sqlite_store.delete(run_id) is False
            for run in (middle, end, start):
                sqlite_store.save(run)

            expected = sqlite_store.get(run_id)
            assert expected == middle
            assert expected is not None
            assert expected.run_id == middle.run_id
            assert expected.checkpoint_id == middle.checkpoint_id
            assert expected.scorecard.composite_score == middle.scorecard.composite_score
            assert expected.scorecard.deploy_decision == middle.scorecard.deploy_decision
            assert expected.created_at == middle.created_at
            assert sqlite_store.count() == 3
            assert len(sqlite_store) == 3
            assert [run.run_id for run in sqlite_store.list_for_checkpoint(_PARITY_CHECKPOINT)] == [
                run_id,
                end.run_id,
            ]
            assert [run.run_id for run in sqlite_store.history()] == [
                start.run_id,
                run_id,
                end.run_id,
            ]
            assert [run.run_id for run in sqlite_store.history(since=_PARITY_MIDDLE)] == [
                run_id,
                end.run_id,
            ]
            assert sqlite_store.delete(run_id) is True
            assert sqlite_store.get(run_id) is None
            assert sqlite_store.count() == 2
            assert len(sqlite_store) == 2
            assert sqlite_store.delete(run_id) is False
        finally:
            sqlite_store.close()


@pytest.mark.skipif(not _postgres_is_configured(), reason="VALIDSIM_PG_URL is not set")
class TestPostgresIntegration:
    def test_configured_postgres_is_reachable(self) -> None:
        store = PostgresValidationStore(table="validations")
        try:
            assert isinstance(store.history(), list)
        finally:
            store.close()

    def test_save_get_round_trip(self) -> None:
        store = PostgresValidationStore(table="validations")
        run_id = f"vrun-{uuid.uuid4().hex[:8]}"
        run = _run(_scorecard(run_id))
        try:
            store.save(run)
            got = store.get(run_id)
            assert got is not None
            assert got.scorecard == run.scorecard
            assert got in store.list_for_checkpoint(run.checkpoint_id)
            assert len(store) >= 1
        finally:
            _cleanup(store, run_id)
            store.close()

    def test_reinsert_does_not_overwrite(self) -> None:
        store = PostgresValidationStore(table="validations")
        run_id = f"vrun-{uuid.uuid4().hex[:8]}"
        try:
            store.save(_run(_scorecard(run_id, composite_score=90.0)))
            store.save(_run(_scorecard(run_id, composite_score=1.0, deploy_decision="BLOCK")))
            got = store.get(run_id)
            assert got is not None
            assert got.scorecard.composite_score == 90.0  # first write wins
        finally:
            _cleanup(store, run_id)
            store.close()


@pytest.mark.skipif(not _postgres_is_configured(), reason="VALIDSIM_PG_URL is not set")
class TestSqlitePostgresParity:
    """Use SQLite's returned records as the behavioral oracle for PostgreSQL."""

    def test_postgres_routes_and_delete_match_sqlite(self) -> None:
        sqlite_store = SqliteValidationStore(":memory:")
        postgres_store = PostgresValidationStore(table=_PARITY_TABLE)
        run_id = f"vrun-{uuid.uuid4().hex[:8]}"
        middle = _parity_run(run_id, _PARITY_CHECKPOINT, _PARITY_MIDDLE)
        end = _parity_run(f"vrun-{uuid.uuid4().hex[:8]}", _PARITY_CHECKPOINT, _PARITY_END)
        start = _parity_run(f"vrun-{uuid.uuid4().hex[:8]}", "ckpt-other", _PARITY_START)
        postgres_ready = False
        try:
            for store in (sqlite_store, postgres_store):
                assert store.get(run_id) is None
                assert store.delete(run_id) is False
            postgres_ready = True
            sqlite_store.save(middle)
            postgres_store.save(middle)

            assert postgres_store.get(run_id) == sqlite_store.get(run_id)
            assert postgres_store.count() == sqlite_store.count() == 1
            assert len(postgres_store) == len(sqlite_store)

            for store in (sqlite_store, postgres_store):
                store.save(end)
                store.save(start)

            expected = sqlite_store.get(run_id)
            assert expected is not None
            actual = postgres_store.get(run_id)
            assert actual is not None
            assert actual.run_id == expected.run_id
            assert actual.checkpoint_id == expected.checkpoint_id
            assert actual.scorecard.composite_score == expected.scorecard.composite_score
            assert actual.scorecard.deploy_decision == expected.scorecard.deploy_decision
            assert actual.created_at == expected.created_at
            assert postgres_store.count() == sqlite_store.count() == 3
            assert len(postgres_store) == len(sqlite_store)

            expected_checkpoint = sqlite_store.list_for_checkpoint(_PARITY_CHECKPOINT)
            assert postgres_store.list_for_checkpoint(_PARITY_CHECKPOINT) == expected_checkpoint
            assert [run.run_id for run in expected_checkpoint] == [run_id, end.run_id]
            assert postgres_store.list_for_checkpoint("ckpt-missing") == (
                sqlite_store.list_for_checkpoint("ckpt-missing")
            )

            expected_history = sqlite_store.history()
            assert postgres_store.history() == expected_history
            assert [run.run_id for run in expected_history] == [start.run_id, run_id, end.run_id]
            assert postgres_store.history(
                since=_PARITY_MIDDLE, until=_PARITY_END
            ) == sqlite_store.history(since=_PARITY_MIDDLE, until=_PARITY_END)

            assert postgres_store.delete(run_id) == sqlite_store.delete(run_id) is True
            assert postgres_store.get(run_id) == sqlite_store.get(run_id) is None
            assert postgres_store.count() == sqlite_store.count() == 2
            assert len(postgres_store) == len(sqlite_store)
            assert postgres_store.list_for_checkpoint(_PARITY_CHECKPOINT) == (
                sqlite_store.list_for_checkpoint(_PARITY_CHECKPOINT)
            )
            assert postgres_store.history() == sqlite_store.history()
            assert postgres_store.delete(run_id) == sqlite_store.delete(run_id) is False
        finally:
            if postgres_ready:
                try:
                    for run in (start, middle, end):
                        if postgres_store.get(run.run_id) is not None:
                            _cleanup(postgres_store, run.run_id)
                finally:
                    postgres_store.close()
            sqlite_store.close()
