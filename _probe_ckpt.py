"""Verify the checkpoint now reaches the backend and changes the outcome."""
from __future__ import annotations

import statistics
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig  # noqa: E402
from validsim.engine.evaluation import evaluate  # noqa: E402
from validsim.engine.safety import compute_safety  # noqa: E402
from validsim.engine.scorecard import build_scorecard  # noqa: E402
from validsim.scenarios.generator import ScenarioGenerator  # noqa: E402
from validsim.sim import create_backend  # noqa: E402
from validsim.sim.runner import run_validation, stable_seed  # noqa: E402


def score_for(checkpoint: str | None, episodes: int = 400, task_id: str = "t1"):
    task = TaskConfig(
        task_id=task_id, robot=RobotSpec(name="franka_panda"),
        environment=EnvironmentSpec(name="kitchen"),
        episodes=episodes, adversarial_count=0, checkpoint_id=checkpoint,
    )
    seed = stable_seed(checkpoint or "", task_id)
    scen = ScenarioGenerator(seed=seed).generate(task_id, 0)
    eps = run_validation(task, create_backend(), scen, seed=seed)
    ev = evaluate(eps)
    return build_scorecard(
        run_id="vrun-00000001", checkpoint_id=checkpoint or "none", task=task,
        evaluation=ev, safety=compute_safety(eps), episodes=eps,
        created_at="2026-01-01T00:00:00+00:00",
    )


print("=== Six different CHECKPOINTS (same task) ===")
ck_scores = {}
for name in ("ckpt-alpha", "ckpt-beta", "ckpt-gamma", "ckpt-delta", "ckpt-eps", "ckpt-zeta"):
    c = score_for(name)
    ck_scores[name] = c.success_rate
    print(f"  {name:<14} success={c.success_rate:.4f}  composite={c.composite_score:6.2f}")

ck_vals = list(ck_scores.values())
print(f"\n  checkpoint spread = {max(ck_vals) - min(ck_vals):.4f}")

print("\n=== CONTROL: six arbitrary TASK names (meaningless input) ===")
t_scores = {}
for name in ("alpha", "beta", "gamma", "delta", "eps", "zeta"):
    c = score_for(None, task_id=name)
    t_scores[name] = c.success_rate
    print(f"  task {name:<10} success={c.success_rate:.4f}")

t_vals = list(t_scores.values())
print(f"\n  task-name spread  = {max(t_vals) - min(t_vals):.4f}")

print("\n=== DETERMINISM: same checkpoint twice ===")
a = score_for("ckpt-alpha")
b = score_for("ckpt-alpha")
print(f"  identical success : {a.success_rate == b.success_rate}")
print(f"  identical composite: {a.composite_score == b.composite_score}")

print("\n=== NO CHECKPOINT: unchanged base behaviour ===")
n = score_for(None)
print(f"  success={n.success_rate:.4f} composite={n.composite_score:.2f}")
