"""API endpoint latency benchmarks at realistic store sizes.

Uses ``TestClient`` (the full ASGI stack: middleware, routing, pydantic,
serialization) so the numbers include everything a caller actually pays for.
The rate limiter is disabled so latency is measured in isolation; limiter and
queue-cap behaviour is asserted in ``test_bench_overload.py``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("VALIDSIM_RATE_LIMIT", "0")

from fastapi.testclient import TestClient  # noqa: E402

from validsim.api.main import create_app  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402

from .conftest import time_benchmark  # noqa: E402
from .fixtures import seeded_store  # noqa: E402

pytestmark = pytest.mark.benchmark

RUN_COUNTS = (1_000, 10_000, 100_000)

#: The dashboard polls two unpaginated read endpoints together (app.js
#: ``refreshPanels``); both scan the full store.
READ_PATHS = (
    "/api/v1/dashboard/history",
    "/api/v1/dashboard/summary",
    "/api/v1/validations",
    "/api/v1/models",
    "/api/v1/regressions",
    "/api/v1/metrics",
)


@pytest.mark.parametrize("count", RUN_COUNTS)
def test_read_endpoint_latency_at_scale(count: int) -> None:
    """p50 for every read the dashboard or CI touches, at each store size."""
    app = create_app(store=seeded_store(count))
    with TestClient(app) as client:
        for path in READ_PATHS:
            slug = path.strip("/").replace("/", "_")
            time_benchmark(
                f"api_{slug}_n{count}", lambda p=path: client.get(p), rounds=10, warmup=3
            )


@pytest.mark.parametrize("episodes", (100, 1_000, 5_000))
def test_sync_post_latency_vs_episodes(episodes: int) -> None:
    """The synchronous validation endpoint - latency as a function of episodes."""
    app = create_app(store=ValidationStore())
    body = {
        "checkpoint_id": "ckpt-post",
        "task": {
            "task_id": "pick-place-cube",
            "robot": {"name": "franka_panda"},
            "environment": {"name": "tabletop"},
            "episodes": episodes,
            "adversarial_count": 0,
        },
    }
    with TestClient(app) as client:
        time_benchmark(
            f"api_post_validations_episodes{episodes}",
            lambda: client.post("/api/v1/validations", json=body),
            rounds=3,
            warmup=1,
        )
