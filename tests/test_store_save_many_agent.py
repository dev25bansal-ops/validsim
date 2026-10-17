"""``save_many`` must be observably identical to a ``save`` loop, only faster.

Why this exists
---------------
``ValidationStore.save`` commits -- and therefore fsyncs -- once per call. That
is correct for the request path (one run per validation) but pathological for
bulk loading: seeding 100 000 rows measured ~15 minutes against under three
seconds through a single transaction. Seeding is pure fixture setup for the
store-scale benchmarks, and an unbounded fixture cost is what stopped
``tests/bench/baseline.json`` from ever being generated -- a benchmark gate that
cannot run measures nothing.

``save_many`` therefore exists for the *cost*, not the semantics. Every test
here pins the semantics: same rows, same order, same append-only rule, same
round-trip, on every backend. A bulk path that quietly diverged from ``save``
would be a data-integrity bug wearing a performance hat, so the equivalence is
asserted rather than assumed.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.bench.fixtures import prototype_run
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

_COUNT = 25


def _runs(count: int = _COUNT) -> list[StoredRun]:
    """``count`` distinct, internally-consistent runs from one scored template.

    The nested ``scorecard.run_id`` is rebound alongside the row id because the
    stores normalise them on read (:func:`~validsim.store.memory.rebind_row_identity`).
    A fixture whose two ids disagreed would compare a normalised record against
    an un-normalised input and fail for a reason that has nothing to do with
    ``save_many``.
    """
    proto = prototype_run(1)
    out: list[StoredRun] = []
    for i in range(count):
        run_id = f"vrun-{i:08x}"
        out.append(
            StoredRun(
                **{
                    **proto.__dict__,
                    "run_id": run_id,
                    "scorecard": replace(proto.scorecard, run_id=run_id),
                }
            )
        )
    return out


@pytest.fixture()
def sqlite_store(tmp_path: Path) -> SqliteValidationStore:
    store = SqliteValidationStore(tmp_path / "save_many.db")
    try:
        yield store
    finally:
        store.close()


class TestEquivalenceWithSaveLoop:
    """The bulk path must land exactly what the loop would."""

    def test_memory_matches_a_save_loop(self) -> None:
        runs = _runs()
        bulk = ValidationStore()
        loop = ValidationStore()
        bulk.save_many(runs)
        for run in runs:
            loop.save(run)
        assert bulk.count() == loop.count() == _COUNT
        assert [bulk.get(r.run_id) for r in runs] == [loop.get(r.run_id) for r in runs]

    def test_sqlite_matches_a_save_loop(self, sqlite_store: SqliteValidationStore) -> None:
        runs = _runs()
        sqlite_store.save_many(runs)
        assert sqlite_store.count() == _COUNT
        # Round-trip equality, not merely a count: the JSON blobs must decode
        # back to the very objects that went in.
        assert [sqlite_store.get(r.run_id) for r in runs] == runs

    def test_history_order_matches_insertion_order(
        self, sqlite_store: SqliteValidationStore
    ) -> None:
        runs = _runs()
        sqlite_store.save_many(runs)
        assert [r.run_id for r in sqlite_store.history()] == [r.run_id for r in runs]


class TestAppendOnlyRuleIsPreserved:
    """``ON CONFLICT DO NOTHING`` semantics must survive batching."""

    def test_a_duplicate_id_within_one_batch_is_stored_once(self) -> None:
        runs = _runs(3)
        store = ValidationStore()
        store.save_many([runs[0], runs[0], runs[1]])
        assert store.count() == 2

    def test_a_duplicate_id_within_one_batch_is_stored_once_sqlite(
        self, sqlite_store: SqliteValidationStore
    ) -> None:
        runs = _runs(3)
        sqlite_store.save_many([runs[0], runs[0], runs[1]])
        assert sqlite_store.count() == 2

    def test_batching_does_not_overwrite_an_existing_record(
        self, sqlite_store: SqliteValidationStore
    ) -> None:
        """The first write wins -- batching must not become last-write-wins."""
        original = _runs(1)[0]
        sqlite_store.save(original)
        # A second, visibly different run under the same id.
        replacement = StoredRun(**{**original.__dict__, "task_id": "different-task"})
        sqlite_store.save_many([replacement])
        assert sqlite_store.get(original.run_id).task_id == original.task_id


class TestEdgeCases:
    def test_an_empty_batch_is_a_no_op(self) -> None:
        mem = ValidationStore()
        assert mem.save_many([]) == []
        assert mem.count() == 0

    def test_an_empty_batch_is_a_no_op_sqlite(self, sqlite_store: SqliteValidationStore) -> None:
        assert sqlite_store.save_many([]) == []
        assert sqlite_store.count() == 0

    def test_the_returned_runs_are_the_inputs_in_order(self) -> None:
        runs = _runs(5)
        assert ValidationStore().save_many(runs) == runs
        assert ValidationStore().save_many(iter(runs)) == runs  # any iterable

    def test_a_generator_is_accepted(self, sqlite_store: SqliteValidationStore) -> None:
        runs = _runs()
        sqlite_store.save_many(r for r in runs)
        assert sqlite_store.count() == _COUNT
