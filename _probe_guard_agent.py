"""Is the `adversarial_rate < _ADVERSARIAL_SUCCESS_FLOOR` guard reachable at the
shipped alpha=0.05?  If no (n,k) with rate >= floor is ever significant, the
guard is dead code and the mutation is EQUIVALENT -- not a test gap.
"""
import math
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine import scorecard as sm

FLOOR, ALPHA = 0.60, 0.05
print(f"floor={FLOOR} alpha={ALPHA}")

# The lower-tail probability decreases in k, so the largest significant k is
# the only candidate that needs checking per n: if it is < ceil(FLOOR*n) then
# no at-or-above-floor count is ever significant.
violations = []
max_n = 5000
for n in range(1, max_n + 1):
    # binary search the largest k with P(X<=k) < alpha
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        p = math.fsum(
            math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                     + i * math.log(FLOOR) + (n - i) * math.log1p(-FLOOR))
            for i in range(mid + 1)
        )
        if p < ALPHA:
            lo = mid
        else:
            hi = mid - 1
    largest_significant = lo
    floor_min = math.ceil(FLOOR * n)
    if largest_significant >= floor_min:
        violations.append((n, largest_significant, floor_min))

print(f"n swept: 1..{max_n}")
print(f"(n, largest_significant_k, floor_min_k) where a rate >= floor is significant: "
      f"{len(violations)}")
for row in violations[:10]:
    print("   ", row)

if not violations:
    print()
    print("CONCLUSION: the explicit `rate < floor` guard is UNREACHABLE at the")
    print("shipped alpha=0.05. `p < alpha` already implies `rate < floor` for")
    print("every (n, k) with n <= 5000, so deleting the guard is an")
    print("EQUIVALENT mutation -- no test can distinguish them, and none")
    print("should pretend to.")

print()
print("At a more permissive alpha the guard DOES bind (so it is not")
print("decorative, it is defence-in-depth for a retuned alpha):")
for a in (0.10, 0.20, 0.30, 0.50):
    n = 1000
    hits = [k for k in range(math.ceil(FLOOR * n), n + 1)
            if sm._adversarial_significant(k, n, FLOOR, a)]
    print(f"   alpha={a}: {len(hits)} at/above-floor rates are significant "
          f"(k={hits[0] if hits else '-'}..{hits[-1] if hits else '-'})")
