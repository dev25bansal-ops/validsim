"""PF-3 end-to-end: does a baseline comparison hit the permutation cost?"""
from __future__ import annotations

import sys
import time
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, r"d:\SIM-TO-REAL")

from fastapi.testclient import TestClient  # noqa: E402

from validsim.api.main import create_app  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402

store = ValidationStore()
client = TestClient(create_app(store))


def body(eps: int, baseline: str | None = None) -> dict:
    payload = {
        "checkpoint_id": "ck1",
        "task": {
            "task_id": "t1",
            "robot": {"name": "franka_panda"},
            "environment": {"name": "kitchen"},
            "episodes": eps,
        },
    }
    if baseline:
        payload["baseline_run_id"] = baseline
    return payload


print("No baseline (regression path NOT exercised):")
for eps in (1_000, 5_000, 10_000):
    t = time.perf_counter()
    r = client.post("/api/v1/validations", json=body(eps))
    print(f"  episodes={eps:6d} -> {(time.perf_counter() - t) * 1000:8.1f} ms ({r.status_code})")

print("\nSeeding a baseline store...")
base_store = ValidationStore()
base_client = TestClient(create_app(base_store))
base_id = base_client.post("/api/v1/validations", json=body(5000)).json()["run_id"]
print(f"  baseline run_id = {base_id}")

# A separate store seeded with that baseline id, so lookups succeed.
seeded = ValidationStore()
src = base_store.get(base_id)
seeded.save(src)
sc = TestClient(create_app(seeded))

print("\nWITH baseline_run_id (permutation test runs):")
for eps in (1_000, 5_000, 10_000):
    t = time.perf_counter()
    r = sc.post("/api/v1/validations", json=body(eps, base_id))
    ms = (time.perf_counter() - t) * 1000
    print(f"  episodes={eps:6d} -> {ms:8.1f} ms ({r.status_code})")
