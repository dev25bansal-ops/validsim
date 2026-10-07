"""End-to-end pipeline throughput and peak-memory benchmarks.

Uses the real ``run_and_score`` and the real ``MockIsaacBackend`` so the
measurement includes every allocation the request path makes (episode list,
scorecard, store row). Peak RSS comes from the stdlib platform counter in
``conftest`` - no psutil dependency.
"""

from __future__ import annotations

import pytest

from validsim.engine.pipeline import run_and_score
from validsim.sim import create_backend
from validsim.store.memory import ValidationStore

from .conftest import peak_rss_bytes, time_benchmark
from .fixtures import task_config

pytestmark = pytest.mark.benchmark

#: Throughput tiers. 20k is the largest the budget-aware timer can afford
#: (one call is ~3.7s); 100k is measured in the nightly tier below.
EPISODE_TIERS = (1_000, 5_000, 20_000)


@pytest.mark.parametrize("n", EPISODE_TIERS)
def test_run_and_score_throughput(n: int) -> None:
    """episodes/second for a full synchronous validation."""
    store = ValidationStore()
    backend = create_backend()
    cfg = task_config(n)
    median_ms = time_benchmark(
        f"run_and_score_episodes{n}",
        lambda: run_and_score(cfg, "ckpt-bench", store, backend=backend),
    )
    print(f"[bench] run_and_score episodes={n}: {n / (median_ms / 1000):,.0f} episodes/second")


@pytest.mark.slow
@pytest.mark.parametrize("n", (50_000, 100_000))
def test_run_and_score_max_episodes(n: int) -> None:
    """The configuration maximum - 100_000 is what TaskConfig allows.

    One call only (9-19s), so this is the ``slow`` tier: the nightly workflow,
    not the PR gate. Still budget-aware, so it is never the reason a run times
    out.
    """
    store = ValidationStore()
    backend = create_backend()
    cfg = task_config(n)
    median_ms = time_benchmark(
        f"run_and_score_episodes{n}",
        lambda: run_and_score(cfg, "ckpt-max", store, backend=backend),
        rounds=1,
        warmup=0,
    )
    peak_mib = peak_rss_bytes() / 1048576
    print(
        f"[bench] run_and_score episodes={n}: {median_ms / 1000:.1f}s, "
        f"{n / (median_ms / 1000):,.0f} episodes/second, peak RSS {peak_mib:.0f} MiB"
    )
