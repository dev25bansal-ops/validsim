"""Activation tests for the SMTP email channel of :mod:`validsim.notify`.

The email notifier (:mod:`validsim.notify.email`) was fully implemented --
HMAC-free but with header-injection guards, TLS verification, HTML and text
bodies, dry-run support and a delivery record -- yet **no production path
ever constructed it**. The webhook channel was wired; the email channel was
not, so a completed run could never produce a mail.

These tests pin the activation:

* the channel is configurable from the environment and reports itself in
  ``/api/v1/health``;
* it is **off by default** and **dry-run by default**, so enabling it never
  sends mail on its own;
* a completed run reaches it, and it shares the webhook channel's severity
  routing so both channels agree on whether an event was worth sending;
* a recipient that fails validation is dropped rather than disabling the
  valid ones, and a crafted address can never smuggle a second recipient;
* a delivery failure is reported, never raised -- a broken mail server must
  not lose a finished run.

No test in this module performs network I/O: ``smtplib.SMTP`` is replaced by
a stand-in that fails loudly, so a regression that leaks a real send fails the
suite rather than mailing someone.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import notify_config_from_env
from validsim.notify.config import EmailSpec
from validsim.notify.dispatcher import notify_run_completion, severity_from_scorecard

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Env vars cleared so an operator's ambient environment can never make the
#: suite send real mail.
_EMAIL_ENV = (
    "VALIDSIM_NOTIFY_ENABLED",
    "VALIDSIM_SMTP_TO",
    "VALIDSIM_SMTP_MIN_SEVERITY",
    "VALIDSIM_SMTP_LIVE",
    "VALIDSIM_SMTP_HOST",
    "VALIDSIM_SMTP_PORT",
    "VALIDSIM_SMTP_USER",
    "VALIDSIM_SMTP_PASSWORD",
    "VALIDSIM_SMTP_FROM",
    "VALIDSIM_SMTP_TLS",
    "VALIDSIM_WEBHOOK_URLS",
    "VALIDSIM_WEBHOOK_SECRETS",
    "VALIDSIM_WEBHOOK_FORMATS",
    "VALIDSIM_WEBHOOK_MIN_SEVERITY",
    "VALIDSIM_WEBHOOKS_LIVE",
)

_RECIPIENT = "ops@example.com"
_SECOND_RECIPIENT = "oncall@example.org"


def _no_socket(*args: object, **kwargs: object) -> None:
    """Fail loudly if a send path opens an SMTP socket."""
    raise AssertionError("SMTP socket opened")


@pytest.fixture(autouse=True)
def _isolate_email_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every email env var so ambient state cannot enable egress."""
    for name in _EMAIL_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _block_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test in this file can send a mail, opt-in or not."""
    monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _no_socket)


def _env(**values: str) -> dict[str, str]:
    """A complete environment mapping for :func:`notify_config_from_env`."""
    base = {name: "" for name in _EMAIL_ENV}
    base.update(values)
    return base


def _scorecard(decision: str = "APPROVE", composite: float = 90.0) -> Scorecard:
    return Scorecard(
        run_id="vrun-email01",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=composite,
        success_rate=composite / 100.0,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


# ---------------------------------------------------------------------------
# (1) Configuration -- the channel is expressible.
# ---------------------------------------------------------------------------
class TestEmailConfig:
    def test_absent_env_means_no_email_channel(self) -> None:
        """With no environment set there is no channel at all."""
        config = notify_config_from_env(_env())
        assert config.email is None

    def test_recipients_configure_the_channel_but_leave_it_disabled(self) -> None:
        """A recipient is a destination, not consent to send.

        This is the same rule the webhook channel follows: the master switch
        is what enables dispatch.
        """
        config = notify_config_from_env(_env(VALIDSIM_SMTP_TO=_RECIPIENT))
        assert config.email is not None
        assert config.email.to == (_RECIPIENT,)
        assert config.enabled is False

    def test_multiple_recipients_are_kept_in_order(self) -> None:
        config = notify_config_from_env(
            _env(VALIDSIM_SMTP_TO=f"{_RECIPIENT},{_SECOND_RECIPIENT}")
        )
        assert config.email is not None
        assert config.email.to == (_RECIPIENT, _SECOND_RECIPIENT)

    def test_channel_is_dry_run_by_default(self) -> None:
        """Live delivery requires an explicit opt-in.

        A configured recipient must never, by itself, start sending mail.
        """
        config = notify_config_from_env(_env(VALIDSIM_SMTP_TO=_RECIPIENT))
        assert config.email is not None
        assert config.email.live is False

    def test_live_requires_the_exact_value_one(self) -> None:
        """Only ``"1"`` enables live delivery.

        Mirrors the webhook channel's ``VALIDSIM_WEBHOOKS_LIVE`` gate: an
        ambiguous value is not consent.
        """
        for value in ("1",):
            config = notify_config_from_env(
                _env(VALIDSIM_SMTP_TO=_RECIPIENT, VALIDSIM_SMTP_LIVE=value)
            )
            assert config.email is not None and config.email.live is True

        for value in ("", "0", "true", "yes", "on", "live", "2"):
            config = notify_config_from_env(
                _env(VALIDSIM_SMTP_TO=_RECIPIENT, VALIDSIM_SMTP_LIVE=value)
            )
            assert config.email is not None, value
            assert config.email.live is False, value

    def test_min_severity_defaults_to_info(self) -> None:
        config = notify_config_from_env(_env(VALIDSIM_SMTP_TO=_RECIPIENT))
        assert config.email is not None
        assert config.email.min_severity == "info"

    def test_min_severity_is_read_from_the_environment(self) -> None:
        config = notify_config_from_env(
            _env(VALIDSIM_SMTP_TO=_RECIPIENT, VALIDSIM_SMTP_MIN_SEVERITY="warn")
        )
        assert config.email is not None
        assert config.email.min_severity == "warn"

    def test_unknown_min_severity_falls_back_to_info(self) -> None:
        """An unparseable threshold widens delivery rather than dropping it.

        Failing closed on severity would silently stop notifications, which
        is the failure an operator is least likely to notice.
        """
        config = notify_config_from_env(
            _env(VALIDSIM_SMTP_TO=_RECIPIENT, VALIDSIM_SMTP_MIN_SEVERITY="fatal")
        )
        assert config.email is not None
        assert config.email.min_severity == "info"

    def test_an_invalid_recipient_is_dropped_not_fatal(self) -> None:
        """One bad address must not disable the good ones."""
        config = notify_config_from_env(
            _env(VALIDSIM_SMTP_TO=f"not-an-address,{_RECIPIENT}")
        )
        assert config.email is not None
        assert config.email.to == (_RECIPIENT,)

    def test_a_fully_invalid_recipient_list_disables_the_channel(self) -> None:
        """With nothing valid left there is no channel to dispatch to."""
        config = notify_config_from_env(_env(VALIDSIM_SMTP_TO="not-an-address"))
        assert config.email is None

    def test_recipient_injection_attempts_are_rejected(self) -> None:
        """A crafted address must never smuggle a second recipient.

        The comma is the *list separator* -- the same convention as
        ``VALIDSIM_WEBHOOK_URLS`` -- so a comma yields two recipients, both
        validated. What must never survive is an address carrying the
        characters that enable SMTP header injection: CR/LF (to append a
        ``Bcc:`` header), whitespace, angle brackets or quotes. Those are
        rejected before the mail is ever assembled.
        """
        crafted = [
            "a@b.com\r\nBcc: victim@example.com",
            "a@b.com\nBcc: victim@example.com",
            "a b@example.com",
            "<a@example.com>",
            "a@example.com>",
            '"quoted"@example.com',
            "a@evil.com\r\nTo: victim@example.com",
            "a@example.com; b@example.com",
        ]
        for address in crafted:
            config = notify_config_from_env(_env(VALIDSIM_SMTP_TO=address))
            assert config.email is None, address

    def test_a_comma_separates_recipients_rather_than_joining_them(self) -> None:
        """The separator is a comma, and every resulting address is vetted.

        This is the same list convention the webhook channel uses, so an
        operator does not have to learn two different syntaxes.
        """
        config = notify_config_from_env(
            _env(VALIDSIM_SMTP_TO=f"{_RECIPIENT}, {_SECOND_RECIPIENT}")
        )
        assert config.email is not None
        assert config.email.to == (_RECIPIENT, _SECOND_RECIPIENT)
        # And no parsed address may contain a character that could split a
        # header when the recipients are joined into the To: header.
        for address in config.email.to:
            assert not any(ch in address for ch in "\r\n,;<>\""), address

    def test_summary_never_leaks_a_recipient_address(self) -> None:
        """The health summary reports a count, never an address.

        A recipient list is personally identifying, and the health probe is
        not the place to publish one.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_NOTIFY_ENABLED="1",
                VALIDSIM_SMTP_TO=f"{_RECIPIENT},{_SECOND_RECIPIENT}",
            )
        )
        summary = config.summary()
        email = summary["email"]
        assert isinstance(email, dict)
        assert email["configured"] is True
        assert email["recipient_count"] == 2
        rendered = repr(summary)
        assert _RECIPIENT not in rendered
        assert _SECOND_RECIPIENT not in rendered

    def test_summary_reports_the_channel_as_absent_by_default(self) -> None:
        summary = notify_config_from_env(_env()).summary()
        email = summary["email"]
        assert isinstance(email, dict)
        assert email["configured"] is False
        assert email["recipient_count"] == 0
        assert email["live"] == 0
        assert email["min_severity"] is None


