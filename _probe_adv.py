"""Reproduce: composite is blind to nominal<->adversarial composition."""
from __future__ import annotations

import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig  # noqa: E402
from validsim.engine.evaluation import evaluate  # noqa: E402
from validsim.engine.safety import SafetyResult  # noqa: E402
from validsim.engine.scorecard import build_scorecard  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402


def run(nominal_total: int, nominal_ok: int, adv_total: int, adv_ok: int):
    """Build a run with an explicit nominal/adversarial split."""
    eps = []
    for i in range(nominal_total):
        eps.append(EpisodeResult(
            episode_id=f"n{i}", task_id="t", seed=i,
            success=i < nominal_ok, duration_s=5.0,
            randomization_level="full",
            failure_mode=None if i < nominal_ok else "collision",
        ))
    for j in range(adv_total):
        eps.append(EpisodeResult(
            episode_id=f"a{j}", task_id="t", seed=1000 + j,
            success=j < adv_ok, duration_s=7.0,
            randomization_level="full",
            failure_mode=None if j < adv_ok else "collision",
        ))
    return eps


TASK = TaskConfig(
    task_id="t", robot=RobotSpec(name="r"), environment=EnvironmentSpec(name="e"),
    episodes=100,
)
SAFETY = SafetyResult(0.0, 0.0, None, 0.0, 100.0)


def score(eps):
    return build_scorecard(
        run_id="vrun-00000001", checkpoint_id="c", task=TASK,
        evaluation=evaluate(eps), safety=SAFETY, episodes=eps,
        created_at="2026-01-01T00:00:00+00:00",
    )


print("Two runs, SAME pooled success rate, opposite profiles:\n")
# 80 nominal @ 100% + 20 adversarial @ 0%  -> pooled 0.80
a = run(80, 80, 20, 0)
# 80 nominal @ 68.75% + 20 adversarial @ 100% -> pooled 0.80
b = run(80, 55, 20, 20)

ca, cb = score(a), score(b)
print(f"  A: nominal 80/80 (1.000), adversarial 0/20 (0.000) -> success={ca.success_rate:.4f} composite={ca.composite_score}")
print(f"  B: nominal 55/80 (0.688), adversarial 20/20 (1.000) -> success={cb.success_rate:.4f} composite={cb.composite_score}")
print(f"\n  identical success rate: {ca.success_rate == cb.success_rate}")
print(f"  identical composite   : {ca.composite_score == cb.composite_score}")
print(f"  A fails EVERY adversarial episode, B fails NONE. Gate cannot tell them apart.\n")

print("Control — a genuine 10pp nominal drop must move the composite:")
c = run(80, 55, 20, 10)
cc = score(c)
print(f"  C: nominal 55/80 (0.688), adversarial 10/20 (0.500) -> success={cc.success_rate:.4f} composite={cc.composite_score}")
print(f"  delta vs A = {cc.composite_score - ca.composite_score:+.2f} (non-zero proves the metric responds)")

print("\nWorst case: every ADVERSARIAL episode fails, nominal perfect")
d = run(500, 500, 100, 0)
cd = score(d)
print(f"  composite={cd.composite_score} decision={cd.deploy_decision}  (100 adversarial failures)")
