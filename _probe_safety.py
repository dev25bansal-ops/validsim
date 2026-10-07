"""Throwaway probe: verify C1 + C2 safety findings independently."""
from __future__ import annotations

import math
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.engine.safety import compute_safety  # noqa: E402
from validsim.sim.runner import EpisodeResult  # noqa: E402


def ep(i: int, *, force: float = 0.0, human: float | None = None) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"e{i}", task_id="t", seed=i, success=True,
        collision_count=0, max_contact_force_n=force, min_human_distance_m=human,
        randomization_level="full",
    )


print("=== C2: NaN force observable ===")
r = compute_safety([ep(i, force=float("nan")) for i in range(10)])
print(f"  safety_score = {r.safety_score}  (NaN > limit is False -> no violation)")
print(f"  max_force_exceeded_rate = {r.max_force_exceeded_rate}")
print(f"  -> fail-open? {r.safety_score == 100.0}")

print()
print("=== C2b: Inf force observable ===")
r2 = compute_safety([ep(i, force=math.inf) for i in range(10)])
print(f"  safety_score = {r2.safety_score}  rate = {r2.max_force_exceeded_rate}")

print()
print("=== C1: human-proximity dilution ===")
r3 = compute_safety([ep(0, human=0.2), ep(1), ep(2), ep(3)])
print(f"  min_human_proximity_m = {r3.min_human_proximity_m}")
print(f"  proximity_violation_rate = {r3.proximity_violation_rate}")
print(f"  safety_score = {r3.safety_score}")

print()
print("=== Control: all episodes have a human ===")
r4 = compute_safety([ep(0, human=0.2), ep(1, human=5.0), ep(2, human=5.0), ep(3, human=5.0)])
print(f"  proximity_violation_rate = {r4.proximity_violation_rate}  safety = {r4.safety_score}")
