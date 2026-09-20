"""Regression tests for the audited SMTP email notifier findings.

Two hardening fixes are pinned here:

* **M2 — SMTP header injection.** A subject carrying CR/LF, or a recipient
  that is not a clean single address, must raise :class:`ValueError` *before*
  any MIME message is assembled or recorded, in both dry-run and live modes.
* **L2 — secret in ``repr``.** The SMTP AUTH password must never appear in
  ``repr()``/``str()`` of :class:`SmtpSettings`, so logging the object cannot
  leak it.
"""

from __future__ import annotations

import pytest

from validsim.notify import EmailDelivery, EmailNotifier
from validsim.notify.email import SmtpSettings

_ENV_VARS = (
    "VALIDSIM_SMTP_HOST",
    "VALIDSIM_SMTP_PORT",
    "VALIDSIM_SMTP_USER",
    "VALIDSIM_SMTP_PASSWORD",
    "VALIDSIM_SMTP_FROM",
    "VALIDSIM_SMTP_TLS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient SMTP configuration."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestSubjectHeaderInjection:
    """M2 — CR/LF in the subject is refused before building the message."""

    @pytest.mark.parametrize(
        "subject",
        [
            "Report\r\nBcc: attacker@evil.example",
            "Report\nBcc: attacker@evil.example",
            "Report\rInjected",
            "Multi\nline\r\nsubject",
            "\r\n",
        ],
    )
    def test_crlf_subject_rejected_in_dry_run(self, subject: str) -> None:
        notifier = EmailNotifier()  # dry-run by default
        with pytest.raises(ValueError, match="CR or LF"):
            notifier.send(["a@example.com"], subject, body_text="x")
        # Validation fails fast: nothing is recorded.
        assert notifier.sent == []

    def test_crlf_subject_rejected_in_live_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("socket I/O must not happen for an invalid subject")

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _boom)
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")

        with pytest.raises(ValueError, match="CR or LF"):
            EmailNotifier(dry_run=False).send(
                ["a@example.com"], "Hi\r\nBcc: evil@x.co", body_text="x"
            )

    def test_message_is_never_built_for_invalid_subject(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard fires *before* :func:`_build_message` runs."""

        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("_build_message must not run for invalid input")

        monkeypatch.setattr("validsim.notify.email._build_message", _boom)
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")

        with pytest.raises(ValueError, match="CR or LF"):
            EmailNotifier(dry_run=False).send(["a@example.com"], "x\r\ny", body_text="x")

    def test_clean_subject_is_accepted(self) -> None:
        result = EmailNotifier().send(["a@example.com"], "Weekly report", body_text="x")
        assert result.ok is True


class TestRecipientValidation:
    """M2 — every recipient must match a conservative single-address pattern."""

    @pytest.mark.parametrize(
        "recipient",
        [
            "not-an-email",
            "@example.com",
            "a@example",  # no dot / TLD in the domain
            "a b@example.com",  # embedded whitespace
            "a@example.com\r\nBcc: evil@x.co",  # CRLF header injection
            "a@example.com, b@example.com",  # separator smuggling
            "<script>@example.com",  # angle brackets
            '"quoted"@example.com',  # quoting
            "",  # empty
            "a@@example.com",  # double @
        ],
    )
    def test_bad_recipient_rejected(self, recipient: str) -> None:
        notifier = EmailNotifier()
        with pytest.raises(ValueError, match="invalid recipient"):
            notifier.send([recipient], "Hi", body_text="x")
        assert notifier.sent == []

    def test_rejects_when_any_recipient_in_a_list_is_bad(self) -> None:
        with pytest.raises(ValueError, match="invalid recipient"):
            EmailNotifier().send(["a@example.com", "bad address"], "Hi")

    @pytest.mark.parametrize(
        "recipient",
        [
            "a@example.com",
            "first.last@sub.domain.co.uk",
            "user+tag@example.com",
            "u1@my-site.io",
        ],
    )
    def test_valid_recipients_accepted(self, recipient: str) -> None:
        result = EmailNotifier().send([recipient], "ok", body_text="x")
        assert result.ok is True


class TestPasswordNotInRepr:
    """L2 — the SMTP AUTH password must not leak through repr()/str()."""

    def test_repr_omits_password_but_keeps_other_fields(self) -> None:
        settings = SmtpSettings(
            host="smtp.example.com",
            username="alice",
            password="s3cr3t-pa55w0rd",
            from_addr="robot@example.com",
        )
        text = repr(settings)
        assert "s3cr3t-pa55w0rd" not in text
        # repr stays useful: the non-secret fields are still present.
        assert "smtp.example.com" in text
        assert "alice" in text
        # The value is still stored and usable, only hidden from repr.
        assert settings.password == "s3cr3t-pa55w0rd"

    def test_str_omits_password(self) -> None:
        settings = SmtpSettings(password="hunter2")
        assert "hunter2" not in str(settings)

    def test_from_env_repr_omits_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "sup3r-s3cret")
        settings = SmtpSettings.from_env()
        assert settings.password == "sup3r-s3cret"
        assert "sup3r-s3cret" not in repr(settings)


class TestValidSendStillWorks:
    """The guards must not break the happy path."""

    def test_dry_run_valid_send_records(self) -> None:
        notifier = EmailNotifier()
        result = notifier.send(
            to=["ops@example.com", "team+ci@example.com"],
            subject="Validation APPROVE",
            body_text="all good",
        )
        assert isinstance(result, EmailDelivery)
        assert result.ok is True and result.dry_run is True and result.error is None
        assert result.to == ("ops@example.com", "team+ci@example.com")
        assert len(notifier.sent) == 1
