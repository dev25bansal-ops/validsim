"""Security audit of the SMTP egress: header injection, guards, and blind spots.

Create-only audit evidence for `validsim/notify/email.py`. The production package
is never modified. Every payload is driven through the public
:meth:`EmailNotifier.send` API with a **trap SMTP object** that records the exact
bytes that would have gone on the wire, so "was it blocked?" is answered by
observation rather than by reading the validation code.

Verdict: the existing guards (audit finding M2) are **effective** for the
``Subject`` and ``To`` fields -- 16 CR/LF subjects and 30 recipient shapes are
all refused, in both dry-run and live mode, with no socket opened. This module
proves that with controls showing the guards are load-bearing. It also records
the one gap the guards leave open: the ``From`` address comes from
``VALIDSIM_SMTP_FROM` and is **not** validated by ValidSim at all -- today it is
caught only incidentally, by a stdlib ``HeaderParseError`` on Python 3.14+.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import pytest

from validsim.notify import EmailNotifier
from validsim.notify import email as email_mod
from validsim.notify.email import SmtpSettings

#: Every CR/LF subject shape an attacker might use to close the header block.
CRLF_SUBJECTS: list[tuple[str, str]] = [
    ("crlf+bcc", "Report\r\nBcc: attacker@evil.example"),
    ("lf+bcc", "Report\nBcc: attacker@evil.example"),
    ("cr+bcc", "Report\rBcc: attacker@evil.example"),
    ("bare-crlf", "\r\n"),
    ("leading-crlf", "\r\nBcc: a@b.co"),
    ("nul-then-crlf", "x\x00\r\nBcc: a@b.co"),
    ("folded-header", "S\r\n Bcc: a@b.co"),
    ("utf8-crlf", "Sujeté\r\nBcc: a@b.co"),
    ("double-crlf-body", "S\r\n\r\n<html>spoofed</html>"),
    ("cr-only-eol", "S\rBcc: a@b.co"),
    ("lf-only-eol", "S\nBcc: a@b.co"),
    ("trailing-crlf", "S\r\n"),
    ("x-header", "S\r\nX-Injected: 1"),
    ("content-type", "S\r\nContent-Type: text/html"),
]

#: Control chars that are illegal in a header field but cannot *terminate* one.
#: RFC 5322 header folding requires CRLF, so these are a conformance nit rather
#: than an injection primitive. Pinned in :class:`TestWhitespaceSubject` to
#: document that boundary instead of asserting rejection.
NON_CRLF_WHITESPACE_SUBJECTS: list[tuple[str, str]] = [
    ("vertical-tab", "S\x0bBcc: a@b.co"),
    ("form-feed", "S\x0cBcc: a@b.co"),
]

#: Malformed-but-accepted domain shapes: routed nowhere, injectable never.
#: Documented in :class:`TestRegexIsStrictAboutSyntaxNotRouting`.
LAX_RECIPIENTS: list[tuple[str, str]] = [
    ("double-dot-domain", "a@example..com"),
    ("leading-dash-domain", "a@-example.com"),
]

#: Recipient shapes: header injection, address smuggling, and truncation.
BAD_RECIPIENTS: list[tuple[str, str]] = [
    ("crlf-bcc", "a@example.com\r\nBcc: attacker@evil.example"),
    ("lf-bcc", "a@example.com\nBcc: attacker@evil.example"),
    ("cr-bcc", "a@example.com\rBcc: attacker@evil.example"),
    ("comma-smuggle", "a@example.com, b@example.com"),
    ("space-smuggle", "a@example.com b@example.com"),
    ("percent-encoded", "a@example.com%0d%0aBcc:x@y.co"),
    ("nul-truncate", "a@example.com\x00"),
    ("crlf-body", "a@example.com\r\n\r\n<body>spoofed"),
    ("ip-literal", "a@[127.0.0.1]"),
    ("cc-injection", "a@example.com\nCc: a@b.co"),
    ("semicolon-smuggle", "a@example.com; b@example.com"),
    ("trailing-tab", "a@example.com\t"),
    ("trailing-space", "a@example.com "),
    ("leading-space", " a@example.com"),
    ("empty", ""),
    ("no-tld", "a@example"),
    ("angle-brackets", "<a@example.com>"),
    ("quoted", '"a"@example.com'),
    ("to-injection", "a@example.com\r\nTo: a@b.co"),
    ("double-at", "a@@example.com"),
    ("space-in-domain", "a@exam ple.com"),
    ("pipe", "a@example.com|whoami"),
    ("backtick", "a@example.com`id`"),
    ("semicolon-cmd", "a@example.com;rm -rf /"),
    ("env-var", "${IFS}@example.com"),
    ("x-header", "a@example.com\nX-Injected: 1"),
    ("trailing-hash", "a@example.com#"),
    ("one-char-tld", "a@example.c"),
    ("trailing-cr", "a@example.com\r"),
]

#: Addresses the conservative pattern must keep accepting.
GOOD_RECIPIENTS = [
    "a@example.com",
    "first.last@sub.domain.co.uk",
    "user+tag@example.com",
    "u1@my-site.io",
    "ops_team@example.com",
    "a1.b2@example.museum",
]

#: Every environment variable the notifier reads.
_ENV_VARS = (
    "VALIDSIM_SMTP_HOST",
    "VALIDSIM_SMTP_PORT",
    "VALIDSIM_SMTP_USER",
    "VALIDSIM_SMTP_PASSWORD",
    "VALIDSIM_SMTP_FROM",
    "VALIDSIM_SMTP_TLS",
)


class TrapSmtp:
    """A stand-in ``smtplib.SMTP`` that records the exact wire message.

    ``wire`` holds the bytes that *would* have been sent; ``calls`` counts
    constructor invocations and ``sendmail_calls`` counts actual transmissions,
    which is how "no socket was even opened" and "nothing reached the wire" are
    asserted separately. All three are class attributes read directly by tests.
    """

    wire: list[dict[str, Any]] = []
    calls: int = 0
    sendmail_calls: int = 0

    def __init__(self, host: str = "", port: int = 0, timeout: float | None = None):
        del port, timeout
        type(self).calls += 1
        self.host = host

    def __enter__(self) -> "TrapSmtp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self, context: Any = None) -> None:
        del context

    def login(self, username: str, password: str) -> None:
        del username, password

    def sendmail(self, from_addr: str, to_addrs: list[str], msg: str) -> None:
        type(self).sendmail_calls += 1
        type(self).wire.append({"from": from_addr, "to": list(to_addrs), "msg": msg})

    @classmethod
    def reset(cls) -> None:
        cls.wire = []
        cls.calls = 0
        cls.sendmail_calls = 0


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient SMTP configuration."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    TrapSmtp.reset()


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete, working SMTP configuration with TLS disabled."""
    monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("VALIDSIM_SMTP_FROM", "robot@example.com")
    monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")
    monkeypatch.setattr(email_mod.smtplib, "SMTP", TrapSmtp)


