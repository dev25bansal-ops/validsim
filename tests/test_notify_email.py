"""Tests for the SMTP email notifier (dry-run by default, safe live mode)."""

from __future__ import annotations

import email

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import (
    EmailDelivery,
    EmailNotifier,
    scorecard_email_body,
)
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


def _scorecard(decision: str = "APPROVE") -> Scorecard:
    return Scorecard(
        run_id="vrun-cafe1234",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=91.5,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=-0.1,
        confidence_interval=(0.82, 0.95),
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7, "grasp_failure": 3},
    )


class TestDryRun:
    def test_default_is_dry_run(self) -> None:
        assert EmailNotifier().dry_run is True

    def test_send_records_without_socket_io(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("socket I/O must not happen in dry-run mode")

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _boom)
        notifier = EmailNotifier()
        result = notifier.send(to=["a@example.com"], subject="Hi", body_text="body")

        assert isinstance(result, EmailDelivery)
        assert result.ok is True and result.dry_run is True and result.error is None
        assert result.to == ("a@example.com",)
        assert len(notifier.sent) == 1

    def test_sent_accumulates_across_sends(self) -> None:
        notifier = EmailNotifier()
        notifier.send(["a@example.com"], "one")
        notifier.send(["b@example.com"], "two")
        assert [d.subject for d in notifier.sent] == ["one", "two"]

    def test_empty_recipients_rejected(self) -> None:
        with pytest.raises(ValueError, match="recipient"):
            EmailNotifier().send([], "no one")


class TestLiveMode:
    def _patch_smtp(self, monkeypatch: pytest.MonkeyPatch) -> list:
        created: list = []

        class FakeSMTP:
            def __init__(self, host: str, port: int, timeout: object = None) -> None:
                self.host = host
                self.port = port
                self.timeout = timeout
                self.tls_started = False
                self.login_args: tuple | None = None
                self.sent: list = []
                created.append(self)

            def __enter__(self) -> "FakeSMTP":
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

            def starttls(self) -> None:
                self.tls_started = True

            def login(self, user: str, password: str) -> None:
                self.login_args = (user, password)

            def sendmail(self, from_addr: str, to_addrs: list, msg: str) -> None:
                self.sent.append((from_addr, list(to_addrs), msg))

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", FakeSMTP)
        return created

    def test_live_send_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = self._patch_smtp(monkeypatch)
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_USER", "alice")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "s3cr3t")

        result = EmailNotifier(dry_run=False).send(
            to=["a@example.com", "b@example.com"],
            subject="Report",
            body_html="<h1>Hi</h1>",
            body_text="Hi",
        )

        assert result.ok is True and result.dry_run is False and result.error is None
        assert result.to == ("a@example.com", "b@example.com")

        (smtp,) = created
        assert smtp.host == "mail.example.com"
        assert smtp.port == 587  # default VALIDSIM_SMTP_PORT
        assert smtp.tls_started is True
        assert smtp.login_args == ("alice", "s3cr3t")

        from_addr, to_addrs, raw_msg = smtp.sent[0]
        assert from_addr == "valid@example.com"
        assert to_addrs == ["a@example.com", "b@example.com"]

        # UTF-8 bodies are transfer-encoded, so parse the serialized message
        # and decode the parts to verify the actual content.
        parsed = email.message_from_string(raw_msg)
        assert parsed["Subject"] == "Report"
        assert parsed["From"] == "valid@example.com"
        assert parsed["To"] == "a@example.com, b@example.com"
        bodies = [
            part.get_payload(decode=True).decode("utf-8")
            for part in parsed.walk()
            if not part.is_multipart()
        ]
        assert any("<h1>Hi</h1>" in body for body in bodies)
        assert any("Hi" in body for body in bodies)

    def test_tls_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = self._patch_smtp(monkeypatch)
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")

        EmailNotifier(dry_run=False).send(["a@example.com"], "no tls", body_text="x")

        (smtp,) = created
        assert smtp.tls_started is False
        assert smtp.login_args is None  # no username configured, no login

    def test_transport_error_captured_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise ConnectionError("smtp down")

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _boom)
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")

        result = EmailNotifier(dry_run=False).send(["a@example.com"], "x", body_text="x")

        assert result.ok is False and result.dry_run is False
        assert "smtp down" in (result.error or "")

    def test_missing_host_captured_not_raised(self) -> None:
        result = EmailNotifier(dry_run=False).send(["a@example.com"], "x", body_text="x")
        assert result.ok is False and "VALIDSIM_SMTP_HOST" in (result.error or "")


class TestScorecardBody:
    def test_html_body_built_from_scorecard_dict(self) -> None:
        html = scorecard_email_body(_scorecard().to_dict())
        assert "91.5" in html  # composite score
        assert "APPROVE" in html
        assert "<style>" in html  # self-contained card, not a bare fragment

    def test_send_scorecard_builds_bodies_and_records(self) -> None:
        notifier = EmailNotifier()
        result = notifier.send_scorecard(
            to=["ops@example.com"], subject="Validation", scorecard=_scorecard().to_dict()
        )
        assert result.ok is True
        (recorded,) = notifier.sent
        assert recorded.subject == "Validation"
        assert recorded.to == ("ops@example.com",)


class TestEnvParsing:
    def test_full_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_PORT", "2525")
        monkeypatch.setenv("VALIDSIM_SMTP_USER", "alice")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "secret")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "robot@example.com")
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")

        settings = SmtpSettings.from_env()

        assert settings.host == "smtp.example.com"
        assert settings.port == 2525
        assert settings.username == "alice"
        assert settings.password == "secret"
        assert settings.from_addr == "robot@example.com"
        assert settings.use_tls is False

    def test_defaults_when_env_unset(self) -> None:
        settings = SmtpSettings.from_env()
        assert settings.host is None
        assert settings.port == 587
        assert settings.username is None and settings.password is None
        assert settings.from_addr is None
        assert settings.use_tls is True