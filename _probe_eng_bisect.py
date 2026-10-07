"""Bisect: which statement drops the anomaly for a huge-but-valid int total?"""
import math
import statistics

import validsim.engine.anomaly as A


def r(rid, tax, total=100):
    return {"run_id": rid, "total_episodes": total, "failure_taxonomy": tax}


h = [r(f"b{i}", {"m": 1}, total=10 ** 12) for i in range(3)]
h.append(r("cur", {"m": 500}, total=10 ** 12))

cur = h[-1]
baseline = h[:-1]
current_total = A._total(cur)
current_tax = A._taxonomy(cur)
print("current_total =", current_total, type(current_total).__name__)
print("current_tax   =", current_tax)

for mode in sorted(current_tax):
    observed = current_tax[mode] / current_total
    rates = A._baseline_rates(baseline, mode)
    print("\nmode:", mode)
    print("  observed            =", repr(observed))
    print("  len(rates)          =", len(rates))
    print("  rates               =", rates)
    print("  expected = fmean    =", repr(statistics.fmean(rates)))
    print("  spread   = pstdev   =", repr(statistics.pstdev(rates)))
    print("  se_mean  = boot_se  =", repr(A._bootstrap_se(rates)))
    bv = max(0.0, statistics.fmean(rates) * (1.0 - statistics.fmean(rates))) / current_total
    print("  binomial_var        =", repr(bv))
    sig = math.sqrt(0.0 * 0.0 + 0.0 * 0.0 + bv)
    sig2 = math.sqrt(statistics.pstdev(rates) ** 2 + A._bootstrap_se(rates) ** 2 + bv)
    print("  sigma (correct)     =", repr(sig2))
    print("  z_score             =", repr((observed - statistics.fmean(rates)) / sig2))
    print("  z >= 2.0 ?          =", (observed - statistics.fmean(rates)) / sig2 >= 2.0)
