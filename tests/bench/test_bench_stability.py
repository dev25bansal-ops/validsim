"""Long-run stability: heartbeat threads, metrics cardinality, store growth.

A compressed soak - seconds, not hours - so it can live in CI. The full
multi-hour soak is specified in docs/PERFORMANCE.md; this file pins the
invariants that soak would look for, so a regression surfaces as a failed test
rather than a production discovery.
"""

from __future__ import annotations

import threading

import pytest

from validsim.api.metrics import Metrics
from validsim.jobs.models import JobSpec, JobStatus
from validsim.jobs.queue import JobQueue
from validsim.jobs.worker import JobWorker
from validsim.store.memory import ValidationStore

from .conftest import peak_rss_bytes
from .fixtures import task_config

pytestmark = pytest.mark.benchmark


def test_heartbeat_thread_does_not_leak() -> None:
    """One daemon heartbeat thread per job must be joined every cycle.

    ``JobWorker`` starts a daemon thread per claimed job to renew the lease.
    A thread that is never joined accumulates for the process lifetime.
    """
    store = ValidationStore()
    queue = JobQueue(max_depth=10_000, lease_seconds=300)
    worker = JobWorker(queue=queue, store=store)
    baseline = threading.active_count()
    for i in range(50):
        queue.enqueue(
            JobSpec(
                run_id=f"vrun-{i:08x}", checkpoint_id="c", task_id="t", episodes=1, adversarial=0
            )
        )
        claim = queue.claim_next()
        assert claim is not None, "claim returned None for a non-empty queue"
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=worker._heartbeat,
            args=(claim.job_id, claim.lease_epoch, stop),
            daemon=True,
        )
        heartbeat.start()
        stop.set()
        heartbeat.join(timeout=2.0)
        assert not heartbeat.is_alive(), "heartbeat thread did not join"
        queue.update_status(
            claim.job_id, JobStatus.DONE, result=claim.job_id, lease_epoch=claim.lease_epoch
        )
    assert threading.active_count() <= baseline, "threads leaked across job cycles"


def test_metrics_series_cardinality_is_bounded() -> None:
    """Request counters key on a fixed status-class set, so they never grow."""
    metrics = Metrics()
    before = len(metrics.snapshot_http())
    for _ in range(100_000):
        metrics.observe_status(200)
        metrics.observe_status(404)
    after = metrics.snapshot_http()
    assert len(after) == before
    assert after["2xx"] == 100_000
    assert after["4xx"] == 100_000


@pytest.mark.slow
def test_store_growth_per_run_is_linear() -> None:
    """Pin the per-run resident cost of the unbounded in-memory store.

    The store never evicts, so 100k runs is the dashboard's practical ceiling
    (docs/PERFORMANCE.md). This detects an order-of-magnitude regression in
    per-run footprint, not 10% drift.
    """
    from validsim.engine.pipeline import run_and_score
    from validsim.sim import create_backend

    before = peak_rss_bytes()
    store = ValidationStore()
    backend = create_backend()
    task = task_config(1)
    for i in range(5_000):
        store.save(
            run_and_score(task, f"ckpt-{i}", store, backend=backend, run_id=f"vrun-{i:08x}")
        )
    growth = peak_rss_bytes() - before
    # 5k single-episode runs cost well under 50 MiB; allow 4x headroom.
    assert growth < 200 * 1048576, f"5k single-episode runs grew RSS by {growth / 1048576:.0f} MiB"
