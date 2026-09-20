"""Webhook dispatch of completed scorecards to registered endpoints.

The dispatcher defaults to **dry-run**: it records what *would* be sent in the
:data:`WebhookDispatcher.sent` list and performs no network I/O — the safe
behaviour for tests and local runs. Live HTTP delivery is opt-in via the
``VALIDSIM_WEBHOOKS_LIVE=1`` environment variable (or the ``live`` constructor
flag), which POSTs the scorecard JSON with a short timeout. Delivery failures
are captured on the :class:`DeliveryResult` and never propagate to callers.

Enhancements:
    * ``format="slack"`` hooks receive a Slack-compatible Block Kit payload
      (composite score + deploy decision) instead of the raw JSON.
    * Live sends retry up to :data:`_MAX_RETRIES` times with exponential
      backoff before the failure is recorded.
    * Hooks registered with a ``secret`` get an ``X-ValidSim-Signature``
      header carrying the HMAC-SHA256 of the JSON body.
    * Hooks may declare a ``min_severity`` (``"info"`` | ``"warn"`` |
      ``"critical"``). A notification's severity is derived from its
      scorecard (see :func:`severity_from_scorecard`); on dispatch a hook
      only receives events whose severity meets or exceeds its threshold.
      The default ``"info"`` keeps the historical send-to-everyone behaviour.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from validsim.engine.scorecard import Scorecard

__all__ = [
    "DeliveryResult",
    "WebhookDispatcher",
    "format_slack_blocks",
    "severity_from_scorecard",
]

#: Env var that, when set to ``"1"``, enables real network delivery.
_LIVE_ENV = "VALIDSIM_WEBHOOKS_LIVE"
#: Per-request timeout (seconds) for live deliveries.
_TIMEOUT_S = 5.0
#: Maximum number of *retries* (i.e. 1 initial attempt + 2 retries).
_MAX_RETRIES = 2
#: Base delay (seconds) for exponential backoff between retries.
_BACKOFF_BASE_S = 0.1
#: Custom signature header name for HMAC-SHA256 body signing.
_SIGNATURE_HEADER = "X-ValidSim-Signature"

#: Supported hook payload formats.
HookFormat = Literal["json", "slack"]

#: Severity levels a notification can carry, from lowest to highest urgency.
Severity = Literal["info", "warn", "critical"]

#: Ordering used to compare a hook's ``min_severity`` against an event's
#: severity: a hook fires only when its rank is ``<=`` the event's rank.
_SEVERITY_RANK: dict[str, int] = {"info": 0, "warn": 1, "critical": 2}


def severity_from_scorecard(payload: dict[str, Any]) -> Severity:
    """Derive a notification severity from a scorecard ``to_dict()`` payload.

    The mapping is:

    * ``BLOCK`` with ``composite_score`` below ``threshold`` -> ``"critical"``
      (a hard failure that also missed the bar).
    * ``BLOCK`` otherwise -> ``"warn"``.
    * ``APPROVE`` (or any non-BLOCK decision) -> ``"info"``.

    Args:
        payload: Scorecard ``to_dict()`` output (missing keys are tolerated).

    Returns:
        One of ``"info"``, ``"warn"``, or ``"critical"``.
    """
    decision = str(payload.get("deploy_decision", "")).strip().upper()
    if decision == "BLOCK":
        try:
            composite = float(payload.get("composite_score", 0.0))
            threshold = float(payload.get("threshold", 85.0))
        except (TypeError, ValueError):
            composite, threshold = 0.0, 85.0
        return "critical" if composite < threshold else "warn"
    return "info"


def format_slack_blocks(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a Slack-compatible Block Kit payload from a scorecard dict.

    The message highlights the composite score and the deploy decision.

    Args:
        payload: Scorecard ``to_dict()`` output.

    Returns:
        A ``{"blocks": [...]}`` dict suitable for the Slack webhook API.
    """
    decision = str(payload.get("deploy_decision", "UNKNOWN"))
    emoji = ":white_check_mark:" if decision == "APPROVE" else ":no_entry:"
    composite = payload.get("composite_score", 0.0)
    threshold = payload.get("threshold", 85.0)
    run_id = payload.get("run_id", "unknown")
    success_rate = payload.get("success_rate", 0.0)
    safety = payload.get("safety_score", 0.0)
    robustness = payload.get("robustness_score", 0.0)

    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"ValidSim {emoji} {decision}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Run:* `{run_id}`\n"
                        f"*Composite Score:* {composite} (threshold {threshold})\n"
                        f"*Deploy Decision:* {decision}"
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Success Rate:*\n{success_rate}"},
                    {"type": "mrkdwn", "text": f"*Safety Score:*\n{safety}"},
                    {"type": "mrkdwn", "text": f"*Robustness:*\n{robustness}"},
                ],
            },
        ]
    }


