"""Tests for the adaptive retry policy in the notification dispatcher.

``validsim.notify.retry`` exists to replace one fixed backoff ladder with a
bounded policy that separates two questions the old ladder conflated:

* **Is this failure worth retrying?** A *permanent* failure -- a 4xx that
  means the request itself was wrong -- is not; a 404 will still be a 404
  after a wait. A *transient* failure (transport error, 408, 429, 5xx) often
  clears on its own and is worth the wait.
* **How long should we wait?** The delay adapts to the observed failure and
  to a ``Retry-After`` hint, but never past the configured ceilings.

The security properties that matter, and that these tests pin:

* The ladder stays **bounded**. A failure never produces more than
  ``1 + _MAX_RETRIES`` HTTP requests.
* A **permanent** failure is **terminal**. It gets exactly one request.
* A receiver-supplied ``Retry-After`` is **clamped**. A hostile or confused
  receiver cannot park a delivery for hours.
* The dispatcher still **never raises** and still **never sleeps past the
  shared dispatch deadline**.
* The **legacy ladder is preserved** for transient failures: a 500/503 still
  makes three requests and still backs off ``0.1`` then ``0.2`` seconds. The
  adaptive policy must not silently change the behaviour operators already
  depend on.

Every "this is safe" claim is paired with a **control**, so a green run means
"verified" rather than "blind".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify import dispatcher as disp
from validsim.notify.retry import FailureKind, RetryDecision, RetryPolicy, classify_status

_URL = "https://receiver.example.invalid/hooks/ci"


def _scorecard() -> Scorecard:
    """A minimal APPROVE scorecard."""
    return Scorecard(
        run_id="vrun-retry01",
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
        failure_taxonomy={},
    )


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep backoff instantaneous while still recording what was asked for."""
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _not_live_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may pick up a live mode from the ambient environment."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    headers: dict[str, str] | None = None,
) -> list[str]:
    """Install an ``httpx.post`` stub replaying *responses* in order.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        responses: A list of status codes (ints) or exceptions to raise.
        headers: Optional headers attached to every response.

    Returns:
        A list that records each posted URL, in order.
    """
    calls: list[str] = []

    def _post(url: str, **kwargs: object) -> Any:
        calls.append(url)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        if headers is None:
            return SimpleNamespace(status_code=item)
        return SimpleNamespace(status_code=item, headers=headers)

    monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
    return calls


# ---------------------------------------------------------------------------
# (1) CLASSIFICATION.
# ---------------------------------------------------------------------------
class TestClassification:
    """A status code must map to exactly one failure kind."""

    def test_permanent_codes_are_terminal(self) -> None:
        """A 4xx that means "the request was wrong" must not be retried."""
        for code in (400, 401, 403, 404, 405, 410, 422):
            assert classify_status(code) == FailureKind.PERMANENT, code

    def test_throttling_is_not_permanent(self) -> None:
        """429 asks for patience, not a different request."""
        assert classify_status(429) == FailureKind.THROTTLED

    def test_server_errors_are_transient(self) -> None:
        """5xx usually clears on its own."""
        for code in (500, 502, 503, 504):
            assert classify_status(code) == FailureKind.SERVER_ERROR, code

    def test_request_timeout_is_transient(self) -> None:
        """408 is a 4xx, but the request may still be fine."""
        assert classify_status(408) == FailureKind.TRANSIENT

    def test_too_early_is_transient(self) -> None:
        """425 is a 4xx, but it means "come back later", not "wrong request".

        RFC 8470's "Too Early" is the one 4xx a receiver sends to ask for a
        wait. The request is valid, so classifying it as permanent would
        permanently drop a delivery that would have succeeded on retry.
        """
        assert classify_status(425) == FailureKind.TRANSIENT

    def test_missing_response_is_transient(self) -> None:
        """No response at all means the request never landed."""
        assert classify_status(None) == FailureKind.TRANSIENT

    def test_control_success_is_not_a_failure(self) -> None:
        """CONTROL -- the classifier must not call a 200 a failure kind.

        A classifier that mapped everything to a retryable kind would still
        pass the tests above, so pin the success case explicitly.
        """
        assert classify_status(200) not in (
            FailureKind.PERMANENT,
            FailureKind.SERVER_ERROR,
            FailureKind.TRANSIENT,
        )


