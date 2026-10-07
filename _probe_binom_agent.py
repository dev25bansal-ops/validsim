"""Verify _adversarial_significant against a hand-computed exact binomial CDF."""
import math
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine import scorecard as sc

print("scorecard file:", sc.__file__)
import inspect
print(inspect.getsource(sc._adversarial_significant))

FLOOR, ALPHA = 0.60, 0.05


def exact_cdf(k, n, p):
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


print(f"{'n':>5}{'ok':>5}{'rate':>8}{'impl p':>14}{'hand CDF':>14}{'impl':>8}{'hand':>8}")
for n in (30, 100, 500):
    for frac in (0.0, 0.3, 0.5, 0.55, 0.59, 0.60, 0.61, 0.8, 1.0):
        ok = int(n * frac)
        got = sc._adversarial_significant(ok, n, FLOOR, ALPHA)
        hand_p = exact_cdf(ok, n, FLOOR)
        hand = hand_p < ALPHA
        flag = "" if got == hand else "   <<< MISMATCH"
        print(f"{n:>5}{ok:>5}{ok / n:>8.2f}{'':>14}{hand_p:>14.4f}"
              f"{str(got):>8}{str(hand):>8}{flag}")

print()
print("=== the operator-meaning check: does rate >= floor ever block? ===")
violations = []
for n in (30, 50, 100, 200, 500, 1000):
    for ok in range(n + 1):
        if ok / n >= FLOOR and sc._adversarial_significant(ok, n, FLOOR, ALPHA):
            violations.append((n, ok, ok / n))
print(f"pairs at/above the {FLOOR:.0%} floor that block: {len(violations)}")
for n, ok, r in violations[:12]:
    print(f"   n={n} ok={ok} rate={r:.3f}  -> BLOCKS but is AT/ABOVE the floor")
if len(violations) > 12:
    print(f"   ... and {len(violations) - 12} more")
