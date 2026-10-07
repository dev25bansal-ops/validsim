"""Shared builders for the benchmark suite.

Centralised so the store/API/store-scale benchmarks all build *identical*
fixtures - a benchmark that measures a different shape than the one the
baseline captured is worse than no benchmark.
"""

from __future__ import annotations

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import run_and_score
from validsim.sim import create_backend
from validsim.store.memory import StoredRun, ValidationStore


def task_config(episodes: int) -> TaskConfig:
    """A representative task: the one shape every benchmark scores."""
    return TaskConfig(
        task_id="pick-place-cube",
        robot=RobotSpec(name="franka_panda"),
        environment=EnvironmentSpec(name="tabletop"),
        episodes=episodes,
        adversarial_count=0,
    )


def prototype_run(episodes: int = 1) -> StoredRun:
    """One real, scored run used as a template for scale fixtures."""
    store = ValidationStore()
    return run_and_score(
        task_config(episodes), "ckpt-0", store, backend=create_backend(), run_id="vrun-00000000"
    )


def _stamp(index: int) -> str:
    """A distinct second-resolution ISO-8601 stamp for run ``index``.

    Distinct values matter: ``history()`` sorts on ``created_at``, so duplicate
    stamps would make the sort a no-op and understate the measured cost.
    """
    secs, mins, hrs = index % 60, (index // 60) % 60, (index // 3600) % 24
    days = (index // 86400) % 28 + 1
    return f"2026-01-{days:02d}T{hrs:02d}:{mins:02d}:{secs:02d}+00:00"


def seeded_store(count: int) -> ValidationStore:
    """An in-memory store of ``count`` distinct runs at one episode each."""
    proto = prototype_run(1)
    store = ValidationStore()
    for i in range(count):
        store.save(
            StoredRun(
                **{**proto.__dict__, "run_id": f"vrun-{i:08x}", "created_at": _stamp(i)}
            )
        )
    return store


def clone_runs(proto: StoredRun, count: int, *, distinct_stamps: bool = True) -> list[StoredRun]:
    """``count`` copies of ``proto`` under fresh run ids."""
    out = []
    for i in range(count):
        fields = {**proto.__dict__, "run_id": f"vrun-{i:08x}"}
        if distinct_stamps:
            fields["created_at"] = _stamp(i)
        out.append(StoredRun(**fields))
    return out
