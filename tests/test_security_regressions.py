"""Regression tests for security and robustness defects fixed in this iteration.

Each test here pins a specific defect that previously shipped. They are written
to FAIL against the old code and PASS against the fix, so a future refactor
cannot silently reintroduce the bug.

Covered defects:
  1. Webhook POSTs omitted ``Content-Type: application/json`` (httpx only sets it
     for ``json=``), so Slack and other JSON receivers rejected/misparsed bodies.
  2. ``smtplib.starttls()`` was called with no SSL context, so certificates and
     hostnames were never verified -- a MITM on the SMTP channel.
  3. ReportLab ``Paragraph`` interpolates a lightweight XML-ish markup, so
     caller-supplied ids and failure-mode names were injected unescaped.
  4. ``anomaly._taxonomy`` used a bare ``int(v)`` instead of the tolerant
     ``_coerce.as_int``, so malformed taxonomy counts raised instead of
     degrading to 0 like every sibling reader.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from validsim.engine.anomaly import detect_anomalies
from validsim.engine.scorecard import Scorecard
from validsim.notify.dispatcher import WebhookDispatcher

# --------------------------------------------------------------------------- #
# 1. Webhook Content-Type
# --------------------------------------------------------------------------- #


def test_webhook_post_sends_json_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live webhook POST must declare application/json.

    httpx does not infer Content-Type when the body is passed via ``content=``,
    so the header has to be set explicitly or receivers may 400 or misparse it.
    """
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

    def _fake_post(url: str, *, content: str, headers: dict[str, str], timeout: float) -> Any:
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return _Response()

    monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _fake_post)

    dispatcher = WebhookDispatcher(live=True)
    dispatcher.register("hook", "https://hooks.example.com/abc")
    results = dispatcher.dispatch(_scorecard())

    assert [r.ok for r in results] == [True]
    assert captured["headers"].get("Content-Type") == "application/json"
    # The body must still be valid JSON so a receiver can parse what we declared.
    assert json.loads(captured["content"])["run_id"] == "vrun-cafe1234"


