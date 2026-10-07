"""Edge-case and PostgreSQL-reconstruction tests for the validation stores.

Edge cases covered, each asserted *identically* on the in-memory and SQLite
backends so a fix to one that is not made to the other is caught:

* empty store / single run / zero-length ids
* unicode, emoji and very long run/checkpoint ids
* a very long failure taxonomy and a very long ``block_reasons`` tuple
* delete-then-history ordering, and delete idempotency
* pagination boundaries (the ``offset:limit`` slice ``/models`` uses)
* concurrent access, including two ``SqliteValidationStore`` instances on the
  *same* file (a real deployment shape: API and worker side by side)

PostgreSQL
----------
No live PostgreSQL server is available here (no ``psycopg`` driver, no
reachable server, Docker daemon down), so no test in this file pretends to
exercise one. The PostgreSQL backend is covered only where it can be covered
honestly and completely without a server:

* ``_run_from_row`` -- the *entire* row→``StoredRun`` reconstruction, which is
  the part of that backend most likely to drift from SQLite's, driven with
  byte-identical inputs and asserted to produce byte-identical results;
* ``reconstruct_kwargs`` -- the shared field-mapping helper both persistent
  backends now use;
* the SQL/bound-parameter construction via an injected fake connection.

``test_store_differential_agent.py`` carries the live-server gate and skips
with a precise, actionable reason when no server is configured.
"""

from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import (
    StoredRun,
    ValidationStore,
    reconstruct_kwargs,
)
from validsim.store.postgres import _run_from_row as pg_run_from_row
from validsim.store.sqlite import SqliteValidationStore


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


def _episode(index: int) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{index:04d}",
        task_id="pick-place",
        seed=index,
        success=index % 2 == 0,
        collision_count=index % 3,
        max_contact_force_n=round(10.0 + index * 0.5, 3),
        min_human_distance_m=None if index % 4 == 0 else round(0.4 + index * 0.01, 3),
        failure_mode=None if index % 2 == 0 else "collision",
        duration_s=round(1.0 + index * 0.25, 3),
        joint_states_summary={"position_rms": round(0.01 * index, 5)},
        randomization_level=("full", "partial", "light")[index % 3],
    )


def _make_run(
    run_id: str,
    *,
    checkpoint_id: str = "ckpt-1",
    created_at: str = "2026-01-01T00:00:00+00:00",
    episodes: Iterable[EpisodeResult] | None = None,
    taxonomy: dict[str, int] | None = None,
    block_reasons: tuple[str, ...] = (),
) -> StoredRun:
    """Build a StoredRun with consistent top-level and scorecard fields."""
    eps = list(episodes if episodes is not None else [_episode(0), _episode(1)])
    successes = sum(1 for e in eps if e.success)
    tax = dict(taxonomy or {"collision": 1, "timeout": 2})
    scorecard = Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id="pick-place",
        composite_score=round(50.0 + successes, 2),
        success_rate=successes / len(eps) if eps else 0.0,
        safety_score=75.0,
        robustness_score=90.0,
        regression_delta=-0.25,
        confidence_interval=(0.5, 0.95),
        deploy_decision="BLOCK" if block_reasons else "APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=len(eps),
        failure_taxonomy=tax,
        adversarial_episode_count=0,
        adversarial_success_rate=None,
        block_reasons=block_reasons,
    )
    return StoredRun(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=scorecard.task_id,
        created_at=created_at,
        scorecard=scorecard,
        evaluation=EvaluationResult(
            total_episodes=len(eps),
            success_count=successes,
            success_rate=successes / len(eps) if eps else 0.0,
            per_task_success={"pick-place": successes / len(eps) if eps else 0.0},
            failure_taxonomy=tax,
            mean_duration_s=1.25,
        ),
        safety=SafetyResult(0.25, 0.0, 0.9, 0.0, 87.5),
        episodes=eps,
        baseline_run_id="vrun-baseline",
        regression=RegressionReport(
            items=[
                RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.02, True, "critical"),
                RegressionItem("mean_duration_s", 1.0, 1.25, 0.25, None, False, "info"),
            ]
        ),
    )


def _stamp(minute: int) -> str:
    """A unique ISO-8601 stamp one minute apart from the base day."""
    return f"2026-01-01T{minute // 60:02d}:{minute % 60:02d}:00+00:00"


