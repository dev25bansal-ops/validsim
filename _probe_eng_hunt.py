"""HUNT: remaining reproducible correctness issues in the engine.

Run:  python _probe_eng_hunt.py
"""
from __future__ import annotations

import math
import statistics

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import compare
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import _adversarial_significant
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test
from validsim.sim.runner import EpisodeResult


def probe(label, fn):
    try:
        print(f"  {label:56s} -> {fn()!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:56s} -> !! {type(exc).__name__}: {exc}")


print("=" * 80)
print("A. two_proportion_bootstrap_test: permutation loop CORRELATION")
print("=" * 80)
# The loop shuffles `pooled` in place and reuses it, so each iteration's
# starting arrangement is the previous iteration's END arrangement, not the
# original sample. That is the textbook requirement for a permutation test
# and is CORRECT. But check the p-value floor.
for n in (2, 5, 10, 50, 100, 1000):
    _, p, _ = two_proportion_bootstrap_test([1.0] * 5, [0.0] * 5, n_resamples=n)
    print(f"  n_resamples={n:5d}  identical-rate samples -> p={p:.6f}  (floor 1/(n+1)={1/(n+1):.6f})")

print()
print("  a=[1.0]*5 vs b=[0.0]*5 is the MAXIMUM possible difference.")
print("  With n_resamples=1000 the reported p should be near the floor.")
_, p1000, _ = two_proportion_bootstrap_test([1.0] * 5, [0.0] * 5, n_resamples=1000)
_, p100, _ = two_proportion_bootstrap_test([1.0] * 5, [0.0] * 5, n_resamples=100)
print(f"  -> p(1000)={p1000:.5f}  p(100)={p100:.5f}")
print("  A p-value that is ~5x larger purely from 10x fewer resamples is")
print("  expected of a Monte-Carlo estimate, not a bug. Reporting only.")

print()
print("=" * 80)
print("B. _adversarial_significant: exactness / numeric")
print("=" * 80)
for ok, total in ((0, 100), (30, 100), (59, 100), (60, 100), (61, 100), (70, 100)):
    p = sum(math.comb(total, k) * (0.6 ** k) * (0.4 ** (total - k)) for k in range(ok + 1))
    print(f"  ok={ok:3d}/{total}  p={p:.6g}  blocks={p < 0.05}")

print()
print("  Large total (math.comb on 1000s is slow but exact):")
import time
t0 = time.perf_counter()
p = sum(math.comb(1000, k) * (0.6 ** k) * (0.4 ** (1000 - k)) for k in range(300))
t1 = time.perf_counter()
print(f"  ok=300/1000  p={p:.6g}  blocks={p < 0.05}  took {1000*(t1-t0):.1f} ms")
print("  (adversarial_count is capped at 1000 by TaskConfig, so this is bounded)")

print()
print("=" * 80)
print("C. bootstrap_ci: the [min(low,point), max(high,point)] widening")
print("=" * 80)
# Does the widening ever make the CI WIDER than the percentile interval
# in a way that misleads? Check monotonicity in n_resamples.
for n in (2, 3, 5, 10, 50, 200, 1000):
    low, high, point = bootstrap_ci([0.0, 1.0] * 50, n_resamples=n, seed=42)
    print(f"  n_resamples={n:5d}  CI=[{low:.4f}, {high:.4f}]  point={point:.4f}  width={high-low:.4f}")

print()
print("=" * 80)
print("D. CI width vs n_resamples: is the reported CI stable?")
print("=" * 80)
vals = [0.0, 1.0] * 50
widths = [bootstrap_ci(vals, n_resamples=n, seed=42)[1] - bootstrap_ci(vals, n_resamples=n, seed=42)[0]
          for n in (50, 100, 200, 500, 1000, 2000)]
print("  widths:", [f"{w:.4f}" for w in widths])
print("  A real 95% CI for p=0.5, n=100 is about 0.40-0.60 (width 0.20).")
print("  n_resamples=2 gives width", f"{bootstrap_ci(vals, n_resamples=2, seed=42)[1] - bootstrap_ci(vals, n_resamples=2, seed=42)[0]:.4f}",
      "-> an absurdly TIGHT interval from 2 resamples.")

print()
print("=" * 80)
print("E. compare(): duration item when baseline mean_duration_s == 0")
print("=" * 80)


def eps(n, dur, ok=True):
    return [EpisodeResult(episode_id=f"e{i}", task_id="t", seed=i, success=ok,
                          duration_s=dur) for i in range(n)]


cur = evaluate(eps(10, 100.0, ok=True))
base = evaluate(eps(10, 0.0, ok=True))
rep = compare(cur, base)
for it in rep.items:
    rel = f"{it.delta / it.before:.1f}" if it.before else "n/a(before=0)"
    print(f"  {it.metric:18s} before={it.before:8.3f} after={it.after:8.3f} "
          f"delta={it.delta:8.3f} rel={rel:>12s} sev={it.severity}")
print("  baseline duration 0 -> relative=0.0 -> 'info'. A 0s -> 100s jump is reported as CLEAN.")
print("  That is a divide-by-zero-as-zero: 'infinitely worse' silently becomes 'no change'.")

print()
print("=" * 80)
print("F. compare(): success item when one side has 0 episodes")
print("=" * 80)
cur0 = evaluate([])
base_full = evaluate(eps(100, 5.0, ok=True))
rep2 = compare(cur0, base_full)
for it in rep2.items:
    print(f"  {it.metric:18s} before={it.before} after={it.after} delta={it.delta} "
          f"p={it.p_value} sig={it.significant} sev={it.severity}")
print("  An empty current run vs a healthy baseline -> severity=info, NOT flagged.")
print("  (scorecard blocks empty runs separately, but the regression report lies.)")
