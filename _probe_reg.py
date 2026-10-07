"""Is the n_resamples=2 failure a regression from rng.choices, or pre-existing?"""
from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")


def _bernoulli(p, n, seed):
    rng = random.Random(seed)
    return [1.0 if rng.random() < p else 0.0 for _ in range(n)]


def old(values, n_resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    point = float(statistics.fmean(values))
    est = sorted(statistics.fmean([values[rng.randrange(n)] for _ in range(n)])
                 for _ in range(n_resamples))
    return est[0], est[-1], point


def new(values, n_resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    point = float(statistics.fmean(values))
    est = sorted(statistics.fmean(rng.choices(values, k=n))
                 for _ in range(n_resamples))
    return est[0], est[-1], point


SEED = 1337
values = _bernoulli(0.5, 40, seed=3)
print(f"values: n={len(values)} mean={statistics.fmean(values):.6f}\n")

for r in (2, 3, 8, 64, 500):
    lo_o, hi_o, p_o = old(values, r, SEED)
    lo_n, hi_n, p_n = new(values, r, SEED)
    ok_o = lo_o <= p_o <= hi_o
    ok_n = lo_n <= p_n <= hi_n
    print(f"n_resamples={r:4d}  old=[{lo_o:.6f},{hi_o:.6f}] contains={ok_o}   "
          f"new=[{lo_n:.6f},{hi_n:.6f}] contains={ok_n}")

print("\nWith 2 resamples there are only 2 order statistics, so the percentile")
print("interval is [min, max] of two noisy resample means and need not bracket")
print("the point estimate. That is a property of tiny-n, not of the sampler.")
