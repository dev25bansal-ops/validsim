"""SCRATCH: split episodes_json cost into fetch / json.loads / EpisodeResult build. DELETED."""
from __future__ import annotations

import gc
import json
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")
sys.path.insert(0, r"d:\SIM-TO-REAL\tests")

from validsim.sim.runner import EpisodeResult  # noqa: E402
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
    st = SqliteValidationStore(f"probe_split_{episodes}_{n}.db")
    proto = prototype_run(episodes)
    for i in range(n):
        st.save(type(proto)(**{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": stamp(i)}))
    conn = st._conn
    print(f"\n=== episodes/run={episodes}  n={n} ===")

    def fetch_bytes():
        return [r[0] for r in conn.execute("SELECT episodes_json FROM validations")]

    def fetch_then_loads():
        return [json.loads(r[0]) for r in conn.execute("SELECT episodes_json FROM validations")]

    def fetch_then_build():
        return [EpisodeResult(**e) for r in conn.execute("SELECT episodes_json FROM validations")
                for e in json.loads(r[0])]

    def row_key_access():
        rows = conn.execute("SELECT * FROM validations").fetchall()
        return [r["episodes_json"] for r in rows]

    def row_key_6x():
        rows = conn.execute("SELECT * FROM validations").fetchall()
        return [(r["scorecard_json"], r["episodes_json"], r["evaluation_json"],
                 r["safety_json"], r["regression_json"], r["baseline_run_id"]) for r in rows]

    cases = [
        ("1 fetch episodes_json bytes", fetch_bytes),
        ("2 + json.loads", fetch_then_loads),
        ("3 + EpisodeResult(**e)  [episodes path]", fetch_then_build),
        ("4 fetch all rows, 1 string-key access", row_key_access),
        ("5 fetch all rows, 6 string-key accesses", row_key_6x),
        ("6 history() total (reference)", st.history),
    ]
    for name, fn in cases:
        w, r = (1, 3) if n >= 100_000 else (2, 7)
        print(f"{name:<45} {timeit(fn, w, r):>10.2f} ms")
    st.close()


if __name__ == "__main__":
    main()
