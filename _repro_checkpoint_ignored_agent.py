"""REPRODUCER: MockIsaacBackend ignores the checkpoint under test.

Run:  .venv\\Scripts\\python.exe _repro_checkpoint_ignored_agent.py
"""
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.sim.runner import MockIsaacBackend

try:
    from validsim.sim.runner import _checkpoint_offset
except ImportError:
    _checkpoint_offset = None

print("checkpoint_id is propagated onto the task by the pipeline:")
from validsim.engine.pipeline import run_and_score
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import ValidationStore


class Spy:
    name = "spy"

    def run_episode(self, task, seed, randomization_level, scenario=None):
        return EpisodeResult(f"s{seed}", task.task_id, seed, True, 0, 5.0, 1.5,
                             None, 5.0, {}, randomization_level)


seen = []
_orig = Spy.run_episode


def rec(self, task, seed, randomization_level, scenario=None):
    seen.append(task.checkpoint_id)
    return _orig(self, task, seed, randomization_level, scenario)


Spy.run_episode = rec
t = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
               environment=EnvironmentSpec(name="e"), episodes=2)
run_and_score(t, "ckpt-abc", ValidationStore(), backend=Spy())
print(f"  backend saw checkpoint_id = {set(seen)}   -> propagation OK")

print()
print("but the backend then ignores it:")
b = MockIsaacBackend(base_success_rate=0.9)
for cid in ("ckpt-aaa", "ckpt-bbb", "ckpt-ccc", "ckpt-ddd", "ckpt-zzz"):
    task = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                      environment=EnvironmentSpec(name="e"), episodes=1,
                      checkpoint_id=cid)
    rate = sum(b.run_episode(task, seed=s, randomization_level="none").success
               for s in range(1500)) / 1500
    print(f"  {cid}: {rate:.6f}")

print()
if _checkpoint_offset is None:
    print("_checkpoint_offset: DOES NOT EXIST in validsim.sim.runner")
    print("  -> the checkpoint-dependent success-rate shift has been removed")
else:
    print(f"_checkpoint_offset('ckpt-aaa') = {_checkpoint_offset('ckpt-aaa')}")
import inspect
print("success_probability signature:",
      inspect.signature(MockIsaacBackend.success_probability))
src = inspect.getsource(MockIsaacBackend.run_episode)
print("run_episode reads task.checkpoint_id:", "checkpoint_id" in src)
