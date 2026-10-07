"""Verify the three failing assertions are MY bugs, not product bugs."""
import math
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine import scorecard as sm

FLOOR, ALPHA = 0.60, 0.05


def cdf(k, n, p):
    return math.fsum(
        math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                 + i * math.log(p) + (n - i) * math.log1p(-p))
        for i in range(k + 1)
    )


print("Q1: is {k : p(k) < alpha} a PREFIX (True then False)?")
for n in (30, 100, 500):
    verdicts = [sm._adversarial_significant(k, n, FLOOR, ALPHA)
                for k in range(n + 1)]
    first_false = next((i for i, v in enumerate(verdicts) if not v), None)
    print(f"  n={n}: first_true=0? {verdicts[0]}, first_false={first_false}")
    ok = all(verdicts[k] for k in range(first_false)) and \
        all(not verdicts[k] for k in range(first_false, n + 1))
    print(f"     prefix property holds: {ok}")
    print(f"     k at first_false: rate={first_false / n:.3f} "
          f"p={cdf(first_false, n, FLOOR):.4f}")
print("  => my test asserted the OPPOSITE (True for all k >= first_true).")
print("     Since p(0)~0, first_true is always 0, so the loop started at 0")
print("     and demanded every k block. The correct property is a prefix of")
print("     True, i.e. True for k < first_false.")

print()
print("Q2: find a real pivot where 0.005 < p < 0.09")
for n in (100, 200):
    for k in range(n):
        p = cdf(k, n, FLOOR)
        if 0.005 < p < 0.09:
            print(f"  n={n} k={k} rate={k / n:.3f} p={p:.6f}  <-- pivot")
            break

print()
print("Q3: n=1000, 550/1000 = 55% -- should it block?")
k, n = 550, 1000
p = cdf(k, n, FLOOR)
print(f"  rate={k / n:.3f}  floor={FLOOR}  rate < floor: {k / n < FLOOR}")
print(f"  p={p:.3e}  p < alpha: {p < ALPHA}")
print(f"  gate says: {sm._adversarial_significant(k, n, FLOOR, ALPHA)}")
print("  => BOTH conditions hold, so blocking is CORRECT. At n=1000 the")
print("     binomial test is sharp enough to call 55% significantly below 60%.")
print("     My test premise ('the rate guard saves this') was wrong: with")
print("     large n, p < alpha already implies rate < floor, so the rate")
print("     guard is near-redundant rather than protective.")

print()
print("Q4: is the rate guard EVER the binding constraint?")
print("  (i.e. p<alpha true but rate>=floor, so only the guard prevents a block)")
found = 0
for n in range(1, 400):
    for k in range(n + 1):
        if cdf(k, n, FLOOR) < ALPHA and k / n >= FLOOR:
            found += 1
            if found <= 5:
                print(f"     n={n} k={k} rate={k / n:.4f} p={cdf(k, n, FLOOR):.2e}")
print(f"  total such (n,k) pairs in 1..400: {found}")
print("  => if 0, the guard is unreachable dead code; if >0, it binds.")
