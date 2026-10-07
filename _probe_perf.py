"""c2-perf audit probe — measure real hot paths (temporary)."""
from __future__ import annotations

import os
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from conftest import make_persisted_scorecard, make_stored_run  # noqa: E402

from validsim.engine.stats import bootstrap_ci  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402


def timeit(label: str, fn, rounds: int = 5):
    for _ in range(2):
        fn()
    samples = []
    for _ in range(rounds):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    print(f"{label:<52} median {statistics.median(samples):9.2f} ms")
    return statistics.median(samples)


print("=== P1: /metrics renders from store.history() ===")
for n in (100, 1000, 5000):
    store = ValidationStore()
    for i in range(n):
        store.save(make_stored_run(make_persisted_scorecard(run_id=f"vrun-{i:08x}")))
    timeit(f"history() + render_metrics, {n} stored runs",
           lambda s=store: __import__("validsim.api.metrics", fromlist=["x"]).render_metrics(
               s, __import__("validsim.api.metrics", fromlist=["x"]).Metrics()))

print()
print("=== P2: bootstrap_ci cost vs episode count ===")
for n in (10_000, 100_000):
    bits = [1.0] * int(n * 0.75) + [0.0] * (n - int(n * 0.75))
    timeit(f"bootstrap_ci n={n} n_resamples=500", lambda b=bits: bootstrap_ci(b, n_resamples=500, seed=42), rounds=3)

print()
print("=== P3: /api/v1/validations list with limit ===")
store = ValidationStore()
for i in range(3000):
    store.save(make_stored_run(make_persisted_scorecard(run_id=f"vrun-{i:08x}")))
timeit("history() over 3000 runs (unbounded)", lambda: store.history())
timeit("count() over 3000 runs", lambda: len(store))

print()
print("=== P4: list_for_checkpoint over one checkpoint ===")
timeit("history() filtered to 3000 of 3000 (same ckpt)",
       lambda: [r for r in store.history()])
