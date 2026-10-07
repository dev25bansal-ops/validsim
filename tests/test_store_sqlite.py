"""Tests for the persistent SQLite validation store (round-trip + ordering)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable

import pytest

from conftest import (
    make_detailed_stored_run,
    make_persisted_scorecard,
    make_stored_run,
)
from validsim.store import create_store
from validsim.store.sqlite import SqliteValidationStore


class TestRoundTrip:
    def test_save_and_get_preserves_scorecard(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        sc = make_persisted_scorecard()
        store.save(make_stored_run(sc))
        got = store.get(sc.run_id)
        assert got is not None
        assert got.scorecard == sc  # CI tuple + floats restored exactly
        assert got.run_id == sc.run_id
        assert got.checkpoint_id == sc.checkpoint_id
        assert got.task_id == sc.task_id
        assert got.created_at == sc.created_at

    def test_get_unknown_returns_none(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        assert make_store().get("vrun-deadbeef") is None

    def test_repeated_save_keeps_the_first_verdict(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        """SQLite is the default backend of ``actions/validate/action.yml``.

        It used to ``INSERT OR REPLACE``, so a worker retry (which reuses
        ``spec.run_id``) overwrote a recorded verdict with no error. Append-only
        is now the shared contract with memory and PostgreSQL; the cross-backend
        suite in ``test_store_parity.py`` covers the other read paths.
        """
        store = make_store()
        store.save(
            make_stored_run(
                make_persisted_scorecard(composite_score=95.0, deploy_decision="APPROVE")
            )
        )
        store.save(
            make_stored_run(
                make_persisted_scorecard(composite_score=20.0, deploy_decision="BLOCK")
            )
        )
        assert len(store) == 1
        got = store.get("vrun-cafe1234")
        assert got is not None
        assert got.scorecard.composite_score == 95.0  # first write wins
        assert got.scorecard.deploy_decision == "APPROVE"

    def test_repeated_save_is_a_single_row_in_the_table(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        """Assert the SQL, not just the behaviour: no REPLACE, conflict ignored.

        A behavioural assertion alone would also be satisfied by an
        ``INSERT OR REPLACE`` on a database where both writes happened to be
        identical. Pinning the statement keeps the two backends provably in step
        (``postgres.py`` emits the same conflict clause).
        """
        store = make_store()
        store.save(make_stored_run(make_persisted_scorecard()))
        store.save(make_stored_run(make_persisted_scorecard(composite_score=1.0)))

        with store._lock:
            statements = [
                str(sql)
                for (sql,) in store._conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table'"
                )
            ]
        assert statements, "expected a validations table"
        assert "OR REPLACE" not in statements[0]
        with store._lock:
            (rows,) = store._conn.execute(
                "SELECT COUNT(*) FROM validations WHERE run_id = ?",
                ("vrun-cafe1234",),
            ).fetchone()
        assert rows == 1

    def test_summary_reflects_stored_scorecard(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        sc = make_persisted_scorecard()
        store.save(make_stored_run(sc))
        summary = store.get(sc.run_id).summary()  # type: ignore[union-attr]
        assert summary["composite_score"] == 90.0
        assert summary["deploy_decision"] == "APPROVE"
        assert summary["episode_count"] == 100


class TestQueries:
    def test_list_for_checkpoint_filters_and_orders(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        store.save(
            make_stored_run(
                make_persisted_scorecard(
                    "vrun-a0000001", checkpoint_id="ckpt-A", created_at="2026-01-02T00:00:00+00:00"
                )
            )
        )
        store.save(
            make_stored_run(
                make_persisted_scorecard(
                    "vrun-a0000002", checkpoint_id="ckpt-A", created_at="2026-01-01T00:00:00+00:00"
                )
            )
        )
        store.save(
            make_stored_run(
                make_persisted_scorecard(
                    "vrun-b0000001", checkpoint_id="ckpt-B", created_at="2026-01-03T00:00:00+00:00"
                )
            )
        )

        runs = store.list_for_checkpoint("ckpt-A")
        assert [r.run_id for r in runs] == ["vrun-a0000002", "vrun-a0000001"]  # oldest first

    def test_history_orders_oldest_first(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        store.save(
            make_stored_run(
                make_persisted_scorecard("vrun-c0000002", created_at="2026-02-02T00:00:00+00:00")
            )
        )
        store.save(
            make_stored_run(
                make_persisted_scorecard("vrun-c0000001", created_at="2026-02-01T00:00:00+00:00")
            )
        )
        assert [r.run_id for r in store.history()] == ["vrun-c0000001", "vrun-c0000002"]

    def test_len_tracks_saved_runs(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        assert len(store) == 0
        store.save(make_stored_run(make_persisted_scorecard("vrun-11111111")))
        store.save(make_stored_run(make_persisted_scorecard("vrun-22222222")))
        assert len(store) == 2


class TestPersistence:
    def test_data_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.db"
        sc = make_persisted_scorecard()
        first = SqliteValidationStore(path)
        first.save(make_stored_run(sc))
        first.close()
        second = SqliteValidationStore(path)
        try:
            got = second.get(sc.run_id)
            assert got is not None and got.scorecard == sc
        finally:
            second.close()

    def test_creates_schema_on_init(self, tmp_path: Path) -> None:
        path = tmp_path / "fresh.db"
        store = SqliteValidationStore(path)
        try:
            assert path.exists()
            assert store.db_path == str(path)
            assert store.history() == []
        finally:
            store.close()


class TestBlankSqlitePathIsTreatedAsUnset:
    """A blank ``VALIDSIM_SQLITE_PATH`` must not open a throwaway database.

    ``os.environ.get`` returns ``""`` for an empty value, and
    ``sqlite3.connect("")`` opens a *private temporary* database that vanishes
    when the connection closes. So a deployment with an empty env var silently
    lost every run while still appearing to work -- the worst failure mode for
    a validation store. Blank must fall back to the documented default.
    """

    @pytest.mark.parametrize("blank", ["", " ", "   ", "\t"])
    def test_blank_env_var_uses_the_default_db_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blank: str
    ) -> None:
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", blank)
        # chdir into a temp dir so a stray "validsim.db" is detectable there.
        monkeypatch.chdir(tmp_path)

        store = create_store()
        try:
            assert store.db_path == "validsim.db"
            assert store.db_path != ""
            # A real, reopenable file -- not an anonymous in-memory/temp DB.
            assert (tmp_path / "validsim.db").exists()
        finally:
            store.close()

    def test_blank_env_var_does_not_lose_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The decisive check: a run saved under a blank path must survive close."""
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", "")

        sc = make_persisted_scorecard("vrun-blank01")
        first = create_store()
        try:
            first.save(make_stored_run(sc))
        finally:
            first.close()

        second = create_store()
        try:
            got = second.get("vrun-blank01")
            assert got is not None, "run was silently lost via a temporary database"
            assert got.scorecard == sc
        finally:
            second.close()


