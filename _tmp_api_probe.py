"""MEASUREMENT (throwaway probe, deleted after use).

Part A2: N concurrent 20k-episode POSTs -> is the anyio worker-thread limiter
        exhausted so that /api/v1/health (also a sync `def` route) can no longer
        be served at all?
Part B : control -- a minimal FastAPI app with ONE sync `def` route proves the
        exhaustion is a FastAPI/anyio framework property, not ValidSim code.

Run:  python _tmp_api_probe.py
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import traceback

import httpx
import uvicorn

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(app) -> tuple[str, uvicorn.Server, threading.Thread]:
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/v1/health", timeout=1).status_code == 200:
                return base, server, thread
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("server did not come up")


BODY = {
    "checkpoint_id": "ckpt-probe",
    "task": {
        "task_id": "pick-place",
        "robot": {"name": "franka"},
        "environment": {"name": "kitchen"},
        "episodes": 20000,
        "adversarial_count": 0,
    },
}


# --------------------------------------------------------------------------- #
# Part A2
# --------------------------------------------------------------------------- #
def part_a2(concurrency: int = 45) -> None:
    print(f"--- Part A2: {concurrency} concurrent 20k-episode POSTs ------------")
    base, server, thread = serve(create_app(ValidationStore()))
    try:
        with httpx.Client(base_url=base, timeout=600.0) as hc:
            t0 = time.perf_counter()
            resp = hc.post("/api/v1/validations", json=BODY)
            solo = time.perf_counter() - t0
            print(f"CONTROL (1 heavy POST alone): status={resp.status_code} {solo:.2f}s")

            results: list[int] = []

            def fire() -> None:
                try:
                    results.append(hc.post("/api/v1/validations", json=BODY).status_code)
                except Exception as exc:
                    results.append(f"ERR:{type(exc).__name__}")

            threads = [threading.Thread(target=fire, daemon=True) for _ in range(concurrency)]
            t_launch = time.perf_counter()
            for t in threads:
                t.start()
            time.sleep(1.0)

            t0 = time.perf_counter()
            try:
                health = hc.get("/api/v1/health", timeout=20.0)
                print(f"/health while {concurrency} heavy POSTs in flight: "
                      f"status={health.status_code} in {(time.perf_counter()-t0):.2f}s")
            except Exception as exc:
                print(f"/health while {concurrency} heavy POSTs in flight: "
                      f"FAILED {type(exc).__name__} after {time.perf_counter()-t0:.2f}s "
                      f"(Docker HEALTHCHECK --timeout=5s would fail)")

            for t in threads:
                t.join(timeout=300)
            ok = sum(1 for r in results if r == 201)
            print(f"heavy POSTs completed: {len(results)} in {time.perf_counter()-t_launch:.1f}s, "
                  f"status 201 x{ok}, others={sorted(set(r for r in results if r != 201))}")
    finally:
        server.should_exit = True
        thread.join(timeout=20)


# --------------------------------------------------------------------------- #
# Part B: control
# --------------------------------------------------------------------------- #
async def part_b() -> None:
    import anyio.to_thread
    from fastapi import FastAPI

    print("--- Part B: control, minimal app, two sync `def` routes ----------")
    limiter = anyio.to_thread.current_default_thread_limiter()
    print(f"anyio default thread limiter total_tokens={limiter.total_tokens}")

    app = FastAPI()
    started = threading.Event()
    release = threading.Event()
    n_blockers = 45

    @app.get("/block")
    def block() -> dict[str, bool]:
        started.set()
        release.wait(60)
        return {"ok": True}

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/ping", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    with httpx.Client(base_url=base, timeout=90.0) as hc:
        blockers: list[threading.Thread] = []
        for _ in range(n_blockers):
            t = threading.Thread(target=lambda: hc.get("/block"), daemon=True)
            t.start()
            blockers.append(t)
        started.wait(10)
        time.sleep(0.5)
        t0 = time.perf_counter()
        try:
            r = hc.get("/ping", timeout=20.0)
            print(f"CONTROL ping while {n_blockers} blocking sync routes: "
                  f"status={r.status_code} in {time.perf_counter()-t0:.2f}s")
        except Exception as exc:
            print(f"CONTROL ping while {n_blockers} blocking sync routes: "
                  f"FAILED {type(exc).__name__} after {time.perf_counter()-t0:.2f}s")
        release.set()
        for t in blockers:
            t.join(timeout=70)
    server.should_exit = True
    thread.join(timeout=20)


if __name__ == "__main__":
    for fn in (part_a2, part_b):
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
        except Exception:
            traceback.print_exc()
    print("done")
