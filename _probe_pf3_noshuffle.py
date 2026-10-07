"""Can the shuffle be avoided while keeping EXACTLY the current semantics?

Key insight: rng.shuffle(pooled) on a pool of 0/1 values, followed by
sum(pooled[:na]), produces ones_a ~ Hypergeometric. The CURRENT code's exact
behaviour is a permutation test. The question is whether we can keep the same
statistic while skipping the full shuffle.

Alternative: instead of shuffling the whole pool, note that the sum of the first
na entries only depends on which values land there. We can pick the indices
directly.
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


def current(a, b, n_resamples, seed):
    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    pooled = list(a) + list(b)
    total_ones = sum(pooled)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_resamples):
        rng.shuffle(pooled)
        ones_a = sum(pooled[:na])
        if abs(ones_a / na - (total_ones - ones_a) / nb) >= abs(delta) - 1e-12:
            extreme += 1
    return (extreme + 1) / (n_resamples + 1)


def partial_shuffle(a, b, n_resamples, seed):
    """Only permute the FIRST na slots' worth of what matters.

    Instead of shuffling all N=na+nb items, draw the multiset of values for the
    first na slots directly: we need ones_a, the count of 1s among na draws
    without replacement from a pool of total_ones ones and (N-total_ones) zeros.
    That is exactly Hypergeometric(N, total_ones, na).
    """
    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    total_ones = int(sum(list(a) + list(b)))
    N = na + nb
    rng = random.Random(seed)
    extreme = 0
    target = abs(delta)
    # exact hypergeometric via sequential conditional Bernoulli
    ones_a = 0
    rem_ones, rem_total = total_ones, N
    for _ in range(na):
        if rem_ones > 0 and rng.randrange(rem_total) < rem_ones:
            ones_a += 1
            rem_ones -= 1
        rem_total -= 1
    # (single draw path is too slow; vectorised below)
    return None


# Practical fast approach: precompute the hypergeometric PMF and sample from it
# with bisect. O(1) per draw regardless of n.
from bisect import bisect_left  # noqa: E402


def make_hypergeom_sampler(K: int, N: int, na: int):
    """Build an exact Hypergeometric(N, K, na) sampler with an alias-free
    cumulative table. Support size is at most min(K, na) + 1, which for binary
    success-rate data is <= na + 1."""
    from math import comb
    lo = max(0, K - (N - na))
    hi = min(K, na)
    support = list(range(lo, hi + 1))
    weights = [comb(K, k) * comb(N - K, na - k) for k in support]
    total_w = sum(weights)
    cum, acc = [], 0.0
    for w in weights:
        acc += w / total_w
        cum.append(acc)
    cum[-1] = 1.0
    return support, cum


def fast(a, b, n_resamples, seed):
    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    K = int(sum(list(a) + list(b)))
    N = na + nb
    support, cum = make_hypergeom_sampler(K, N, na)
    rng = random.Random(seed)
    extreme = 0
    target = abs(delta)
    for _ in range(n_resamples):
        u = rng.random()
        ones_a = support[bisect_left(cum, u)]
        if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
            extreme += 1
    return (extreme + 1) / (n_resamples + 1)


print("=== Equivalence (24 cases) ===")
mismatch = 0
for n in (200, 500, 1_000, 5_000):
    for pa, pb in ((0.75, 0.80), (0.60, 0.75), (0.90, 0.90), (0.50, 0.50),
                   (0.95, 0.75), (0.80, 0.82)):
        a = binary(int(n * pa), n)
        b = binary(int(n * pb), n)
        p1 = current(a, b, 1000, 42)
        p2 = fast(a, b, 1000, 42)
        if (p1 < 0.05) != (p2 < 0.05):
            mismatch += 1
            print(f"  MISMATCH n={n} {pa}/{pb}: {p1:.4f} vs {p2:.4f}")
print(f"  significance mismatches = {mismatch} / 24")

print("\n=== Speed (n=20000) ===")
n = 20_000
a = binary(15_000, n)
b = binary(16_000, n)
t = time.perf_counter()
current(a, b, 1000, 42)
print(f"  shuffle : {(time.perf_counter() - t) * 1000:9.1f} ms")
t = time.perf_counter()
fast(a, b, 1000, 42)
print(f"  hypergeo: {(time.perf_counter() - t) * 1000:9.1f} ms")
