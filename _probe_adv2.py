"""Exact counterexample: equal pooled success, opposite nominal/adversarial mix."""
from __future__ import annotations

import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig  # noqa: E402
from validsim.engine.evaluation import evaluate  # noqa: E402
from validsim.engine.safety import SafetyResult  # noqa: E402
from validsim.engine.scorecard import build_scorecard  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402


def run(nominal_total, nominal_ok, adv_total, adv_ok):
    eps = []
    for i in range(nominal_total):
        ok = i < nominal_ok
        eps.append(EpisodeResult(
            episode_id=f"n{i}", task_id="t", seed=i, success=ok, duration_s=5.0,
            randomization_level="full", failure_mode=None if ok else "collision"))
    for j in range(adv_total):
        ok = j < adv_ok
        eps.append(EpisodeResult(
            episode_id=f"a{j}", task_id="t", seed=1000 + j, success=ok, duration_s=7.0,
            randomization_level="full", failure_mode=None if ok else "collision"))
    return eps


TASK = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                  environment=EnvironmentSpec(name="e"), episodes=100)
SAFETY = SafetyResult(0.0, 0.0, None, 0.0, 100.0)


def score(eps):
    return build_scorecard(run_id="vrun-00000001", checkpoint_id="c", task=TASK,
                           evaluation=evaluate(eps), safety=SAFETY, episodes=eps,
                           created_at="2026-01-01T00:00:00+00:00")


# 4 scenarios, 100 episodes each = 400 total.
# A: nominal 0.900, adversarial 0.000  -> pooled (360+0)/400 = 0.900
# B: nominal 0.800, adversarial 1.000  -> pooled (320+100)/400 = 1.05 > 1, impossible.
# Use a bigger adversarial share:
# A: 400 nominal @ 0.90 (360 ok) + 100 adv @ 0.00 (0 ok)  -> 360/500 = 0.720
# B: 400 nominal @ 0.65 (260 ok) + 100 adv @ 1.00 (100 ok) -> 360/500 = 0.720
A = run(400, 360, 100, 0)
B = run(400, 260, 100, 100)
ca, cb = score(A), score(B)

print("Run A: nominal 360/400 = 0.900 | adversarial   0/100 = 0.000")
print(f"       pooled success = {ca.success_rate:.4f}  composite = {ca.composite_score}  {ca.deploy_decision}")
print()
print("Run B: nominal 260/400 = 0.650 | adversarial 100/100 = 1.000")
print(f"       pooled success = {cb.success_rate:.4f}  composite = {cb.composite_score}  {cb.deploy_decision}")
print()
print(f"  IDENTICAL pooled success : {ca.success_rate == cb.success_rate}")
print(f"  IDENTICAL composite      : {ca.composite_score == cb.composite_score}")
print()
print("  A fails EVERY adversarial episode. B fails NONE.")
print("  The gate scores them the same -> it is blind to which segment failed.")

print("\nControl: a real 10pp nominal drop must move the composite")
C = run(400, 300, 100, 50)  # 0.75 nominal, 0.50 adversarial
cc = score(C)
print(f"  C composite={cc.composite_score}  delta vs A = {cc.composite_score - ca.composite_score:+.2f}")
print("  (non-zero proves the metric is responsive; the 0.00 above is a real blind spot)")
