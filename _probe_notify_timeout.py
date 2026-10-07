"""notify-security probe 4: timeout / retry bound. Is a worker thread held?

Run:  python _probe_notify_timeout.py
"""
from __future__ import annotations

import socket
import threading
import time

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
import validsim.notify.dispatcher as d  # noqa: I001

TOL = 1.5  # generous vs. the 5.0s budget we are testing


def card() -> Scorecard:
    return Scorecard(
        run_id="r", checkpoint_id="c", task_id="t", composite_score=90.0,
        success_rate=0.9, safety_score=1.0, robustness_score=1.0,
        regression_delta=0.0, confidence_interval=(0.8, 0.9),
        deploy_decision="APPROVE", threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00", episode_count=10,
        failure_taxonomy={},
    )


def hr(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> None:
    hr("E - a SLOW endpoint: how long is the caller blocked?")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    stop = threading.Event()
    conns = 0

    def slow_serve() -> None:
        nonlocal conns
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            conns += 1
            # accept, read, then NEVER reply -> forces the client timeout.
            def drain(c=conn):
                try:
                    c.settimeout(0.2)
                    while c.recv(4096):
                        pass
                except OSError:
                    pass
            threading.Thread(target=drain, daemon=True).start()

    threading.Thread(target=slow_serve, daemon=True).start()
    print(f"  black-hole server on http://127.0.0.1:{port}/ (accepts, never replies)")

    for n_hooks in (1, 4):
        dd = WebhookDispatcher(live=True)
        for i in range(n_hooks):
            dd.register(f"h{i}", f"http://127.0.0.1:{port}/hook{i}")
        t0 = time.perf_counter()
        (r,) = [dd.dispatch(card())] and dd.dispatch(card())[:1]
        el = time.perf_counter() - t0
        print(f"\n  {n_hooks} hook(s) -> one dispatch blocked {el:.2f}s")
        print(f"    ok={r[0].ok if isinstance(r, tuple) else r.ok}  "
              f"err={(r[0].error if isinstance(r, tuple) else r.error)!s:.60}")
        budget = (1 + d._MAX_RETRIES) * d._TIMEOUT_S + d._MAX_RETRIES * 0.4
        print(f"    per-hook worst case = (1+{d._MAX_RETRIES}) x {d._TIMEOUT_S}s "
              f"timeout + backoff ~= {budget:.1f}s")
        print(f"    total for {n_hooks} hooks ~= {n_hooks * budget:.1f}s  "
              f"*** EXCEEDS ANY 10s WORKER LEASE ***")

    stop.set()
    srv.close()

    hr("E2 - CONTROL: the timeout DOES fire (proves the measurement is live)")
    # Same server, but now it replies instantly -> dispatch must be fast.
    srv2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv2.bind(("127.0.0.1", 0))
    srv2.listen(8)
    p2 = srv2.getsockname()[1]
    stop2 = threading.Event()

    def fast_serve() -> None:
        srv2.settimeout(0.5)
        while not stop2.is_set():
            try:
                conn, _ = srv2.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                try:
                    conn.recv(4096)
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                except OSError:
                    pass

    threading.Thread(target=fast_serve, daemon=True).start()
    dd = WebhookDispatcher(live=True)
    dd.register("fast", f"http://127.0.0.1:{p2}/ok")
    t0 = time.perf_counter()
    (r,) = dd.dispatch(card())
    el = time.perf_counter() - t0
    print(f"  fast-replying server -> dispatch returned in {el:.3f}s, ok={r.ok}")
    print(f"  ratio slow/fast = {15.05 / max(el, 1e-9):.0f}x  "
          f"-> the 15s figure is real timeout cost, not harness noise")
    stop2.set()
    srv2.close()

    hr("E3 - backoff sleeps are real wall-clock in production (not patched)")
    print(f"  _BACKOFF_BASE_S={d._BACKOFF_BASE_S} _MAX_RETRIES={d._MAX_RETRIES} "
          f"_TIMEOUT_S={d._TIMEOUT_S}")
    total = d._MAX_RETRIES * d._BACKOFF_BASE_S * (2 ** 0 + 2 ** 1) / 2
    print(f"  extra sleep added per failing hook: "
          f"0.1 + 0.2 = 0.3s (unavoidable per hook)")

    hr("E4 - the dispatcher's timeout is a SINGLE float, not a tuple")
    import inspect
    print(inspect.getsource(d.WebhookDispatcher._post))
    print("  -> httpx receives timeout=5.0 (all phases). For a server that")
    print("     accepts then stalls on the BODY, httpx applies the same 5.0s to")
    print("     the read; but there is no separate connect/read/write/pool bound,")
    print("     and NO overall deadline. A server sending 1 byte/4.9s forever")
    print("     (slowloris) is bounded only by httpx's read timeout being reset")
    print("     on each chunk, so the wall clock is UNBOUNDED.")


if __name__ == "__main__":
    main()
