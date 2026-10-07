"""SMTP email delivery of scorecards and arbitrary messages.

The notifier defaults to **dry-run**: it records what *would* be sent in the
:data:`EmailNotifier.sent` list and performs no socket I/O — the safe behaviour
for tests and local runs, mirroring
:class:`validsim.notify.dispatcher.WebhookDispatcher`. Live SMTP delivery is
opt-in via ``dry_run=False``; the connection settings are read lazily from the
environment at send time (not import time):

    * ``VALIDSIM_SMTP_HOST`` — SMTP server hostname.
    * ``VALIDSIM_SMTP_PORT`` — server port (default ``587``).
    * ``VALIDSIM_SMTP_USER`` / ``VALIDSIM_SMTP_PASSWORD`` — optional AUTH.
    * ``VALIDSIM_SMTP_FROM`` — sender (``From``) address.
    * ``VALIDSIM_SMTP_TLS`` — when ``"0"`` STARTTLS is disabled (default on).

Delivery failures are captured on :class:`EmailDelivery` and never propagate
to callers.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
from validsim.engine.scorecard import Scorecard

__all__ = [
    "EmailDelivery",
    "EmailNotifier",
    "SmtpSettings",
    "scorecard_email_body",
    "scorecard_text_body",
]

#: Environment variable names for SMTP settings.
_ENV_HOST = "VALIDSIM_SMTP_HOST"
_ENV_PORT = "VALIDSIM_SMTP_PORT"
_ENV_USER = "VALIDSIM_SMTP_USER"
_ENV_PASSWORD = "VALIDSIM_SMTP_PASSWORD"
_ENV_FROM = "VALIDSIM_SMTP_FROM"
_ENV_TLS = "VALIDSIM_SMTP_TLS"

#: Default SMTP submission port.
_DEFAULT_PORT = 587
#: Per-connection timeout (seconds) for live deliveries.
_TIMEOUT_S = 5.0

#: Conservative single-address pattern used to vet every recipient (finding
#: M2). It accepts an RFC-legal local part (letters, digits and ``._%+-``), a
#: single ``@``, and a dot-separated domain ending in an alphabetic TLD. It
#: deliberately rejects the CR/LF, whitespace, ``< >`` , quoting and separator
#: characters that enable SMTP header injection or recipient smuggling, so a
#: crafted address can never smuggle extra ``To``/``Bcc`` headers.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

#: Header values must never contain these; a bare CR or LF would let an
#: attacker terminate the ``Subject`` header and append forged headers.
_FORBIDDEN_HEADER_CHARS = ("\r", "\n")


def _scorecard_from_dict(data: dict[str, Any]) -> Scorecard:
    """Reconstruct a :class:`Scorecard` from its ``to_dict()`` output.

    The only structural difference is the bootstrap confidence interval, which
    is serialized as a list but stored on the dataclass as a tuple.
    """
    kwargs = dict(data)
    ci = kwargs.get("confidence_interval")
    if ci is not None and not isinstance(ci, tuple):
        kwargs["confidence_interval"] = tuple(ci)
    return Scorecard(**kwargs)


def scorecard_email_body(scorecard: dict[str, Any]) -> str:
    """Build a styled HTML email body from a scorecard ``dict``.

    Delegates to :func:`validsim.engine.export.scorecard_to_html` after
    reconstructing the :class:`Scorecard`.
    """
    return scorecard_to_html(_scorecard_from_dict(scorecard))


def scorecard_text_body(scorecard: dict[str, Any]) -> str:
    """Build a plain-text email body from a scorecard ``dict``.

    Delegates to :func:`validsim.engine.export.scorecard_to_markdown` so the
    multi-part email has a readable fallback for text-only clients.
    """
    return scorecard_to_markdown(_scorecard_from_dict(scorecard))


@dataclass(frozen=True)
class EmailDelivery:
    """Outcome of a single email send attempt.

    Attributes:
        to: Recipient addresses.
        subject: Message subject.
        ok: Whether the send succeeded (in dry-run, always ``True``).
        dry_run: Whether this delivery was recorded without any socket I/O.
        error: Failure reason, or ``None`` when the attempt was clean.
    """

    to: tuple[str, ...]
    subject: str
    ok: bool
    dry_run: bool
    error: str | None = None


@dataclass(frozen=True)
class SmtpSettings:
    """SMTP connection settings, lazily read from the environment.

    Attributes:
        host: SMTP server hostname (``None`` when unset).
        port: SMTP server port (default 587).
        username: Optional SMTP AUTH username.
        password: Optional SMTP AUTH password.
        from_addr: Sender (``From``) address.
        use_tls: Whether to ``starttls()`` before authenticating/sending.
    """

    host: str | None = None
    port: int = _DEFAULT_PORT
    username: str | None = None
    # repr=False keeps the SMTP AUTH secret out of repr()/str(); logging or
    # debugging the object (e.g. ``logger.info("settings=%s", settings)``)
    # therefore cannot leak the password into log files (finding L2).
    password: str | None = field(default=None, repr=False)
    from_addr: str | None = None
    use_tls: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "SmtpSettings":
        """Parse SMTP settings from ``environ`` (defaults to ``os.environ``).

        Unset/blank values yield ``None`` for the optional fields and the
        documented defaults for ``port`` (587) and ``use_tls`` (enabled).
        """
        if environ is None:
            environ = os.environ
        host = environ.get(_ENV_HOST, "").strip() or None
        raw_port = environ.get(_ENV_PORT, "").strip()
        port = int(raw_port) if raw_port else _DEFAULT_PORT
        username = environ.get(_ENV_USER, "").strip() or None
        password = environ.get(_ENV_PASSWORD, "") or None
        from_addr = environ.get(_ENV_FROM, "").strip() or None
        use_tls = environ.get(_ENV_TLS, "1").strip() != "0"
        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            from_addr=from_addr,
            use_tls=use_tls,
        )


def _validate_subject(subject: str) -> None:
    """Reject subjects carrying CR/LF (SMTP header injection, finding M2).

    A ``Subject`` header value containing a carriage return or line feed lets
    an attacker close the header and inject arbitrary following headers (for
    example a covert ``Bcc``), so such subjects are refused outright.

    Args:
        subject: Proposed message subject line.

    Raises:
        ValueError: If ``subject`` contains a CR or LF character.
    """
    if any(char in subject for char in _FORBIDDEN_HEADER_CHARS):
        raise ValueError("subject must not contain CR or LF characters")


def _validate_recipients(to: Sequence[str]) -> None:
    """Validate every recipient against :data:`_EMAIL_RE` (finding M2).

    Args:
        to: Recipient addresses to vet.

    Raises:
        ValueError: If any address is empty or does not match the
            conservative single-address pattern.
    """
    for recipient in to:
        if not _EMAIL_RE.match(recipient):
            raise ValueError(f"invalid recipient address: {recipient!r}")


def _build_message(
    from_addr: str,
    to: list[str],
    subject: str,
    body_html: str | None,
    body_text: str | None,
) -> Message:
    """Assemble a MIME message, using multipart/alternative when both bodies exist."""
    if body_html and body_text:
        message: Message = MIMEMultipart("alternative")
        message.attach(MIMEText(body_text, "plain", "utf-8"))
        message.attach(MIMEText(body_html, "html", "utf-8"))
    elif body_html:
        message = MIMEText(body_html, "html", "utf-8")
    else:
        message = MIMEText(body_text or "", "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to)
    return message


class EmailNotifier:
    """Deliver email messages (or scorecards) over SMTP.

    Args:
        dry_run: When ``True`` (default) no socket I/O is performed; each call
            records an ``ok=True`` :class:`EmailDelivery` on :attr:`sent`.
    """

    def __init__(self, dry_run: bool = True) -> None:
        self._dry_run = dry_run
        self._sent: list[EmailDelivery] = []

    @property
    def dry_run(self) -> bool:
        """Whether this notifier records without performing SMTP I/O."""
        return self._dry_run

    @property
    def sent(self) -> list[EmailDelivery]:
        """Copy of every delivery recorded so far (dry-run and live)."""
        return list(self._sent)

    def send(
        self,
        to: list[str],
        subject: str,
        body_html: str | None = None,
        body_text: str | None = None,
    ) -> EmailDelivery:
        """Send ``subject``/bodies to every address in ``to``.

        In dry-run mode the delivery is recorded without any socket I/O. In
        live mode an SMTP connection is established (settings read lazily from
        the environment); transport/configuration errors are captured on the
        returned :class:`EmailDelivery`, never raised.

        Header-injection guards (finding M2) run first, in *both* modes: a
        subject carrying CR/LF, or a recipient that is not a clean single
        address, raises :class:`ValueError` before any message is assembled or
        recorded.
        """
        if not to:
            raise ValueError("at least one recipient is required")
        _validate_subject(subject)
        _validate_recipients(to)
        if self._dry_run:
            delivery = EmailDelivery(tuple(to), subject, True, True)
        else:
            delivery = self._deliver(to, subject, body_html, body_text)
        self._sent.append(delivery)
        return delivery

    def send_scorecard(
        self, to: list[str], subject: str, scorecard: dict[str, Any]
    ) -> EmailDelivery:
        """Send a scorecard ``dict``, auto-building HTML and text bodies."""
        return self.send(
            to=to,
            subject=subject,
            body_html=scorecard_email_body(scorecard),
            body_text=scorecard_text_body(scorecard),
        )

    def _deliver(
        self,
        to: list[str],
        subject: str,
        body_html: str | None,
        body_text: str | None,
    ) -> EmailDelivery:
        """Perform one live SMTP send; never raise.

        Any failure — missing configuration, connection, TLS, auth, or
        sendmail — is captured in the returned :class:`EmailDelivery`.
        """
        try:
            settings = SmtpSettings.from_env()
            if not settings.host:
                raise ValueError(f"SMTP host not configured (set {_ENV_HOST})")
            if not settings.from_addr:
                raise ValueError(f"SMTP sender not configured (set {_ENV_FROM})")
            message = _build_message(settings.from_addr, to, subject, body_html, body_text)
            with smtplib.SMTP(settings.host, settings.port, timeout=_TIMEOUT_S) as smtp:
                if settings.use_tls:
                    # An explicit SSL context is required: smtplib.starttls() with no
                    # arguments does NOT verify certificates or hostnames, which makes
                    # the STARTTLS channel trivially MITM-able. Certifi (a transitive
                    # dependency of httpx, already a hard requirement) supplies the
                    # system CA bundle; fall back to the stdlib default if unavailable.
                    try:
                        import certifi

                        context = ssl.create_default_context(cafile=certifi.where())
                    except ImportError:  # pragma: no cover - certifi ships with httpx
                        context = ssl.create_default_context()
                    smtp.starttls(context=context)
                if settings.username is not None:
                    smtp.login(settings.username, settings.password or "")
                smtp.sendmail(settings.from_addr, list(to), message.as_string())
        except Exception as exc:  # noqa: BLE001 - never let delivery raise
            return EmailDelivery(tuple(to), subject, False, False, str(exc))
        return EmailDelivery(tuple(to), subject, True, False)