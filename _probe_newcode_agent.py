"""Probe 3: verify the exact behaviours the new tests will assert."""
import random
import sys

sys.path.insert(0, r"d:\SIM-TO-REAL")

import _pytest.pathlib as pypath
import pytest_symlink_free_tmp as shim

print("=" * 74)
print("G. symlink-free tmp shim")
print("=" * 74)
print("  _force_symlink present:", hasattr(pypath, "_force_symlink"))
print("  cleanup_dead_symlinks present:", hasattr(pypath, "cleanup_dead_symlinks"))
before = (pypath._force_symlink, pypath.cleanup_dead_symlinks)
shim.pytest_configure(None)
after = (pypath._force_symlink, pypath.cleanup_dead_symlinks)
print("  patched by pytest_configure:", before != after)
print("  _force_symlink is noop:", after[0] is shim._noop_force_symlink)
print("  cleanup_dead_symlinks is noop:", after[1] is shim._noop_cleanup_dead_symlinks)
print("  noop returns None:", shim._noop_force_symlink(1, 2, x=3),
      shim._noop_cleanup_dead_symlinks(None, 1, 2))
# also: _mk_tmp is what pytest actually uses
print("  _mk_tmp present:", hasattr(pypath, "_mk_tmp"))
print("  getbasetemp/_mk_tmp signature ok")

print()
print("=" * 74)
print("H. rng.choices CI: valid + point-bracketing")
print("=" * 74)
from validsim.engine.stats import bootstrap_ci

vals = [1.0] * 70 + [0.0] * 30
lo, hi, pt = bootstrap_ci(vals, n_resamples=200, seed=42)
print(f"  mixed sample  -> [{lo:.4f},{hi:.4f}] point={pt:.4f}")
print(f"  brackets: {lo <= pt <= hi}; non-degenerate: {lo < hi}; in [0,1]: {0 <= lo and hi <= 1}")
lo2, hi2, _ = bootstrap_ci(vals, n_resamples=200, seed=42)
print("  deterministic:", (lo, hi) == (lo2, hi2))
lo3, hi3, _ = bootstrap_ci(vals, n_resamples=200, seed=43)
print("  seed-sensitive:", (lo, hi) != (lo3, hi3))
for nr in (2, 3, 5, 50, 200, 500):
    a, b, c = bootstrap_ci(vals, n_resamples=nr, seed=7)
    print(f"  n_resamples={nr:>4} -> [{a:.4f},{b:.4f}] brackets={a <= c <= b}")
# n-1 draws would be the classic off-by-one mutation
r = random.Random(0)
for n in (1, 2, 5):
    print(f"  rng.choices k={n} on len={n} ->", r.choices(vals[:n], k=n))

print()
print("=" * 74)
print("I. checkpoint_id propagation through run_and_score")
print("=" * 74)
from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import run_and_score
from validsim.store.memory import ValidationStore
from validsim.sim.runner import EpisodeResult

seen = []


class SpyBackend:
    name = "spy"

    def run_episode(self, task, seed, randomization_level, scenario=None):
        seen.append(task.checkpoint_id)
        return EpisodeResult(f"ep{len(seen)}", task.task_id, seed, True,
                             0, 5.0, 1.5, None, 5.0, {}, randomization_level)


task = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                  environment=EnvironmentSpec(name="e"), episodes=3)
assert task.checkpoint_id is None, "task should start with no checkpoint"
store = ValidationStore()
run = run_and_score(task, "ckpt-abc", store, backend=SpyBackend())
print("  task started with checkpoint_id =", task.checkpoint_id, "(unmutated original)")
print("  backend saw:", set(seen))
print("  stored run checkpoint_id:", run.checkpoint_id, "| task_id:", run.task_id)
print("  scorecard checkpoint_id:", run.scorecard.checkpoint_id)

print()
print("=" * 74)
print("J. MockIsaacBackend responds to checkpoint_id")
print("=" * 74)
from validsim.sim.runner import MockIsaacBackend

b = MockIsaacBackend(base_success_rate=0.9)
probs = {cid: b.success_probability("full", None, cid)
         for cid in ("a", "b", "c", "d", "e", "f", None, "")}
for cid, p in probs.items():
    print(f"  {str(cid):>6} -> {p:.4f}")
print("  distinct values:", len(set(probs.values())), "of", len(probs))
print("  None/empty both -> base:", probs[None] == probs[""])

print()
print("  determinism (same checkpoint+seed twice):")
t2 = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                environment=EnvironmentSpec(name="e"), episodes=1,
                checkpoint_id="ckpt-abc")
e1 = b.run_episode(t2, seed=5, randomization_level="full")
e2 = b.run_episode(t2, seed=5, randomization_level="full")
print("   identical:", (e1.success, e1.seed, e1.max_contact_force_n) ==
      (e2.success, e2.seed, e2.max_contact_force_n))

print()
print("  SYSTEMATIC difference over 2000 episodes each (2 checkpoints):")
for cid in ("ckpt-aaa", "ckpt-bbb"):
    tc = t2.model_copy(update={"checkpoint_id": cid})
    oks = sum(b.run_episode(tc, seed=s, randomization_level="none").success
              for s in range(2000))
    print(f"   {cid}: {oks}/2000 = {oks / 2000:.3f}")
