"""SCRATCH: isolate WHICH SQLite column drives the re-hydration cost. DELETED before report."""
from __future__ import annotations

import gc
import json
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from validsim.store.sqlite import SqliteValidationStore  # noqa: E402
from bench.fixtures import prototype_run  # noqa: E402


def stamp(i: int) -> str:
    s, m, h = i % 60, (i // 60) % 60, (i // 3600) % 24
    d = (i // 86400) % 28 + 1
    return f"2026-01-{d:02d}T{h:02d}:{m:02d}:{s:02d}+00:00"


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
    db = f"probe_break_{episodes}_{n}.db"
    proto = prototype_run(episodes)
    st = SqliteValidationStore(db)
    for i in range(n):
        st.save(type(proto)(**{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": stamp(i)}))
    conn = st._conn
    tbl_bytes = conn.execute("SELECT SUM(LENGTH(episodes_json)) FROM validations").fetchone()[0]
    sc_bytes = conn.execute("SELECT SUM(LENGTH(scorecard_json)) FROM validations").fetchone()[0]
    print(f"\n=== episodes/run={episodes}  n={n} ===")
    print(f"episodes_json total = {tbl_bytes/1e6:.1f} MB   scorecard_json total = {sc_bytes/1e6:.1f} MB")

    cases = [
        ("A SELECT COUNT(*)", lambda: conn.execute("SELECT COUNT(*) FROM validations").fetchone()),
        ("B SELECT promoted cols only (no JSON)",
         lambda: conn.execute("SELECT run_id,checkpoint_id,task_id,composite_score,deploy_decision,created_at "
                              "FROM validations ORDER BY created_at ASC").fetchall()),
        ("C SELECT * (no re-hydrate)",
         lambda: conn.execute("SELECT * FROM validations ORDER BY created_at ASC").fetchall()),
        ("D history() FULL re-hydrate", lambda: st.history()),
        ("E SELECT * MINUS episodes_json",
         lambda: conn.execute("SELECT run_id,checkpoint_id,task_id,composite_score,deploy_decision,created_at,"
                              "scorecard_json,baseline_run_id,evaluation_json,safety_json,regression_json "
                              "FROM validations ORDER BY created_at ASC").fetchall()),
        ("F episodes_json bytes only",
         lambda: conn.execute("SELECT episodes_json FROM validations").fetchall()),
        ("G json.loads(episodes_json) all rows",
         lambda: [json.loads(r[0]) for r in conn.execute("SELECT episodes_json FROM validations").fetchall()]),
        ("H scorecard re-hydrate only",
         lambda: [json.loads(r[0]) for r in conn.execute("SELECT scorecard_json FROM validations").fetchall()]),
    ]
    for name, fn in cases:
        w, r = (1, 3) if n >= 100_000 else (2, 7)
        print(f"{name:<40} {timeit(fn, w, r):>10.2f} ms")
    st.close()


if __name__ == "__main__":
    main()