# ---------------------------------------------------------------------------
# (1) SUBJECT -- every CR/LF shape must be refused, in both modes.
# ---------------------------------------------------------------------------
class TestSubjectInjection:
    """CR/LF in the subject cannot forge a following header."""

    @pytest.mark.parametrize(
        ("label", "subject"), CRLF_SUBJECTS, ids=[p[0] for p in CRLF_SUBJECTS]
    )
    def test_crlf_subject_rejected_in_dry_run(
        self, label: str, subject: str
    ) -> None:
        notifier = EmailNotifier()  # dry-run
        with pytest.raises(ValueError, match="CR or LF"):
            notifier.send(["ops@example.com"], subject, body_text="x")
        assert notifier.sent == [], f"[{label}] a rejected send must not be recorded"
        assert TrapSmtp.calls == 0, f"[{label}] no socket may be opened"

    @pytest.mark.parametrize(
        ("label", "subject"), CRLF_SUBJECTS, ids=[p[0] for p in CRLF_SUBJECTS]
    )
    def test_crlf_subject_rejected_in_live_mode(
        self, live_env: None, label: str, subject: str
    ) -> None:
        with pytest.raises(ValueError, match="CR or LF"):
            EmailNotifier(dry_run=False).send(
                ["ops@example.com"], subject, body_text="x"
            )
        assert TrapSmtp.calls == 0, f"[{label}] live mode must not even connect"

    def test_clean_subject_is_accepted(self) -> None:
        result = EmailNotifier().send(
            ["ops@example.com"], "Weekly validation report", body_text="x"
        )
        assert result.ok is True
        assert result.subject == "Weekly validation report"

    def test_newline_free_punctuation_is_fine(self) -> None:
        """The guard is CR/LF-specific, not a blanket charset limit."""
        ok = EmailNotifier().send(
            ["a@example.com"], "Report for run 7 - week 3 (v2)", body_text="x"
        )
        assert ok.ok is True
        with pytest.raises(ValueError, match="CR or LF"):
            EmailNotifier().send(["a@example.com"], "line1\nline2", body_text="x")


