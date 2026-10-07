"""REPRO: degenerate-input edge cases in stats.py and anomaly.py.

Run:  python _probe_eng_stats.py
"""
from __future__ import annotations

import math
import statistics
import traceback

from validsim.engine.anomaly import detect_anomalies
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test


def probe(label, fn):
    try:
        out = fn()
        print(f"  {label:52s} -> {out!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:52s} -> !! {type(exc).__name__}: {exc}")


print("=" * 78)
print("1. bootstrap_ci: single-observation sample")
print("=" * 78)
probe("bootstrap_ci([1.0])", lambda: bootstrap_ci([1.0], n_resamples=100))
probe("bootstrap_ci([0.0], n_resamples=2)  [minimum]", lambda: bootstrap_ci([0.0], n_resamples=2))
probe("n_resamples=2, 50 mixed", lambda: bootstrap_ci([0.0, 1.0] * 25, n_resamples=2))
probe("n_resamples=2, 100 mixed", lambda: bootstrap_ci([0.0, 1.0] * 50, n_resamples=2))
probe("n_resamples=3, 100 mixed", lambda: bootstrap_ci([0.0, 1.0] * 50, n_resamples=3))
probe("control n_resamples=1000, 100 mixed", lambda: bootstrap_ci([0.0, 1.0] * 50, n_resamples=1000))

print()
print("=" * 78)
print("2. bootstrap_ci: non-0/1 values, and values outside the rate range")
print("=" * 78)
probe("all 1.5 (rate > 1)", lambda: bootstrap_ci([1.5, 1.5, 1.5]))
probe("control all 0.9", lambda: bootstrap_ci([0.9, 0.9, 0.9]))

print()
print("=" * 78)
print("3. two_proportion_bootstrap_test: degenerate samples")
print("=" * 78)
probe("a=1 sample, b=1 sample", lambda: two_proportion_bootstrap_test([1.0], [0.0]))
probe("a=1 elem, b=1000 elems", lambda: two_proportion_bootstrap_test([1.0], [0.0] * 1000))
probe("a=1000 elems, b=1 elem", lambda: two_proportion_bootstrap_test([1.0] * 1000, [0.0]))
probe("CONTROL a=500/1000, b=0/1000", lambda: two_proportion_bootstrap_test([1.0] * 500, [0.0] * 1000, n_resamples=200))
probe("identical samples a==b (500/1000)", lambda: two_proportion_bootstrap_test([1.0] * 500, [1.0] * 500, n_resamples=200))

print()
print("=" * 78)
print("4. permutation test p-value for a 1-elem vs 1000-elem sample")
print("=" * 78)
delta, p, sig = two_proportion_bootstrap_test([1.0], [0.0] * 1000, n_resamples=200)
print(f"  a=[1.0] (1/1 = 100%) vs b=[0.0]*1000 (0/1000 = 0%)")
print(f"  delta={delta}  p={p}  significant={sig}")
print("  A 1-vs-1000 'regression' is being called statistically SIGNIFICANT.")
print("  Is that defensible? A single observation vs a thousand.")

print()
print("=" * 78)
print("5. detect_anomalies: degenerate / inconsistent history")
print("=" * 78)


def run(rid, total, tax):
    return {"run_id": rid, "total_episodes": total, "failure_taxonomy": tax}


base = [run(f"b{i}", 100, {"collision": 5}) for i in range(3)]
probe("control: 3 identical baselines, current 50/100",
      lambda: detect_anomalies(base + [run("cur", 100, {"collision": 50})]))
probe("baseline total=0 in all runs",
      lambda: detect_anomalies([run(f"b{i}", 0, {"collision": 5}) for i in range(3)]
                               + [run("cur", 100, {"collision": 50})]))
probe("current count > total (taxonomy 500 of 100)",
      lambda: detect_anomalies([run(f"b{i}", 100, {"collision": 500}) for i in range(3)]
                               + [run("cur", 100, {"collision": 500})]))
probe("baseline counts > total (500 of 100), current 5",
      lambda: detect_anomalies([run(f"b{i}", 100, {"collision": 500}) for i in range(3)]
                               + [run("cur", 100, {"collision": 5})]))
probe("negative counts",
      lambda: detect_anomalies([run(f"b{i}", 100, {"collision": -5}) for i in range(3)]
                               + [run("cur", 100, {"collision": -50})]))
probe("NaN total_episodes",
      lambda: detect_anomalies([run(f"b{i}", float("nan"), {"collision": 5}) for i in range(3)]
                               + [run("cur", 100, {"collision": 50})]))
probe("inf total_episodes",
      lambda: detect_anomalies([run(f"b{i}", float("inf"), {"collision": 5}) for i in range(3)]
                               + [run("cur", 100, {"collision": 50})]))
probe("current total is inf",
      lambda: detect_anomalies(base + [run("cur", float("inf"), {"collision": 50})]))
probe("taxonomy is a list, not a dict",
      lambda: detect_anomalies([run(f"b{i}", 100, ["collision"]) for i in range(3)]
                               + [run("cur", 100, ["collision"])]))
probe("exactly 4 runs (boundary of _MIN_BASELINE_RUNS=3)",
      lambda: detect_anomalies([run(f"b{i}", 100, {"collision": 5}) for i in range(3)]
                               + [run("cur", 100, {"collision": 50})]) and "see above")
probe("exactly 3 runs (should short-circuit)",
      lambda: detect_anomalies([run(f"b{i}", 100, {"collision": 5}) for i in range(2)]
                               + [run("cur", 100, {"collision": 50})]))