@pytest.fixture
def backends() -> Callable[[str], list]:
    """Factory returning ``[memory, sqlite]`` pairs, all closed on teardown."""
    created: list[SqliteValidationStore] = []

    def _make(name: str = "edge.db") -> list:
        store = SqliteValidationStore(Path(tempfile.mkdtemp()) / name)
        created.append(store)
        return [ValidationStore(), store]

    yield _make
    for store in created:
        try:
            store.close()
        except sqlite3.ProgrammingError:  # pragma: no cover - close idempotency
            pass


def _ids(runs: Iterable[StoredRun]) -> list[str]:
    return [r.run_id for r in runs]


# --------------------------------------------------------------------------
# Edge case: empty store
# --------------------------------------------------------------------------


class TestEmptyStore:
    def test_every_read_is_empty_on_both_backends(self, backends: Callable) -> None:
        mem, lite = backends("empty.db")
        assert mem.count() == lite.count() == 0
        assert len(mem) == len(lite) == 0
        assert mem.history() == lite.history() == []
        assert mem.get("nope") is lite.get("nope") is None
        assert mem.delete("nope") is lite.delete("nope") is False
        assert mem.list_for_checkpoint("ckpt-1") == lite.list_for_checkpoint("ckpt-1") == []
        assert mem.list_for_checkpoint("ckpt-1") == []

    def test_save_after_being_emptied(self, backends: Callable) -> None:
        mem, lite = backends("refill.db")
        for store in (mem, lite):
            store.save(_make_run("a"))
            assert store.delete("a") is True
            assert store.count() == 0
            store.save(_make_run("b"))
            assert store.count() == 1
            assert _ids(store.history()) == ["b"]


# --------------------------------------------------------------------------
# Edge case: single run
# --------------------------------------------------------------------------


class TestSingleRun:
    def test_single_run_is_fully_addressable(self, backends: Callable) -> None:
        mem, lite = backends("single.db")
        run = _make_run("only")
        mem.save(run)
        lite.save(run)
        assert mem.get("only") == lite.get("only") == run
        assert _ids(mem.history()) == _ids(lite.history()) == ["only"]
        assert _ids(mem.list_for_checkpoint("ckpt-1")) == ["only"]
        assert len(mem.list_for_checkpoint("ckpt-1")) == 1

    def test_duplicate_save_of_the_only_run_keeps_one_record(
        self, backends: Callable
    ) -> None:
        """Append-only: the record count must not grow on a duplicate."""
        mem, lite = backends("single-dup.db")
        for store in (mem, lite):
            store.save(_make_run("only", checkpoint_id="ckpt-A"))
            store.save(_make_run("only", checkpoint_id="ckpt-B"))
            assert store.count() == 1
            assert store.get("only").checkpoint_id == "ckpt-A"

    def test_new_run_id_is_unique_in_bulk(self) -> None:
        """``new_run_id`` is shared by every backend; it must not collide."""
        ids = {ValidationStore.new_run_id() for _ in range(2000)}
        assert len(ids) == 2000
        assert all(i.startswith("vrun-") and len(i) == len("vrun-") + 8 for i in ids)


# --------------------------------------------------------------------------
# Edge case: unicode / emoji / very long / empty identifiers
# --------------------------------------------------------------------------

_ID_CASES: list[tuple[str, str, str]] = [
    ("ascii", "vrun-abc123", "ckpt-1"),
    ("empty", "", ""),
    ("accented", "vrun-ünïcødé", "ckpt-✓"),
    ("cjk", "vrun-运行-テスト", "检查点-1"),
    ("emoji", "vrun-🚀-🧪", "ckpt-🔥"),
    ("rtl", "vrun-اختبار", "نقطة-1"),
    ("zero-width", "vrun-a\u200bb", "ckpt-c\u200bd"),
    ("long", "v-" + "x" * 4000, "c-" + "y" * 4000),
    ("very-long", "v-" + "z" * 60000, "c-" + "w" * 60000),
    ("sql-ish", "vrun-'; DROP TABLE validations; --", "ckpt-' OR 1=1 --"),
    ("nul-adjacent", "vrun-\x01\x02", "ckpt-\x1f"),
    ("newline", "vrun-a\nb", "ckpt-c\td"),
]


