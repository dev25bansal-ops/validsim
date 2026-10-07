"""Probe: does MockIsaacBackend respond to scenario.params / category?"""
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import MockIsaacBackend

task = TaskConfig(
    task_id="pick-place", robot=RobotSpec(name="franka"),
    environment=EnvironmentSpec(name="kitchen"), episodes=10,
)
backend = MockIsaacBackend()

print("=== A. same difficulty, different params (lighting_change lux) ===")
for lux in (5, 50, 500, 3000):
    s = AdversarialScenario(
        id=f"adv-{lux}", category="lighting_change", name="L",
        params={"lux": lux, "color_temperature_k": 5000, "flicker_hz": 0.0},
        difficulty=0.5,
    )
    e = backend.run_episode(task, seed=42, randomization_level="full", scenario=s)
    print(f"  lux={lux:5d} -> success={e.success} force={e.max_contact_force_n} "
          f"dur={e.duration_s} fm={e.failure_mode} summary={e.joint_states_summary}")

print("=== B. same params, different category ===")
params = {"human_distance_m": 0.2, "human_speed_mps": 1.5, "crossing": True}
for cat in ("human_proximity", "sensor_degradation", "emergency_scenario", "temporal_pressure"):
    s = AdversarialScenario(id=f"a-{cat}", category=cat, name="X", params=dict(params), difficulty=0.5)
    e = backend.run_episode(task, seed=42, randomization_level="full", scenario=s)
    print(f"  {cat:26s} -> success={e.success} dist={e.min_human_distance_m} fm={e.failure_mode}")

print("=== C. failure_mode distribution vs category (human_proximity only) ===")
from collections import Counter
c = Counter()
for s in range(300):
    sc = AdversarialScenario(id="a", category="human_proximity", name="X",
                             params={"human_distance_m": 0.15, "human_speed_mps": 1.8, "crossing": True},
                             difficulty=0.95)
    e = backend.run_episode(task, seed=s, randomization_level="full", scenario=sc)
    if not e.success:
        c[e.failure_mode] += 1
print("  ", dict(c))

print("=== D. min_human_distance vs declared human_distance_m ===")
for d in (0.1, 0.5, 1.2):
    obs = [backend.run_episode(task, seed=s, randomization_level="full", scenario=AdversarialScenario(
        id="a", category="human_proximity", name="X",
        params={"human_distance_m": d, "human_speed_mps": 1.0, "crossing": False},
        difficulty=0.5)).min_human_distance_m for s in range(40)]
    print(f"  declared={d} -> observed min={min(obs)} max={max(obs)} mean={sum(obs)/len(obs):.3f}")

print("=== E. human_proximity contract: is min_human_distance always reported? ===")
sc = AdversarialScenario(id="a", category="sensor_degradation", name="X",
                         params={"camera_dropout_rate": 0.5}, difficulty=0.5)
vals = {backend.run_episode(task, seed=s, randomization_level="full", scenario=sc).min_human_distance_m is None
        for s in range(100)}
print("  non-human scenario, None observed:", vals)
