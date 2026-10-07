"""Capture golden nominal-path episodes so a refactor can be proven byte-identical."""
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.sim.runner import MockIsaacBackend

task = TaskConfig(
    task_id="pick-place", robot=RobotSpec(name="franka"),
    environment=EnvironmentSpec(name="kitchen"), episodes=10,
)
b = MockIsaacBackend()
for s in (0, 1, 2, 42, 99):
    print(repr(b.run_episode(task, seed=s, randomization_level="full")))
