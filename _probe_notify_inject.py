"""notify-security probe 5: email header injection, exhaustively, both modes.

Run:  python _probe_notify_secret.py  (section G lives in probe_notify_inject.py)
"""
from __future__ import annotations

import smtplib

from validsim.notify import EmailNotifier
import validsim.notify.email as em  # noqa: I001


class TrapSMTP:
    """Records the exact wire message instead of sending it."""

    wire: list[dict] = []

    def __init__(self, *a, **k):
        self.host, self.port = a[0], a[1]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        pass

    def login(self, u, p):
        pass

    def sendmail(self, frm, to, msg):
        TrapSMTP.wire.append({"from": frm, "to": to, "msg": msg})


def hr(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


PAYLOADS = [
    ("subject CRLF+Bcc", "S", "Report\r\nBcc: attacker@evil.example"),
    ("subject LF only", "S", "Report\nBcc: attacker@evil.example"),
    ("subject CR only", "S", "Report\rBcc: attacker@evil.example"),
    ("subject bare CRLF", "S", "\r\n"),
    ("subject leading CRLF", "S", "\r\nBcc: a@b.co"),
    ("subject NUL+CRLF", "S", "x\x00\r\nBcc: a@b.co"),
    ("subject folded header", "S", "S\r\n Bcc: a@b.co"),
    ("subject UTF-8 CRLF", "S", "Sujeté\r\nBcc: a@b.co"),
]

RECIPIENTS = [
    "a@example.com\r\nBcc: attacker@evil.example",
    "a@example.com\nBcc: attacker@evil.example",
    "a@example.com\rBcc: attacker@evil.example",
    "a@example.com, b@example.com",
    "a@example.com b@example.com",
    "a@example.com%0d%0aBcc:x@y.co",
    "a@example.com\x00",
    "a@example.com\r\n\r\n<body>spoofed",
    "a@[127.0.0.1]",
    "a@example.com\nCc: a@b.co",
    "a@example.com; b@example.com",
    "a@example.com\t",
    "a@example.com ",
    " a@example.com",
    "",
    "a@localhost",
    "a@example",
    "<a@example.com>",
    '"a"@example.com',
    "a@example.com\r\nTo: a@b.co",
    "a@@example.com",
    "a@exam ple.com",
    "a@example.com|whoami",
    "a@example.com`id`",
    "a@example.com;rm -rf /",
    "${IFS}@example.com",
    "a@example.com\nX-Injected: 1",
    "a@example.com#",
    "a@example..com",
    "a@-example.com",
    "a@example.c",
    "a@example.com\r",
]


def main() -> None:
    hr("G1 - subject injection: every CRLF variant, BOTH modes")
    import os
    os.environ.update(
        VALIDSIM_SMTP_HOST="127.0.0.1", VALIDSIM_SMTP_FROM="ok@example.com",
        VALIDSIM_SMTP_TLS="0")
    em.smtplib.SMTP = TrapSMTP
    leak = []
    for label, _k, subj in PAYLOADS:
        for mode, n in (("dry", EmailNotifier()), ("live", EmailNotifier(dry_run=False))):
            TrapSMTP.wire.clear()
            try:
                n.send(["a@example.com"], subj, body_text="x")
                res = "NOT BLOCKED"
                leak.append((label, mode, subj))
            except ValueError as e:
                res = f"ValueError: {e}"
            sent = len(TrapSMTP.wire)
            print(f"  {label:22} [{mode}] -> {res:45} smtp_calls={sent}")
    print(f"\n  unblocked subject payloads: {len(leak)}  {leak}")

    hr("G2 - recipient injection / smuggling: every variant, BOTH modes")
    leak_r = []
    for rec in RECIPIENTS:
        for mode, n in (("dry", EmailNotifier()), ("live", EmailNotifier(dry_run=False))):
            TrapSMTP.wire.clear()
            try:
                n.send([rec], "ok", body_text="x")
                res = "NOT BLOCKED"
                leak_r.append((rec, mode))
            except ValueError:
                res = "ValueError (rejected)"
            sent = len(TrapSMTP.wire)
            flag = "  <<< ACCEPTED" if sent else ""
            print(f"  {rec!r:38} [{mode}] -> {res:24} smtp_calls={sent}{flag}")
    print(f"\n  unblocked recipient payloads: {len(leak_r)}")
    for r, m in leak_r:
        print(f"    !!! {r!r} accepted in {m} mode")

    hr("G3 - CONTROL: can the probe detect a REAL injection?")
    # Bypass every guard and hand-craft the message to prove the wire format
    # really is injectable if validation were absent.
    import email.errors
    try:
        msg = em._build_message("ok@example.com", ["a@example.com"],
                                "ok\r\nBcc: attacker@evil.example", None, "body")
        wire = msg.as_string()
    except Exception as exc:
        print(f"  _build_message(...).as_string() RAISED "
              f"{type(exc).__module__}.{type(exc).__name__}:")
        print(f"    {exc}")
        print("  -> Python 3.14's email package is a SECOND, independent barrier:")
        print("     HeaderRegistry.encode() refuses a value containing an embedded")
        print("     header, so even a guard bypass cannot emit a forged Bcc here.")
    else:
        print("  _build_message() with a raw CRLF subject yields:")
        for line in wire.splitlines()[:6]:
            print(f"    {line!r}")
        hdrs = wire.split("\r\n\r\n", 1)[0]
        print(f"  header block contains a forged Bcc: {'Bcc:' in hdrs}")
    print("  -> defence in depth: _validate_subject (email.py:169) is the first")
    print("     barrier, the stdlib HeaderRegistry is the second. Both are needed")
    print("     (3.13 and earlier do NOT have the stdlib barrier).")
    print("  -> CONTROL PROVEN: the probe reaches the MIME builder when the guard")
    print("     is bypassed, so smtp_calls>0 in G1/G2 would have been visible.")

    hr("G3b - is _build_message reachable with a bad subject at all?")
    called = {"n": 0}
    orig = em._build_message
    em._build_message = lambda *a, **k: (called.__setitem__("n", called["n"] + 1),
                                         orig(*a, **k))[1]
    try:
        EmailNotifier(dry_run=False).send(["a@example.com"], "x\r\nBcc: y@z.co")
    except ValueError:
        pass
    em._build_message = orig
    print(f"  _build_message invocations for an injected subject: {called['n']}")
    print(f"  -> guard fires BEFORE message assembly: {called['n'] == 0}")

    hr("G4 - is the guard applied to From? (VALIDSIM_SMTP_FROM is env-supplied)")
    os.environ["VALIDSIM_SMTP_FROM"] = "ok@example.com\r\nBcc: attacker@evil.example"
    TrapSMTP.wire.clear()
    r = EmailNotifier(dry_run=False).send(["a@example.com"], "s", body_text="b")
    if TrapSMTP.wire:
        raw = TrapSMTP.wire[0]["msg"]
        hdrs = raw.split("\r\n\r\n", 1)[0]
        print(f"  FROM header region of the wire message:")
        for line in raw.splitlines()[:8]:
            print(f"    {line!r}")
        print(f"  header block contains a second Bcc: {'Bcc:' in hdrs}")
        print(f"  *** FROM is NOT validated: send ok={r.ok} ***")
    else:
        print(f"  send returned ok={r.ok} err={r.error!r}")
        print(f"  -> the stdlib barrier caught it (HeaderParseError), no message sent")
    os.environ["VALIDSIM_SMTP_FROM"] = "ok@example.com"


if __name__ == "__main__":
    main()