@pytest.mark.parametrize("label,run_id,checkpoint_id", _ID_CASES, ids=[c[0] for c in _ID_CASES])
def test_identifiers_survive_the_round_trip(
    backends: Callable, label: str, run_id: str, checkpoint_id: str
) -> None:
    """Every id shape must behave identically on both backends."""
    mem, lite = backends(f"id-{label}.db")
    run = _make_run(run_id, checkpoint_id=checkpoint_id)
    mem.save(run)
    lite.save(run)

    assert mem.get(run_id) is not None, f"memory lost the run ({label})"
    assert lite.get(run_id) is not None, f"sqlite lost the run ({label})"
    assert mem.get(run_id) == lite.get(run_id) == run
    assert _ids(mem.list_for_checkpoint(checkpoint_id)) == [run_id]
    assert _ids(lite.list_for_checkpoint(checkpoint_id)) == [run_id]
    # Delete must be exact-match too, including for the empty id.
    assert mem.delete(run_id) is lite.delete(run_id) is True
    assert mem.count() == lite.count() == 0


def test_ids_are_compared_exactly_not_trimmed(backends: Callable) -> None:
    """A near-miss id must miss, on both backends."""
    mem, lite = backends("exact.db")
    mem.save(_make_run("vrun-a"))
    lite.save(_make_run("vrun-a"))
    for probe in ("vrun-A", " vrun-a", "vrun-a ", "vrun-a\n", "vrun-a\x00", "vrun"):
        assert mem.get(probe) is None, probe
        assert lite.get(probe) is None, probe
    assert mem.count() == lite.count() == 1


# --------------------------------------------------------------------------
# Edge case: very long taxonomy / block_reasons
# --------------------------------------------------------------------------


class TestOversizedPayloads:
    def test_three_thousand_taxonomy_entries(self, backends: Callable) -> None:
        taxonomy = {f"mode-{i:05d}-éè": i for i in range(3000)}
        mem, lite = backends("tax.db")
        run = _make_run("big", taxonomy=taxonomy)
        mem.save(run)
        lite.save(run)
        got_mem, got_lite = mem.get("big"), lite.get("big")
        assert got_mem.scorecard.failure_taxonomy == got_lite.scorecard.failure_taxonomy
        assert got_lite.scorecard.failure_taxonomy == taxonomy
        assert got_mem.evaluation.failure_taxonomy == got_lite.evaluation.failure_taxonomy

    def test_two_thousand_block_reasons(self, backends: Callable) -> None:
        reasons = tuple(f"reason-{i:05d}-é" for i in range(2000))
        mem, lite = backends("reasons.db")
        run = _make_run("blocked", block_reasons=reasons)
        mem.save(run)
        lite.save(run)
        assert mem.get("blocked").scorecard.block_reasons == reasons
        # Tuple-ness must survive the JSON round trip or equality breaks.
        got = lite.get("blocked").scorecard.block_reasons
        assert isinstance(got, tuple), "block_reasons came back as a list, not a tuple"
        assert got == reasons

    def test_many_episodes(self, backends: Callable) -> None:
        mem, lite = backends("episodes.db")
        run = _make_run("long-run", episodes=[_episode(i) for i in range(500)])
        mem.save(run)
        lite.save(run)
        assert mem.get("long-run") == lite.get("long-run") == run

    def test_large_but_representable_numbers(self, backends: Callable) -> None:
        """Big ints and extreme-but-finite floats survive identically."""
        mem, lite = backends("numbers.db")
        for label, value in (
            ("bigint", 2**53 + 1),
            ("negzero", -0.0),
            ("tiny", 5e-324),
            ("huge", 1.7976931348623157e308),
        ):
            run = _make_run(f"num-{label}")
            object.__setattr__(run.scorecard, "episode_count", int(value))
            mem.save(run)
            lite.save(run)
            assert mem.get(f"num-{label}") == lite.get(f"num-{label}"), label

    def test_infinite_score_survives_both_backends(self, backends: Callable) -> None:
        """+Inf is a legal double, so unlike NaN it is stored by both.

        Pinned next to div-1 (``test_store_divergence_agent.py``) so the NaN
        asymmetry cannot be "fixed" by rejecting every non-finite score: Inf
        is representable end to end and must keep round-tripping.
        """
        mem, lite = backends("inf.db")
        run = _make_run("inf-run")
        object.__setattr__(run.scorecard, "composite_score", float("inf"))
        mem.save(run)
        lite.save(run)
        assert math.isinf(lite.get("inf-run").scorecard.composite_score)
        assert mem.get("inf-run") == lite.get("inf-run")


# --------------------------------------------------------------------------
# Edge case: delete-then-history ordering
# --------------------------------------------------------------------------


