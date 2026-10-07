"""Store scaling benchmarks - history()/count() and resident memory.

The in-memory store is unbounded and ``history()`` copies + sorts the whole
dict on every call, so both read cost and payload build are pinned here at the
three sizes the dashboard and list endpoints are actually used at.
"""

from __future__ import annotations

import pytest

from validsim.store.sqlite import SqliteValidationStore

from .conftest import time_benchmark
from .fixtures import prototype_run, seeded_store

pytestmark = pytest.mark.benchmark

RUN_COUNTS = (1_000, 10_000, 100_000)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_history(count: int) -> None:
    """history() copies and sorts every stored run on every call."""
    store = seeded_store(count)
    time_benchmark(f"memstore_history_n{count}", store.history, rounds=7, warmup=2)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_count(count: int) -> None:
    """count() is O(1) under the lock - this pins that it stays O(1)."""
    store = seeded_store(count)
    time_benchmark(f"memstore_count_n{count}", store.count, rounds=50, warmup=5)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_memory_store_dashboard_payload(count: int) -> None:
    """What /api/v1/dashboard/history builds: history() + reversed + summary()."""
    store = seeded_store(count)
    time_benchmark(
        f"memstore_dashboard_payload_n{count}",
        lambda: [r.summary() for r in reversed(store.history())],
        rounds=5,
        warmup=2,
    )


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_sqlite_store_history(tmp_path, count: int) -> None:
    """SQLite history() deserialises every row's JSON blobs - the cost the
    dashboard pays if VALIDSIM_STORE=sqlite is used."""
    store = SqliteValidationStore(tmp_path / "bench.db")
    proto = prototype_run(1)
    for i in range(count):
        store.save(type(proto)(**{**proto.__dict__, "run_id": f"vrun-{i:08x}"}))
    time_benchmark(f"sqlitestore_history_n{count}", store.history, rounds=3, warmup=1)
    store.close()


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_sqlite_store_count(tmp_path, count: int) -> None:
    store = SqliteValidationStore(tmp_path / "bench.db")
    proto = prototype_run(1)
    for i in range(count):
        store.save(type(proto)(**{**proto.__dict__, "run_id": f"vrun-{i:08x}"}))
    time_benchmark(f"sqlitestore_count_n{count}", store.count, rounds=5, warmup=2)
    store.close()
