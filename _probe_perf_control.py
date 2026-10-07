"""Control: is the bootstrap cost inherent, or an implementation artifact?"""
from __future__ import annotations

import random
import statistics
import sys
import time

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import bootstrap_ci  # noqa: E402

N = 20_000
RESAMPLES = 500
bits = [1.0] * (N * 3 // 4) + [0.0] * (N - N * 3 // 4)


def timeit(label, fn):
    fn()
    t = time.perf_counter()
    fn()
    print(f"{label:<50} {(time.perf_counter() - t) * 1000:9.1f} ms")


print(f"n={N}, n_resamples={RESAMPLES}, statistic=mean\n")

timeit("current: [values[rng.randrange(n)] for _ in range(n)]",
       lambda: bootstrap_ci(bits, n_resamples=RESAMPLES, seed=42))

# Control A: random.choices (C-level index generation) instead of randrange loop.
def variant_choices():
    rng = random.Random(42)
    n = len(bits)
    out = []
    for _ in range(RESAMPLES):
        sample = rng.choices(bits, k=n)
        out.append(statistics.fmean(sample))
    return out

timeit("variant: rng.choices (C-level sampling)", variant_choices)


# Control B: the real observation -- for a 0/1 sample the mean of a bootstrap
# resample is determined ENTIRELY by how many 1s were drawn (hypergeometric).
# No index array is needed at all.
def variant_counts():
    rng = random.Random(42)
    n = len(bits)
    total = len(bits)
    k = sum(1 for b in bits if b == 1.0)
    out = []
    for _ in range(RESAMPLES):
        # number of successes drawn ~ Hypergeometric(total, k, n)
        drawn = sum(1 for _ in range(n) if rng.random() < k / total)
        out.append(drawn / n)
    return out

timeit("variant: count-only (no per-element list)", variant_counts)

print("\nIf count-only is materially faster, the cost is the index-materialisation,")
print("not the statistics — the resample only needs the success count.")