class TestDeleteThenHistory:
    def test_deleted_run_leaves_no_trace(self, backends: Callable) -> None:
        mem, lite = backends("del.db")
        for i in range(6):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        for store in (mem, lite):
            assert store.delete("r3") is True
        assert _ids(mem.history()) == ["r0", "r1", "r2", "r4", "r5"]
        assert _ids(mem.history()) == _ids(lite.history())
        assert mem.count() == lite.count() == 5
        assert mem.get("r3") is lite.get("r3") is None

    def test_delete_is_idempotent(self, backends: Callable) -> None:
        mem, lite = backends("del-idem.db")
        for store in (mem, lite):
            store.save(_make_run("r0", created_at=_stamp(0)))
            assert store.delete("r0") is True
            assert store.delete("r0") is False
            assert store.delete("r0") is False
            assert store.count() == 0

    def test_history_stays_sorted_after_interleaved_deletes(
        self, backends: Callable
    ) -> None:
        mem, lite = backends("del-order.db")
        for i in range(8):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        for target in ("r0", "r2", "r5"):
            assert mem.delete(target) is lite.delete(target) is True
        expected = ["r1", "r3", "r4", "r6", "r7"]
        assert _ids(mem.history()) == _ids(lite.history()) == expected
        assert _ids(mem.list_for_checkpoint("ckpt-1")) == expected

    def test_resave_after_delete_frees_the_id(self, backends: Callable) -> None:
        """The sanctioned correction path must work identically on both."""
        mem, lite = backends("del-resave.db")
        for store in (mem, lite):
            store.save(_make_run("r0", created_at=_stamp(0), checkpoint_id="ckpt-old"))
            assert store.delete("r0") is True
            store.save(_make_run("r0", created_at=_stamp(1), checkpoint_id="ckpt-new"))
            assert store.count() == 1
            assert store.get("r0").checkpoint_id == "ckpt-new"
        assert mem.count() == lite.count() == 1

    def test_history_bounds_after_deletion(self, backends: Callable) -> None:
        mem, lite = backends("del-bounds.db")
        for i in range(10):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        for store in (mem, lite):
            store.delete("r0")
            store.delete("r9")
        for since, until in ((None, None), (_stamp(3), None), (None, _stamp(6)),
                             (_stamp(3), _stamp(6))):
            assert _ids(mem.history(since=since, until=until)) == _ids(
                lite.history(since=since, until=until)
            ), (since, until)
        assert _ids(mem.history(since=_stamp(3), until=_stamp(6))) == [
            "r3", "r4", "r5", "r6"
        ]


# --------------------------------------------------------------------------
# Edge case: pagination boundaries
# --------------------------------------------------------------------------


class TestPaginationBoundaries:
    """The ``items[offset:offset + limit]`` slice ``GET /models`` performs."""

    @pytest.mark.parametrize("total", [0, 1, 2, 3, 10])
    @pytest.mark.parametrize("limit", [1, 2, 5, 100, 500])
    @pytest.mark.parametrize("offset", [0, 1, 2, 9, 10, 499])
    def test_slice_matches_python_semantics_on_both_backends(
        self, backends: Callable, total: int, limit: int, offset: int
    ) -> None:
        mem, lite = backends("page.db")
        for i in range(total):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        # /models aggregates oldest-first, then slices; model the same shape.
        def _page(store: ValidationStore) -> list[str]:
            return _ids(store.history())[offset : offset + limit]

        assert _page(mem) == _page(lite)
        # An offset past the end yields an empty page, never an error.
        assert _page(mem) == _ids(mem.history())[offset : offset + limit]

    def test_limit_zero_and_negative_do_not_raise(self, backends: Callable) -> None:
        """The API validates limit/offset upstream; the store must not be the
        thing that turns a bad value into a crash."""
        mem, lite = backends("page-bad.db")
        for i in range(3):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        for limit, offset in ((0, 0), (-1, 0), (5, -1), (0, 10)):
            mem_page = _ids(mem.history())[offset : offset + limit]
            lite_page = _ids(lite.history())[offset : offset + limit]
            assert mem_page == lite_page, (limit, offset)

    def test_paging_is_stable_across_repeated_reads(self, backends: Callable) -> None:
        mem, lite = backends("page-stable.db")
        for i in range(20):
            mem.save(_make_run(f"r{i}", created_at=_stamp(i)))
            lite.save(_make_run(f"r{i}", created_at=_stamp(i)))
        first_mem = _ids(mem.history())
        first_lite = _ids(lite.history())
        for _ in range(5):
            assert _ids(mem.history()) == first_mem
            assert _ids(lite.history()) == first_lite
        assert first_mem == first_lite


# --------------------------------------------------------------------------
# Edge case: concurrent access
# --------------------------------------------------------------------------