def _sign_body(body: str, secret: str) -> str:
    """Return the hex HMAC-SHA256 of ``body`` keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a single webhook delivery attempt.

    Attributes:
        name: Registered hook name.
        url: Target URL.
        ok: Whether the delivery succeeded (in dry-run, always ``True``).
        status_code: HTTP status for live deliveries, else ``None``.
        error: Failure reason, or ``None`` when the attempt was clean.
    """

    name: str
    url: str
    ok: bool
    status_code: int | None = None
    error: str | None = None


class WebhookDispatcher:
    """Fan-out of a scorecard to a set of named webhook endpoints.

    Args:
        live: Force live mode on/off. When ``None`` (default), the
            ``VALIDSIM_WEBHOOKS_LIVE`` environment variable decides.
    """

    def __init__(self, live: bool | None = None) -> None:
        """Create a dispatcher, resolving live vs. dry-run mode."""
        if live is None:
            live = os.environ.get(_LIVE_ENV, "").strip() == "1"
        self._live = live
        self._hooks: list[tuple[str, str, HookFormat, str | None, Severity]] = []
        self._sent: list[DeliveryResult] = []

    @property
    def live(self) -> bool:
        """Whether real HTTP delivery is enabled."""
        return self._live

    @property
    def sent(self) -> list[DeliveryResult]:
        """Copy of every delivery recorded so far (dry-run and live)."""
        return list(self._sent)

    def register(
        self,
        name: str,
        url: str,
        format: HookFormat = "json",  # noqa: A002 - keep kwarg friendly
        secret: str | None = None,
        min_severity: Severity = "info",
    ) -> None:
        """Register a webhook target; order is preserved on dispatch.

        Args:
            name: Hook name (appears on delivery results).
            url: Target endpoint URL.
            format: ``"json"`` (raw scorecard) or ``"slack"`` (Block Kit).
            secret: Optional HMAC secret; when set, live sends include an
                ``X-ValidSim-Signature`` header of the JSON body.
            min_severity: Minimum event severity this hook wants to receive
                (``"info"`` | ``"warn"`` | ``"critical"``). Defaults to
                ``"info"``, which receives every event (the historical
                send-to-everyone behaviour). A ``"critical"`` hook only fires
                on critical notifications, a ``"warn"`` hook on warn+critical.
        """
        if format not in ("json", "slack"):
            raise ValueError(f"unsupported hook format: {format!r}")
        if min_severity not in _SEVERITY_RANK:
            raise ValueError(f"unsupported min_severity: {min_severity!r}")
        self._hooks.append((name, url, format, secret, min_severity))

    def dispatch(self, scorecard: Scorecard) -> list[DeliveryResult]:
        """Send ``scorecard`` to every *eligible* hook, returning the results.

        Eligibility is severity-routed: the event's severity is derived from
        the scorecard via :func:`severity_from_scorecard`, and a hook only
        fires when its ``min_severity`` is at or below that level. A critical
        event therefore reaches every hook while an info event reaches only
        ``"info"``-level hooks. Because every hook defaults to ``"info"``,
        omitting ``min_severity`` preserves the original send-to-everyone
        behaviour.

        In dry-run mode each eligible hook yields an ``ok=True`` result
        without any network call. In live mode each is POSTed its formatted
        payload; transport/HTTP errors are captured, never raised, after up to
        :data:`_MAX_RETRIES` retries with exponential backoff.
        """
        base_payload = scorecard.to_dict()
        event_rank = _SEVERITY_RANK[severity_from_scorecard(base_payload)]

        results: list[DeliveryResult] = []
        for name, url, fmt, _secret, min_severity in self._hooks:
            if _SEVERITY_RANK[min_severity] > event_rank:
                continue  # hook wants a higher severity than this event carries
            if self._live:
                payload = format_slack_blocks(base_payload) if fmt == "slack" else base_payload
                result = self._post(name, url, payload, _secret)
            else:
                result = DeliveryResult(name, url, True)
            self._sent.append(result)
            results.append(result)
        return results

    def _post(self, name: str, url: str, payload: dict, secret: str | None) -> DeliveryResult:
        """Perform one live HTTP POST with retry/backoff; never raise.

        Retries on transport errors and non-2xx responses up to
        ``_MAX_RETRIES`` times (exponential backoff between attempts).
        """
        body = json.dumps(payload)
        headers: dict[str, str] = {}
        if secret is not None:
            headers[_SIGNATURE_HEADER] = _sign_body(body, secret)

        last: DeliveryResult | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = httpx.post(
                    url, content=body, headers=headers, timeout=_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001 - never let delivery raise
                last = DeliveryResult(name, url, False, None, str(exc))
            else:
                ok = 200 <= response.status_code < 300
                error = None if ok else f"HTTP {response.status_code}"
                last = DeliveryResult(name, url, ok, response.status_code, error)
                if ok:
                    return last
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE_S * (2**attempt))
        assert last is not None  # loop always runs at least once
        return last
