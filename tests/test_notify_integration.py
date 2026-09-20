"""End-to-end integration of the *whole* notify surface on ONE scorecard dict.

Where the unit tests (``test_notify*.py``) each pin a single behaviour in
isolation, this module wires the public pieces together and drives them from a
single :class:`~validsim.engine.scorecard.Scorecard`:

* :class:`WebhookDispatcher` with a ``slack`` hook + a ``json`` hook + an
  HMAC-secret hook — all three fire in **dry-run** with zero network I/O.
* :class:`EmailNotifier` in **dry-run** — ``send_scorecard`` renders an HTML
  body from the *same* scorecard via :func:`scorecard_to_html`.
* Live signature correctness — the ``X-ValidSim-Signature`` header is
  recomputed independently with :mod:`hmac` over the exact bytes sent.
* Live retry/backoff — a flaky transport (monkeypatched ``httpx.post``) is
  retried with exponential backoff and eventually succeeds.

Source is never modified; every collaborator is stubbed through ``monkeypatch``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from validsim.engine.export import scorecard_to_html
from validsim.engine.scorecard import Scorecard
from validsim.notify import (
    DeliveryResult,
    EmailDelivery,
    EmailNotifier,
    WebhookDispatcher,
    scorecard_email_body,
)

#: Shared HMAC secret for the signed hook.
_SECRET = "s3cr3t-integration-key"
#: Distinct target URLs so captured live POSTs can be told apart.
_SLACK_URL = "https://example.invalid/hooks/slack"
_JSON_URL = "https://example.invalid/hooks/json"
_SECURE_URL = "https://example.invalid/hooks/secure"
#: Header name the dispatcher uses for the HMAC body signature.
_SIG_HEADER = "X-ValidSim-Signature"


def _no_network(*args: object, **kwargs: object) -> None:
    """Stand-in for ``httpx.post`` that fails loudly if dry-run touches it."""
    raise AssertionError("dry-run must not perform network I/O")


def _no_socket(*args: object, **kwargs: object) -> None:
    """Stand-in for ``smtplib.SMTP`` that fails loudly if dry-run opens one."""
    raise AssertionError("dry-run must not open an SMTP socket")


@pytest.fixture
def scorecard() -> Scorecard:
    """The single scorecard every test in this module renders/dispatches."""
    return Scorecard(
        run_id="vrun-integr01",
        checkpoint_id="ckpt-7",
        task_id="pick-place",
        composite_score=91.5,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=-0.1,
        confidence_interval=(0.82, 0.95),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7, "grasp_failure": 3},
    )


@pytest.fixture(autouse=True)
def _force_dry_run_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambient env must not silently flip the dispatcher into live mode."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry backoff instantaneous unless a test records it explicitly."""
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# (1) Dispatcher: slack + json + HMAC-secret hooks all fire in dry-run.
# ---------------------------------------------------------------------------
class TestDispatcherDryRunAllHooks:
    def test_three_hooks_fire_in_dry_run_without_network(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _no_network)
        dispatcher = WebhookDispatcher()  # dry-run default
        assert dispatcher.live is False

        dispatcher.register("ops-slack", _SLACK_URL, format="slack")
        dispatcher.register("ci-json", _JSON_URL)  # default format="json"
        dispatcher.register("secure", _SECURE_URL, secret=_SECRET)

        results = dispatcher.dispatch(scorecard)

        # Registration order preserved; every hook reports a clean dry-run send.
        assert [r.name for r in results] == ["ops-slack", "ci-json", "secure"]
        assert all(isinstance(r, DeliveryResult) and r.ok for r in results)
        assert all(r.status_code is None and r.error is None for r in results)
        assert [r.url for r in results] == [_SLACK_URL, _JSON_URL, _SECURE_URL]
        # The accumulator mirrors the returned list (one record per hook).
        assert len(dispatcher.sent) == 3


# ---------------------------------------------------------------------------
# (2) EmailNotifier dry-run produces an HTML body from the same scorecard.
# ---------------------------------------------------------------------------
class TestEmailDryRunHtmlBody:
    def test_send_scorecard_renders_html_via_scorecard_to_html(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        card_dict = scorecard.to_dict()
        produced: list[str] = []
        original = scorecard_to_html

        def _spy(sc: Scorecard) -> str:
            html = original(sc)
            produced.append(html)
            return html

        # Observe (without altering) that the dry-run send renders HTML through
        # scorecard_to_html, and that no SMTP socket is ever opened.
        monkeypatch.setattr("validsim.notify.email.scorecard_to_html", _spy)
        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _no_socket)

        delivery = EmailNotifier().send_scorecard(
            to=["ops@example.com"], subject="Validation", scorecard=card_dict
        )

        assert isinstance(delivery, EmailDelivery)
        assert delivery.ok is True and delivery.dry_run is True and delivery.error is None
        assert delivery.to == ("ops@example.com",)

        assert produced, "dry-run send_scorecard must render the HTML body"
        html = produced[0]
        # The body is *exactly* what scorecard_to_html produces for this card.
        assert html == scorecard_to_html(scorecard)
        assert "<!DOCTYPE html>" in html
        assert "91.5" in html and "APPROVE" in html and "<style>" in html

    def test_email_body_equals_direct_render_of_same_scorecard(
        self, scorecard: Scorecard
    ) -> None:
        # The public helper the notifier uses is a thin delegation to export.
        assert scorecard_email_body(scorecard.to_dict()) == scorecard_to_html(scorecard)


