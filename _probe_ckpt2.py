"""End-to-end: does the checkpoint now change the score through the real API?"""
from __future__ import annotations

import statistics
import sys
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, r"d:\SIM-TO-REAL")

from fastapi.testclient import TestClient  # noqa: E402

from validsim.api.main import create_app  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402

client = TestClient(create_app(ValidationStore()))

BODY = {
    "task": {
        "task_id": "t1",
        "robot": {"name": "franka_panda"},
        "environment": {"name": "kitchen"},
        "episodes": 300,
        "adversarial_count": 0,
    }
}

print("=== Six different checkpoints through POST /api/v1/validations ===")
scores = []
for name in ("ckpt-alpha", "ckpt-beta", "ckpt-gamma", "ckpt-delta", "ckpt-eps", "ckpt-zeta"):
    r = client.post("/api/v1/validations", json={**BODY, "checkpoint_id": name}).json()
    scores.append(r["composite_score"])
    print(f"  {name:<14} success={r['success_rate']:.4f} composite={r['composite_score']:6.2f}")

print(f"\n  checkpoint spread = {max(scores) - min(scores):.2f} composite points")
print(f"  stdev             = {statistics.pstdev(scores):.2f}")

print("\n=== DETERMINISM: same checkpoint twice ===")
a = client.post("/api/v1/validations", json={**BODY, "checkpoint_id": "ckpt-alpha"}).json()
b = client.post("/api/v1/validations", json={**BODY, "checkpoint_id": "ckpt-alpha"}).json()
print(f"  identical composite: {a['composite_score'] == b['composite_score']}")

print("\n=== REGRESSION DETECTION: now possible between two checkpoints? ===")
base = client.post("/api/v1/validations", json={**BODY, "checkpoint_id": "ckpt-zeta"}).json()
cand = client.post(
    "/api/v1/validations",
    json={**BODY, "checkpoint_id": "ckpt-alpha", "baseline_run_id": base["run_id"]},
).json()
print(f"  baseline {base['run_id']} composite={base['composite_score']:.2f}")
print(f"  candidate {cand['run_id']} composite={cand['composite_score']:.2f}")
print(f"  regression_delta   = {cand['regression_delta']}")