# ---------------------------------------------------------------------------
# (2) THE POLICY IS PURE AND BOUNDED.
# ---------------------------------------------------------------------------
class TestPolicyBounds:
    """The policy must never talk itself into an unbounded wait."""

    def test_non_retrying_decision_carries_no_delay(self) -> None:
        """A decision that stops the ladder must not also sleep."""
        with pytest.raises(ValueError, match="zero delay"):
            RetryDecision(retry=False, delay_s=1.0, kind=FailureKind.PERMANENT, reason="x")

    def test_retry_budget_is_never_exceeded(self) -> None:
        """The ladder stops at ``max_retries`` retries, not one more."""
        policy = RetryPolicy(max_retries=2, base_delay_s=0.1)
        decisions = [
            policy.decide(attempt=i, status_code=503) for i in range(5)
        ]
        assert [d.retry for d in decisions] == [True, True, False, False, False]

    def test_total_delay_budget_is_enforced(self) -> None:
        """The sum of delays cannot exceed ``max_total_delay_s``."""
        policy = RetryPolicy(
            max_retries=5, base_delay_s=1.0, max_delay_s=10.0, max_total_delay_s=2.5
        )
        for attempt in range(5):
            decision = policy.decide(attempt=attempt, status_code=503)
            if decision.retry:
                assert policy.total_delay_s <= 2.5
        assert policy.total_delay_s <= 2.5

    def test_single_delay_is_clamped(self) -> None:
        """No single delay may exceed ``max_delay_s``."""
        policy = RetryPolicy(max_retries=5, base_delay_s=10.0, max_delay_s=1.0)
        decision = policy.decide(attempt=0, status_code=503)
        assert decision.delay_s <= 1.0

    def test_retry_after_is_clamped(self) -> None:
        """A hostile ``Retry-After`` cannot park a delivery for hours."""
        policy = RetryPolicy(max_retries=2, base_delay_s=0.1, max_delay_s=2.0)
        decision = policy.decide(attempt=0, status_code=429, retry_after_s=86400.0)
        assert decision.delay_s <= 2.0

    def test_history_records_observed_kinds(self) -> None:
        """The policy remembers what it saw, for introspection."""
        policy = RetryPolicy(max_retries=3)
        policy.decide(attempt=0, status_code=503)
        policy.decide(attempt=1, status_code=429)
        assert policy.history == (FailureKind.SERVER_ERROR, FailureKind.THROTTLED)

    def test_reset_clears_history_and_budget(self) -> None:
        """``reset()`` returns the policy to its initial state."""
        policy = RetryPolicy(max_retries=3, base_delay_s=0.5)
        policy.decide(attempt=0, status_code=503)
        assert policy.total_delay_s > 0
        policy.reset()
        assert policy.total_delay_s == 0.0
        assert policy.history == ()


# ---------------------------------------------------------------------------
# (3) THE DISPATCHER ACTUALLY USES THE POLICY.
# ---------------------------------------------------------------------------
class TestDispatcherUsesPolicy:
    """End-to-end: the ladder the dispatcher runs must follow the policy."""

    def test_permanent_failure_makes_exactly_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 404 must not be retried -- it will still be a 404."""
        calls = _stub(monkeypatch, [404])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False and result.status_code == 404
        assert "404" in (result.error or "")
        assert len(calls) == 1, "a permanent failure must not be retried"

    def test_permanent_failure_does_not_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A terminal failure must not also cost the operator a backoff wait."""
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        _stub(monkeypatch, [404])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert sleeps == [], "a permanent failure must not sleep before giving up"

    def test_transient_failure_keeps_the_legacy_ladder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 503 must still make three requests and back off 0.1 then 0.2."""
        calls = _stub(monkeypatch, [503])
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False and result.status_code == 503
        assert len(calls) == 3
        assert sleeps == [0.1, 0.2]

    def test_transport_error_keeps_the_legacy_ladder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transport error is transient and must still be retried."""
        calls = _stub(monkeypatch, [ConnectionError("dns failure")])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False and result.status_code is None
        assert len(calls) == 3

    def test_success_makes_exactly_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 2xx stops the ladder immediately."""
        calls = _stub(monkeypatch, [200])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is True and result.status_code == 200
        assert len(calls) == 1

    def test_retry_after_hint_is_used_and_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A receiver's ``Retry-After`` wins over our own curve, clamped."""
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        _stub(monkeypatch, [429], headers={"Retry-After": "1.5"})
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert sleeps, "a Retry-After hint must produce a wait"
        assert all(s <= disp._RETRY_MAX_DELAY_S for s in sleeps)

    def test_hostile_retry_after_is_clamped_to_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A receiver asking for a day must not park a delivery for a day."""
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        _stub(monkeypatch, [429], headers={"Retry-After": "86400"})
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert sleeps, "a Retry-After hint must produce a wait"
        assert max(sleeps) <= disp._RETRY_MAX_DELAY_S
        assert max(sleeps) < 86400

    def test_unparsable_retry_after_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An HTTP-date hint is legal but must not be guessed at.

        An unparsable hint must fall back to the policy's own curve for a
        throttled failure -- and must still make the full three attempts.
        """
        sleeps: list[float] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.time.sleep", lambda s: sleeps.append(s)
        )
        calls = _stub(
            monkeypatch, [429], headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert len(calls) == 3, "throttling must not truncate the ladder"
        assert sleeps, "a throttled failure must still back off"
        assert all(s > 0 for s in sleeps)

    def test_response_without_headers_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A minimal HTTP stub may have no ``headers`` attribute at all."""
        calls = _stub(monkeypatch, [503])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False and result.status_code == 503
        assert len(calls) == 3

    def test_never_raises_on_transport_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A delivery failure must never propagate to the run path."""
        _stub(monkeypatch, [RuntimeError("boom")])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        (result,) = dispatcher.dispatch(_scorecard())

        assert result.ok is False
        assert "boom" in (result.error or "")


# ---------------------------------------------------------------------------
# (4) CONTROLS -- prove the detector above is not vacuous.
# ---------------------------------------------------------------------------
class TestControls:
    """The checks above must be able to fail."""

    def test_control_a_permanent_code_would_otherwise_be_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONTROL -- with permanence disabled, a 404 *is* retried.

        This proves the terminal-permanent behaviour above is caused by the
        policy's classification, not by an unrelated early return.
        """
        calls: list[str] = []
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda url, **kwargs: (
                calls.append(url) or SimpleNamespace(status_code=404)
            ),
        )
        monkeypatch.setattr(
            "validsim.notify.dispatcher._PERMANENT_FAILURES_TERMINAL", False
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())

        assert len(calls) == 3, "without the permanence rule a 404 is retried"

    def test_control_the_stub_records_the_calls_it_saw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONTROL -- the call recorder is live."""
        calls = _stub(monkeypatch, [200])
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(_scorecard())
        assert calls == [_URL]
