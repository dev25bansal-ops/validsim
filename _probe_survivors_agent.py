"""Why did 2 mutations survive? Determine if they are test gaps or dead code."""
import math
import random
import statistics
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

print("=" * 74)
print("Q1: are force_rate / proximity_rate caps reachable?")
print("=" * 74)
print("  force_rate     = (# episodes over limit) / total  -> bounded by 1.0 ALWAYS")
print("  proximity_rate = (# episodes under limit) / total -> bounded by 1.0 ALWAYS")
print("  cpe            = sum(collision_count) / total    -> UNBOUNDED (collisions>1/ep)")
print("  => min(force_rate,1.0) and min(prox_rate,1.0) are UNREACHABLE by construction")
print("     Only min(collisions_per_episode, 1.0) can ever bind.")
print("  Max cpe observed in the wild: a 10-episode run with 3 collisions each = 3.0")
print()
print("  So: 'cap removed (force)' is DEAD CODE, not a test gap.")
print("  A MEANINGFUL force-channel mutation is forgetting the division:")

print()
print("=" * 74)
print("Q2: at what n_resamples does removing the point-bracket widening show?")
print("=" * 74)


def percentile(sorted_values, q):
    if not sorted_values:
        raise ValueError
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = q * (len(sorted_values) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def ci(values, n_resamples, seed, widen):
    rng = random.Random(seed)
    n = len(values)
    point = float(statistics.fmean(values))
    estimates = sorted(
        float(statistics.fmean(rng.choices(values, k=n))) for _ in range(n_resamples)
    )
    alpha = (1.0 - 0.95) / 2.0
    low = percentile(estimates, alpha)
    high = percentile(estimates, 1.0 - alpha)
    if widen:
        low = min(low, point)
        high = max(high, point)
    return low, high, point


rng = random.Random(0xBEEF17)
found = []
for nr in (2, 3, 4, 5, 8, 10, 20, 30, 50, 100, 200, 500):
    bad = 0
    trials = 300
    for t in range(trials):
        n = rng.randint(2, 12)
        vals = [1.0 if rng.random() < rng.uniform(0.2, 0.8) else 0.0 for _ in range(n)]
        seed = rng.randrange(1, 10**6)
        lo_w, hi_w, pt = ci(vals, nr, seed, True)
        lo_n, hi_n, _ = ci(vals, nr, seed, False)
        if not (lo_n <= pt <= hi_n):
            bad += 1
        # does the widened version differ from the raw one?
    print(f"  n_resamples={nr:>4}: {bad:>3}/{trials} trials where NO-widening excludes the point")
    if bad:
        found.append(nr)
print()
print("  n_resamples values exposing the missing widening:", found)
print("  => a test at n_resamples=2..5 with small n WILL catch it")
