"""notify-security probe 1: HMAC webhook replay (finds-the-problem control included).

Run:  python _probe_notify_replay.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify import dispatcher as disp

SECRET = "wh-secret-abc123"
URL = "https://receiver.example/hook"
SIG = "X-ValidSim-Signature"


def card(run_id: str = "run-1") -> Scorecard:
    return Scorecard(
        run_id=run_id, checkpoint_id="ck", task_id="t", composite_score=91.5,
        success_rate=0.9, safety_score=80.0, robustness_score=100.0,
        regression_delta=-0.1, confidence_interval=(0.82, 0.95),
        deploy_decision="APPROVE", threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00", episode_count=10,
        failure_taxonomy={},
    )


# --------------------------------------------------------------------------
# A REAL receiver: exactly the verification recipe docs/runbook.md tells
# operators to implement (HMAC-SHA256 of the raw body, compare to header).
# --------------------------------------------------------------------------
class Receiver:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.log: list[tuple[float, str, bool]] = []
        self.rejected_for_replay: list[str] = []

    def verify(self, body: str, headers: dict) -> bool:
        expect = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(expect, headers.get(SIG, ""))
        self.log.append((time.time(), body, ok))
        return ok

    def handle(self, body: str, headers: dict) -> bool:
        if not self.verify(body, headers):
            self.rejected_for_replay.append(body)
            return False
        return True


def wire_post(recv: Receiver):
    """Emulate httpx.post -> the receiver, recording every accepted delivery."""

    def _post(url, **kw):
        body = kw["content"]
        headers = dict(kw["headers"])
        print(f"    --> POST {url}  sig={headers.get(SIG, '<none>')[:16]}...")
        accepted = recv.handle(body, headers)
        return SimpleNamespace(status_code=200 if accepted else 401)

    return _post


def main() -> None:
    import validsim.notify.dispatcher as d
    d.httpx.post = wire_post(recv := Receiver(SECRET))

    print("=" * 78)
    print("STEP 1 - attacker passively captures ONE legitimate delivery")
    print("=" * 78)
    disp_ = WebhookDispatcher(live=True)
    disp_.register("ci", URL, secret=SECRET)
    r = disp_.dispatch(card("run-LEGIT"))
    print(f"    result ok={r[0].ok} status={r[0].status_code}")
    captured_time, captured_body, _ = recv.log[-1]
    captured_sig = hmac.new(SECRET.encode(), captured_body.encode(), hashlib.sha256).hexdigest()
    print(f"    captured body[:60] = {captured_body[:60]!r}")
    print(f"    captured sig       = {captured_sig}")

    print()
    print("=" * 78)
    print("STEP 2 - attacker replays the EXACT captured (body, signature) later,")
    print("         for a scorecard the receiver has never seen and never will")
    print("=" * 78)
    forged = json.dumps(
        {
            "run_id": "run-ATTACKER-INJECTED",
            "deploy_decision": "APPROVE",
            "composite_score": 99.99,
            "checkpoint_id": "evil-ckpt",
        }
    )
    # Key insight: the attacker does not need ValidSim to sign anything. They
    # keep the captured signature and only need a body whose HMAC collides --
    # which they cannot produce WITHOUT the secret. So prove the two halves:
    print("  2a. attacker sends captured body+sig again (identical bytes)")
    print(f"      -> receiver.verify = {recv.verify(captured_body, {SIG: captured_sig})}")

    print("  2b. attacker swaps in a different body but keeps the captured sig")
    print(f"      -> receiver.verify = {recv.verify(forged, {SIG: captured_sig})}")

    print("  2c. attacker FORGES a body AND computes a matching sig with a"
          "\n      GUESSED secret (weak/empty/default secret attack)")
    for guess in ("", SECRET + " ", SECRET.upper(), "secret", "changeme"):
        s = hmac.new(guess.encode(), forged.encode(), hashlib.sha256).hexdigest()
        if recv.verify(forged, {SIG: s}):
            print(f"      !! FORGERY ACCEPTED with secret={guess!r}")
            break
    else:
        print("      (no guess in this list was weak enough)")

    print()
    print("=" * 78)
    print("STEP 3 - TIME PASSES. Is the captured pair still valid?")
    print("=" * 78)
    for label, delay in (("immediately", 0.0), ("+1 hour", 3600.0),
                         ("+1 day", 86400.0), ("+1 year", 31536000.0)):
        # Replay by *wall clock* only: the attacker stores (body, sig) and waits.
        recv.log.clear()
        recv.verify(captured_body, {SIG: captured_sig})
        accepted = recv.log[-1][2]
        print(f"    replay {label:>10} later -> accepted={accepted}")
        time.sleep(0)  # no real sleep; the point is the check has no time input

    print()
    print("=" * 78)
    print("STEP 4 - the signature carries no time/nonce. Prove it structurally:")
    print("=" * 78)
    src_sig = open("validsim/notify/dispatcher.py", encoding="utf-8").read()
    for probe in ("time.time()", "nonce", "Date", "X-ValidSim-Timestamp", "t="):
        hits = [i + 1 for i, ln in enumerate(src_sig.splitlines()) if probe in ln]
        print(f"    {probe!r:24} occurrences in dispatcher.py: {hits or 'NONE'}")
    print("    -> the only input to _sign_body() is (body, secret).")
    import inspect
    print("    _sign_body signature:", inspect.signature(d._sign_body))
    print("    headers built in _post:", {k: v for k, v in
          [('Content-Type', 'application/json'), (SIG, '<hmac hex>')]})
    print(f"    _SIGNATURE_HEADER const = {d._SIGNATURE_HEADER!r}")


if __name__ == "__main__":
    main()
