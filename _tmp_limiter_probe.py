"""MEASUREMENT (throwaway probe, deleted after use).

1. What does the ungated /docs/oauth2-redirect leak to an anonymous caller?
2. Is DELETE metered by _RateLimitMiddleware?
3. Does _SlidingWindowRateLimiter._sweep ever actually run / reclaim anything?

Run:  python _tmp_limiter_probe.py
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["VALIDSIM_RATE_LIMIT"] = "3"
os.environ["VALIDSIM_RATE_WINDOW_SECONDS"] = "1"

from validsim.api.main import (  # noqa: E402
    _SlidingWindowRateLimiter,
    _is_rate_limited_request,
    create_app,
)
from validsim.store.memory import ValidationStore  # noqa: E402

BODY = {
    "checkpoint_id": "ckpt-probe",
    "task": {
        "task_id": "pick-place",
        "robot": {"name": "franka"},
        "environment": {"name": "kitchen"},
        "episodes": 2,
        "adversarial_count": 0,
    },
}

print("=== 1. /docs/oauth2-redirect with a key configured ===")
os.environ["VALIDSIM_API_KEY"] = "secret-key"
app = create_app(ValidationStore())
client = TestClient(app)
r = client.get("/docs/oauth2-redirect")
print(f"status={r.status_code} content-type={r.headers.get('content-type')}")
print("body:")
print(r.text[:800])
os.environ.pop("VALIDSIM_API_KEY", None)

print()
print("=== 2. is DELETE metered? ===")
os.environ["VALIDSIM_RATE_LIMIT"] = "2"
app2 = create_app(ValidationStore())
c2 = TestClient(app2)
run_id = c2.post("/api/v1/validations", json=BODY).json()["run_id"]
codes = []
for _ in range(6):
    codes.append(c2.delete(f"/api/v1/validations/{run_id}").status_code)
print(f"rate limit = 2/60s; 6x DELETE -> {codes}")
print(f"any 429? {429 in codes}  (POST control below must show 429)")
codes2 = [c2.post("/api/v1/validations", json=BODY).status_code for _ in range(4)]
print(f"4x POST -> {codes2}")

print()
print("=== 3. _sweep reachability and effect ===")
lim = _SlidingWindowRateLimiter(limit=1, window_seconds=1.0, max_buckets=4)
sweep_runs = 0
original_sweep = lim._sweep


def counting_sweep(current):
    global sweep_runs
    sweep_runs += 1
    return original_sweep(current)


lim._sweep = counting_sweep  # type: ignore[method-assign]

# 200 distinct clients -> 200 checks, far past _SWEEP_INTERVAL (1024)?  No: 200 < 1024.
for i in range(300):
    lim.check(f"10.0.0.{i}", now=0.0)
print(f"after 300 allowed checks: _sweep called {sweep_runs} times, "
      f"buckets alive={len(lim._hits)}")

# Now exhaust the allowance and keep requesting: denied requests never reach the
# sweep counter, so the sweep can be starved indefinitely.
for i in range(3000):
    lim.check("10.0.9.9", now=0.0)
print(f"after 3000 DENIED checks: _sweep called {sweep_runs} times, "
      f"buckets alive={len(lim._hits)}")

for i in range(724):
    lim.check(f"10.1.0.{i}", now=0.0)
print(f"after 724 more allowed checks (total allowed=1024): "
      f"_sweep called {sweep_runs} times, buckets alive={len(lim._hits)}")

# All buckets are now 10s old relative to `now=10`; a correct sweep reclaims them.
before = len(lim._hits)
lim._sweep(10.0)
print(f"explicit _sweep(10.0): {before} -> {len(lim._hits)} buckets")
print(f"operations counter after sweeps: {lim._operations}")
