"""Control: can the shuffle be replaced by an exact hypergeometric draw?"""
from __future__ import annotations

import random
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import two_proportion_bootstrap_test  # noqa: E402


def binary(ones: int, total: int) -> list[float]:
    return [1.0] * ones + [0.0] * (total - ones)


print("=== Does the p-value match a hypergeometric (no-shuffle) formulation? ===\n")

# The permutation test's null distribution: under H0 the pooled labels are
# exchangeable, so ones_a ~ Hypergeometric(N=na+nb, K=total_ones, n=na).
# A direct random draw from that distribution is the same test without ever
# materializing the permutation.
for n, pa, pb in ((1_000, 0.75, 0.80), (5_000, 0.75, 0.80), (2_000, 0.60, 0.75)):
    a = binary(int(n * pa), n)
    b = binary(int(n * pb), n)
    d_old, p_old, sig_old = two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)

    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    pooled = list(a) + list(b)
    K = int(sum(pooled))
    N = na + nb
    rng = random.Random(42)
    target = abs(delta)
    extreme = 0
    for _ in range(1000):
        ones_a = rng.randrange(max(0, K - nb), min(K, na) + 1)
        pd_ = ones_a / na - (K - ones_a) / nb
        if abs(pd_) >= target - 1e-12:
            extreme += 1
    p_new = (extreme + 1) / 1001

    print(f"n={n:6d} pa={pa} pb={pb}")
    print(f"  shuffle p-value = {p_old:.4f}  significant={sig_old}")
    print(f"  direct   p-value = {p_new:.4f}  significant={p_new < 0.05}")
    print(f"  |delta| identical = {abs(delta) < 1e-15}")
    print(f"  same significance verdict = {(p_old < 0.05) == (p_new < 0.05)}")
    print()

print("=== Speed comparison (n=20000) ===")
n = 20_000
a = binary(15_000, n)
b = binary(16_000, n)

t = time.perf_counter()
two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)
print(f"  current (shuffle) : {(time.perf_counter() - t) * 1000:9.1f} ms")

na, nb = len(a), len(b)
delta = statistics.fmean(a) - statistics.fmean(b)
K = int(sum(list(a) + list(b)))
target = abs(delta)
t = time.perf_counter()
rng = random.Random(42)
extreme = 0
lo, hi = max(0, K - nb), min(K, na)
for _ in range(1000):
    ones_a = rng.randrange(lo, hi + 1)
    if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
        extreme += 1
print(f"  direct hypergeometric: {(time.perf_counter() - t) * 1000:9.1f} ms")
