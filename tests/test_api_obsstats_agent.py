"""Correctness + benchmark tests for the ``store_stats`` observability projection.

Correctness is the point of this file: every fast-path value must equal the
value the existing ``history()``-based code produces, on all three backends.
The benchmark at the bottom then reports what the equality costs.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

os.environ.setdefault("VALIDSIM_RATE_LIMIT", "0")

from validsim.api.store_stats import (  # noqa: E402
    StoreTotals,
    latest_composite,
    run_summaries,
    store_totals,
    strategy_for,
)
from validsim.engine.evaluation import EvaluationResult  # noqa: E402
from validsim.engine.safety import SafetyResult  # noqa: E402
from validsim.engine.scorecard import Scorecard  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402
from validsim.store.memory import StoredRun, ValidationStore  # noqa: E402
from validsim.store.sqlite import SqliteValidationStore  # noqa: E402

APPROVE = "APPROVE"
BLOCK = "BLOCK"


def _stamp(i: int) -> str:
    """Distinct second-resolution ISO stamp for run ``i`` (history() sorts on it)."""
    s, m, h = i % 60, (i // 60) % 60, (i // 3600) % 24
    d = (i // 86400) % 28 + 1
    return f"2026-01-{d:02d}T{h:02d}:{m:02d}:{s:02d}+00:00"


def _run(i: int, *, decision: str, score: float, episodes: int = 3) -> StoredRun:
    """A stored run with distinct stamps, decisions and scores per index."""
    card = Scorecard(
        run_id=f"vrun-{i:08x}",
        checkpoint_id=f"ckpt-{i % 3}",
        task_id="pick-place-cube",
        composite_score=score,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=70.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,
        threshold=85.0,
        created_at=_stamp(i),
        episode_count=episodes,
        failure_taxonomy={"collision": 1} if decision == BLOCK else {},
    )
    return StoredRun(
        run_id=card.run_id,
        checkpoint_id=card.checkpoint_id,
        task_id=card.task_id,
        created_at=card.created_at,
        scorecard=card,
        evaluation=EvaluationResult(
            total_episodes=episodes, success_count=episodes - 1, success_rate=0.66
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
        episodes=[
            EpisodeResult(
                episode_id=f"ep-{i}-{j}",
                task_id="pick-place-cube",
                seed=i * 10 + j,
                success=(decision == APPROVE),
                collision_count=0,
                max_contact_force_n=1.0,
                min_human_distance_m=1.0,
                failure_mode=None if decision == APPROVE else "collision",
                duration_s=1.0,
                joint_states_summary={},
                randomization_level="none",
            )
            for j in range(episodes)
        ],
        baseline_run_id=None,
    )


def _mixed_runs(n: int) -> list[StoredRun]:
    """``n`` runs with a deterministic mix of decisions and scores."""
    return [
        _run(
            i,
            decision=APPROVE if i % 2 == 0 else BLOCK,
            score=60.0 + (i % 7) * 5.0,
            episodes=1 + (i % 4),
        )
        for i in range(n)
    ]


def _slow_totals(store: ValidationStore) -> StoreTotals:
    """The current ``/api/v1/dashboard/summary`` body, verbatim."""
    runs = store.history()
    total = len(runs)
    approvals = sum(1 for r in runs if r.scorecard.deploy_decision == APPROVE)
    composites = [r.scorecard.composite_score for r in runs]
    return StoreTotals(
        total_runs=total,
        approvals=approvals,
        blocks=total - approvals,
        avg_composite=round(sum(composites) / total, 2) if total else None,
    )


def _slow_latest(store: ValidationStore) -> float:
    """The current ``validsim_composite_score`` derivation, verbatim."""
    runs = store.history()
    return runs[-1].scorecard.composite_score if runs else 0.0


def _slow_summaries(store: ValidationStore, limit: int | None, offset: int) -> list[dict]:
    """The current list/dashboard-history body, verbatim."""
    runs = store.history()
    newest_first = list(reversed(runs))
    page = newest_first[offset:] if limit is None else newest_first[offset : offset + limit]
    return [r.summary() for r in page]


def _stores(tmp_path, n: int):
    """Yield ``(label, store)`` for memory and SQLite at size ``n``."""
    runs = _mixed_runs(n)
    mem = ValidationStore()
    for r in runs:
        mem.save(r)
    yield "memory", mem
    if n:
        sq = SqliteValidationStore(tmp_path / f"obs_{n}.db")
        for r in runs:
            sq.save(r)
        yield "sqlite", sq


# ---------------------------------------------------------------- correctness


class TestFastPathMatchesHistoryPath:
    """The only assertion that matters: equality with the code being replaced."""

    @pytest.mark.parametrize("n", [0, 1, 2, 5, 37])
    def test_totals_match(self, tmp_path, n: int) -> None:
        for label, store in _stores(tmp_path, n):
            assert store_totals(store) == _slow_totals(store), f"{label} n={n}"

    @pytest.mark.parametrize("n", [0, 1, 2, 5, 37])
    def test_latest_composite_matches(self, tmp_path, n: int) -> None:
        for label, store in _stores(tmp_path, n):
            assert latest_composite(store) == _slow_latest(store), f"{label} n={n}"

    @pytest.mark.parametrize("n", [1, 2, 5, 37])
    @pytest.mark.parametrize("limit,offset", [(None, 0), (1, 0), (2, 0), (5, 1), (100, 0), (3, 35)])
    def test_summaries_match(self, tmp_path, n: int, limit, offset) -> None:
        for label, store in _stores(tmp_path, n):
            assert run_summaries(store, limit=limit, offset=offset) == _slow_summaries(
                store, limit, offset
            ), f"{label} n={n} limit={limit} offset={offset}"

    def test_summaries_key_shape_is_identical(self, tmp_path) -> None:
        """The fast path must not add, drop or rename a summary key."""
        for label, store in _stores(tmp_path, 4):
            fast = run_summaries(store, limit=1)
            slow = _slow_summaries(store, 1, 0)
            assert set(fast[0]) == set(slow[0]), f"{label}: {set(fast[0]) ^ set(slow[0])}"

    def test_totals_as_dict_is_the_dashboard_body(self, tmp_path) -> None:
        for label, store in _stores(tmp_path, 9):
            body = store_totals(store).as_dict()
            assert set(body) == {"total_runs", "approvals", "blocks", "avg_composite"}, label
            assert body == json.loads(json.dumps(body)), label  # JSON round-trippable

    def test_empty_store_yields_none_avg(self) -> None:
        t = store_totals(ValidationStore())
        assert t.total_runs == 0 and t.approvals == 0 and t.blocks == 0
        assert t.avg_composite is None
        assert latest_composite(ValidationStore()) == 0.0
        assert run_summaries(ValidationStore()) == []


class _SqlRecorder:
    """Record the SQL a store actually issues, via sqlite3's trace callback.

    ``sqlite3.Connection.execute`` is a read-only C attribute and
    :class:`sqlite3.Connection` cannot be subclassed, so neither patching nor
    subclassing works. ``set_trace_callback`` is the documented public hook for
    observing executed statements; it fires for the statements the module under
    test actually issued, so the captures below are not inferred from reading
    the source.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[str] = []
        conn.set_trace_callback(self.statements.append)

    def stop(self) -> None:
        """Detach the trace callback."""
        self._conn.set_trace_callback(None)

    @property
    def joined(self) -> str:
        """All captured statements, concatenated for substring assertions."""
        return "\n".join(self.statements)


