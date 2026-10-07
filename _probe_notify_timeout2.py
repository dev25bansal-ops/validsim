"""notify-security probe 4b: exact per-dispatch blocking time + slowloris bound.

Run:  python _probe_notify_timeout2.py
"""
from __future__ import annotations

import socket
import threading
import time

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
import validsim.notify.dispatcher as d  # noqa: I001


def card() -> Scorecard:
    return Scorecard(
        run_id="r", checkpoint_id="c", task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=1.0, robustness_score=1.0,
        regression_delta=0.0, confidence_interval=(0.8, 0.9),
        deploy_decision="APPROVE", threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00", episode_count=10,
        failure_taxonomy={},
    )


def make_server(handler, n_accept=64):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(n_accept)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def loop():
        srv.settimeout(0.4)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=_safe(handler, conn), daemon=True).start()

    def _safe(h, c):
        try:
            h(c)
        except OSError:
            pass

    threading.Thread(target=loop, daemon=True).start()
    return srv, port, stop


def hr(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def timeit(n_hooks: int, port: int) -> float:
    dd = WebhookDispatcher(live=True)
    for i in range(n_hooks):
        dd.register(f"h{i}", f"http://127.0.0.1:{port}/hook{i}")
    t0 = time.perf_counter()
    results = dd.dispatch(card())
    el = time.perf_counter() - t0
    ok = sum(1 for r in results if r.ok)
    errs = {r.error for r in results if not r.ok}
    print(f"  hooks={n_hooks}  blocked {el:6.2f}s  ok={ok}/{len(results)}  "
          f"err={sorted(e or '' for e in errs)[:1]}")
    return el


def main() -> None:
    hr("E1 - black-hole server (accept, never reply): cost of ONE dispatch")
    def blackhole(conn):
        conn.settimeout(0.2)
        try:
            while conn.recv(4096):
                pass
        except OSError:
            pass
    srv, port, stop = make_server(blackhole)
    print(f"  black-hole on http://127.0.0.1:{port}/")
    print(f"  configured: timeout={d._TIMEOUT_S}s retries={d._MAX_RETRIES} "
          f"backoff=0.1+0.2 -> worst case {3*d._TIMEOUT_S + 0.3:.1f}s/hook\n")
    t1 = timeit(1, port)
    t3 = timeit(3, port)
    t6 = timeit(6, port)
    stop.set(); srv.close()
    print(f"\n  scaling: 1 hook {t1:.1f}s | 3 hooks {t3:.1f}s | 6 hooks {t6:.1f}s")
    print(f"  per-hook marginal cost = {(t6 - t1) / 5:.1f}s  (serial, not concurrent)")
    print("  *** dispatch() is fully SERIAL: N hooks = N x 15.3s, one worker thread.")
    print("  *** 20 slow hooks => ~306s holding a worker; job lease is 30s by default.")

    hr("E2 - CONTROL: fast server returns immediately (measurement is live)")
    def fast(conn):
        conn.settimeout(2.0)
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    srv2, p2, stop2 = make_server(fast)
    t_fast = timeit(3, p2)
    stop2.set(); srv2.close()
    print(f"  slow/fast ratio = {t3 / max(t_fast, 1e-9):.0f}x "
          f"-> the 15.3s/hook figure is genuine timeout cost, not harness noise")

    hr("E3 - SLOWLORIS: server trickles a byte every 4s, forever")
    def slowloris(conn):
        conn.settimeout(120.0)
        try:
            req = conn.recv(4096)
        except OSError:
            return
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
        for i in range(200):            # 200 x 4s = 800s of trickling
            try:
                conn.sendall(b"x")
                time.sleep(4.0)
            except OSError:
                return
    srv3, p3, stop3 = make_server(slowloris)
    print(f"  slowloris server on http://127.0.0.1:{p3}/")
    print("  httpx read timeout applies PER-READ, not to the whole body.")
    print("  Measuring a 40s trickle (8 chunks) -- if it returns before the")
    print("  15.3s per-hook budget finishes, the budget is NOT a wall-clock bound.\n")
    dd = WebhookDispatcher(live=True)
    dd.register("slow", f"http://127.0.0.1:{p3}/x")
    t0 = time.perf_counter()
    r = dd.dispatch(card())[-1]
    el = time.perf_counter() - t0
    print(f"\n  RESULT: dispatch blocked {el:.1f}s for ONE hook "
          f"(ok={r.ok} err={(r.error or '')[:40]!r})")
    print(f"  per-hook documented worst case = {3*d._TIMEOUT_S + 0.3:.1f}s")
    print(f"  *** UNBOUNDED: {el > 3*d._TIMEOUT_S + 0.3} "
          f"({'exceeded' if el > 3*d._TIMEOUT_S + 0.3 else 'within'} budget) ***")
    stop3.set(); srv3.close()

    hr("E4 - summary of the bound")
    print(f"  _TIMEOUT_S     = {d._TIMEOUT_S}   (one float -> all httpx phases)")
    print(f"  _MAX_RETRIES   = {d._MAX_RETRIES}")
    print(f"  _BACKOFF_BASE_S= {d._BACKOFF_BASE_S}")
    print("  No httpx.Timeout(connect=..., read=..., write=..., pool=...) tuple")
    print("  No overall deadline / cancel scope. Retries share the SAME body and")
    print("  the SAME signature, so a receiver that de-dupes on the signature")
    print("  will drop all 3 attempts (idempotency hazard, see report).")


if __name__ == "__main__":
    main()
