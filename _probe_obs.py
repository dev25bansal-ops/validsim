"""SCRATCH measurement harness for the API observability task. DELETED before report."""
from __future__ import annotations

import gc
import os
import statistics
import sys
import time

os.environ.setdefault("VALIDSIM_RATE_LIMIT", "0")

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from fastapi.testclient import TestClient  # noqa: E402

from validsim.api.main import create_app  # noqa: E402
from validsim.store.memory import StoredRun, ValidationStore  # noqa: E402
from validsim.store.sqlite import SqliteValidationStore  # noqa: E402
from bench.fixtures import prototype_run  # noqa: E402

PATHS = (
    "/metrics",
    "/api/v1/metrics",
    "/api/v1/validations",
    "/api/v1/dashboard/history",
    "/api/v1/dashboard/summary",
    "/api/v1/models",
    "/api/v1/regressions",
)
SIZES = (1_000, 10_000, 100_000)


def stamp(i: int) -> str:
    s, m, h = i % 60, (i // 60) % 60, (i // 3600) % 24
    d = (i // 86400) % 28 + 1
    return f"2026-01-{d:02d}T{h:02d}:{m:02d}:{s:02d}+00:00"


def build(backend: str, n: int, episodes: int, path: str):
    proto = prototype_run(episodes)
    if backend == "memory":
        st = ValidationStore()
        for i in range(n):
            st.save(StoredRun(**{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": stamp(i)}))
        return st, None
    st = SqliteValidationStore(path)
    for i in range(n):
        st.save(StoredRun(**{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": stamp(i)}))
    return st, path


def timeit(fn, warmup: int, rounds: int) -> tuple[float, float]:
    for _ in range(warmup):
        fn()
    s = []
    for _ in range(rounds):
        gc.collect()
        t = time.perf_counter()
        fn()
        s.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(s), min(s)


def main() -> None:
    episodes = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    backend = sys.argv[2] if len(sys.argv) > 2 else "memory"
    db = sys.argv[3] if len(sys.argv) > 3 else None
    print(f"### backend={backend} episodes/run={episodes}")
    print(f"{'endpoint':<30} {'n':>7} {'p50 ms':>10} {'min ms':>10}")
    for n in SIZES:
        store, _ = build(backend, n, episodes, db or f"probe_{backend}_{n}.db")
        app = create_app(store=store)
        with TestClient(app) as c:
            for p in PATHS:
                r = c.get(p)
                assert r.status_code == 200, (p, r.status_code, r.text[:200])
                w, r_ = (2, 7) if n <= 10_000 else (1, 3)
                p50, mn = timeit(lambda p=p: c.get(p), w, r_)
                print(f"{p:<30} {n:>7} {p50:>10.2f} {mn:>10.2f}", flush=True)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        del store, app
        gc.collect()
    print(f"### END backend={backend} episodes={episodes}")


if __name__ == "__main__":
    main()
