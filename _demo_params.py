"""Before/after demonstration + the measurements other tests pin.

Run:  cmd /c "cd /d d:\\SIM-TO-REAL && python _demo_params.py > _demo_out.txt 2>&1"
"""
import itertools
import random
import statistics
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import ADVERSARIAL_CATEGORIES, AdversarialScenario
from validsim.sim.runner import MockIsaacBackend, _checkpoint_offset

mock = MockIsaacBackend()
TASK = TaskConfig(
    task_id="pick-place",
    robot=RobotSpec(name="franka_panda", urdf_path="robots/franka.urdf", dof=7),
    environment=EnvironmentSpec(name="kitchen", scene_usd="scenes/kitchen.usda"),
    episodes=5, randomization="full", checkpoint_id="ckpt-v41",
)


def _params_for(category, *, easy: bool):
    """A benign / a nasty parameter set for one category (same difficulty)."""
    table = {
        "lighting_change": ({"lux": 3000, "color_temperature_k": 4000, "flicker_hz": 0.0},
                           {"lux": 5, "color_temperature_k": 7500, "flicker_hz": 60.0}),
        "object_property_change": ({"mass_kg": 0.5, "friction_coefficient": 0.8, "restitution": 0.0},
                                   {"mass_kg": 5.0, "friction_coefficient": 0.05, "restitution": 0.95}),
        "human_proximity": ({"human_distance_m": 1.2, "human_speed_mps": 0.3, "crossing": False},
                            {"human_distance_m": 0.1, "human_speed_mps": 1.8, "crossing": True}),
        "unexpected_obstacle": ({"spawn_offset": {"x": 0.5, "y": 0.5, "z": 1.0},
                                 "obstacle_radius_m": 0.02, "appears_at_step": 200},
                                {"spawn_offset": {"x": 0.0, "y": 0.0, "z": 0.5},
                                 "obstacle_radius_m": 0.3, "appears_at_step": 1}),
        "sensor_degradation": ({"camera_dropout_rate": 0.0, "depth_noise_mm": 0.0, "latency_ms": 0},
                               {"camera_dropout_rate": 0.6, "depth_noise_mm": 80.0, "latency_ms": 250}),
        "mechanical_variation": ({"joint_friction_scale": 1.0, "backlash_rad": 0.0, "mass_scale": 1.0},
                                 {"joint_friction_scale": 2.0, "backlash_rad": 0.05, "mass_scale": 1.3}),
        "environmental_disturbance": ({"wind_mps": 0.0, "table_acceleration_mps2": 0.0},
                                      {"wind_mps": 6.0, "table_acceleration_mps2": 3.0}),
        "task_ambiguity": ({"distractor_objects": 1, "goal_description_noise": 0.0, "target_swapped": False},
                           {"distractor_objects": 6, "goal_description_noise": 0.5, "target_swapped": True}),
        "multi_robot_interference": ({"neighbor_robot_count": 1, "neighbor_speed_mps": 0.2,
                                      "shared_workspace": False},
                                     {"neighbor_robot_count": 3, "neighbor_speed_mps": 1.5,
                                      "shared_workspace": True}),
        "emergency_scenario": ({"e_stop_at_step": 300, "fire_alarm": False, "human_down": False},
                               {"e_stop_at_step": 10, "fire_alarm": True, "human_down": True}),
        "adversarial_input": ({"perturbation_eps": 0.001, "attack_surface": "language",
                               "injection_rate": 0.05},
                              {"perturbation_eps": 0.08, "attack_surface": "vision",
                               "injection_rate": 0.5}),
        "temporal_pressure": ({"deadline_scale": 0.9, "stream_speedup": 1.1},
                              {"deadline_scale": 0.3, "stream_speedup": 3.0}),
    }
    benign, nasty = table[category]
    return dict(benign) if easy else dict(nasty)


def scen(**over):
    params = over.pop("params", {"human_distance_m": 0.42, "human_speed_mps": 1.1,
                                 "crossing": True})
    base = dict(id="adv-0", category="human_proximity", name="adv-0",
                params=params, difficulty=0.6)
    base.update(over)
    return AdversarialScenario(**base)