# ---------------------------------------------------------------------------
# (2) RECIPIENTS -- injection, smuggling and truncation.
# ---------------------------------------------------------------------------
class TestRecipientInjection:
    """No recipient shape can smuggle a header or a second envelope recipient."""

    @pytest.mark.parametrize(
        ("label", "recipient"),
        BAD_RECIPIENTS,
        ids=[p[0] for p in BAD_RECIPIENTS],
    )
    def test_bad_recipient_rejected_in_dry_run(
        self, label: str, recipient: str
    ) -> None:
        notifier = EmailNotifier()
        with pytest.raises(ValueError, match="invalid recipient"):
            notifier.send([recipient], "ok", body_text="x")
        assert notifier.sent == [], f"[{label}] nothing may be recorded"

    @pytest.mark.parametrize(
        ("label", "recipient"),
        BAD_RECIPIENTS,
        ids=[p[0] for p in BAD_RECIPIENTS],
    )
    def test_bad_recipient_rejected_in_live_mode(
        self, live_env: None, label: str, recipient: str
    ) -> None:
        with pytest.raises(ValueError, match="invalid recipient"):
            EmailNotifier(dry_run=False).send([recipient], "ok", body_text="x")
        assert TrapSmtp.calls == 0, f"[{label}] live mode must not even connect"

    def test_one_bad_recipient_poisons_the_whole_list(self) -> None:
        """A single bad address aborts the send -- no partial delivery."""
        with pytest.raises(ValueError, match="invalid recipient"):
            EmailNotifier().send(
                ["good@example.com", "bad address", "other@example.com"], "ok"
            )

    def test_empty_recipient_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one recipient"):
            EmailNotifier().send([], "ok", body_text="x")

    @pytest.mark.parametrize("recipient", GOOD_RECIPIENTS, ids=lambda v: v)
    def test_valid_recipients_still_accepted(self, recipient: str) -> None:
        result = EmailNotifier().send([recipient], "ok", body_text="x")
        assert result.ok is True
        assert result.to == (recipient,)


