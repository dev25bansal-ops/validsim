"""Compute exact anchors for the strengthened invariant tests."""
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.safety import compute_safety
from validsim.engine.stats import bootstrap_ci
from validsim.sim.runner import EpisodeResult

_i = [0]


def ep(ok, collisions=0, force=5.0, distance=1.5, lvl="full"):
    _i[0] += 1
    return EpisodeResult(f"e{_i[0]}", "t", _i[0], ok, collisions, force, distance,
                         None, 10.0, {}, lvl)


print("=== safety: cpe > 1 with other channels clean ===")
eps = [ep(False, collisions=3) for _ in range(10)]
r = compute_safety(eps)
print(f"  10 eps x 3 collisions, clean force/proximity:")
print(f"    cpe={r.collisions_per_episode} force_rate={r.max_force_exceeded_rate} "
      f"prox_rate={r.proximity_violation_rate} score={r.safety_score}")
print(f"    expected with cap: 100 - 1.0*0.5*100 = {100 - 50.0}")
print(f"    would be WITHOUT cap: 100 - 3.0*0.5*100 = {100 - 150.0} -> floored 0.0")

print()
print("=== safety: 2 collisions on 1 of 10 (cpe=0.2), force 1 of 10, prox 1 of 10 ===")
eps = [ep(True) for _ in range(7)]
eps.append(ep(False, collisions=2, force=5.0, distance=1.5))
eps.append(ep(True, force=80.0, distance=1.5))
eps.append(ep(True, force=5.0, distance=0.2))
r = compute_safety(eps)
print(f"    cpe={r.collisions_per_episode} f={r.max_force_exceeded_rate} "
      f"p={r.proximity_violation_rate} score={r.safety_score}")
print(f"    expected: 100 - (0.2*0.5 + 0.1*0.3 + 0.1*0.2)*100 = "
      f"{100 - (0.2 * 0.5 + 0.1 * 0.3 + 0.1 * 0.2) * 100}")

print()
print("=== exact bootstrap CI anchors (n_resamples=200, seed=42) ===")
sample = [1.0] * 70 + [0.0] * 30
for n in (100, 200, 400, 1000):
    vals = [sample[i % len(sample)] for i in range(n)]
    lo, hi, pt = bootstrap_ci(vals, n_resamples=200, seed=42)
    print(f"  n={n:>4} point={pt:.6f} low={lo:.6f} high={hi:.6f} width={hi - lo:.6f}")

print()
print("=== exact robustness anchors ===")
from validsim.engine import scorecard as sc
for good_a, total_a, good_b, total_b in ((9, 10, 5, 10), (10, 10, 0, 10), (8, 10, 6, 10)):
    eps = [ep(True) for _ in range(good_a)] + [ep(False) for _ in range(total_a - good_a)]
    eps2 = [ep(True) for _ in range(good_b)] + [ep(False) for _ in range(total_b - good_b)]
    # group A
    a = [ep(True) for _ in range(good_a)] + [ep(False) for _ in range(total_a - good_a)]
    a = []
    for i in range(total_a):
        a.append(ep(i < good_a, lvl="none"))
    b = []
    for i in range(total_b):
        b.append(ep(i < good_b, lvl="full"))
    print(f"  rates {good_a / total_a}/{good_b / total_b} -> "
          f"robustness={sc._robustness_score(a + b)}  groups="
          f"{sc._randomization_group_count(a + b)}")

print()
print("=== max attainable safety penalty (weights sum 1.0, rates capped at 1.0) ===")
print("  1.0*0.5 + 1.0*0.3 + 1.0*0.2 =", 1.0 * 0.5 + 1.0 * 0.3 + 1.0 * 0.2)
print("  -> penalty max = 100.0 exactly, so max(0.0, ...) can never bind")