# ---------------------------------------------------------------------------
# (3) Signature header correctness, recomputed independently with hmac.
# ---------------------------------------------------------------------------
class TestSignatureCorrectness:
    def test_signature_matches_hmac_and_only_signed_hook_carries_it(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        captured: list[dict[str, object]] = []

        def _post(url: str, **kwargs: object) -> SimpleNamespace:
            captured.append(
                {
                    "url": url,
                    "body": kwargs["content"],
                    "headers": dict(kwargs["headers"]),  # type: ignore[arg-type]
                }
            )
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ops-slack", _SLACK_URL, format="slack")
        dispatcher.register("ci-json", _JSON_URL)
        dispatcher.register("secure", _SECURE_URL, secret=_SECRET)

        results = dispatcher.dispatch(scorecard)
        assert all(r.ok and r.status_code == 200 for r in results)

        by_url = {c["url"]: c for c in captured}
        assert set(by_url) == {_SLACK_URL, _JSON_URL, _SECURE_URL}

        # The signed hook's header equals an independent HMAC-SHA256 of the
        # exact JSON bytes that went over the wire.
        secure = by_url[_SECURE_URL]
        expected = hmac.new(
            _SECRET.encode("utf-8"), secure["body"].encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert secure["headers"][_SIG_HEADER] == expected

        # Hooks without a secret must NOT advertise a signature.
        assert _SIG_HEADER not in by_url[_JSON_URL]["headers"]
        assert _SIG_HEADER not in by_url[_SLACK_URL]["headers"]

        # Each format renders the same scorecard differently on the wire.
        slack_body = json.loads(by_url[_SLACK_URL]["body"])
        assert "blocks" in slack_body
        json_body = json.loads(by_url[_JSON_URL]["body"])
        assert json_body["composite_score"] == scorecard.composite_score
        assert "blocks" not in json_body

    def test_tampered_body_invalidates_signature(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """A signature only validates the precise bytes it was computed over."""
        captured: dict[str, object] = {}

        def _post(url: str, **kwargs: object) -> SimpleNamespace:
            captured["body"] = kwargs["content"]
            captured["headers"] = dict(kwargs["headers"])  # type: ignore[arg-type]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("secure", _SECURE_URL, secret=_SECRET)
        dispatcher.dispatch(scorecard)

        header = captured["headers"][_SIG_HEADER]  # type: ignore[index]
        tampered = str(captured["body"]) + " "  # one trailing byte changes all
        recomputed = hmac.new(
            _SECRET.encode("utf-8"), tampered.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert header != recomputed


# ---------------------------------------------------------------------------
# (4) Retry backoff on a flaky transport (monkeypatched httpx).
# ---------------------------------------------------------------------------
class TestRetryBackoff:
    def test_flaky_transport_recovers_after_exponential_backoff(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        calls: list[str] = []

        def _flaky(url: str, **kwargs: object) -> SimpleNamespace:
            calls.append(url)
            if len(calls) < 3:
                raise ConnectionError("transient blip")
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _flaky)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci-json", _JSON_URL, secret=_SECRET)

        (result,) = dispatcher.dispatch(scorecard)

        assert result.ok is True and result.status_code == 200 and result.error is None
        assert len(calls) == 3  # 1 initial attempt + 2 retries
        assert sleeps == [0.1, 0.2]  # base * 2**attempt

    def test_permanent_failure_gives_up_after_two_retries(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        calls: list[str] = []

        def _broken(url: str, **kwargs: object) -> SimpleNamespace:
            calls.append(url)
            return SimpleNamespace(status_code=503)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _broken)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci-json", _JSON_URL)

        (result,) = dispatcher.dispatch(scorecard)

        assert result.ok is False and result.status_code == 503
        assert "503" in (result.error or "")
        assert len(calls) == 3  # never more than 2 retries
        assert sleeps == [0.1, 0.2]


# ---------------------------------------------------------------------------
# Cross-surface smoke: both dry-run paths share one scorecard, zero I/O.
# ---------------------------------------------------------------------------
class TestFullSurfaceDryRun:
    def test_dispatcher_and_email_share_one_scorecard_without_io(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        card_dict = scorecard.to_dict()
        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _no_network)
        monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _no_socket)

        dispatcher = WebhookDispatcher()
        dispatcher.register("ops-slack", _SLACK_URL, format="slack")
        dispatcher.register("ci-json", _JSON_URL)
        dispatcher.register("secure", _SECURE_URL, secret=_SECRET)
        dispatch_results = dispatcher.dispatch(scorecard)

        notifier = EmailNotifier()
        delivery = notifier.send_scorecard(
            to=["ops@example.com"], subject="Validation", scorecard=card_dict
        )

        assert len(dispatch_results) == 3 and all(r.ok for r in dispatch_results)
        assert delivery.ok is True and delivery.dry_run is True
        # The HTML the email would send reflects the very same numbers the
        # dispatcher fans out (composite score + decision present in both).
        html = scorecard_email_body(card_dict)
        assert str(card_dict["composite_score"]) in html
        assert card_dict["deploy_decision"] in html