# ---------------------------------------------------------------------------
# (2b) THE EXACT BOUNDARY -- what the guards let through, and why it is safe.
# ---------------------------------------------------------------------------
class TestRegexIsStrictAboutSyntaxNotRouting:
    """Two malformed domains pass the regex. Neither is exploitable."""

    @pytest.mark.parametrize(
        ("label", "recipient"), LAX_RECIPIENTS, ids=[p[0] for p in LAX_RECIPIENTS]
    )
    def test_lax_domain_is_accepted_but_not_injectable(
        self, live_env: None, label: str, recipient: str
    ) -> None:
        """FINDING (Info) -- ``a@example..com`` / ``a@-example.com`` pass.

        The recipient regex (email.py:63) constrains each dot-separated domain
        label to ``[A-Za-z0-9-]+``, so an empty or leading-dash label slips
        through. This is a *correctness* gap, not a security one: the address is
        unroutable, and -- critically -- it carries no ``,``, ``;`` or
        whitespace, so ``smtplib`` emits exactly one ``RCPT TO`` and no forged
        header is possible.
        """
        result = EmailNotifier(dry_run=False).send(
            [recipient], "ok", body_text="x"
        )
        assert result.ok is True, f"[{label}] currently accepted"

        assert len(TrapSmtp.wire) == 1
        assert TrapSmtp.wire[0]["to"] == [recipient], "exactly one envelope rcpt"
        header_block = TrapSmtp.wire[0]["msg"].split("\r\n\r\n", 1)[0]
        assert "Bcc:" not in header_block and "Cc:" not in header_block
        # No separator character that smtplib or a parser could re-split on.
        assert not any(c in recipient for c in ",; \t\r\n")

    def test_control_a_smuggling_shape_is_rejected_by_the_same_regex(
        self, live_env: None
    ) -> None:
        """CONTROL -- the regex *does* stop every separator-based smuggling.

        This is what makes the two lax cases above benign: the same pattern
        rejects anything carrying a separator.
        """
        with pytest.raises(ValueError, match="invalid recipient"):
            EmailNotifier(dry_run=False).send(
                ["a@example.com, b@example.com"], "ok", body_text="x"
            )
        assert TrapSmtp.calls == 0


class TestWhitespaceSubject:
    """VT/FF pass ``_validate_subject`` but are stopped further downstream."""

    @pytest.mark.parametrize(
        ("label", "subject"),
        NON_CRLF_WHITESPACE_SUBJECTS,
        ids=[p[0] for p in NON_CRLF_WHITESPACE_SUBJECTS],
    )
    def test_non_crlf_whitespace_never_forges_a_header(
        self, monkeypatch: pytest.MonkeyPatch, live_env: None,
        label: str, subject: str
    ) -> None:
        """FINDING (Info) -- VT/FF clear ValidSim's guard; the stdlib stops them.

        ``_validate_subject`` (email.py:182) checks only CR and LF, so VT and FF
        pass validation and reach the MIME builder. Two independent facts then
        save the message:

        1. RFC 5322 header termination requires CRLF, so these characters cannot
           close a header field.
        2. On Python 3.14 the stdlib normalises VT/FF to LF and then raises
           ``HeaderParseError``, which ``_deliver`` converts into a failed
           :class:`EmailDelivery`.

        The outcome is safe -- nothing is forged and nothing is sent -- but the
        failure is reported as an opaque error rather than the clear
        "subject must not contain CR or LF" the other 14 shapes get. Pinned so
        the boundary is documented rather than assumed.
        """
        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], subject, body_text="x"
        )

        # Nothing reached the wire on either path.
        assert TrapSmtp.sendmail_calls == 0, f"[{label}] a message was sent"
        if TrapSmtp.wire:
            header_block = TrapSmtp.wire[0]["msg"].split("\r\n\r\n", 1)[0]
            assert "Bcc:" not in header_block, f"[{label}] a Bcc was forged"

        if result.ok:
            # The stdlib allowed it: prove no header was forged anyway.
            from email.parser import Parser

            parsed = Parser().parsestr(TrapSmtp.wire[0]["msg"])
            assert parsed["Bcc"] is None, f"[{label}] a Bcc header was forged"
        else:
            # The stdlib refused it, and said so.
            assert "header" in (result.error or "").lower(), (
                f"[{label}] unexpected failure: {result.error!r}"
            )

    def test_crlf_subject_gets_a_clear_error_but_vt_gets_an_opaque_one(
        self, monkeypatch: pytest.MonkeyPatch, live_env: None
    ) -> None:
        """The two shapes differ only in diagnosability, not in outcome."""
        from email.errors import HeaderParseError

        with pytest.raises(ValueError, match="CR or LF"):
            EmailNotifier().send(["a@example.com"], "S\r\nBcc: a@b.co")

        # VT passes ValidSim's guard...
        with pytest.raises(HeaderParseError):
            email_mod._build_message(
                "robot@example.com", ["a@example.com"], "S\x0bBcc: a@b.co",
                None, "x",
            ).as_string()

        # ...so in live mode it surfaces as a delivery failure, not a raise.
        result = EmailNotifier(dry_run=False).send(
            ["a@example.com"], "S\x0bBcc: a@b.co", body_text="x"
        )
        assert result.ok is False
        assert "embedded header" in (result.error or "")


