"""Correct control: sample the TRUE null distribution (hypergeometric)."""
from __future__ import annotations

import random
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import two_proportion_bootstrap_test  # noqa: E402


def binary(ones: int, total: int) -> list[float]:
    return [1.0] * ones + [0.0] * (total - ones)


# Under H0, ones_a in the first na slots of a uniformly shuffled pool of N
# items containing K ones is EXACTLY Hypergeometric(N, K, na). The correct
# sampler draws from that law, not uniformly over the support.
def hypergeom_draw(K: int, N: int, na: int, rng: random.Random) -> int:
    """Draw ones_a ~ Hypergeometric(N, K, na) exactly, via the beta/binomial
    representation: choose na of N positions without replacement."""
    # Sample the K "success" positions uniformly from N; ones_a = how many of
    # them land in the first na slots.
    ones_a = 0
    remaining = K
    slots = N
    picks = na
    # Walk the na slots, deciding success/failure against the remaining counts.
    for _ in range(na):
        if remaining > 0 and rng.randrange(slots) < remaining:
            ones_a += 1
            remaining -= 1
        slots -= 1
        picks -= 1
    return ones_a


print("=== True hypergeometric vs shuffle ===\n")
for n, pa, pb in ((1_000, 0.75, 0.80), (5_000, 0.75, 0.80), (2_000, 0.60, 0.75)):
    a = binary(int(n * pa), n)
    b = binary(int(n * pb), n)
    d_old, p_old, sig_old = two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)

    na, nb = len(a), len(b)
    delta = statistics.fmean(a) - statistics.fmean(b)
    K = int(sum(list(a) + list(b)))
    N = na + nb
    target = abs(delta)
    rng = random.Random(42)
    extreme = 0
    for _ in range(1000):
        ones_a = hypergeom_draw(K, N, na, rng)
        if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
            extreme += 1
    p_new = (extreme + 1) / 1001
    print(f"n={n:5d} pa={pa} pb={pb}: shuffle p={p_old:.4f} ({sig_old}) | "
          f"hypergeom p={p_new:.4f} ({p_new < 0.05}) | same={((p_old < 0.05) == (p_new < 0.05))}")

print("\n=== Speed (n=20000) ===")
n = 20_000
a = binary(15_000, n)
b = binary(16_000, n)
t = time.perf_counter()
two_proportion_bootstrap_test(a, b, n_resamples=1000, seed=42)
print(f"  shuffle   : {(time.perf_counter() - t) * 1000:9.1f} ms")
na, nb = len(a), len(b)
delta = statistics.fmean(a) - statistics.fmean(b)
K = int(sum(list(a) + list(b)))
N = na + nb
target = abs(delta)
t = time.perf_counter()
rng = random.Random(42)
extreme = 0
for _ in range(1000):
    ones_a = hypergeom_draw(K, N, na, rng)
    if abs(ones_a / na - (K - ones_a) / nb) >= target - 1e-12:
        extreme += 1
print(f"  hypergeom : {(time.perf_counter() - t) * 1000:9.1f} ms")
