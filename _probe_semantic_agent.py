"""Probe: hunt for semantically wrong metrics in high-coverage modules."""
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine import safety as sf
from validsim.engine import scorecard as sc
from validsim.engine.anomaly import detect_anomalies
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

print("=" * 72)
print("PROBE 1: detect_anomalies, brand-new failure mode (never in baseline)")
print("=" * 72)
hist = [
    {"run_id": f"r{i}", "total_episodes": 100, "failure_taxonomy": {"timeout": 2}}
    for i in range(5)
]
hist.append({"run_id": "cur", "total_episodes": 100,
             "failure_taxonomy": {"timeout": 2, "grasp_failure": 50}})
res = detect_anomalies(hist)
print("anomalies:", res)
print(">>> FLAGGED (expected True, 0.00 -> 0.50):", bool(res))

hist2 = [
    {"run_id": f"r{i}", "total_episodes": 100, "failure_taxonomy": {"grasp_failure": 1}}
    for i in range(5)
]
hist2.append({"run_id": "cur", "total_episodes": 100,
              "failure_taxonomy": {"grasp_failure": 50}})
res2 = detect_anomalies(hist2)
print("tiny-but-nonzero baseline variant -> anomalies:", res2)
print(">>> FLAGGED:", bool(res2))

print()
print("=" * 72)
print("PROBE 2: bootstrap_ci degenerate CI on a perfect run")
print("=" * 72)
for bits in ([1.0] * 30, [0.0] * 30, [1.0] * 3, [1.0] * 200):
    low, high, point = bootstrap_ci(bits, n_resamples=500, seed=42)
    print(f"n={len(bits):>4} all={bits[0]:.0f} -> CI=[{low:.6f}, {high:.6f}] "
          f"width={high - low:.6f} point={point:.6f}")
print("  30/30 successes is consistent with a true rate down to ~0.88 at 95%,")
print("  but the bootstrap reports width 0.0 -> false certainty.")

print()
print("=" * 72)
print("PROBE 3: two_proportion_bootstrap_test on NON-binary data")
print("=" * 72)
a = [0.90, 0.90, 0.90, 0.90]
b = [0.10, 0.10, 0.10, 0.10]
d, p, sig = two_proportion_bootstrap_test(a, b, n_resamples=500, seed=42)
print(f"non-binary: delta={d:.4f} p={p:.6f} significant={sig}")
a2 = [1.0, 1.0, 1.0, 1.0]
b2 = [0.0, 0.0, 0.0, 0.0]
d2, p2, sig2 = two_proportion_bootstrap_test(a2, b2, n_resamples=500, seed=42)
print(f"binary    : delta={d2:.4f} p={p2:.6f} significant={sig2}")
print(">>> the permutation null uses integer ones_a/na (a different statistic).")

print()
print("=" * 72)
print("PROBE 4: two_proportion_bootstrap_test input mutation")
print("=" * 72)
a3 = [1.0, 0.0, 1.0, 0.0]
b3 = [0.0, 0.0, 1.0, 1.0]
a_before, b_before = list(a3), list(b3)
two_proportion_bootstrap_test(a3, b3, n_resamples=200, seed=1)
print("a unchanged:", a3 == a_before, "| b unchanged:", b3 == b_before)

print()
print("=" * 72)
print("PROBE 5: weight sums")
print("=" * 72)
w = (sc._W_SUCCESS, sc._W_SAFETY, sc._W_ROBUSTNESS, sc._W_REGRESSION)
print("scorecard weights:", w, "sum =", sum(w))
print("safety weights sum =", sf._COLLISION_WEIGHT + sf._FORCE_WEIGHT + sf._PROXIMITY_WEIGHT)

print()
print("=" * 72)
print("PROBE 6: _adversarial_significant vs observed_ok (monotone?)")
print("=" * 72)
for n in (29, 30, 100, 500):
    cells = []
    for frac in (0.0, 0.25, 0.5, 0.55, 0.59, 0.60, 0.61, 0.8, 1.0):
        ok = int(n * frac)
        cells.append((ok, round(sc._adversarial_significant(ok, n, 0.60, 0.05), 5)))
    print(f"n={n}")
    for ok, pv in cells:
        print(f"   ok={ok:>4}/{n:<4} rate={ok / n:.2f} p={pv:<10} "
              f"blocks={pv < 0.05}")
