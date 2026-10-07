"""Tests for optional date-range filtering on the stores' ``history()``.

Every backend persists ``created_at`` as ISO-8601 UTC text in a fixed
``+00:00``-offset format, so the ``since``/``until`` bounds are compared
lexicographically — which equals chronological order for these stamps, and
makes both bounds inclusive.

The in-memory and SQLite backends are exercised through *real* filtering.
The PostgreSQL backend is checked through an injected fake connection that
captures the generated SQL and bound parameters (no live server, the psycopg
driver is never imported): we assert the ``WHERE`` clauses use placeholders
and that the bound values are never interpolated into the statement.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore


def _scorecard(run_id: str, created_at: str) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )


def _run(run_id: str, created_at: str) -> StoredRun:
    sc = _scorecard(run_id, created_at)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9,
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


#: Three runs a day apart, ordered oldest -> newest.
_STAMPS: list[tuple[str, str]] = [
    ("vrun-00000001", "2026-01-01T00:00:00+00:00"),
    ("vrun-00000002", "2026-01-02T00:00:00+00:00"),
    ("vrun-00000003", "2026-01-03T00:00:00+00:00"),
]


def _ids(runs: list[StoredRun]) -> list[str]:
    return [r.run_id for r in runs]


# Shared expectation table for the two real-filtering backends:
# (history kwargs, expected run ids oldest-first).
_CASES: list[tuple[dict[str, str], list[str]]] = [
    ({}, ["vrun-00000001", "vrun-00000002", "vrun-00000003"]),
    ({"since": "2026-01-02T00:00:00+00:00"}, ["vrun-00000002", "vrun-00000003"]),
    ({"until": "2026-01-02T00:00:00+00:00"}, ["vrun-00000001", "vrun-00000002"]),
    (
        {"since": "2026-01-02T00:00:00+00:00", "until": "2026-01-02T00:00:00+00:00"},
        ["vrun-00000002"],
    ),
    # A mid-day lower bound excludes the first run's 00:00:00 stamp.
    ({"since": "2026-01-01T12:00:00+00:00"}, ["vrun-00000002", "vrun-00000003"]),
    # Bounds that match nothing yield an empty history.
    ({"since": "2026-02-01T00:00:00+00:00"}, []),
    ({"until": "2025-12-31T23:59:59+00:00"}, []),
]


class TestMemoryHistoryFilters:
    def _store(self) -> ValidationStore:
        store = ValidationStore()
        for run_id, ts in _STAMPS:
            store.save(_run(run_id, ts))
        return store

    @pytest.mark.parametrize("kwargs,expected", _CASES)
    def test_filters(
        self, kwargs: dict[str, str], expected: list[str]
    ) -> None:
        assert _ids(self._store().history(**kwargs)) == expected

    def test_default_matches_explicit_none_bounds(self) -> None:
        store = self._store()
        assert store.history() == store.history(None, None)
        assert _ids(store.history()) == [rid for rid, _ in _STAMPS]

    def test_bounds_inclusive_on_exact_match(self) -> None:
        store = self._store()
        exact = store.history(
            since="2026-01-01T00:00:00+00:00", until="2026-01-03T00:00:00+00:00"
        )
        assert _ids(exact) == [rid for rid, _ in _STAMPS]


class TestSqliteHistoryFilters:
    def _store(self, make_store: Callable[[str], SqliteValidationStore]) -> SqliteValidationStore:
        store = make_store()
        for run_id, ts in _STAMPS:
            store.save(_run(run_id, ts))
        return store

    @pytest.mark.parametrize("kwargs,expected", _CASES)
    def test_filters(
        self,
        make_store: Callable[[str], SqliteValidationStore],
        kwargs: dict[str, str],
        expected: list[str],
    ) -> None:
        store = self._store(make_store)
        assert _ids(store.history(**kwargs)) == expected

    def test_default_matches_full_history(
        self, make_store: Callable[[str], SqliteValidationStore]
    ) -> None:
        store = self._store(make_store)
        assert store.history() == store.history(None, None)
        assert _ids(store.history()) == [rid for rid, _ in _STAMPS]


# ---------------------------------------------------------------------------
# PostgreSQL: SQL construction asserted through an injected fake connection.
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal cursor exposing fetchone/fetchall over queued rows."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeConnection:
    """Stand-in for a psycopg 3 connection recording every executed query."""

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

    def history_query(self) -> tuple[str, tuple[Any, ...]]:
        """Return the captured ``history()`` SELECT (scorecard projection)."""
        for sql, params in self.calls:
            head = sql.strip().upper()
            if head.startswith("SELECT SCORECARD") and "ORDER BY CREATED_AT ASC" in head:
                return sql, params
        raise AssertionError(f"no history SELECT captured in {[s for s, _ in self.calls]}")


def _store_and_conn() -> tuple[PostgresValidationStore, _FakeConnection]:
    conn = _FakeConnection()
    store = PostgresValidationStore(_conn_factory=lambda: conn)
    return store, conn


class TestPostgresHistorySql:
    def test_no_filters_omits_where(self) -> None:
        store, conn = _store_and_conn()
        store.history()
        sql, params = conn.history_query()
        assert "WHERE" not in sql
        assert params == ()
        assert "ORDER BY created_at ASC" in sql

    def test_since_uses_placeholder_not_interpolation(self) -> None:
        store, conn = _store_and_conn()
        store.history(since="2026-01-02T00:00:00+00:00")
        sql, params = conn.history_query()
        assert "created_at >= %s" in sql
        assert "created_at <= %s" not in sql
        assert params == ("2026-01-02T00:00:00+00:00",)
        # The value must travel only through the placeholder, never the SQL.
        assert "2026-01-02" not in sql

    def test_until_uses_placeholder(self) -> None:
        store, conn = _store_and_conn()
        store.history(until="2026-01-02T00:00:00+00:00")
        sql, params = conn.history_query()
        assert "created_at <= %s" in sql
        assert "created_at >= %s" not in sql
        assert params == ("2026-01-02T00:00:00+00:00",)
        assert "2026-01-02" not in sql

    def test_both_bounds_combined_with_and(self) -> None:
        store, conn = _store_and_conn()
        store.history(
            since="2026-01-01T00:00:00+00:00", until="2026-01-03T00:00:00+00:00"
        )
        sql, params = conn.history_query()
        assert "WHERE created_at >= %s AND created_at <= %s" in sql
        assert params == (
            "2026-01-01T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
        )

    def test_filtered_history_restores_rows(self) -> None:
        """A non-empty result set is still reconstructed via _run_from_row."""
        sc = _scorecard("vrun-00000002", "2026-01-02T00:00:00+00:00")
        conn = _FakeConnection(select_rows=[(sc.to_dict(),)])
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        runs = store.history(since="2026-01-02T00:00:00+00:00")
        assert [r.run_id for r in runs] == ["vrun-00000002"]


# ---------------------------------------------------------------------------
# Backward compatibility: rows written before newer fields existed must load.
# ---------------------------------------------------------------------------
#
# The scorecard is a JSON blob, so adding a field is a *read*-side concern: the
# blob written by an older release simply has no key for the new field. The only
# correct upgrade path is a ``.get`` default at reconstruction time. A
# read-path filter (skip rows missing the key, or require the column) would
# protect the future by breaking the past — the exact failure this file pins.
#
# Both backends' `_scorecard_from_dict` are exercised, plus a real end-to-end
# SQLite read of a pre-adversarial row, so the guarantee is tested on the
# reconstruction *and* on the storage path it feeds.

#: A row exactly as an older release serialised it: no adversarial keys at all.
_LEGACY_SCORECARD: dict[str, object] = {
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

#: Fields added after that snapshot, with the defaults a legacy row must get.
_ADVERSARIAL_DEFAULTS: dict[str, object] = {
    "adversarial_episode_count": 0,
    "adversarial_success_rate": None,
    "block_reasons": (),
}


def _sqlite_scorecard_from_dict(data: dict[str, object]) -> Scorecard:
    from validsim.store.sqlite import _scorecard_from_dict as impl

    return impl(data)  # type: ignore[arg-type]


def _pg_scorecard_from_dict(data: dict[str, object]) -> Scorecard:
    from validsim.store.postgres import _scorecard_from_dict as impl

    return impl(data)  # type: ignore[arg-type]


#: Both backends' reconstructors, so the guarantee is asserted on each.
_RECONSTRUCTORS = [_sqlite_scorecard_from_dict, _pg_scorecard_from_dict]


@pytest.fixture
def legacy_detail_row() -> tuple[object, ...]:
    """The single-column row shape an older PostgreSQL release produced."""
    return (dict(_LEGACY_SCORECARD),)


class TestLegacyScorecardReconstruction:
    """Both backends must read a blob that predates the newer fields."""

    @pytest.mark.parametrize("reconstruct", _RECONSTRUCTORS, ids=["sqlite", "postgres"])
    def test_missing_newer_fields_fall_back_to_dataclass_defaults(
        self, reconstruct: Callable[[dict[str, object]], Scorecard]
    ) -> None:
        card = reconstruct(dict(_LEGACY_SCORECARD))
        for name, expected in _ADVERSARIAL_DEFAULTS.items():
            assert getattr(card, name) == expected, name
        # And the fields the legacy row *does* carry are still read exactly.
        assert card.run_id == "vrun-legacy01"
        assert card.composite_score == 90.0
        assert card.confidence_interval == (0.8, 0.95)
        assert isinstance(card.confidence_interval, tuple)

    @pytest.mark.parametrize("reconstruct", _RECONSTRUCTORS, ids=["sqlite", "postgres"])
    def test_null_newer_fields_are_not_confused_with_missing_ones(
        self, reconstruct: Callable[[dict[str, object]], Scorecard]
    ) -> None:
        """A *legitimate* stored null survives as itself, not as a default.

        This is the case that separates a correct legacy guard from a sloppy
        one. "Absent key -> dataclass default" is right for a row written before
        the field existed, but a guard written as
        ``data.get(k, default) or default`` also fires on a *present* null. The
        old code was written that way, so a stored ``null`` was silently
        rewritten into a plausible-looking value -- claiming a measurement the
        stored data does not contain. ``adversarial_success_rate``
        (``float | None``) is the honest instance: ``null`` is a real value for
        it, and it is preserved. The non-nullable fields are covered by
        ``test_falsy_stored_values_are_read_as_themselves`` and
        ``test_corrupt_null_on_a_non_nullable_field_is_not_silently_coerced``.
        """
        legacy = {
            **_LEGACY_SCORECARD,
            "adversarial_episode_count": 0,
            "adversarial_success_rate": None,
            "block_reasons": (),
        }
        card = reconstruct(legacy)
        # Nullable field: null is a real value and survives.
        assert card.adversarial_success_rate is None
        # Non-nullable fields: a falsy stored value reads as itself.
        assert card.adversarial_episode_count == 0
        assert card.block_reasons == ()

    @pytest.mark.parametrize("reconstruct", _RECONSTRUCTORS, ids=["sqlite", "postgres"])
    def test_falsy_stored_values_are_read_as_themselves(
        self, reconstruct: Callable[[dict[str, object]], Scorecard]
    ) -> None:
        """``0``, ``0.0``, ``()`` and ``{}`` must not read as "absent".

        This is the class of bug that separates a correct legacy guard from a
        sloppy one, and the variant that can actually occur in production: an
        empty ``failure_taxonomy`` or a zero ``randomization_group_count`` is a
        perfectly normal value for a successful run. A guard written as
        ``data.get(k, default) or default`` fires on a *present* falsy value as
        well as on an absent key -- and the old code was written exactly that
        way (``int(data.get("adversarial_episode_count", 0) or 0)``,
        ``tuple(data.get("block_reasons", ()) or ())``), so it coerced stored
        nulls and zero counts into whichever plausible value it preferred
        instead of reporting what was on disk.
        """
        falsy = {
            **_LEGACY_SCORECARD,
            "failure_taxonomy": {},
            "adversarial_episode_count": 0,
            "adversarial_success_rate": 0.0,
            "block_reasons": (),
            "regression_delta": 0.0,
            "randomization_group_count": 0,
        }
        card = reconstruct(falsy)
        assert card.failure_taxonomy == {}
        assert card.adversarial_episode_count == 0
        assert card.adversarial_success_rate == 0.0
        assert card.block_reasons == ()
        assert card.regression_delta == 0.0
        assert card.randomization_group_count == 0

    @pytest.mark.parametrize("reconstruct", _RECONSTRUCTORS, ids=["sqlite", "postgres"])
    def test_corrupt_null_on_a_non_nullable_field_is_not_silently_coerced(
        self, reconstruct: Callable[[dict[str, object]], Scorecard]
    ) -> None:
        """A ``null`` where the dataclass declares ``tuple`` is passed through.

        No shipped writer produces this -- ``to_dict`` always emits a real value
        for a non-nullable field -- so this is a robustness boundary rather than
        a compatibility case. What it pins is the *direction* of the failure. The
        old code coerced it (``tuple(data.get("block_reasons", ()) or ())``
        turned a stored ``null`` into ``()``), i.e. it invented a measurement the
        stored data did not contain and made corrupt data indistinguishable from
        a legitimately empty field. Reconstruction now maps stored values
        faithfully, so the ``None`` survives and the record is visibly corrupt
        to whatever consumes it. It is deliberately not coerced *and* not
        rejected: a dataclass performs no type checking, so silently repairing
        it here would be a second, different lie.
        """
        corrupt = {**_LEGACY_SCORECARD, "block_reasons": None}
        card = reconstruct(corrupt)
        assert card.block_reasons is None  # faithful, not smoothed into ()
        assert card.deploy_decision == "APPROVE"  # the rest of the row is intact


    def test_legacy_row_still_flows_through_a_real_sqlite_read(
        self, tmp_path: Any
    ) -> None:
        """End-to-end: a pre-adversarial row on disk loads through ``get``."""
        import json
        import sqlite3

        path = tmp_path / "legacy-blob.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE validations ("
            " run_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL,"
            " task_id TEXT NOT NULL, composite_score REAL NOT NULL,"
            " deploy_decision TEXT NOT NULL, created_at TEXT NOT NULL,"
            " scorecard_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO validations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "vrun-legacy01",
                "ckpt-1",
                "pick-place",
                90.0,
                "APPROVE",
                "2026-01-01T00:00:00+00:00",
                json.dumps(_LEGACY_SCORECARD),
            ),
        )
        conn.commit()
        conn.close()

        store = SqliteValidationStore(path)
        try:
            got = store.get("vrun-legacy01")
            assert got is not None
            assert got.scorecard.adversarial_episode_count == 0
            assert got.scorecard.block_reasons == ()
            assert got.scorecard.deploy_decision == "APPROVE"
            # The legacy row is still filterable/ordered like any other.
            assert [r.run_id for r in store.history(since="2025-01-01")] == [
                "vrun-legacy01"
            ]
        finally:
            store.close()

    def test_postgres_legacy_row_shape_reconstructs(
        self, legacy_detail_row: tuple[object, ...]
    ) -> None:
        """A pre-detail PostgreSQL row (scorecard only) still loads."""
        conn = _FakeConnection(select_rows=[legacy_detail_row])
        store = PostgresValidationStore(_conn_factory=lambda: conn)
        got = store.get("vrun-legacy01")
        assert got is not None
        assert got.scorecard.deploy_decision == "APPROVE"
        assert got.episodes == []
        assert got.regression is None
        assert got.baseline_run_id is None
        assert got.evaluation.total_episodes == 100  # documented approximation


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
