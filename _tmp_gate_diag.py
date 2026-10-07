"""Throwaway diagnostic: timeline of the admission-gate test."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

import validsim.api.main as api_main
from validsim.api.main import create_app
from validsim.store.memory import ValidationStore

BODY = {
    "checkpoint_id": "c",
    "task": {
        "task_id": "t",
        "robot": {"name": "r"},
        "environment": {"name": "e"},
        "episodes": 1,
        "adversarial_count": 0,
    },
}

T0 = time.perf_counter()
log: list[str] = []


def note(msg: str) -> None:
    log.append(f"{time.perf_counter() - T0:7.3f}s  {msg}")


release = threading.Event()
original = api_main._execute_validation


def _blocking(request, store):
    note(f"pipeline ENTER in_flight={api_main._PIPELINE_GATE.in_flight}")
    got = release.wait(30)
    note(f"pipeline EXIT released={got}")
    return original(request, store)


api_main._execute_validation = _blocking

app = create_app(ValidationStore())
app.state.pipeline_concurrency = 6
api_main._PIPELINE_GATE.resize(6)
note(f"gate limit={api_main._PIPELINE_GATE.limit} in_flight={api_main._PIPELINE_GATE.in_flight}")

with TestClient(app) as client:
    result: dict[str, object] = {}

    def worker() -> None:
        try:
            note("worker posting")
            r = client.post("/api/v1/validations", json=BODY)
            result["worker"] = r.status_code
            note(f"worker done status={r.status_code}")
        except BaseException as exc:
            result["worker"] = f"{type(exc).__name__}: {exc}"
            note(f"worker raised {type(exc).__name__}")

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.6)
    note(f"after sleep in_flight={api_main._PIPELINE_GATE.in_flight}")
    api_main._PIPELINE_GATE.resize(1)
    note("resized to 1")
    r = client.post("/api/v1/validations", json=BODY)
    note(f"main got status={r.status_code}")
    release.set()
    t.join(timeout=10)
    note("joined")

print("\n".join(log))
print("result:", result)