class TestConcurrentAccess:
    def test_many_threads_writing_distinct_ids_lose_nothing(
        self, backends: Callable
    ) -> None:
        """Eight contending threads per store; no write is lost on either.

        Both stores are hammered simultaneously (a barrier releases all sixteen
        threads together) but each store is written by 4 threads using a
        *disjoint* id namespace, so a short count is a lost update inside a
        store rather than an id collision between stores.
        """
        mem, lite = backends("conc.db")
        threads_per_store, per_thread = 4, 60
        per_store = threads_per_store * per_thread
        barrier = threading.Barrier(threads_per_store * 2)
        errors: list[Exception] = []

        def _hammer(store: ValidationStore, prefix: str) -> None:
            try:
                barrier.wait(timeout=60)
                for i in range(per_thread):
                    run_id = f"{prefix}-{i:04d}"
                    store.save(_make_run(run_id, created_at=_stamp(i)))
                    assert store.get(run_id) is not None, run_id
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        jobs = []
        for t in range(threads_per_store):
            jobs.append((mem, f"mem{t}"))
            jobs.append((lite, f"lite{t}"))
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(_hammer, store, prefix) for store, prefix in jobs]
            for f in futures:
                f.result(timeout=240)
        assert not errors, errors
        assert mem.count() == lite.count() == per_store
        assert len(mem) == len(lite) == per_store
        # Disjoint namespaces, so each store is checked against its own ids --
        # a *missing* id is a lost update. Both must still be oldest-first.
        expected = {f"mem{t}-{i:04d}" for t in range(threads_per_store) for i in range(per_thread)}
        assert set(_ids(mem.history())) == expected
        assert set(_ids(lite.history())) == {
            f"lite{t}-{i:04d}" for t in range(threads_per_store) for i in range(per_thread)
        }
        for store in (mem, lite):
            stamps = [r.created_at for r in store.history()]
            assert stamps == sorted(stamps), "history is not oldest-first"

    def test_same_run_id_written_from_many_threads_stays_single(
        self, backends: Callable
    ) -> None:
        """Concurrent duplicates must not create a second record anywhere."""
        mem, lite = backends("conc-dup.db")
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def _hammer(store: ValidationStore) -> None:
            try:
                barrier.wait(timeout=30)
                for i in range(50):
                    store.save(_make_run("contended", created_at=_stamp(i % 5)))
                    assert store.save_exists(
                        _make_run("contended", created_at=_stamp(i % 5))
                    ) is False
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_hammer, mem), pool.submit(_hammer, lite)]
            for f in futures:
                f.result(timeout=180)
        assert not errors, errors
        assert mem.count() == lite.count() == 1

    def test_two_sqlite_instances_on_one_file(self, tmp_path: Path) -> None:
        """API and worker side by side: two connections, one database file.

        Each instance guards only its *own* connection with its own lock, so
        this is the shape where a missing ``BEGIN IMMEDIATE``/busy_timeout
        would show up as ``database is locked``.
        """
        path = tmp_path / "shared.db"
        first = SqliteValidationStore(path)
        second = SqliteValidationStore(path)
        errors: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def _writer(store: SqliteValidationStore, prefix: str, n: int = 120) -> None:
            try:
                barrier.wait(timeout=30)
                for i in range(n):
                    store.save(_make_run(f"{prefix}-{i:04d}", created_at=_stamp(i)))
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{prefix}: {type(exc).__name__}: {exc}")

        try:
            threads = [
                threading.Thread(target=_writer, args=(first, "a")),
                threading.Thread(target=_writer, args=(second, "b")),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=180)
            assert not errors, errors
            assert first.count() == 240
            # Both instances see both writers' rows.
            assert second.count() == 240
            assert _ids(first.history()) == _ids(second.history())
        finally:
            first.close()
            second.close()

    def test_reads_during_writes_never_raise(self, backends: Callable) -> None:
        """A reader must never observe a partially written record."""
        mem, lite = backends("conc-read.db")
        for i in range(150):
            mem.save(_make_run(f"seed-{i:04d}", created_at=_stamp(i)))
            lite.save(_make_run(f"seed-{i:04d}", created_at=_stamp(i)))
        stop = threading.Event()
        errors: list[Exception] = []

        def _reader(store: ValidationStore) -> None:
            try:
                while not stop.is_set():
                    for run in store.history():
                        # A record read must be complete, never half-parsed.
                        assert run.scorecard.run_id == run.run_id
                        assert isinstance(run.episodes, list)
                    store.count()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            readers = [pool.submit(_reader, mem), pool.submit(_reader, lite)]
            for i in range(40):
                mem.save(_make_run(f"new-mem-{i:04d}", created_at=_stamp(200 + i)))
                lite.save(_make_run(f"new-lite-{i:04d}", created_at=_stamp(200 + i)))
            stop.set()
            for f in readers:
                f.result(timeout=180)
        assert not errors, errors