class TestStrategySelection:
    """The claimed strategy must be the one actually taken."""

    def test_memory_and_sqlite_pick_documented_paths(self, tmp_path) -> None:
        assert strategy_for(ValidationStore()) == "memory"
        store = SqliteValidationStore(tmp_path / "s.db")
        try:
            assert strategy_for(store) == "sql"
        finally:
            store.close()

    def test_sql_totals_reads_no_json_column(self, tmp_path) -> None:
        """The totals query must not touch scorecard/episodes blobs at all."""
        store = SqliteValidationStore(tmp_path / "t.db")
        try:
            for r in _mixed_runs(6):
                store.save(r)
            rec = _SqlRecorder(store._conn)
            try:
                store_totals(store)
            finally:
                rec.stop()
            assert rec.statements, "no query issued"
            assert "episodes_json" not in rec.joined, rec.joined
            assert "scorecard_json" not in rec.joined, rec.joined
        finally:
            store.close()

    def test_sql_summaries_bounded_by_page_size(self, tmp_path) -> None:
        """The list path must not read the whole table for one page."""
        store = SqliteValidationStore(tmp_path / "b.db")
        try:
            for r in _mixed_runs(200):
                store.save(r)
            rec = _SqlRecorder(store._conn)
            try:
                page = run_summaries(store, limit=5, offset=0)
            finally:
                rec.stop()
            sql = rec.statements[-1]
            assert "LIMIT" in sql and "OFFSET" in sql, sql
            assert "episodes_json" not in sql, sql
            assert len(page) == 5, len(page)
        finally:
            store.close()


