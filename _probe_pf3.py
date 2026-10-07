"""PF-3 probe: is the regression permutation test actually a bottleneck?"""
from __future__ import annotations

import random
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import two_proportion_bootstrap_test  # noqa: E402


def binary_sample(ones: int, total: int) -> list[float]:
    return [1.0] * ones + [0.0] * (total - ones)


def timeit(label, fn):
    fn()
    t = time.perf_counter()
    fn()
    print(f"{label:<50} {(time.perf_counter() - t) * 1000:9.1f} ms")


print("two_proportion_bootstrap_test, n_resamples=1000 (default)\n")
for n in (1_000, 5_000, 20_000):
    a = binary_sample(int(n * 0.75), n)
    b = binary_sample(int(n * 0.80), n)
    timeit(f"n={n:6d} per side",
           lambda x=a, y=b: two_proportion_bootstrap_test(x, y, seed=42))

print("\nCost decomposition (n=20000, 1000 permutations)")
n = 20_000
pooled = [1.0] * 15_000 + [0.0] * (2 * n - 15_000)
rng = random.Random(42)

t = time.perf_counter()
for _ in range(1000):
    rng.shuffle(pooled)
shuffle_ms = (time.perf_counter() - t) * 1000

t = time.perf_counter()
for _ in range(1000):
    s = sum(pooled[:n])
slice_ms = (time.perf_counter() - t) * 1000

print(f"  rng.shuffle(pooled) x1000 : {shuffle_ms:9.1f} ms")
print(f"  sum(pooled[:n])     x1000 : {slice_ms:9.1f} ms")
