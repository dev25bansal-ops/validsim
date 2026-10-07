"""Final control: is a fast exact-hypergeometric sampler truly equivalent?

Uses random.sample on the K success positions (C-level) instead of a Python
per-slot loop, then counts how many land in the first na slots.
"""
from __future__ import annotations

import random
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import two_proportion_bootstrap_test  # noqa: E402


def binary(ones: int, total: int) -> list[float]:
    return [1.0] * ones + [0.0] * (total - ones)


print("=== Equivalence sweep: shuffle vs exact hypergeometric ===\n")
mismatches = 0
cases = 0
for n in (200, 500, 1_000, 5_000):
    for pa, pb in ((0.75, 0.80), (0.60, 0.75), (0.90, 0.90), (0.50, 0.50),
                   (0.95, 0.75), (0.80, 0.82)):
        a = binary(int(n * pa), n)
        b = binary(int(n * pb), n)
        _, p_shuf, sig_shuf = two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)

        na, nb = len(a), len(b)
        delta = statistics.fmean(a) - statistics.fmean(b)
        K = int(sum(list(a) + list(b)))
        N = na + nb
        target = abs(delta)
        rng = random.Random(42)
        extreme = 0
        for _ in range(1000):
            positions = rng.sample(range(N), K)
            ones_a = sum(1 for p in positions if p < na)
            if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
                extreme += 1
        p_hyp = (extreme + 1) / 1001
        sig_hyp = p_hyp < 0.05
        cases += 1
        if sig_shuf != sig_hyp:
            mismatches += 1
            print(f"  MISMATCH n={n} pa={pa} pb={pb}: shuffle={p_shuf:.4f}({sig_shuf}) "
                  f"hyper={p_hyp:.4f}({sig_hyp})")

print(f"  cases={cases}  significance mismatches={mismatches}")

print("\n=== Speed (n=20000, 1000 perms) ===")
n = 20_000
a = binary(15_000, n)
b = binary(16_000, n)
t = time.perf_counter()
two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)
print(f"  shuffle        : {(time.perf_counter() - t) * 1000:9.1f} ms")

na, nb = len(a), len(b)
delta = statistics.fmean(a) - statistics.fmean(b)
K = int(sum(list(a) + list(b)))
N = na + nb
target = abs(delta)
t = time.perf_counter()
rng = random.Random(42)
extreme = 0
for _ in range(1000):
    positions = rng.sample(range(N), K)
    ones_a = sum(1 for p in positions if p < na)
    if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
        extreme += 1
print(f"  sample-based   : {(time.perf_counter() - t) * 1000:9.1f} ms")
