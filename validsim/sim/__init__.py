"""Simulation execution layer for ValidSim.

Exposes the deterministic :class:`MockIsaacBackend` (the CPU-only default) and
the HTTP :class:`IsaacWorkerBackend` adapter for the containerized GPU worker,
plus :func:`create_backend` which selects between them from the environment.
"""

from __future__ import annotations

import os

from validsim.sim.isaac_worker import IsaacWorkerBackend, SimWorkerError
from validsim.sim.runner import (
    FAILURE_MODES,
    EpisodeResult,
    MockIsaacBackend,
    SimulationBackend,
    run_validation,
    stable_seed,
)

__all__ = [
    "FAILURE_MODES",
    "EpisodeResult",
    "IsaacWorkerBackend",
    "MockIsaacBackend",
    "SimWorkerError",
    "SimulationBackend",
    "create_backend",
    "run_validation",
    "stable_seed",
]

#: Env var selecting the simulation backend ("mock" | "isaac"); defaults to mock.
_BACKEND_ENV = "VALIDSIM_BACKEND"


def create_backend() -> SimulationBackend:
    """Build a simulation backend from the environment configuration.

    Reads ``VALIDSIM_BACKEND`` (case-insensitive):

    * ``"isaac"`` — :class:`IsaacWorkerBackend`, which forwards each episode to
      the containerized Isaac Sim/Lab GPU worker at
      ``VALIDSIM_ISAAC_WORKER_URL``. Construction is side-effect free (no HTTP
      client, no network); a missing URL surfaces as an actionable
      ``ValueError`` at the first episode instead.
    * anything else — the default seeded :class:`MockIsaacBackend`, keeping
      tests and CPU-only runs reproducible and GPU-free.

    Mirrors :func:`validsim.store.create_store`: the factory is the single
    place that maps deployment configuration onto a concrete backend, so
    callers can be swapped without touching simulation code.
    """
    backend = os.environ.get(_BACKEND_ENV, "mock").strip().lower()
    if backend == "isaac":
        return IsaacWorkerBackend()
    return MockIsaacBackend()
