"""Severity-based routing tests for the webhook dispatcher.

These tests pin the *new* behaviour added on top of the existing dispatcher:

* :func:`severity_from_scorecard` derives ``info``/``warn``/``critical`` from a
  scorecard dict (BLOCK+below-threshold => critical; BLOCK => warn; APPROVE =>
  info).
* Hooks registered with a ``min_severity`` only receive events at or above that
  level — a critical event reaches every hook, an info event only info-level
  hooks.
* The default ``min_severity="info"`` preserves the historical
  send-to-everyone behaviour, so the pre-existing ``test_notify*.py`` suites
  stay green.

The dry-run default is exercised everywhere; live routing is checked with a
stubbed ``httpx.post`` so skipped hooks provably never hit the network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify.dispatcher import severity_from_scorecard

_URL = "https://example.invalid/hook"


def _scorecard(
    decision: str = "APPROVE",
    composite: float = 90.0,
    threshold: float = 85.0,
) -> Scorecard:
    """A scorecard whose severity is controlled via ``decision``/scores."""
    return Scorecard(
        run_id="vrun-routing",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=composite,
        success_rate=composite / 100.0,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


@pytest.fixture(autouse=True)
def _force_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test accidentally performs real network delivery."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry backoff instantaneous for live-mode routing tests."""
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# (1) Severity derivation from a scorecard dict.
# ---------------------------------------------------------------------------
class TestSeverityDerivation:
    def test_block_below_threshold_is_critical(self) -> None:
        payload = {"deploy_decision": "BLOCK", "composite_score": 50.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "critical"

    def test_block_above_threshold_is_warn(self) -> None:
        payload = {"deploy_decision": "BLOCK", "composite_score": 90.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "warn"

    def test_block_equal_threshold_is_warn(self) -> None:
        payload = {"deploy_decision": "BLOCK", "composite_score": 85.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "warn"

    def test_approve_is_info_regardless_of_score(self) -> None:
        # Even a low composite on an APPROVE stays "info" (decision wins).
        payload = {"deploy_decision": "APPROVE", "composite_score": 10.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "info"

    def test_empty_payload_is_info(self) -> None:
        assert severity_from_scorecard({}) == "info"

    def test_decision_is_case_insensitive_and_trimmed(self) -> None:
        payload = {"deploy_decision": "  block ", "composite_score": 1.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "critical"

    def test_non_numeric_scores_do_not_crash(self) -> None:
        # Garbage scores fall back to composite 0.0 < threshold 85.0 => critical.
        payload = {"deploy_decision": "BLOCK", "composite_score": "n/a", "threshold": None}
        assert severity_from_scorecard(payload) == "critical"

    def test_real_scorecard_to_dict_maps_to_info(self) -> None:
        assert severity_from_scorecard(_scorecard().to_dict()) == "info"


# ---------------------------------------------------------------------------
# (2) register() validation of min_severity.
# ---------------------------------------------------------------------------
class TestRegisterValidation:
    def test_invalid_min_severity_rejected(self) -> None:
        dispatcher = WebhookDispatcher()
        with pytest.raises(ValueError, match="min_severity"):
            dispatcher.register("bad", _URL, min_severity="fatal")  # type: ignore[arg-type]

    def test_each_valid_level_accepted(self) -> None:
        dispatcher = WebhookDispatcher()
        for level in ("info", "warn", "critical"):
            dispatcher.register(level, _URL, min_severity=level)  # type: ignore[arg-type]

    def test_default_min_severity_receives_info_event(self) -> None:
        dispatcher = WebhookDispatcher()
        dispatcher.register("ci", _URL)  # no min_severity => "info"
        results = dispatcher.dispatch(_scorecard(decision="APPROVE"))
        assert [r.name for r in results] == ["ci"]


# ---------------------------------------------------------------------------
# (3) Dry-run routing matrix.
# ---------------------------------------------------------------------------
class TestDryRunRouting:
    def _three_hooks(self) -> WebhookDispatcher:
        dispatcher = WebhookDispatcher()
        dispatcher.register("all", _URL + "/all", min_severity="info")
        dispatcher.register("warn_up", _URL + "/warn", min_severity="warn")
        dispatcher.register("critical_only", _URL + "/critical", min_severity="critical")
        return dispatcher

    def test_info_event_only_info_level_hooks(self) -> None:
        results = self._three_hooks().dispatch(_scorecard(decision="APPROVE", composite=90.0))
        assert [r.name for r in results] == ["all"]

    def test_warn_event_hits_info_and_warn(self) -> None:
        results = self._three_hooks().dispatch(
            _scorecard(decision="BLOCK", composite=90.0, threshold=85.0)
        )
        assert [r.name for r in results] == ["all", "warn_up"]

    def test_critical_event_hits_all_hooks(self) -> None:
        results = self._three_hooks().dispatch(
            _scorecard(decision="BLOCK", composite=50.0, threshold=85.0)
        )
        assert [r.name for r in results] == ["all", "warn_up", "critical_only"]

    def test_registration_order_preserved_among_eligible(self) -> None:
        dispatcher = WebhookDispatcher()
        dispatcher.register("critical_only", _URL, min_severity="critical")
        dispatcher.register("all", _URL, min_severity="info")
        dispatcher.register("warn_up", _URL, min_severity="warn")
        results = dispatcher.dispatch(_scorecard(decision="BLOCK", composite=50.0))
        assert [r.name for r in results] == ["critical_only", "all", "warn_up"]

    def test_no_eligible_hooks_returns_empty(self) -> None:
        dispatcher = WebhookDispatcher()
        dispatcher.register("critical_only", _URL, min_severity="critical")
        assert dispatcher.dispatch(_scorecard(decision="APPROVE")) == []

    def test_sent_accumulates_only_fired_hooks(self) -> None:
        dispatcher = self._three_hooks()
        dispatcher.dispatch(_scorecard(decision="APPROVE"))  # info -> only "all"
        assert [r.name for r in dispatcher.sent] == ["all"]

    def test_skipped_hooks_never_touch_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("network must not be used in dry-run mode")

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _boom)
        dispatcher = self._three_hooks()
        results = dispatcher.dispatch(_scorecard(decision="APPROVE"))
        assert [r.name for r in results] == ["all"] and results[0].ok


# ---------------------------------------------------------------------------
# (4) Backward compatibility: default info => send-to-everyone.
# ---------------------------------------------------------------------------
class TestBackwardCompat:
    def test_default_hooks_receive_every_severity(self) -> None:
        dispatcher = WebhookDispatcher()
        dispatcher.register("a", _URL + "/a")
        dispatcher.register("b", _URL + "/b")
        cards = (
            _scorecard(decision="APPROVE"),  # info
            _scorecard(decision="BLOCK", composite=90.0),  # warn
            _scorecard(decision="BLOCK", composite=10.0),  # critical
        )
        for card in cards:
            assert [r.name for r in dispatcher.dispatch(card)] == ["a", "b"]


# ---------------------------------------------------------------------------
# (5) Live-mode routing: skipped hooks are provably not posted.
# ---------------------------------------------------------------------------
class TestLiveRouting:
    def _dispatcher(self) -> WebhookDispatcher:
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("all", _URL + "/all", min_severity="info")
        dispatcher.register("warn_up", _URL + "/warn", min_severity="warn")
        dispatcher.register("critical_only", _URL + "/critical", min_severity="critical")
        return dispatcher

    def test_info_event_posts_only_info_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[str] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda url, **kw: (posted.append(url), SimpleNamespace(status_code=200))[1],
        )
        results = self._dispatcher().dispatch(_scorecard(decision="APPROVE"))
        assert [r.name for r in results] == ["all"]
        assert posted == [_URL + "/all"]

    def test_critical_event_posts_every_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[str] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda url, **kw: (posted.append(url), SimpleNamespace(status_code=200))[1],
        )
        results = self._dispatcher().dispatch(_scorecard(decision="BLOCK", composite=10.0))
        assert [r.name for r in results] == ["all", "warn_up", "critical_only"]
        assert posted == [_URL + "/all", _URL + "/warn", _URL + "/critical"]


# ---------------------------------------------------------------------------
# (6) min_severity composes with format/secret.
# ---------------------------------------------------------------------------
class TestSeverityWithOtherOptions:
    def test_slack_hook_respects_routing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: dict[str, object] = {}

        def _post(url: str, **kwargs: object) -> SimpleNamespace:
            posted["url"] = url
            posted["body"] = kwargs["content"]
            return SimpleNamespace(status_code=200)

        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ops", _URL, format="slack", min_severity="critical")

        # Info event -> skipped, nothing on the wire.
        assert dispatcher.dispatch(_scorecard(decision="APPROVE")) == []
        assert "url" not in posted

        # Critical event -> fires, still rendered as Slack blocks.
        results = dispatcher.dispatch(_scorecard(decision="BLOCK", composite=10.0))
        assert [r.name for r in results] == ["ops"]
        assert "blocks" in json.loads(str(posted["body"]))