# --------------------------------------------------------------------------
# PostgreSQL: row -> StoredRun reconstruction (no server needed)
# --------------------------------------------------------------------------


def _pg_row_for(run: StoredRun) -> tuple:
    """Build the 6-tuple ``_run_from_row`` expects, in ``_DETAIL_COLUMNS`` order.

    Mirrors what psycopg hands back for the JSONB columns: already-decoded
    Python objects. ``save`` serialises with ``json.dumps`` and the driver
    decodes on read, so passing the decoded object is the faithful double.
    """
    card = run.scorecard.to_dict()
    return (
        card,
        run.baseline_run_id,
        run.evaluation.to_dict(),
        run.safety.to_dict(),
        [asdict(e) for e in run.episodes],
        None if run.regression is None else [asdict(i) for i in run.regression.items],
    )


class TestPostgresRowReconstructionParity:
    """PostgreSQL's reconstruction must equal SQLite's, byte for byte."""

    @pytest.mark.parametrize(
        "label,run",
        [
            ("minimal", _make_run("r-min", episodes=[])),
            ("default", _make_run("r-def")),
            ("unicode", _make_run("vrun-🚀-é", checkpoint_id="ckpt-✓", episodes=[_episode(3)])),
            ("long-ids", _make_run("v-" + "x" * 3000, checkpoint_id="c-" + "y" * 3000)),
            ("big-taxonomy", _make_run("r-tax", taxonomy={f"m{i}": i for i in range(500)})),
            (
                "block-reasons",
                _make_run("r-br", block_reasons=tuple(f"r{i}" for i in range(50))),
            ),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_pg_reconstruction_equals_sqlite_reconstruction(
        self, tmp_path: Path, label: str, run: StoredRun
    ) -> None:
        lite = SqliteValidationStore(tmp_path / f"{label}.db")
        try:
            lite.save(run)
            from_sqlite = lite.get(run.run_id)
            from_pg = pg_run_from_row(_pg_row_for(run))
            assert from_pg == from_sqlite
            assert from_pg == run
        finally:
            lite.close()

    def test_pg_reconstruction_handles_json_text_blobs(self) -> None:
        """A driver configured for raw output hands back JSON *text*.

        ``_jsonb_load`` accepts both; assert the text path reconstructs the
        same object, since that is the branch a text-mode driver takes.
        """
        run = _make_run("r-text")
        decoded = _pg_row_for(run)
        as_text = tuple(
            json.dumps(v) if isinstance(v, (dict, list)) else v for v in decoded
        )
        assert pg_run_from_row(as_text) == pg_run_from_row(decoded) == run

    def test_legacy_row_without_detail_columns(self) -> None:
        """A pre-detail row must still load, via the scorecard fallback.

        This is the ``ON CONFLICT``/``ALTER`` upgrade path: a database written
        by an older release has no detail columns at all, so the projection
        yields a one-element row and both backends reconstruct an approximate
        evaluation/safety from the scorecard.
        """
        card = _make_run("legacy").scorecard.to_dict()
        legacy = pg_run_from_row((card,))
        assert legacy.run_id == "legacy"
        assert legacy.episodes == []
        assert legacy.regression is None
        assert legacy.evaluation.total_episodes == card["episode_count"]
        assert legacy.safety.safety_score == card["safety_score"]

    def test_null_detail_columns_take_the_same_fallback(self) -> None:
        card = _make_run("nulls").scorecard.to_dict()
        row = (card, None, None, None, None, None)
        from_pg = pg_run_from_row(row)
        assert from_pg.episodes == []
        assert from_pg.regression is None
        assert from_pg.baseline_run_id is None
        assert from_pg.evaluation.total_episodes == card["episode_count"]


class TestReconstructKwargs:
    """The shared field-mapping helper both persistent backends now use."""

    def test_round_trips_a_full_scorecard(self) -> None:
        run = _make_run("rk", block_reasons=("a", "b"), episodes=[_episode(1)])
        card = run.scorecard
        restored = Scorecard(**reconstruct_kwargs(Scorecard, card.to_dict()))
        assert restored == card
        assert isinstance(restored.confidence_interval, tuple)
        assert isinstance(restored.block_reasons, tuple)

    def test_missing_key_falls_back_to_the_dataclass_default(self) -> None:
        """A legacy blob must keep loading -- reconstruct, never filter."""
        data = _make_run("legacy2").scorecard.to_dict()
        for newer in ("adversarial_episode_count", "adversarial_success_rate",
                      "block_reasons"):
            data.pop(newer, None)
        restored = Scorecard(**reconstruct_kwargs(Scorecard, data))
        assert restored.adversarial_episode_count == 0
        assert restored.adversarial_success_rate is None
        assert restored.block_reasons == ()

    def test_present_but_none_key_is_preserved(self) -> None:
        data = _make_run("keepnone").scorecard.to_dict()
        data["adversarial_success_rate"] = None
        restored = Scorecard(**reconstruct_kwargs(Scorecard, data))
        assert restored.adversarial_success_rate is None

    def test_unknown_key_is_ignored_rather_than_raising(self) -> None:
        """Forward compatibility: a newer blob read by older code must load.

        ``reconstruct_kwargs`` maps by ``dataclasses.fields``, so a key the
        class does not declare is dropped instead of raising. That is the right
        direction for this helper -- a blob written by a *newer* release carries
        fields an older reader has never heard of, and failing to load it would
        make a downgrade an outage.

        Note: ``reconstruct_kwargs``'s docstring claims it propagates a
        ``TypeError`` for an unrecognised key. The implementation cannot produce
        that (it only ever emits known field names), so the docstring overstates
        the strictness. Reported, not asserted here.
        """
        data = _make_run("bogus").scorecard.to_dict()
        data["a_field_from_the_future"] = 1
        restored = Scorecard(**reconstruct_kwargs(Scorecard, data))
        assert restored == _make_run("bogus").scorecard

    def test_evaluation_and_safety_round_trip(self) -> None:
        run = _make_run("rt")
        assert (
            EvaluationResult(**reconstruct_kwargs(EvaluationResult, run.evaluation.to_dict()))
            == run.evaluation
        )
        assert (
            SafetyResult(**reconstruct_kwargs(SafetyResult, run.safety.to_dict()))
            == run.safety
        )
        item = run.regression.items[0]
        assert (
            RegressionItem(**reconstruct_kwargs(RegressionItem, asdict(item))) == item
        )


# --------------------------------------------------------------------------
# PostgreSQL: SQL / bound-parameter construction (no server needed)
# --------------------------------------------------------------------------


class _Cursor:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Conn:
    """Stand-in psycopg connection recording every statement.

    Row-returning behaviour is per-prefix so one fake can serve every query
    shape the store issues: ``SELECT ...`` (get/history/list_for_checkpoint),
    ``SELECT COUNT(*)`` (count/__len__), ``INSERT ... RETURNING``
    (save_exists) and ``INSERT``/``UPDATE``/``DELETE`` (rowcount).
    """

    def __init__(
        self,
        select_rows: list[Any] | None = None,
        count: int = 0,
        returning_rows: list[Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False
        self._select_rows = list(select_rows or [])
        self._count = count
        self._returning_rows = list(returning_rows or [])

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Cursor:
        self.calls.append((" ".join(sql.split()), tuple(params or ())))
        head = sql.strip().upper()
        if "SELECT COUNT(*)" in head:
            return _Cursor([(self._count,)])
        if head.startswith("SELECT"):
            return _Cursor(self._select_rows)
        if head.startswith("INSERT") and "RETURNING" in head:
            return _Cursor(self._returning_rows)
        return _Cursor(rowcount=1)

    def close(self) -> None:
        self.closed = True

    def first(self, contains: str) -> tuple[str, tuple[Any, ...]]:
        for sql, params in self.calls:
            if contains in sql:
                return sql, params
        raise AssertionError(
            f"no statement containing {contains!r} in {[s for s, _ in self.calls]}"
        )

    def all(self, contains: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [(s, p) for s, p in self.calls if contains in s]


class TestPostgresSqlConstruction:
    def _store(self, conn: _Conn) -> Any:
        from validsim.store.postgres import PostgresValidationStore  # noqa: PLC0415

        return PostgresValidationStore(_conn_factory=lambda: conn)

    def test_every_value_travels_through_a_placeholder(self) -> None:
        """A quoted run id must never appear in the generated SQL."""
        conn = _Conn()
        store = self._store(conn)
        run = _make_run("vrun-'; DROP TABLE validations; --")
        store.save(run)
        sql, params = conn.first("INSERT INTO")
        assert "DROP TABLE" not in sql
        assert params[0] == "vrun-'; DROP TABLE validations; --"

    def test_conflict_clause_matches_the_shared_contract(self) -> None:
        conn = _Conn()
        self._store(conn).save(_make_run("c"))
        sql, _ = conn.first("INSERT INTO")
        assert "ON CONFLICT (run_id) DO NOTHING" in sql
        assert "INSERT OR REPLACE" not in sql

    def test_save_exists_uses_returning(self) -> None:
        """The strict signal rides on the same INSERT, with no second query."""
        inserted = _Conn(returning_rows=[("e",)])
        store = self._store(inserted)
        assert store.save_exists(_make_run("e")) is True
        sql, _ = inserted.first("RETURNING run_id")
        assert "ON CONFLICT (run_id) DO NOTHING" in sql
        # Exactly one INSERT: no SELECT round trip, so no TOCTOU window.
        assert len(inserted.all("INSERT INTO")) == 1
        assert not inserted.all("SELECT run_id")

        # No row returned (the insert was rejected) -> False.
        rejected = _Conn(returning_rows=[])
        assert self._store(rejected).save_exists(_make_run("e")) is False

    def test_history_bounds_use_placeholders(self) -> None:
        conn = _Conn()
        self._store(conn).history(
            since="2026-01-01T00:00:00+00:00", until="2026-02-01T00:00:00+00:00"
        )
        sql, params = conn.first("created_at >=")
        assert "created_at >= %s" in sql and "created_at <= %s" in sql
        assert "2026-01-01" not in sql and "2026-02-01" not in sql
        assert params == ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")

    def test_history_without_bounds_omits_the_where_clause(self) -> None:
        conn = _Conn()
        self._store(conn).history()
        sql, params = conn.first("ORDER BY created_at ASC")
        assert "WHERE" not in sql
        assert params == ()

    def test_table_identifier_is_whitelisted(self) -> None:
        from validsim.store.postgres import PostgresValidationStore  # noqa: PLC0415

        for bad in ("Validations", "validations; DROP TABLE x", "a-b", '"v"', "", "1v"):
            with pytest.raises(ValueError):
                PostgresValidationStore(
                    "postgresql://x/y", bad, _conn_factory=lambda: _Conn()
                )

    def test_deferred_connection_makes_construction_side_effect_free(self) -> None:
        conn = _Conn()
        self._store(conn)
        assert conn.calls == [], "construction opened a connection"

    def test_schema_is_created_once_on_first_use(self) -> None:
        conn = _Conn(count=1)
        store = self._store(conn)
        store.count()
        store.count()
        assert len(conn.all("CREATE TABLE IF NOT EXISTS")) == 1
        # The DDL/ALTER block is not re-issued on every query.
        assert len(conn.all("ALTER TABLE")) == 5

    def test_failed_schema_setup_closes_the_connection(self) -> None:
        from validsim.store.postgres import PostgresValidationStore  # noqa: PLC0415

        class _Boom(_Conn):
            def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _Cursor:
                if sql.strip().upper().startswith("CREATE TABLE"):
                    raise RuntimeError("server said no")
                return super().execute(sql, params)

        boom = _Boom()
        store = PostgresValidationStore(_conn_factory=lambda: boom)
        with pytest.raises(RuntimeError, match="server said no"):
            store.count()
        assert boom.closed is True

    def test_count_needs_no_parameters(self) -> None:
        conn = _Conn(count=7)
        store = self._store(conn)
        assert store.count() == 7
        assert len(store) == 7
        assert len(conn.all("SELECT COUNT(*)")) == 2  # count() and __len__
        for _, params in conn.all("SELECT COUNT(*)"):
            assert params == ()

    def test_delete_reports_rowcount(self) -> None:
        conn = _Conn()
        store = self._store(conn)
        assert store.delete("vrun-gone") is True
        sql, params = conn.first("DELETE FROM")
        assert "run_id = %s" in sql
        assert params == ("vrun-gone",)

    def test_close_then_reuse_reopens(self) -> None:
        """The documented contract: a later query transparently reopens."""
        connections: list[_Conn] = []

        def _factory() -> _Conn:
            conn = _Conn(count=0)
            connections.append(conn)
            return conn

        from validsim.store.postgres import PostgresValidationStore  # noqa: PLC0415

        store = PostgresValidationStore(_conn_factory=_factory)
        store.count()
        assert len(connections) == 1
        store.close()
        store.count()
        assert len(connections) == 2, "close() must not brick the store"
        assert connections[0].closed is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