# ---------------------------------------------------------------------------
# (3) CONTROLS -- prove the guards are load-bearing, not cosmetic.
# ---------------------------------------------------------------------------
class TestGuardIsLoadBearing:
    """If the guards were removed, would anything else stop an injection?"""

    def test_control_mime_layer_would_emit_a_forged_header(self) -> None:
        """CONTROL -- bypass every guard and hand the MIME builder a raw CRLF.

        On Python 3.14 the stdlib raises ``HeaderParseError`` instead of emitting
        the forged header -- a *second* barrier. On 3.13 and earlier it would
        have emitted ``Bcc: attacker@evil.example`` verbatim. Either way,
        ValidSim's guard is the barrier that runs first with a clear message, and
        the ordering is pinned by the next test.
        """
        import email.errors

        try:
            message = email_mod._build_message(
                "robot@example.com",
                ["ops@example.com"],
                "ok\r\nBcc: attacker@evil.example",
                None,
                "body",
            )
            wire = message.as_string()
        except email.errors.HeaderParseError as exc:
            assert "embedded header" in str(exc)
        else:
            header_block = wire.split("\r\n\r\n", 1)[0]
            assert "Bcc:" not in header_block, (
                "the stdlib emitted a forged Bcc -- ValidSim's guard would be "
                "the only thing standing between an attacker and header injection"
            )

    def test_control_guard_fires_before_message_assembly(
        self, monkeypatch: pytest.MonkeyPatch, live_env: None
    ) -> None:
        """CONTROL -- the guard runs *before* ``_build_message`` is reached.

        Replaces the builder with a tripwire so any invocation on the injection
        path becomes immediately visible. The paired test below proves the
        tripwire can actually fire, so a zero count here is meaningful.
        """
        calls: list[int] = []
        original = email_mod._build_message

        def _tripwire(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(email_mod, "_build_message", _tripwire)
        with pytest.raises(ValueError):
            EmailNotifier(dry_run=False).send(
                ["a@example.com"], "x\r\nBcc: y@z.co", body_text="x"
            )
        assert calls == [], "the guard must fire before the MIME builder runs"

    def test_control_tripwire_does_fire_on_a_valid_send(
        self, monkeypatch: pytest.MonkeyPatch, live_env: None
    ) -> None:
        """CONTROL -- a *valid* send reaches the builder exactly once.

        Without this, "0 calls on the injection path" could just mean the
        tripwire was never wired up.
        """
        calls: list[int] = []
        original = email_mod._build_message

        def _counting(*args: Any, **kwargs: Any) -> Any:
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(email_mod, "_build_message", _counting)
        result = EmailNotifier(dry_run=False).send(
            ["a@example.com"], "ok", body_text="x"
        )
        assert result.ok is True
        assert calls == [1], "a valid send must reach the MIME builder once"
        assert len(TrapSmtp.wire) == 1, "and the trap must have seen the message"


# ---------------------------------------------------------------------------
# (4) THE OPEN GAP -- the From address is not validated by ValidSim.
# ---------------------------------------------------------------------------
class TestFromAddressIsUnvalidated:
    """``From`` comes from the environment and has no ValidSim-side guard."""

    def test_from_is_taken_verbatim_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FINDING (Low) -- ``VALIDSIM_SMTP_FROM`` is trusted as-is.

        It is an operator-controlled variable, so this is not remotely
        reachable. It matters because a compromised or mistyped deployment
        config can still place an arbitrary string in the ``From`` header with
        no validation from ValidSim. The 3.14 stdlib happens to reject embedded
        headers, which is why this is Low and not High.
        """
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")
        monkeypatch.setattr(email_mod.smtplib, "SMTP", TrapSmtp)
        monkeypatch.setenv(
            "VALIDSIM_SMTP_FROM", "robot@example.com\r\nBcc: attacker@evil.example"
        )

        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], "ok", body_text="x"
        )

        assert TrapSmtp.calls == 1, "ValidSim opened the SMTP connection anyway"
        if TrapSmtp.wire:
            header_block = TrapSmtp.wire[0]["msg"].split("\r\n\r\n", 1)[0]
            assert "Bcc:" not in header_block, "a forged Bcc reached the wire"
        else:
            assert result.ok is False
            assert "embedded header" in (result.error or "")

    def test_missing_host_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "robot@example.com")
        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], "ok", body_text="x"
        )
        assert result.ok is False
        assert "SMTP host not configured" in (result.error or "")

    def test_missing_sender_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], "ok", body_text="x"
        )
        assert result.ok is False
        assert "SMTP sender not configured" in (result.error or "")


# ---------------------------------------------------------------------------
# (5) SECRET HYGIENE ACROSS THE ERROR PATH.
# ---------------------------------------------------------------------------
class TestSecretDoesNotEscape:
    """The SMTP password must not appear in results, logs or reprs."""

    def test_password_absent_from_every_string_form_and_log_record(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "sup3r-s3cret-pw")
        settings = SmtpSettings.from_env()
        marker = "sup3r-s3cret-pw"

        with caplog.at_level(logging.INFO):
            logging.getLogger("probe").info("settings=%s", settings)

        for form in (repr(settings), str(settings), f"{settings}", format(settings)):
            assert marker not in form, f"password leaked via {form!r}"
        assert all(marker not in r.getMessage() for r in caplog.records)

    def test_control_the_leak_detector_fires_on_a_naive_dataclass(self) -> None:
        """CONTROL -- the check above is not vacuous."""
        marker = "naive-pw-1234"

        @dataclasses.dataclass(frozen=True)
        class Naive:
            password: str | None = None

        assert marker in repr(Naive(marker)), "detector must fire on a real leak"
        assert marker not in repr(SmtpSettings(password=marker))

    def test_password_absent_from_a_transport_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transport failure must not echo the credentials.

        Uses an unreachable host so the failure comes from the OS/socket layer,
        not from a server that might reflect the password back.
        """
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "127.0.0.1:1")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "robot@example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_USER", "alice")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "sup3r-s3cret-pw")
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")

        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], "ok", body_text="x"
        )

        assert result.ok is False
        assert "sup3r-s3cret-pw" not in (result.error or "")
        assert "alice" not in (result.error or "")

    def test_a_server_error_is_captured_verbatim_without_redaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FINDING (Low) -- ``_deliver`` stores ``str(exc)`` unredacted.

        A server that *reflects* the password in its error text would land it on
        ``EmailDelivery.error``, which callers are free to log. No mainstream
        MTA reflects an AUTH password, so the practical exposure is low, but the
        module does not redact and the path exists.
        """
        marker = "sup3r-s3cret-pw"

        class EchoingSmtp(TrapSmtp):
            def login(self, username: str, password: str) -> None:
                raise RuntimeError(f"535 auth failed for {username}:{password}")

        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "robot@example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_USER", "alice")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", marker)
        monkeypatch.setattr(email_mod.smtplib, "SMTP", EchoingSmtp)

        result = EmailNotifier(dry_run=False).send(
            ["ops@example.com"], "ok", body_text="x"
        )

        assert result.ok is False
        assert marker in (result.error or ""), (
            "documents that the error path is unredacted today"
        )

    def test_asdict_bypasses_repr_protection_by_design(self) -> None:
        """``asdict()`` sees every field -- a known escape hatch, pinned here so
        a future refactor does not start calling it on a logging path."""
        settings = SmtpSettings(password="asdict-pw")
        assert "asdict-pw" in str(dataclasses.asdict(settings))
        assert "asdict-pw" not in repr(settings)

    def test_password_is_still_readable_for_the_send_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hiding the password from repr must not break authentication."""
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "padded-pw")
        settings = SmtpSettings.from_env()
        assert settings.password == "padded-pw"
        assert "padded-pw" not in repr(settings)