def test_webhook_signature_is_computed_over_the_exact_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HMAC must cover the same bytes that are actually transmitted."""
    import hashlib
    import hmac

    captured: dict[str, Any] = {}

    class _Response:
        status_code = 200

    def _fake_post(url: str, *, content: str, headers: dict[str, str], timeout: float) -> Any:
        captured["content"] = content
        captured["headers"] = headers
        return _Response()

    monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _fake_post)

    dispatcher = WebhookDispatcher(live=True)
    dispatcher.register("hook", "https://hooks.example.com/abc", secret="s3cr3t")
    dispatcher.dispatch(_scorecard())

    expected = hmac.new(b"s3cr3t", captured["content"].encode(), hashlib.sha256).hexdigest()
    assert captured["headers"]["X-ValidSim-Signature"] == expected
    assert captured["headers"]["Content-Type"] == "application/json"


# --------------------------------------------------------------------------- #
# 2. STARTTLS certificate verification
# --------------------------------------------------------------------------- #


def test_starttls_receives_a_verifying_ssl_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """STARTTLS must be given a context that actually verifies certificates.

    ``smtplib.starttls()`` with no argument creates a default context that does
    not validate the peer certificate or hostname, which makes the encrypted
    channel trivially MITM-able.
    """
    import ssl

    from validsim.notify.email import EmailNotifier

    seen: dict[str, Any] = {}

    class _FakeSMTP:
        def __init__(self, host: str, port: int, timeout: object = None) -> None:
            pass

        def __enter__(self) -> "_FakeSMTP":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def starttls(self, context: object = None) -> None:
            seen["context"] = context

        def login(self, user: str, password: str) -> None:
            pass

        def sendmail(self, from_addr: str, to_addrs: list, msg: str) -> None:
            pass

    monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _FakeSMTP)
    monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.example.com")
    monkeypatch.setenv("VALIDSIM_SMTP_FROM", "valid@example.com")

    result = EmailNotifier(dry_run=False).send(to=["a@example.com"], subject="Hi")

    assert result.ok is True, result.error
    context = seen.get("context")
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


# --------------------------------------------------------------------------- #
# 3. ReportLab Paragraph markup injection
# --------------------------------------------------------------------------- #


def test_pdf_escapes_markup_in_caller_supplied_identifiers() -> None:
    """Hostile ids must be escaped, not interpreted as ReportLab markup."""

    from validsim.engine.pdf import scorecard_pdf_bytes

    card = _scorecard(
        checkpoint_id="evil</b><font size='40'>INJECTED</font>",
        task_id="a & b < c",
    ).to_dict()

    # Must render rather than raise a ReportLab parse error.
    data = scorecard_pdf_bytes(card)
    assert data.startswith(b"%PDF")


def test_pdf_escapes_markup_in_failure_mode_names() -> None:
    """A hostile failure-mode name must not break or hijack the taxonomy table."""

    from validsim.engine.pdf import scorecard_pdf_bytes

    card = _scorecard().to_dict()
    card["failure_taxonomy"] = {"<b>bold</b> & <i>italic</i>": 3}

    data = scorecard_pdf_bytes(card)
    assert data.startswith(b"%PDF")


def test_pdf_escapes_markup_in_deploy_decision() -> None:
    """A hostile verdict string must render as text, not as ReportLab markup.

    ``deploy_decision`` flows into the verdict banner Paragraph, which parses
    XML-ish markup, so it needs the same escaping as the other caller-supplied
    fields. It must also never be mistaken for a real APPROVE.
    """

    from validsim.engine.pdf import scorecard_pdf_bytes

    card = _scorecard().to_dict()
    card["deploy_decision"] = "APPROVE<b>FAKE</b>"

    data = scorecard_pdf_bytes(card)
    assert data.startswith(b"%PDF")


def test_pdf_escapes_markup_in_taxonomy_count() -> None:
    """The taxonomy count column is caller data too and must be escaped."""

    from validsim.engine.pdf import scorecard_pdf_bytes

    card = _scorecard().to_dict()
    card["failure_taxonomy"] = {"grip-slip": "<i>3</i>"}

    data = scorecard_pdf_bytes(card)
    assert data.startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# 4. Anomaly taxonomy coercion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_count",
    [None, "abc", "", "12abc", [], {}, object()],
)
def test_anomaly_tolerates_malformed_taxonomy_counts(bad_count: object) -> None:
    """Malformed taxonomy counts degrade to 0 instead of raising.

    ``_taxonomy`` previously used a bare ``int(v)``, so a single malformed count
    raised ValueError/TypeError straight out of ``detect_anomalies``, breaking
    the "degrade gracefully, never raise mid-report" contract that the rest of
    the engine relies on.
    """
    baseline = [
        {
            "run_id": f"vrun-{i:08x}",
            "total_episodes": 100,
            "failure_taxonomy": {"collision": 1, "timeout": 1},
        }
        for i in range(4)
    ]
    # A stable baseline plus a current run whose taxonomy is malformed.
    history = baseline + [
        {
            "run_id": "vrun-current",
            "total_episodes": 100,
            "failure_taxonomy": {"collision": bad_count, "timeout": 2},
        }
    ]

    # Must not raise. A malformed count is coerced to 0, so no collision spike.
    anomalies = detect_anomalies(history)
    assert all(a.failure_mode != "collision" for a in anomalies)


def test_anomaly_uses_as_int_so_float_truncation_is_preserved() -> None:
    """A float count is truncated by ``as_int`` exactly as the old reader did."""
    history = [
        {
            "run_id": f"vrun-{i:08x}",
            "total_episodes": 100,
            "failure_taxonomy": {"collision": 1},
        }
        for i in range(4)
    ]
    history.append(
        {
            "run_id": "vrun-current",
            "total_episodes": 100,
            "failure_taxonomy": {"collision": 2.9},
        }
    )

    # 2.9 -> 2 via as_int; baseline is flat at 1, so a small bump, not a spike.
    anomalies = detect_anomalies(history)
    assert all(a.failure_mode != "collision" for a in anomalies)


# --------------------------------------------------------------------------- #
# Shared fixture data
# --------------------------------------------------------------------------- #


def _scorecard(
    *,
    run_id: str = "vrun-cafe1234",
    checkpoint_id: str = "ckpt-1",
    task_id: str = "pick-place",
) -> Scorecard:
    """A minimal, valid scorecard for renderer/dispatcher tests."""
    return Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        composite_score=91.5,
        success_rate=0.94,
        safety_score=95.0,
        robustness_score=90.0,
        regression_delta=None,
        confidence_interval=(0.90, 0.97),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=1000,
        failure_taxonomy={},
    )
