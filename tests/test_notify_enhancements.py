"""Tests for notification enhancements: Slack formatting, retries, HMAC signing.

Dry-run default behaviour is covered by ``tests/test_notify.py`` and must stay
untouched; here we exercise the new live-mode features with stubbed transport.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher, format_slack_blocks

_URL = "https://example.invalid/hook"


def _scorecard() -> Scorecard:
    return Scorecard(
        run_id="vrun-cafe1234",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry backoff instantaneous and observable in tests."""
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _force_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test accidentally performs real network delivery."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)


class TestSlackFormatter:
    def test_blocks_structure(self) -> None:
        payload = format_slack_blocks(_scorecard().to_dict())
        blocks = payload["blocks"]
        assert len(blocks) == 3
        assert blocks[0]["type"] == "header"
        header_text = blocks[0]["text"]["text"]
        assert "APPROVE" in header_text
        section_text = blocks[1]["text"]["text"]
        assert "90.0" in section_text  # composite score
        assert "APPROVE" in section_text
        assert "85.0" in section_text  # threshold

    def test_blocked_decision_uses_negative_marker(self) -> None:
        card = _scorecard()
        blocked = card.to_dict() | {"deploy_decision": "BLOCK"}
        blocks = format_slack_blocks(blocked)["blocks"]
        assert "BLOCK" in blocks[0]["text"]["text"]
        assert ":no_entry:" in blocks[0]["text"]["text"]

    def test_missing_fields_do_not_crash(self) -> None:
        payload = format_slack_blocks({})
        assert payload["blocks"]


class TestSlackDispatchFormat:
    def test_slack_hook_sends_blocks_over_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def _post(url, **kwargs):
            captured["url"] = url
            captured["body"] = kwargs["content"]
            captured["headers"] = kwargs["headers"]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ops", _URL, format="slack")
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is True
        body = json.loads(captured["body"])
        assert "blocks" in body
        assert "APPROVE" in body["blocks"][0]["text"]["text"]

    def test_json_hook_sends_raw_scorecard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def _post(url, **kwargs):
            captured["body"] = kwargs["content"]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)  # default format="json"
        dispatcher.dispatch(_scorecard())

        body = json.loads(captured["body"])
        assert body["composite_score"] == 90.0
        assert "blocks" not in body

    def test_invalid_format_rejected(self) -> None:
        dispatcher = WebhookDispatcher()
        with pytest.raises(ValueError, match="format"):
            dispatcher.register("bad", _URL, format="xml")  # type: ignore[arg-type]


class TestRetryBackoff:
    def test_recovers_after_transient_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def _flaky(url, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("blip")
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _flaky)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is True and result.status_code == 200 and result.error is None
        assert len(calls) == 3  # 1 initial attempt + 2 retries

    def test_permanent_http_failure_gives_up_after_two_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def _broken(url, **kwargs):
            calls.append(1)
            return SimpleNamespace(status_code=503)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _broken)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False and result.status_code == 503
        assert "503" in (result.error or "")
        assert len(calls) == 3  # never more than 2 retries

    def test_backoff_is_exponential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )

        def _broken(url, **kwargs):
            return SimpleNamespace(status_code=500)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _broken)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert sleeps == [0.1, 0.2]  # base * 2**attempt

    def test_dry_run_never_retries_or_hits_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("network must not be used in dry-run mode")

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _boom)
        dispatcher = WebhookDispatcher()  # dry-run default
        dispatcher.register("ci", _URL, format="slack", secret="s3cr3t")
        results = dispatcher.dispatch(_scorecard())

        assert results == [results[0]] and results[0].ok
        assert results[0].status_code is None and results[0].error is None


class TestSecretSignature:
    def test_signature_header_matches_hmac_of_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def _post(url, **kwargs):
            captured["body"] = kwargs["content"]
            captured["headers"] = kwargs["headers"]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret="s3cr3t")
        dispatcher.dispatch(_scorecard())

        expected = hmac.new(
            b"s3cr3t", captured["body"].encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert captured["headers"]["X-ValidSim-Signature"] == expected

    def test_no_signature_header_without_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def _post(url, **kwargs):
            captured["headers"] = kwargs["headers"]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert "X-ValidSim-Signature" not in captured["headers"]

    def test_signed_body_stable_across_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The signature must cover the exact bytes sent, even on retries."""
        bodies: list[str] = []

        def _flaky(url, **kwargs):
            bodies.append(kwargs["content"])
            if len(bodies) < 2:
                raise ConnectionError("blip")
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _flaky)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret="k")
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is True
        assert len(bodies) == 2 and bodies[0] == bodies[1]
