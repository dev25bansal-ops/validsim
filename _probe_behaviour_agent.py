"""Probe 2: behaviour-level probes for high-coverage-wrong-metric hunting."""
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test
from validsim.engine.anomaly import detect_anomalies
from validsim.engine import scorecard as sc

print("=" * 74)
print("A. bootstrap_ci on a degenerate (all-identical) sample")
print("=" * 74)
for bits in ([1.0] * 30, [0.0] * 30, [1.0] * 3, [1.0] * 200, [1.0] * 1000):
    low, high, point = bootstrap_ci(bits, n_resamples=500, seed=42)
    print(f"  n={len(bits):>4} -> CI=[{low:.6f},{high:.6f}] width={high - low:.6f}")

print()
print("=" * 74)
print("B. is a zero-width CI on 30/30 successes defensible?")
print("=" * 74)
print("  30/30 successes: a 95% Clopper-Pearson lower bound is ~0.883.")
print("  The bootstrap reports [1.0, 1.0] -> claims the true rate is exactly 1.")
print("  This is the SAME fail-open class as the fixed safety NaN bug: a")
print("  degenerate sample yields a confident-looking metric that is wrong.")
print("  Is the CI ever consumed as a decision input? ->", end=" ")
import subprocess
print(subprocess.run(
    ["git", "grep", "-n", "confidence_interval"],
    capture_output=True, text=True, cwd=r"d:\SIM-TO-REAL").stdout or "(none)")

print()
print("=" * 74)
print("C. CI width monotonic in n (more evidence -> narrower CI)")
print("=" * 74)
prev = None
for n in (5, 10, 20, 50, 100, 200, 500, 1000):
    bits = [1.0] * 70 + [0.0] * 30
    vals = [bits[i % len(bits)] for i in range(n)]
    low, high, point = bootstrap_ci(vals, n_resamples=200, seed=42)
    w = high - low
    flag = ""
    if prev is not None and w > prev + 1e-9:
        flag = "  <<< WIDER than a smaller sample"
    print(f"  n={n:>5} point={point:.4f} CI=[{low:.4f},{high:.4f}] width={w:.4f}{flag}")
    prev = w

print()
print("=" * 74)
print("D. detect_anomalies: brand-new failure mode vs never-seen baseline")
print("=" * 74)
hist = [
    {"run_id": f"r{i}", "total_episodes": 100, "failure_taxonomy": {"timeout": 2}}
    for i in range(5)
]
hist.append({"run_id": "cur", "total_episodes": 100,
             "failure_taxonomy": {"timeout": 2, "grasp_failure": 50}})
out = detect_anomalies(hist)
print("  new mode 0.00 -> 0.50, anomalies =", out)
print("  FLAGGED:", bool(out))
print("  Reason: baseline rate 0 with zero spread -> sigma == 0 -> `continue`,")
print("  so the single most severe failure mode is never reported.")

print()
print("=" * 74)
print("E. detect_anomalies: mode present in current but in only SOME baselines")
print("=" * 74)
hist = [
    {"run_id": f"r{i}", "total_episodes": 100, "failure_taxonomy": {"grasp_failure": 1}}
    for i in range(5)
]
hist.append({"run_id": "cur", "total_episodes": 100,
             "failure_taxonomy": {"grasp_failure": 50}})
out = detect_anomalies(hist)
print("  baseline rate 0.01 -> 0.50, anomalies =", out)
print("  FLAGGED:", bool(out), "severity:", out[0].severity if out else None)

print()
print("=" * 74)
print("F. composite monotonicity: can more evidence LOWER the score?")
print("=" * 74)
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.sim.runner import EpisodeResult


def ep(i, ok, lvl="full"):
    return EpisodeResult(f"ep{i}", "t", i, ok, 0, 5.0, 1.5, None, 5.0, {}, lvl)


task = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                  environment=EnvironmentSpec(name="e"), episodes=10)
for good, total in ((9, 10), (8, 10), (10, 10)):
    eps = [ep(i, i < good) for i in range(total)]
    card = sc.build_scorecard("v", "c", task, evaluate(eps),
                              compute_safety(eps), eps)
    print(f"  {good}/{total} successes -> composite={card.composite_score:>6} "
          f"success_rate={card.success_rate} decision={card.deploy_decision}")

print()
print("  Under-delivered run (worker returned 1 of 10 requested):")
eps = [ep(0, True)]
card = sc.build_scorecard("v", "c", task, evaluate(eps), compute_safety(eps), eps)
print(f"    1/10 delivered -> composite={card.composite_score} "
      f"decision={card.deploy_decision} reasons={card.block_reasons}")
print("    ^ a perfect 1-of-1 run outscores / equals a genuine 9/10 run:",
      card.composite_score >= 86.0)
