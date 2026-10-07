"""Verify the adversarial gate: blocks real failure, spares healthy runs."""
from __future__ import annotations

import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig  # noqa: E402
from validsim.engine.evaluation import evaluate  # noqa: E402
from validsim.engine.safety import SafetyResult  # noqa: E402
from validsim.engine.scorecard import build_scorecard  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402

SAFETY = SafetyResult(0.0, 0.0, None, 0.0, 100.0)


def build(nominal_total, nominal_ok, adv_total, adv_ok, episodes_field):
    eps = [
        EpisodeResult(episode_id=f"n{i}", task_id="t", seed=i, success=i < nominal_ok,
                      duration_s=5.0, randomization_level="full",
                      failure_mode=None if i < nominal_ok else "collision")
        for i in range(nominal_total)
    ]
    eps += [
        EpisodeResult(episode_id=f"a{j}", task_id="t", seed=1000 + j, success=j < adv_ok,
                      duration_s=7.0, randomization_level="full",
                      failure_mode=None if j < adv_ok else "collision")
        for j in range(adv_total)
    ]
    task = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                      environment=EnvironmentSpec(name="e"), episodes=episodes_field)
    return build_scorecard(
        run_id="vrun-00000001", checkpoint_id="c", task=task,
        evaluation=evaluate(eps), safety=SAFETY, episodes=eps,
        created_at="2026-01-01T00:00:00+00:00")


print("=== MUST BLOCK: adversarial genuinely failing ===")
cases_block = [
    ("nominal perfect, adv 0/100", 400, 400, 100, 0),
    ("nominal perfect, adv 10/100", 400, 400, 100, 10),
    ("nominal 90%,     adv 20/100", 400, 360, 100, 20),
]
for label, nt, nok, at, aok in cases_block:
    c = build(nt, nok, at, aok, nt)
    print(f"  {label:<34} adv_rate={c.adversarial_success_rate:.3f} -> {c.deploy_decision}")

print("\n=== MUST PASS: adversarial healthy ===")
cases_pass = [
    ("nominal perfect, adv 100/100", 400, 400, 100, 100),
    ("nominal perfect, adv 85/100", 400, 400, 100, 85),
    ("nominal perfect, adv 70/100", 400, 400, 100, 70),
    ("nominal 90%,     adv 90/100", 400, 360, 100, 90),
]
for label, nt, nok, at, aok in cases_pass:
    c = build(nt, nok, at, aok, nt)
    print(f"  {label:<34} adv_rate={c.adversarial_success_rate:.3f} -> {c.deploy_decision}")

print("\n=== SMALL SAMPLES: reported, never gated (noise) ===")
for at, aok in ((4, 2), (12, 7), (20, 12)):
    c = build(400, 400, at, aok, 400)
    print(f"  adv {aok}/{at} = {c.adversarial_success_rate:.3f} -> {c.deploy_decision} "
          f"(gated={bool([r for r in c.block_reasons if 'adversarial' in r])})")

print("\n=== NO ADVERSARIAL EPISODES: unaffected ===")
c = build(400, 400, 0, 0, 400)
print(f"  nominal-only -> {c.deploy_decision}  adv_rate={c.adversarial_success_rate} "
      f"adv_n={c.adversarial_episode_count}")
