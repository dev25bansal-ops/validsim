"""Probe 2: does C1 dilution actually exist? Test the real scenario."""
from __future__ import annotations

import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.safety import compute_safety  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402


def ep(i: int, *, human: float | None = None) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"e{i}", task_id="t", seed=i, success=True,
        collision_count=0, max_contact_force_n=0.0, min_human_distance_m=human,
        randomization_level="full",
    )


print("Scenario: 1 human episode with a REAL violation, 99 with no human present")
eps = [ep(0, human=0.2)] + [ep(i) for i in range(1, 100)]
r = compute_safety(eps)
print(f"  min_human_proximity_m = {r.min_human_proximity_m}  <- a real violation")
print(f"  proximity_violation_rate = {r.proximity_violation_rate}")
print(f"  safety_score = {r.safety_score}")

print()
print("Same single violation, but all 100 episodes have a human present:")
eps2 = [ep(0, human=0.2)] + [ep(i, human=5.0) for i in range(1, 100)]
r2 = compute_safety(eps2)
print(f"  proximity_violation_rate = {r2.proximity_violation_rate}")
print(f"  safety_score = {r2.safety_score}")

print()
print(f"Understatement factor = {r2.proximity_violation_rate / r.proximity_violation_rate:.0f}x")