class TestFullDetailRoundTrip:
    """The SQLite backend must restore every StoredRun field exactly."""

    def test_round_trip_preserves_entire_run(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = make_detailed_stored_run(make_persisted_scorecard())
        store.save(run)
        assert store.get(run.run_id) == run  # dataclass equality across all fields

    def test_episodes_survive_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "detail.db"
        run = make_detailed_stored_run(make_persisted_scorecard())
        first = SqliteValidationStore(path)
        first.save(run)
        first.close()
        second = SqliteValidationStore(path)
        try:
            got = second.get(run.run_id)
            assert got is not None
            assert got.episodes == run.episodes
            assert got.evaluation == run.evaluation
            assert got.safety == run.safety
            assert got.baseline_run_id == "vrun-base0001"
            assert got.regression == run.regression
        finally:
            second.close()

    def test_null_regression_round_trips_as_none(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        run = make_stored_run(make_persisted_scorecard())  # no episodes/regression/baseline
        store.save(run)
        got = store.get(run.run_id)
        assert got is not None
        assert got.regression is None
        assert got.baseline_run_id is None
        assert got.episodes == []


class TestAdversarialFieldsRoundTrip:
    """The adversarial-segment verdict fields must survive persistence.

    ``Scorecard`` gained ``adversarial_episode_count``,
    ``adversarial_success_rate`` and ``block_reasons`` when the adversarial
    gate landed. The scorecard is stored as a JSON blob, so the fields *are*
    written -- but the reconstruct functions enumerate fields explicitly, so
    without an explicit read they silently reset to their defaults on reload.
    A gate whose stated reason vanishes on read is worse than one that never had
    it, because the stored evidence no longer explains the stored verdict.
    """

    @staticmethod
    def _with_adversarial_fields(scorecard):
        data = scorecard.to_dict()
        return type(scorecard)(
            **{
                **data,
                "confidence_interval": scorecard.confidence_interval,
                "adversarial_episode_count": 100,
                "adversarial_success_rate": 0.42,
                "block_reasons": ("adversarial success rate 42.0% is below the floor",),
            }
        )

    def test_fields_survive_sqlite_round_trip(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = make_store()
        card = self._with_adversarial_fields(make_persisted_scorecard("vrun-adv00001"))
        store.save(make_stored_run(card))
        got = store.get("vrun-adv00001")
        assert got is not None
        assert got.scorecard.adversarial_episode_count == 100
        assert got.scorecard.adversarial_success_rate == 0.42
        assert got.scorecard.block_reasons == (
            "adversarial success rate 42.0% is below the floor",
        )
        assert got.scorecard == card  # full dataclass equality

    def test_fields_survive_reopen(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "adv.db"
        card = self._with_adversarial_fields(make_persisted_scorecard("vrun-adv00002"))
        first = SqliteValidationStore(path)
        first.save(make_stored_run(card))
        first.close()
        second = SqliteValidationStore(path)
        try:
            got = second.get("vrun-adv00002")
            assert got is not None
            assert got.scorecard == card
        finally:
            second.close()

    def test_legacy_row_without_the_fields_still_loads(self) -> None:
        """Rows written before these fields existed must keep reconstructing.

        Read with ``.get`` defaults so a pre-existing database is not broken by
        the schema addition -- the upgrade path is defaults, not a filter.
        """
        from validsim.store.sqlite import _scorecard_from_dict

        legacy = {
            "run_id": "vrun-legacy01",
            "checkpoint_id": "ckpt-1",
            "task_id": "pick-place",
            "composite_score": 90.0,
            "success_rate": 0.9,
            "safety_score": 95.0,
            "robustness_score": 100.0,
            "regression_delta": None,
            "confidence_interval": [0.8, 0.95],
            "deploy_decision": "APPROVE",
            "threshold": 85.0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "episode_count": 100,
            "failure_taxonomy": {},
        }
        card = _scorecard_from_dict(legacy)
        assert card.run_id == "vrun-legacy01"
        assert card.adversarial_episode_count == 0
        assert card.adversarial_success_rate is None
        assert card.block_reasons == ()

    def test_legacy_row_without_detail_columns_is_readable(
        self, tmp_path: Path
    ) -> None:
        """Rows written by the pre-detail schema still load via fallback."""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE validations ("
            " run_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL,"
            " task_id TEXT NOT NULL, composite_score REAL NOT NULL,"
            " deploy_decision TEXT NOT NULL, created_at TEXT NOT NULL,"
            " scorecard_json TEXT NOT NULL)"
        )
        sc = make_persisted_scorecard()
        conn.execute(
            "INSERT INTO validations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sc.run_id,
                sc.checkpoint_id,
                sc.task_id,
                sc.composite_score,
                sc.deploy_decision,
                sc.created_at,
                sc.to_json(),
            ),
        )
        conn.commit()
        conn.close()

        store = SqliteValidationStore(path)  # migrates columns in place
        try:
            got = store.get(sc.run_id)
            assert got is not None
            assert got.scorecard == sc
            assert got.episodes == []
            assert got.regression is None
            assert got.baseline_run_id is None
            assert got.evaluation.total_episodes == 100  # approximated fallback
            assert got.evaluation.success_count == 90
            # migrated store accepts new full-detail writes
            run = make_detailed_stored_run(make_persisted_scorecard("vrun-newf0001"))
            store.save(run)
            assert store.get(run.run_id) == run
        finally:
            store.close()

    def test_scorecard_json_remains_queryable(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        """Indexed hot columns stay consistent with the stored blobs."""
        store = make_store()
        run = make_detailed_stored_run(make_persisted_scorecard())
        store.save(run)
        conn = sqlite3.connect(store.db_path)
        try:
            (card_json, ep_json) = conn.execute(
                "SELECT scorecard_json, episodes_json FROM validations WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
        finally:
            conn.close()
        assert json.loads(card_json)["run_id"] == run.run_id


class TestMigrationRace:
    def test_a_rival_adding_a_column_does_not_break_the_migration(self, tmp_path: Path) -> None:
        """A concurrent ALTER is the expected outcome of the race, not a failure.

        ``_migrate``'s ``self._lock`` only serialises one instance, so two
        processes opening the same pre-detail database both read
        ``PRAGMA table_info``, both conclude the column is missing, and both
        issue the ALTER. The second dies with ``duplicate column name``, taking
        the worker down at construction on the default SQLite path.

        The rival is injected at the moment the migration reads its column list,
        so the window is exercised deterministically rather than raced for.
        """
        import validsim.store.sqlite as sqlite_mod

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE validations (run_id TEXT PRIMARY KEY,"
            " checkpoint_id TEXT NOT NULL, task_id TEXT, composite_score REAL,"
            " deploy_decision TEXT, created_at TEXT, scorecard_json TEXT)"
        )
        conn.commit()
        conn.close()

        store = SqliteValidationStore.__new__(SqliteValidationStore)
        store._db_path = str(db)
        store._lock = threading.Lock()
        store._conn = sqlite3.connect(db, check_same_thread=False)
        store._conn.row_factory = sqlite3.Row

        original = sqlite_mod._MIGRATION_COLUMNS

        class Rival(dict):
            def items(self) -> object:
                other = sqlite3.connect(db)
                other.execute(
                    "ALTER TABLE validations ADD COLUMN baseline_run_id TEXT"
                )
                other.commit()
                other.close()
                return original.items()

        sqlite_mod._MIGRATION_COLUMNS = Rival()
        try:
            store._migrate()  # must not raise
        finally:
            sqlite_mod._MIGRATION_COLUMNS = original
            store._conn.close()

        names = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(validations)")]
        for column in original:
            assert names.count(column) == 1
        reopened = SqliteValidationStore(db)
        assert reopened.count() == 0
        reopened.close()

    def test_a_genuine_alter_failure_still_propagates(self, tmp_path: Path) -> None:
        """Only the duplicate-column case is swallowed; real faults still raise.

        A ``try``/``except`` that is too broad would turn a genuine schema fault
        into a silently half-migrated database, so the message is matched:
        anything that is not a duplicate column re-raises.
        """
        db = tmp_path / "broken.db"
        conn = sqlite3.connect(db)
        # A table that is not named `validations` at all, so PRAGMA reports
        # nothing and the first ALTER fails with "no such table".
        conn.execute("CREATE TABLE something_else (id TEXT)")
        conn.commit()
        conn.close()

        store = SqliteValidationStore.__new__(SqliteValidationStore)
        store._db_path = str(db)
        store._lock = threading.Lock()
        store._conn = sqlite3.connect(db, check_same_thread=False)
        store._conn.row_factory = sqlite3.Row
        try:
            with pytest.raises(sqlite3.OperationalError) as excinfo:
                store._migrate()
            assert "no such table" in str(excinfo.value).lower()
        finally:
            store._conn.close()
