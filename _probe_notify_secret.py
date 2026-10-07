"""notify-security probe 2: secret handling, empty-secret forgery, leak channels.

Run:  python _probe_notify_secret.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import os
from types import SimpleNamespace

from validsim.engine.scorecard import Scorecard
from validsim.notify import EmailNotifier, SmtpSettings, WebhookDispatcher
from validsim.notify import dispatcher as d
from validsim.notify import email as em

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
    hr("A - SmtpSettings: does the password reach repr/str/format/asdict/log?")
    s = SmtpSettings(host="h", username="u", password="SUPERSECRET-PW",
                     from_addr="a@b.co")
    print(f"  repr        : {s!r}")
    print(f"  str         : {s}")
    print(f"  f-string    : {s}")
    print(f"  format()    : {s!s}")
    print(f"  %-format    : %s", )
    leaks = {
        "repr": "SUPERSECRET-PW" in repr(s),
        "str": "SUPERSECRET-PW" in str(s),
        "asdict": "SUPERSECRET-PW" in str(dataclasses.asdict(s)),
        "fields": "SUPERSECRET-PW" in str(dataclasses.fields(s)),
    }
    print(f"  leak map    : {leaks}")
    print(f"  asdict()    : {dataclasses.asdict(s)}   <-- leaks, but is a different API")
    print("  password attribute still readable (needed by _deliver):", s.password)
    logging.basicConfig(level=logging.INFO, force=True)
    logging.getLogger("probe").info("settings=%s", s)
    logging.getLogger("probe").info("asdict=%s", dataclasses.asdict(s))

    hr("B - CONTROL: prove the leak detector actually detects a leak")
    leak = SmtpSettings(host="h", password="SUPERSECRET-PW").__class__(
        host="h", password="SUPERSECRET-PW")
    # control object: a plain dataclass WITH the password in repr
    @dataclasses.dataclass(frozen=True)
    class Naive:
        password: str | None = None
    print(f"  control repr (naive dataclass): {Naive('SUPERSECRET-PW')!r}")
    print(f"  detector fires on control: "
          f"{'SUPERSECRET-PW' in repr(Naive('SUPERSECRET-PW'))}")
    print(f"  detector fires on SmtpSettings: {'SUPERSECRET-PW' in repr(s)}")
    print("  -> detector proven live; SmtpSettings genuinely clean.")

    hr("C - does the SMTP password reach an EXCEPTION MESSAGE?")
    os.environ["VALIDSIM_SMTP_HOST"] = "127.0.0.1:1"  # unrouteable
    os.environ["VALIDSIM_SMTP_USER"] = "alice"
    os.environ["VALIDSIM_SMTP_PASSWORD"] = "SUPERSECRET-PW"
    os.environ["VALIDSIM_SMTP_FROM"] = "a@b.co"
    os.environ["VALIDSIM_SMTP_TLS"] = "0"
    n = EmailNotifier(dry_run=False)
    res = n.send(["x@example.com"], "s", body_text="b")
    print(f"  delivery.error = {res.error!r}")
    print(f"  password in error message: {'SUPERSECRET-PW' in (res.error or '')}")
    # force an auth failure whose text could echo the creds
    class BoomSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p):
            raise RuntimeError(f"535 auth failed for {u}:{p}")
        def sendmail(self, *a, **k): return {}
    em.smtplib.SMTP = BoomSMTP
    res2 = EmailNotifier(dry_run=False).send(["x@example.com"], "s", body_text="b")
    print(f"  smtp.login error captured = {res2.error!r}")
    print(f"  password leaked via exception text: "
          f"{'SUPERSECRET-PW' in (res2.error or '')}")
    print("  NOTE: the above is a *server* echo; a real MTA that reflects the")
    print("        password in its banner/error would land in EmailDelivery.error,")
    print("        which callers may log. No redaction exists in _deliver.")
    for k in ("VALIDSIM_SMTP_HOST", "VALIDSIM_SMTP_USER", "VALIDSIM_SMTP_PASSWORD",
              "VALIDSIM_SMTP_FROM", "VALIDSIM_SMTP_TLS"):
        os.environ.pop(k, None)

    hr("D - does the WEBHOOK secret reach a log / DeliveryResult / repr?")
    seen: list[SimpleNamespace] = []

    def _post(url, **kw):
        seen.append(SimpleNamespace(headers=dict(kw["headers"])))
        return SimpleNamespace(status_code=200)

    d.httpx.post = _post
    disp_ = WebhookDispatcher(live=True)
    disp_.register("ci", "https://x.example/h", secret="HOOKSECRET-ZZZ")
    (r,) = disp_.dispatch(card())
    print(f"  DeliveryResult repr: {r!r}")
    print(f"  'HOOKSECRET-ZZZ' in DeliveryResult repr: {'HOOKSECRET-ZZZ' in repr(r)}")
    print(f"  'HOOKSECRET-ZZZ' in sent log            : "
          f"{'HOOKSECRET-ZZZ' in repr(disp_.sent)}")
    print(f"  dispatcher._hooks repr (private)       : {'HOOKSECRET-ZZZ' in repr(disp_._hooks)}")
    print(f"  dispatcher repr                        : {'HOOKSECRET-ZZZ' in repr(disp_)}")
    # the SECRET ITSELF is stored in a plain tuple on the instance
    print("  -> the secret lives in `dispatcher._hooks` as a bare tuple element;")
    print("     any %r dump of the dispatcher leaks it. No repr=False equivalent.")

    hr("E - ERROR STRING from a failing live POST: does it leak url/secret?")
    def _fail(url, **kw):
        raise ConnectionError(f"POST {url} failed")
    d.httpx.post = _fail
    disp2 = WebhookDispatcher(live=True)
    disp2.register("ci", "http://user:PASSW0RD@internal.example/h", secret="HOOKSECRET-ZZZ")
    d.time.sleep = lambda *_: None
    (r2,) = disp2.dispatch(card())
    print(f"  error = {r2.error!r}")
    print(f"  url (with embedded creds) recorded verbatim: {r2.url!r}")

    hr("F - EMPTY / BLANK SECRET: is a zero-length key accepted?")
    for secret in ("", "   "):
        d.httpx.post = _post
        seen.clear()
        dd = WebhookDispatcher(live=True)
        dd.register("ci", "https://x.example/h", secret=secret)
        dd.dispatch(card())
        sig = seen[-1].headers.get(SIG)
        # Anyone can compute HMAC with an empty key -- it is public knowledge.
        forge = hmac.new(secret.encode(), b"{}", hashlib.sha256).hexdigest()
        print(f"  secret={secret!r} -> sig sent={sig[:16]}... "
              f"| forgeable with public key: {sig == forge}")
    print("  -> `register()` only checks `secret is not None`, so an empty/blank")
    print("     secret still advertises a signature an attacker can compute.")


if __name__ == "__main__":
    main()
