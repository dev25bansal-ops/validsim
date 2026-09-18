"""Tests for the webhook dispatcher (dry-run by default, safe live mode)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import DeliveryResult, WebhookDispatcher


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
def _force_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test accidentally performs real network delivery."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)


class TestDryRun:
    def test_default_is_dry_run(self) -> None:
        assert WebhookDispatcher().live is False

    def test_dispatch_records_ok_without_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("network must not be used in dry-run mode")

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _boom)
        dispatcher = WebhookDispatcher()
        dispatcher.register("ci", "https://example.invalid/ci")
        dispatcher.register("slack", "https://example.invalid/slack")
        results = dispatcher.dispatch(_scorecard())

        assert [r.name for r in results] == ["ci", "slack"]  # registration order preserved
        assert all(isinstance(r, DeliveryResult) and r.ok for r in results)
        assert all(r.status_code is None and r.error is None for r in results)
        assert len(dispatcher.sent) == 2

    def test_no_hooks_returns_empty(self) -> None:
        assert WebhookDispatcher().dispatch(_scorecard()) == []

    def test_sent_accumulates_across_dispatches(self) -> None:
        dispatcher = WebhookDispatcher()
        dispatcher.register("a", "https://example.invalid/a")
        dispatcher.dispatch(_scorecard())
        dispatcher.dispatch(_scorecard())
        assert len(dispatcher.sent) == 2


class TestLiveMode:
    def test_env_var_enables_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_WEBHOOKS_LIVE", "1")
        assert WebhookDispatcher().live is True

    def test_success_captures_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda *a, **k: SimpleNamespace(status_code=204),
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", "https://example.invalid/ci")
        (result,) = dispatcher.dispatch(_scorecard())
        assert result.ok is True and result.status_code == 204 and result.error is None

    def test_http_error_is_captured_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda *a, **k: SimpleNamespace(status_code=500),
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", "https://example.invalid/ci")
        (result,) = dispatcher.dispatch(_scorecard())
        assert result.ok is False and result.status_code == 500 and "500" in (result.error or "")

    def test_transport_error_is_captured_not_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail(*args: object, **kwargs: object) -> None:
            raise ConnectionError("dns failure")

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _fail)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", "https://example.invalid/ci")
        (result,) = dispatcher.dispatch(_scorecard())
        assert result.ok is False and result.status_code is None
        assert "dns failure" in (result.error or "")
