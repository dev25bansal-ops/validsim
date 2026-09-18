"""Webhook dispatch of completed scorecards to registered endpoints.

The dispatcher defaults to **dry-run**: it records what *would* be sent in the
:data:`WebhookDispatcher.sent` list and performs no network I/O — the safe
behaviour for tests and local runs. Live HTTP delivery is opt-in via the
``VALIDSIM_WEBHOOKS_LIVE=1`` environment variable (or the ``live`` constructor
flag), which POSTs the scorecard JSON with a short timeout. Delivery failures
are captured on the :class:`DeliveryResult` and never propagate to callers.
"""

from __future__ import annotations

import os

from dataclasses import dataclass

import httpx

from validsim.engine.scorecard import Scorecard

__all__ = ["DeliveryResult", "WebhookDispatcher"]

#: Env var that, when set to ``"1"``, enables real network delivery.
_LIVE_ENV = "VALIDSIM_WEBHOOKS_LIVE"
#: Per-request timeout (seconds) for live deliveries.
_TIMEOUT_S = 5.0


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
        self._hooks: list[tuple[str, str]] = []
        self._sent: list[DeliveryResult] = []

    @property
    def live(self) -> bool:
        """Whether real HTTP delivery is enabled."""
        return self._live

    @property
    def sent(self) -> list[DeliveryResult]:
        """Copy of every delivery recorded so far (dry-run and live)."""
        return list(self._sent)

    def register(self, name: str, url: str) -> None:
        """Register a webhook target; order is preserved on dispatch."""
        self._hooks.append((name, url))

    def dispatch(self, scorecard: Scorecard) -> list[DeliveryResult]:
        """Send ``scorecard`` to every registered hook and return the results.

        In dry-run mode each hook yields an ``ok=True`` result without any
        network call. In live mode each hook is POSTed the scorecard JSON;
        transport/HTTP errors are captured, never raised.
        """
        payload = scorecard.to_dict()
        results: list[DeliveryResult] = []
        for name, url in self._hooks:
            result = self._post(name, url, payload) if self._live else DeliveryResult(name, url, True)
            self._sent.append(result)
            results.append(result)
        return results

    def _post(self, name: str, url: str, payload: dict) -> DeliveryResult:
        """Perform one live HTTP POST, converting any error into a result."""
        try:
            response = httpx.post(url, json=payload, timeout=_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - never let delivery raise
            return DeliveryResult(name, url, False, None, str(exc))
        ok = 200 <= response.status_code < 300
        error = None if ok else f"HTTP {response.status_code}"
        return DeliveryResult(name, url, ok, response.status_code, error)