# ---------------------------------------------------------------------------
# (2) Dispatch -- the channel is reachable and shares the webhook routing.
# ---------------------------------------------------------------------------
class TestEmailDispatch:
    def test_disabled_by_default_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A recipient alone must not produce a delivery."""
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        assert notify_run_completion(_scorecard()) == []

    def test_enabled_without_any_destination_sends_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        assert notify_run_completion(_scorecard()) == []

    def test_enabled_with_a_recipient_delivers_in_dry_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)

        results = notify_run_completion(_scorecard())

        assert len(results) == 1
        result = results[0]
        assert result.name == "email"
        assert result.ok is True
        # A dry-run result must be distinguishable from a real send.
        assert result.dry_run is True
        assert result.status_code is None
        assert result.error is None

    def test_the_result_never_carries_the_recipient_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A delivery result ends up in logs; it must not carry addresses."""
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)

        results = notify_run_completion(_scorecard())

        assert _RECIPIENT not in repr(results)

    def test_severity_routing_matches_the_webhook_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A channel that wants 'warn' must not receive an 'info' event.

        The email channel shares the webhook channel's severity decision, so
        both agree on whether an event was worth sending.
        """
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_MIN_SEVERITY", "warn")

        # APPROVE with a high composite is an "info" event.
        assert severity_from_scorecard(_scorecard("APPROVE", 90.0).to_dict()) == "info"
        assert notify_run_completion(_scorecard("APPROVE", 90.0)) == []

        # A BLOCK that clears the threshold is a "warn" event, so it does
        # reach the channel.
        assert severity_from_scorecard(_scorecard("BLOCK", 90.0).to_dict()) == "warn"
        assert notify_run_completion(_scorecard("BLOCK", 90.0)) != []

    def test_a_critical_only_channel_skips_a_warn_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 'critical' channel must not be woken by a mere 'warn'."""
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_MIN_SEVERITY", "critical")

        # A BLOCK above the threshold is "warn": not enough for this channel.
        assert severity_from_scorecard(_scorecard("BLOCK", 90.0).to_dict()) == "warn"
        assert notify_run_completion(_scorecard("BLOCK", 90.0)) == []

    def test_a_critical_only_channel_receives_a_critical_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A BLOCK below the threshold is 'critical' and does reach it."""
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_MIN_SEVERITY", "critical")

        assert severity_from_scorecard(_scorecard("BLOCK", 40.0).to_dict()) == "critical"
        assert notify_run_completion(_scorecard("BLOCK", 40.0)) != []

    def test_an_info_channel_receives_every_severity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_MIN_SEVERITY", "info")

        for decision, composite in (("APPROVE", 90.0), ("BLOCK", 40.0)):
            results = notify_run_completion(_scorecard(decision, composite))
            assert len(results) == 1, (decision, composite)

    def test_the_webhook_and_email_channels_are_independent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configuring one channel must not silence the other."""
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_WEBHOOK_URLS", "https://example.invalid/hooks/ci")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)

        results = notify_run_completion(_scorecard())
        assert len(results) == 2
        assert {r.name for r in results} == {"webhook-1", "email"}

    def test_a_config_layer_error_never_propagates_into_the_run_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken configuration must not lose a finished run."""
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("config exploded")

        monkeypatch.setattr(
            "validsim.notify.config.notify_config_from_env",
            _boom,
        )
        assert notify_run_completion(_scorecard()) == []


# ---------------------------------------------------------------------------
# (3) Live mode -- the opt-in gate.
# ---------------------------------------------------------------------------
class TestEmailLiveMode:
    def test_live_mode_attempts_a_real_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the opt-in set, the notifier really talks to SMTP.

        The socket is a stand-in that records the call, so the assertion is
        that the *code path* was taken, not that a mail left the machine.
        """
        sent: list[tuple[object, ...]] = []

        class _FakeSMTP:
            def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
                sent.append((host, port))

            def __enter__(self) -> "_FakeSMTP":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def starttls(self, context: object = None) -> None:
                return None

            def login(self, user: str, password: str) -> None:
                return None

            def sendmail(self, sender: str, to: list[str], message: bytes) -> None:
                sent.append((sender, tuple(to)))

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _FakeSMTP)
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_LIVE", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.invalid")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "validsim@example.invalid")

        results = notify_run_completion(_scorecard())

        assert len(results) == 1
        assert results[0].ok is True
        # A live result is NOT a dry run -- this is the distinction a
        # monitoring check needs.
        assert results[0].dry_run is False
        assert sent, "no SMTP attempt was made"

    def test_a_live_failure_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken mail server must not lose a finished run.

        The failure has to be *visible*, though: a silent drop would look
        exactly like a healthy quiet day.
        """
        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("connection refused")

        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _boom)
        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", _RECIPIENT)
        monkeypatch.setenv("VALIDSIM_SMTP_LIVE", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_HOST", "smtp.example.invalid")
        monkeypatch.setenv("VALIDSIM_SMTP_FROM", "validsim@example.invalid")

        results = notify_run_completion(_scorecard())

        assert len(results) == 1
        assert results[0].ok is False
        assert results[0].error
        assert results[0].dry_run is False


# ---------------------------------------------------------------------------
# (4) Health surface -- the channel is operator-visible.
# ---------------------------------------------------------------------------
class TestEmailHealthSurface:
    def test_health_reports_the_configured_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from validsim.api.main import create_app
        from validsim.store import ValidationStore

        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_SMTP_TO", f"{_RECIPIENT},{_SECOND_RECIPIENT}")

        client = TestClient(create_app(ValidationStore()))
        body = client.get("/api/v1/health").json()

        email = body["notify"]["email"]
        assert email["configured"] is True
        assert email["recipient_count"] == 2
        assert email["live"] == 0
        # The addresses themselves are not published.
        assert _RECIPIENT not in repr(body)


# ---------------------------------------------------------------------------
# (5) The channel is genuinely part of the shipped package.
# ---------------------------------------------------------------------------
class TestEmailIsShipped:
    def test_the_email_module_is_imported_by_a_shipped_entrypoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The channel must be reachable from a real entrypoint.

        A capability that only the test suite can reach is dead code wearing
        a feature's clothes -- which is exactly how the email channel spent
        its life before this change.
        """
        script = (
            "import sys; import validsim.cli; import validsim.api.main; "
            "assert 'validsim.notify.email' in sys.modules, sorted(sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr

    def test_the_email_spec_is_exported_for_operators(self) -> None:
        import validsim.notify as notify

        assert "EmailSpec" in notify.__all__
        assert "ENV_EMAIL_TO" in notify.__all__

    def test_the_exported_email_spec_is_usable_on_its_own(self) -> None:
        """``EmailSpec`` is a public dataclass, so its defaults are a contract.

        An operator's own code (or a downstream integration) constructs this
        directly rather than going through the environment, so the safe
        defaults have to hold for the type itself and not only for the
        environment-parsed instance.
        """
        spec = EmailSpec(to=(_RECIPIENT,))
        assert spec.to == (_RECIPIENT,)
        # Dry-run is the default: constructing the spec is not consent to send.
        assert spec.live is False
        assert spec.min_severity == "info"


# ---------------------------------------------------------------------------
# (6) The env-var surface is classified.
# ---------------------------------------------------------------------------
class TestEmailEnvVarClassification:
    def test_every_email_env_var_is_classified_as_infrastructure(self) -> None:
        """Every env var the package reads must be registered.

        An unregistered variable would escape the suite-wide isolation
        guarantee, so an operator's exported value could reach the tests.
        """
        from validsim.project_config import INFRA_KEYS

        for name in (
            "VALIDSIM_SMTP_TO",
            "VALIDSIM_SMTP_MIN_SEVERITY",
            "VALIDSIM_SMTP_LIVE",
        ):
            assert name in INFRA_KEYS, name

    def test_the_recipient_list_is_not_treated_as_a_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A recipient list is PII, not a credential.

        It is classified as infrastructure, so it is reported as an ordinary
        key rather than masked -- but it is still never published by the
        health summary, which is what the previous test pins.
        """
        from validsim.project_config import SECRET_KEYS

        assert "VALIDSIM_SMTP_TO" not in SECRET_KEYS