print("=" * 74)
print("A. PARAM/CATEGORY SENSITIVITY -- bit-identical episodes?")
print("=" * 74)
for cat in ADVERSARIAL_CATEGORIES:
    lo = AdversarialScenario(id="lo", category=cat, name="lo",
                             params=_params_for(cat, easy=True), difficulty=0.6)
    hi = AdversarialScenario(id="hi", category=cat, name="hi",
                             params=_params_for(cat, easy=False), difficulty=0.6)
    same = sum(
        mock.run_episode(TASK, seed=s, randomization_level="full", scenario=lo).__dict__
        == mock.run_episode(TASK, seed=s, randomization_level="full", scenario=hi).__dict__
        for s in range(200)
    )
    p_lo = mock.success_probability("full", lo, "ckpt-v41")
    p_hi = mock.success_probability("full", hi, "ckpt-v41")
    print(f"  {cat:30} p_easy={p_lo:.4f} p_hard={p_hi:.4f} "
          f"identical_episodes={same}/200")

print()
print("  category-only difference (same params, 2 categories):")
shared = {"lux": 300.0, "color_temperature_k": 4000, "flicker_hz": 1.0}
a = AdversarialScenario(id="a", category="lighting_change", name="a",
                        params=dict(shared), difficulty=0.6)
b = AdversarialScenario(id="a", category="sensor_degradation", name="a",
                        params=dict(shared), difficulty=0.6)
modes = []
for s in range(400):
    for sc, acc in ((a, modes), (b, modes)):
        r = mock.run_episode(TASK, seed=s, randomization_level="full", scenario=sc)
        acc.append(r.failure_mode)
n = 400
from collections import Counter
print("   lighting_change top modes :",
      Counter(m for m in modes[:n] if m).most_common(3))
print("   sensor_degradation modes  :",
      Counter(m for m in modes[n:] if m).most_common(3))

print()
print("=" * 74)
print("B. dict-order independence (sorted key iteration)")
print("=" * 74)
fwd = {"human_distance_m": 0.42, "human_speed_mps": 1.1, "crossing": True}
rev = {k: fwd[k] for k in reversed(list(fwd))}
s1 = AdversarialScenario(id="x", category="human_proximity", name="x", params=fwd, difficulty=0.6)
s2 = AdversarialScenario(id="x", category="human_proximity", name="x", params=rev, difficulty=0.6)
print("  reversed-order scenario gives identical episodes:",
      all(mock.run_episode(TASK, seed=s, randomization_level="full", scenario=s1).__dict__
          == mock.run_episode(TASK, seed=s, randomization_level="full", scenario=s2).__dict__
          for s in range(200)))

print()
print("=" * 74)
print("C. determinism: same (checkpoint, seed, scenario) -> same episode")
print("=" * 74)
ok = all(
    mock.run_episode(TASK, seed=s, randomization_level="full", scenario=sc).__dict__
    == MockIsaacBackend().run_episode(TASK, seed=s, randomization_level="full",
                                      scenario=sc).__dict__
    for s in range(60)
    for sc in [None] + [scen(id=f"s{i}") for i in range(12)]
)
print("  fresh-backend replay identical over 720 episodes:", ok)

print()
print("=" * 74)
print("D. the four RED tests' numbers")
print("=" * 74)
off200 = {mock.success_probability("full", None, f"ckpt-{i}") for i in range(200)}
print("  T1 distinct probabilities over ckpt-0..199 :", len(off200))
obs = {round(mock.success_probability("full", None, f"ckpt-{i}"), 10) for i in range(500)}
base = 0.9 - 0.20
want = {round(base + s, 10) for s in (-0.12, -0.06, 0.0, 0.06, 0.12)}
print("  T2 observed == base+five steps :", obs == want, sorted(obs))
differing = 0
pairs = (("ckpt-v41", "ckpt-v42"), ("ckpt-v43", "ckpt-v44"),
         ("ckpt-v45", "ckpt-v46"), ("ckpt-v47", "ckpt-v48"))
