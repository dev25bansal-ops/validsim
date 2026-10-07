"""SCRATCH: cumulative decomposition of the REAL SqliteValidationStore.history() path. DELETED."""
from __future__ import annotations

import gc
import json
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from validsim.engine.evaluation import EvaluationResult  # noqa: E402
from validsim.engine.regression import RegressionItem, RegressionReport  # noqa: E402
from validsim.engine.safety import SafetyResult  # noqa: E402
from validsim.engine.scorecard import Scorecard  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402
from validsim.store.sqlite import SqliteValidationStore, _scorecard_from_dict  # noqa: E402
from bench.fixtures import prototype_run  # noqa: E402


def stamp(i: int) -> str:
    s, m, h = i % 60, (i // 60) % 60, (i // 3600) % 24
    d = (i // 86400) % 28 + 1
    return f"2026-01-{d:02d}T{h:02d}:{m:02d}:{s:02d}+00:00"


def run(store, upto: int) -> int:
    """Replicate history() but stop after `upto` stages. Returns rows touched."""
    conn = store._conn
    rows = conn.execute("SELECT * FROM validations ORDER BY created_at ASC").fetchall()
    if upto == 0:
        return len(rows)
    scorecards = [_scorecard_from_dict(json.loads(r["scorecard_json"])) for r in rows]
    if upto == 1:
        return len(scorecards)
    episodes = [[EpisodeResult(**e) for e in json.loads(r["episodes_json"])] for r in rows]
    if upto == 2:
        return sum(len(e) for e in episodes)
    evals = [EvaluationResult(**json.loads(r["evaluation_json"])) for r in rows]
    safes = [SafetyResult(**json.loads(r["safety_json"])) for r in rows]
    if upto == 3:
        return len(evals) + len(safes)
    regs = [r["regression_json"] for r in rows]
    _ = [RegressionReport(items=[RegressionItem(**i) for i in json.loads(x)]) if x else None for x in regs]
    return len(scorecards) + len(episodes) + len(evals)


def timeit(fn, warmup, rounds):
    for _ in range(warmup):
        fn()
    s = []
    for _ in range(rounds):
        gc.collect()
        t = time.perf_counter()
        fn()
        s.append((time.perf_counter() - t) * 1000.0)
    return statistics.median(s)


def main():
    episodes = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10_000
    st = SqliteValidationStore(f"probe_decomp_{episodes}_{n}.db")
    proto = prototype_run(episodes)
    for i in range(n):
        st.save(type(proto)(**{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": stamp(i)}))
    w, r = (1, 3) if n >= 100_000 else (2, 5)
    print(f"\n=== episodes/run={episodes}  n={n} (cumulative, in ONE process, same DB) ===")
    labels = ["1 SELECT * fetchall", "+ scorecard Scorecard()", "+ episodes EpisodeResult()",
              "+ evaluation + safety", "+ regression"]
    prev = 0.0
    for i, lab in enumerate(labels):
        ms = timeit(lambda i=i: run(st, i), w, r)
        print(f"{lab:<32} cum {ms:>9.2f} ms   delta {ms - prev:>9.2f} ms")
        prev = ms
    real = timeit(st.history, w, r)
    print(f"{'REAL store.history()':<32}     {real:>9.2f} ms")
    st.close()


if __name__ == "__main__":
    main()
