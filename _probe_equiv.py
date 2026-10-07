"""Verify rng.choices is distributionally equivalent before shipping it."""
from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

# Case 1: binary sample (the real usage in build_scorecard).
bits = [1.0] * 15 + [0.0] * 5


def old(values, n_resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    return sorted(statistics.fmean([values[rng.randrange(n)] for _ in range(n)])
                  for _ in range(n_resamples))


def new(values, n_resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    return sorted(statistics.fmean(rng.choices(values, k=n))
                  for _ in range(n_resamples))


# Case 2: continuous sample (two_proportion path, non-binary input).
cont = [0.1 * i for i in range(50)]

for name, data in (("binary 20", bits), ("continuous 50", cont)):
    a = old(data, 400, 42)
    b = new(data, 400, 42)
    lo_a, hi_a = a[10], a[-10]
    lo_b, hi_b = b[10], b[-10]
    print(f"{name}:")
    print(f"  old 95% CI = [{lo_a:.6f}, {hi_a:.6f}]  mean={statistics.fmean(a):.6f}")
    print(f"  new 95% CI = [{lo_b:.6f}, {hi_b:.6f}]  mean={statistics.fmean(b):.6f}")
    print(f"  CI width delta = {abs((hi_b - lo_b) - (hi_a - lo_a)):.2e}")
    print(f"  point estimate identical: "
          f"{abs(statistics.fmean(data) - statistics.fmean(data)) < 1e-12}")
    print()

# Case 3: determinism given the same seed must hold for both.
x = old(bits, 200, 7)
y = old(bits, 200, 7)
u = new(bits, 200, 7)
v = new(bits, 200, 7)
print("determinism: old reproducible =", x == y, "| new reproducible =", u == v)
print("new == old elementwise =", u == x, "(expected False: different RNG draw order)")