for seed in range(200):
    for l, r in pairs:
        a = mock.run_episode(TASK.model_copy(update={"checkpoint_id": l}), seed=seed,
                             randomization_level="full")
        b = mock.run_episode(TASK.model_copy(update={"checkpoint_id": r}), seed=seed,
                             randomization_level="full")
        differing += a != b
print(f"  T3 differing {differing}/{200 * len(pairs)} = {differing / 800:.3f} (need > 0.01)")
easy = mock.success_probability("full", scen(id="a", difficulty=0.0), "ckpt-v41")
hard = mock.success_probability("full", scen(id="b", difficulty=1.0), "ckpt-v41")
print(f"  T4 easy={easy:.4f} hard={hard:.4f} gap={easy - hard:.4f} (need 0.30 < gap <= 0.5)")
calm = AdversarialScenario(id="c", category="lighting_change", name="c",
                           params={"lux": 3000.0}, difficulty=0.6)
harsh = AdversarialScenario(id="h", category="lighting_change", name="h",
                            params={"lux": 5.0}, difficulty=0.6)
pc = mock.success_probability("full", calm, "ckpt-v41")
ph = mock.success_probability("full", harsh, "ckpt-v41")
print(f"  T4 calm={pc:.4f} > harsh={ph:.4f} :", pc > ph)
print("  ckpt-v41 tier:", _checkpoint_offset("ckpt-v41"))

print()
print("=" * 74)
print("E. runner.py's own bands")
print("=" * 74)
for level in ("none", "partial", "full"):
    rate = sum(mock.run_episode(TASK.model_copy(update={"randomization": level}), seed=1000 + i,
                                randomization_level=level).success for i in range(4000)) / 4000
    print(f"  level {level:8} rate={rate:.4f}")
task60 = TASK.model_copy(update={"episodes": 600, "adversarial_count": 120})
from validsim.scenarios.generator import ScenarioGenerator
from validsim.sim.runner import run_validation
scen_list = ScenarioGenerator(seed=42).generate(task60.task_id, 120)
res = run_validation(task60, MockIsaacBackend(), scen_list, seed=42)
nom = res[:600]
adv = res[600:]
nr = sum(r.success for r in nom) / 600
ar = sum(r.success for r in adv) / 120
print(f"  nominal={nr:.4f} adversarial={ar:.4f} gap={nr - ar:.4f} (need > 0.1)")
base_rate = sum(MockIsaacBackend(base_success_rate=0.9).run_episode(
    TASK.model_copy(update={"checkpoint_id": None}), seed=s,
    randomization_level="none").success for s in range(800)) / 800
print(f"  success_rate_near_base (need 0.83<{base_rate:.4f}<0.96)")
print("  all durations/forces positive:",
      all(r.duration_s > 0 and r.max_contact_force_n > 0 for r in res))

print()
print("=" * 74)
print("F. shadow-gate mock rates at the pinned checkpoints")
print("=" * 74)


def mock_rate(ckpt, level="full", episodes=20, scen_n=0, difficulty=1.0, seed=42):
    task = TASK.model_copy(update={"checkpoint_id": ckpt, "randomization": level})
    rs = [mock.run_episode(task, seed=seed + i, randomization_level=level) for i in range(episodes)]
    for j in range(scen_n):
        cat = ADVERSARIAL_CATEGORIES[j % 12]
        sc = AdversarialScenario(id=f"adv-{j:04d}", category=cat, name=cat,
                                 params=_params_for(cat, easy=False), difficulty=difficulty)
        rs.append(mock.run_episode(task, seed=seed + episodes + j,
                                   randomization_level=level, scenario=sc))
    return sum(r.success for r in rs) / len(rs)


for ckpt in ("ckpt-41", "ckpt-40", "ckpt-43"):
    print(f"  {ckpt} tier={_checkpoint_offset(ckpt):+.2f} "
          f"nominal20={mock_rate(ckpt):.4f} "
          f"+12adv(d=1.0)={mock_rate(ckpt, scen_n=12):.4f}")
