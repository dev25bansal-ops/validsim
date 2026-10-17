"""Store scaling benchmarks - history()/count() and resident memory.

The in-memory store is unbounded and ``history()`` copies + sorts the whole
dict on every call, so both read cost and payload build are pinned here at the
three sizes the dashboard and list endpoints are actually used at.

Fixture cost
------------
Seeding is the dominant expense of this module, and it is *not* what any of
these benchmarks measure. Two things keep it bounded:

* ``save_many`` loads rows in a single transaction. A ``save`` loop commits --
  and fsyncs -- per row, which measured ~15 minutes for 100 000 rows against
  under three seconds batched, purely because of per-row durability.
* The seeded stores are module-scoped, so the three in-memory benchmarks share
  one store per size and the two SQLite benchmarks share one database per size,
  instead of each rebuilding an identical fixture.

Neither change alters a measured operation: the same rows are present, in the
same order, for every timed call.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from validsim.store.memory import ValidationStore
from validsim.store.sqlite import SqliteValidationStore

from .conftest import time_benchmark
from .fixtures import seed_store, seeded_store

pytestmark = pytest.mark.benchmark

RUN_COUNTS = (1_000, 10_000, 100_000)


@pytest.fixture(scope="module")
def memory_stores() -> dict[int, ValidationStore]:
    """One seeded in-memory store per run count, shared across the benchmarks.

    Every benchmark here only reads, so one instance per size is safe and turns
    three identical 100 000-run builds into one.
    """
    return {count: seeded_store(count) for count in RUN_COUNTS}


@pytest.fixture(scope="module")
def sqlite_dbs(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    """One seeded SQLite database per run count, shared across the benchmarks."""
    base = tmp_path_factory.mktemp("bench-store")
    out: dict[int, Path] = {}
    for count in RUN_COUNTS:
        path = base / f"bench_{count}.db"
        store = SqliteValidationStore(path)
        try:
            seed_store(store, count)
        finally:
            store.close()
        out[count] = path
    return out


@pytest.fixture
def sqlite_store(sqlite_dbs: dict[int, Path]) -> Iterator[dict[int, SqliteValidationStore]]:
    """Open every seeded database once for a benchmark, closing them after."""
    stores = {count: SqliteValidationStore(path) for count, path in sqlite_dbs.items()}
    try:
        yield stores
    finally:
        for store in stores.values():
            store.close()


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_history(memory_stores: dict[int, ValidationStore], count: int) -> None:
    """history() copies and sorts every stored run on every call."""
    time_benchmark(f"memstore_history_n{count}", memory_stores[count].history, rounds=7, warmup=2)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_count(memory_stores: dict[int, ValidationStore], count: int) -> None:
    """count() is O(1) under the lock - this pins that it stays O(1)."""
    time_benchmark(f"memstore_count_n{count}", memory_stores[count].count, rounds=50, warmup=5)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_dashboard_payload(
    memory_stores: dict[int, ValidationStore], count: int
) -> None:
    """What /api/v1/dashboard/history builds: history() + reversed + summary()."""
    store = memory_stores[count]
    time_benchmark(
        f"memstore_dashboard_payload_n{count}",
        lambda: [r.summary() for r in reversed(store.history())],
        rounds=5,
        warmup=2,
    )


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_sqlite_store_history(
    sqlite_store: dict[int, SqliteValidationStore], count: int
) -> None:
    """SQLite history() deserialises every row's JSON blobs - the cost the
    dashboard pays if VALIDSIM_STORE=sqlite is used."""
    time_benchmark(f"sqlitestore_history_n{count}", sqlite_store[count].history, rounds=3, warmup=1)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_sqlite_store_count(
    sqlite_store: dict[int, SqliteValidationStore], count: int
) -> None:
    time_benchmark(f"sqlitestore_count_n{count}", sqlite_store[count].count, rounds=5, warmup=2)