class TestInputValidation:
    """Bad paging arguments raise instead of silently returning nonsense."""

    @pytest.mark.parametrize("kwargs", [{"limit": -1}, {"offset": -1}])
    def test_negative_paging_rejected(self, kwargs) -> None:
        with pytest.raises(ValueError):
            run_summaries(ValidationStore(), **kwargs)

    def test_null_lock_standin_is_used_for_lockless_stores(self) -> None:
        class _Bare:
            """A store with no ``_runs`` and no ``_lock`` at all."""

            def history(self):
                return []

        bare = _Bare()
        assert store_totals(bare).total_runs == 0
        assert strategy_for(bare) == "generic"


# ----------------------------------------------------------------- benchmark

BENCH_SIZES = (1_000, 10_000)


def _median_ms(fn, *, warmup: int, rounds: int) -> float:
    """Median wall-clock of ``fn`` in ms, after discarding ``warmup`` calls."""
    import statistics
    import time

    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


@pytest.mark.benchmark
@pytest.mark.parametrize("count", BENCH_SIZES)
def test_bench_totals_fast_vs_slow(tmp_path, count: int) -> None:
    """store_totals() vs the history() body, on both backends."""
    import logging

    logging.disable(logging.CRITICAL)
    for label, store in _stores(tmp_path, count):
        slow = _median_ms(lambda: _slow_totals(store), warmup=1, rounds=3)
        fast = _median_ms(lambda: store_totals(store), warmup=2, rounds=7)
        print(
            f"\n[bench] totals {label:<7} n={count:<7} slow(history) {slow:9.2f}ms "
            f"-> fast(projection) {fast:7.3f}ms  ({slow / fast:.0f}x)"
        )
        assert store_totals(store) == _slow_totals(store)


@pytest.mark.benchmark
@pytest.mark.parametrize("count", BENCH_SIZES)
def test_bench_summaries_page_fast_vs_slow(tmp_path, count: int) -> None:
    """A single 25-item page: the list endpoint's actual access pattern."""
    import logging

    logging.disable(logging.CRITICAL)
    for label, store in _stores(tmp_path, count):
        slow = _median_ms(lambda: _slow_summaries(store, 25, 0), warmup=1, rounds=3)
        fast = _median_ms(lambda: run_summaries(store, limit=25, offset=0), warmup=2, rounds=7)
        print(
            f"\n[bench] page25 {label:<7} n={count:<7} slow(history) {slow:9.2f}ms "
            f"-> fast(projection) {fast:7.3f}ms  ({slow / fast:.0f}x)"
        )
        assert run_summaries(store, limit=25, offset=0) == _slow_summaries(store, 25, 0)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
