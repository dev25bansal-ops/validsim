"""Webhook dispatch of completed scorecards to registered endpoints.

This module is **implemented, configurable, tested, and wired into the
run-completion flow**. :func:`validsim.engine.pipeline.run_and_score` calls
:func:`notify_run_completion` after a run is newly persisted (the retry /
duplicate path re-reads the stored record and deliberately does NOT
re-notify, so one persisted run yields exactly one notification). The
dispatcher is **inert by default**: with no environment set,
:func:`notify_run_completion` returns an empty list without constructing a
dispatcher or performing any network I/O.

Delivery is gated by two independent switches that both default to off:
``VALIDSIM_NOTIFY_ENABLED`` decides *whether* a finished run notifies at all
(see :func:`notify_run_completion`), and ``VALIDSIM_WEBHOOKS_LIVE`` decides
whether a notification leaves the process. A destination alone is deliberately
not consent to send.

The dispatcher defaults to **dry-run**: it records what *would* be sent in the
:data:`WebhookDispatcher.sent` list and performs no network I/O — the safe
behaviour for tests and local runs. Live HTTP delivery is opt-in via the
``VALIDSIM_WEBHOOKS_LIVE=1`` environment variable (or the ``live`` constructor
flag), which POSTs the scorecard JSON with a short timeout and bounded
retries (see :data:`_MAX_RETRIES`). Delivery failures are captured on the
:class:`DeliveryResult` and never propagate to callers, so a webhook outage
cannot lose or corrupt an already-persisted run result.

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
    * An **SMTP email channel** is dispatched alongside the webhooks, using
      the same severity routing so both channels agree on whether an event
      was worth sending. It is configured by ``VALIDSIM_SMTP_TO`` and is
      dry-run by default; ``VALIDSIM_SMTP_LIVE=1`` performs real delivery.
      The email layer itself is :mod:`validsim.notify.email`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx

from validsim._log import get_logger
from validsim.engine.scorecard import Scorecard
from validsim.notify.retry import RetryPolicy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from validsim.notify.config import EmailSpec

__all__ = [
    "DeliveryResult",
    "HookFormat",
    "Severity",
    "WebhookDispatcher",
    "format_slack_blocks",
    "notify_run_completion",
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
#: Ceiling (seconds) on any *single* adaptive wait, including one derived
#: from a receiver's ``Retry-After`` header. A receiver controls that header,
#: so it is clamped rather than trusted.
_RETRY_MAX_DELAY_S = 2.0
#: Ceiling (seconds) on the *sum* of every wait in one delivery's ladder.
#:
#: This must stay comfortably above the legacy fixed ladder
#: (``0.1 + 0.2 = 0.3``) so the adaptive policy never truncates a transient
#: failure's retries, while remaining well inside the fan-out deadline
#: (:data:`_DISPATCH_BUDGET_S`).
_RETRY_MAX_TOTAL_DELAY_S = 2.0
#: Wall-clock ceiling (seconds) for ONE ``dispatch()`` fan-out, across every
#: hook. Hooks are delivered serially and each may burn
#: ``(1 + _MAX_RETRIES) * _TIMEOUT_S`` on a black hole, so without a shared
#: ceiling the cost is that product *per hook* (measured: 15.8s for one, 31.5s
#: for two). One ``deadline`` is taken at the top of the fan-out and shared by
#: every hook; each attempt's timeout is clamped to what remains of it.
_DISPATCH_BUDGET_S = 5.0
#: Custom signature header name for HMAC-SHA256 body signing.
_SIGNATURE_HEADER = "X-ValidSim-Signature"
#: Whether a *permanent* failure (a 4xx that means the request itself was
#: wrong) ends the retry ladder immediately.
#:
#: Retrying a 404/401/400 is pure waste: the same request will be rejected
#: again, and every retry delays the operator's signal by another backoff
#: interval. Transient failures (transport errors, 408, 429, 5xx) still get
#: the full ladder.
_PERMANENT_FAILURES_TERMINAL = True
#: Logger for the notification layer.
_logger = get_logger("notify.dispatcher")

#: Supported hook payload formats.
HookFormat = Literal["json", "slack"]

#: Severity levels a notification can carry, from lowest to highest urgency.
Severity = Literal["info", "warn", "critical"]

#: Ordering used to compare a hook's ``min_severity`` against an event's
#: severity: a hook fires only when its rank is ``<=`` the event's rank.
_SEVERITY_RANK: dict[str, int] = {"info": 0, "warn": 1, "critical": 2}

#: Lowercased prefixes of the ``block_reasons`` entries
#: :func:`validsim.engine.scorecard.build_scorecard` records that represent a
#: **safety or evidence** failure rather than a plain score miss. A BLOCK
#: carrying any of these is ``"critical"`` even when the composite is above the
#: threshold. Matched as prefixes because the entries embed run-specific
#: numbers; deliberately *not* matching the threshold entry, which is already
#: classified by the composite comparison below.
_CRITICAL_BLOCK_REASON_PREFIXES: tuple[str, ...] = (
    "adversarial success rate",
    "insufficient evidence",
)


def severity_from_scorecard(payload: dict[str, Any]) -> Severity:
    """Derive a notification severity from a scorecard ``to_dict()`` payload.

    The mapping is:

    * ``APPROVE`` (or any non-BLOCK decision) -> ``"info"``.
    * ``BLOCK`` -> at least ``"warn"``.
    * ``BLOCK`` carrying a **safety or evidence** ``block_reasons`` entry ->
      ``"critical"``.

    Args:
        payload: Scorecard ``to_dict()`` output (missing keys are tolerated).

    Returns:
        One of ``"info"``, ``"warn"``, or ``"critical"``.

    .. note::
       ``block_reasons`` is authoritative when present. Reading only
       ``deploy_decision`` plus the composite missed the two reasons
       :func:`validsim.engine.scorecard.build_scorecard` records *without* a
       low composite — a significantly-below-floor adversarial suite, and an
       under-delivered run. Both are the events an operator most wants paged
       for, and a composite can be *high* in the under-delivered case
       (robustness and the regression component are structural 100s in a
       single-group run with no baseline). Worse, the
       ``composite {x} is below the configured threshold {y}`` entry cannot be
       identified by substring matching alone, so "any reason at all" would
       also promote a plain threshold miss. The two non-score reasons are
       matched by their stable prefixes instead.
    """
    decision = str(payload.get("deploy_decision", "")).strip().upper()
    if decision != "BLOCK":
        return "info"

    reasons = payload.get("block_reasons") or ()
    if isinstance(reasons, (list, tuple)):
        if any(
            str(reason).strip().lower().startswith(prefix) for reason in reasons
            for prefix in _CRITICAL_BLOCK_REASON_PREFIXES
        ):
            return "critical"

    try:
        composite = float(payload.get("composite_score", 0.0))
        threshold = float(payload.get("threshold", 85.0))
    except (TypeError, ValueError):
        composite, threshold = 0.0, 85.0
    return "critical" if composite < threshold else "warn"


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


def _retry_after_seconds(response: Any) -> float | None:
    """Extract a usable ``Retry-After`` hint from a response, if any.

    The receiver's hint is *better* information than our own backoff curve,
    so it is preferred when present. It is also the only header a remote
    party controls, so it is treated as hostile input:

    * a non-numeric or negative value is ignored, not coerced;
    * the value is clamped by the policy's ``max_delay_s`` before use, so a
      receiver cannot park a delivery for hours;
    * a missing ``headers`` attribute (which a minimal HTTP stub may not
      provide) is simply "no hint".

    Args:
        response: The HTTP response, or ``None`` when the attempt never
            produced one.

    Returns:
        The hint in seconds, or ``None`` when there is no usable hint.
    """
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After")
    except AttributeError:  # pragma: no cover - exotic header containers
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        # An HTTP-date form is legal but not worth parsing here: guessing a
        # clock-skewed date would be worse than ignoring the hint.
        return None
    if value <= 0:
        return None
    return value


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a single webhook delivery attempt.

    Attributes:
        name: Registered hook name.
        url: Target URL.
        ok: Whether the delivery succeeded (in dry-run, always ``True``).
        status_code: HTTP status for live deliveries, else ``None``.
        error: Failure reason, or ``None`` when the attempt was clean.
        dry_run: Whether this result was recorded **without any network I/O**.

          Mirrors :class:`~validsim.notify.email.EmailDelivery` so both notifier
          surfaces report success the same way. It exists because a dry-run
          ``ok=True`` is otherwise indistinguishable from a real send:
          ``status_code`` is ``None`` in *both* the dry-run case and the
          live-failure case, so the pre-existing fields cannot separate
          "recorded but never sent" from "sent and the receiver rejected it".
          A monitoring check that treats ``ok`` as proof of delivery will read a
          rehearsal as a success. When ``dry_run`` is ``True``, ``ok`` is
          vacuous and ``status_code`` is always ``None``.
    """

    name: str
    url: str
    ok: bool
    status_code: int | None = None
    error: str | None = None
    dry_run: bool = False


class WebhookDispatcher:
    """Fan-out of a scorecard to a set of named webhook endpoints.

    Args:
        live: Force live mode on/off. When ``None`` (default), the
            ``VALIDSIM_WEBHOOKS_LIVE`` environment variable decides — and it is
            re-read on **every** dispatch, not frozen here at construction.

    .. note::
       The env-derived decision is deliberately lazy. Reading it in
       ``__init__`` was harmless while nothing in the shipped path constructed
       a dispatcher, but the moment dispatch was wired into the run path a
       long-lived process (a job worker, the API server) would keep using the
       value that happened to be set when the object was first built. An
       operator who then exported ``VALIDSIM_WEBHOOKS_LIVE=1`` and triggered a
       run would get silence and no error. Resolving per dispatch costs one
       dict lookup and matches how
       :class:`~validsim.notify.email.EmailNotifier` reads its SMTP settings at
       send time.
    """

    def __init__(self, live: bool | None = None) -> None:
        """Create a dispatcher, remembering whether *live* was pinned."""
        self._forced_live = live
        self._hooks: list[tuple[str, str, HookFormat, str | None, Severity]] = []
        self._sent: list[DeliveryResult] = []

    @property
    def live(self) -> bool:
        """Whether real HTTP delivery is enabled **right now**.

        An explicit constructor ``live=`` argument wins; otherwise the
        ``VALIDSIM_WEBHOOKS_LIVE`` environment variable is consulted at the
        moment of the call.
        """
        if self._forced_live is not None:
            return self._forced_live
        return os.environ.get(_LIVE_ENV, "").strip() == "1"

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

        In dry-run mode each eligible hook yields an ``ok=True``,
        ``dry_run=True`` result without any network call. In live mode each is
        POSTed its formatted payload; transport/HTTP errors are captured, never
        raised, after up to :data:`_MAX_RETRIES` retries with exponential
        backoff. Use the :attr:`DeliveryResult.dry_run` flag rather than
        ``ok`` to decide whether a delivery actually left the process.
        """
        base_payload = scorecard.to_dict()
        event_rank = _SEVERITY_RANK[severity_from_scorecard(base_payload)]
        # Resolved per dispatch (see the class docstring), not frozen at
        # construction, so a live-mode change takes effect on the next event.
        live = self.live
        # One deadline for the WHOLE fan-out, taken once (monotonic so a clock
        # adjustment cannot extend or collapse the budget). Per-hook budgets
        # would be indistinguishable from no budget at all: the first hook would
        # happily spend the whole allowance and leave nothing for the rest.
        deadline = time.monotonic() + _DISPATCH_BUDGET_S

        results: list[DeliveryResult] = []
        for name, url, fmt, _secret, min_severity in self._hooks:
            if _SEVERITY_RANK[min_severity] > event_rank:
                continue  # hook wants a higher severity than this event carries
            if live:
                payload = format_slack_blocks(base_payload) if fmt == "slack" else base_payload
                # Clamp this hook's *whole* per-hook allowance (retries included)
                # to what is left of the fan-out budget, so no single hook can
                # overrun the deadline by more than its one in-flight attempt.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Budget spent by earlier hooks. This hook is NOT silently
                    # dropped: it still returns a result so a caller iterating
                    # the list learns the hook exists and why it was not sent.
                    result = DeliveryResult(
                        name, url, False, None,
                        f"skipped: dispatch budget of {_DISPATCH_BUDGET_S}s exhausted",
                        dry_run=False,
                    )
                else:
                    result = self._post(
                        name, url, payload, _secret,
                        min(_TIMEOUT_S, remaining),
                        deadline=deadline,
                    )
            else:
                result = DeliveryResult(name, url, True, dry_run=True)
            self._sent.append(result)
            results.append(result)
        return results

    def _post(
        self,
        name: str,
        url: str,
        payload: dict,
        secret: str | None,
        timeout: float = _TIMEOUT_S,
        deadline: float | None = None,
    ) -> DeliveryResult:
        """Perform one live HTTP POST with retry/backoff; never raise.

        Retries on transport errors and non-2xx responses up to
        ``_MAX_RETRIES`` times (exponential backoff between attempts).

        Args:
            timeout: Ceiling for a *single* attempt, already clamped by the
                caller to the remaining dispatch budget.
            deadline: Shared fan-out deadline (``time.monotonic()`` value). When
                given, no attempt may start after it and a retry is skipped once
                it has passed, so the retry ladder cannot walk past the budget.
        """
        body = json.dumps(payload)
        # httpx only auto-sets Content-Type for json=; with content= we must set it
        # explicitly or receivers (Slack in particular) reject or misparse the body.
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if secret is not None:
            headers[_SIGNATURE_HEADER] = _sign_body(body, secret)

        last: DeliveryResult | None = None
        # One policy per delivery: it owns the retry *decision* and the
        # accumulated delay budget for this hook. The policy is pure -- it
        # never sleeps, never touches the network, never reads a clock -- so
        # every timing decision stays here in the dispatcher, where the shared
        # fan-out deadline can clamp it.
        policy = RetryPolicy(
            max_retries=_MAX_RETRIES,
            base_delay_s=_BACKOFF_BASE_S,
            max_delay_s=_RETRY_MAX_DELAY_S,
            max_total_delay_s=_RETRY_MAX_TOTAL_DELAY_S,
            permanent_is_terminal=_PERMANENT_FAILURES_TERMINAL,
        )
        for attempt in range(_MAX_RETRIES + 1):
            # Re-clamp per attempt: earlier hooks and earlier retries of this
            # hook may have spent the budget since it was computed.
            attempt_timeout = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break  # budget gone: keep the last failure, stop retrying
                attempt_timeout = min(attempt_timeout, remaining)
            response = None
            try:
                response = httpx.post(
                    url, content=body, headers=headers, timeout=attempt_timeout
                )
            except Exception as exc:  # noqa: BLE001 - never let delivery raise
                last = DeliveryResult(name, url, False, None, str(exc), dry_run=False)
                status_code: int | None = None
                error_text: str | None = str(exc)
            else:
                status_code = response.status_code
                ok = 200 <= status_code < 300
                error_text = None if ok else f"HTTP {status_code}"
                last = DeliveryResult(name, url, ok, status_code, error_text, dry_run=False)
                if ok:
                    return last
            decision = policy.decide(
                attempt=attempt,
                status_code=status_code,
                error=error_text,
                retry_after_s=_retry_after_seconds(response),
            )
            if not decision.retry:
                # The policy declined another attempt: a permanent failure, an
                # exhausted retry budget, or an exhausted delay budget. The
                # last result already describes the failure, so the reason is
                # only worth a log line.
                _logger.warning(
                    "notification delivery stopped: %s",
                    decision.reason,
                    extra={"hook": name, "failure_kind": decision.kind},
                )
                break
            # Backoff is itself budget: do not sleep past the deadline.
            delay = decision.delay_s
            if deadline is not None:
                delay = min(delay, max(0.0, deadline - time.monotonic()))
            time.sleep(delay)
        if last is None:
            # Unreachable while ``_MAX_RETRIES >= 0``, but a zero-retry future
            # would otherwise trip the assert below with an unhelpful message.
            last = DeliveryResult(
                name, url, False, None,
                f"skipped: dispatch budget of {_DISPATCH_BUDGET_S}s exhausted",
                dry_run=False,
            )
        return last


def notify_run_completion(scorecard: Scorecard) -> list[DeliveryResult]:
    """Fan a completed run's scorecard out to every configured channel.

    This is the seam between a finished run and the notification layer, and it
    **is called from the shipped code**: :func:`validsim.engine.pipeline.run_and_score`
    invokes it after a run is newly persisted (the retry / duplicate path
    re-reads the stored record and deliberately does not re-notify). Its other
    callers are in ``tests/``.

    The function itself is **inert by default**: with no environment set it
    returns an empty list without constructing a dispatcher, so wiring it in
    later will not change behaviour until an operator opts in.

    Enabling requires *both* ``VALIDSIM_NOTIFY_ENABLED`` to be set to a
    recognised truthy value *and* at least one destination — a webhook URL
    (see ``VALIDSIM_WEBHOOK_URLS``) or an SMTP recipient (see
    ``VALIDSIM_SMTP_TO``). A destination alone is deliberately not consent to
    send.

    Both channels share one severity routing decision, so a mail and a webhook
    about the same run agree on whether the event was worth sending.

    Args:
        scorecard: The finished run's scorecard.

    Returns:
        The per-destination delivery results, or ``[]`` when notifications are
        disabled, no destinations are configured, or no destination was
        eligible for this event's severity.

    .. note::
       This function **never raises**. A run that has already completed and
       been persisted must not be lost because a webhook was misconfigured, so
       both configuration errors and dispatcher construction are contained
       here and reported as "nothing delivered".
    """
    # Imported lazily: validsim.notify.config imports this module, so a
    # top-level import would be circular.
    from validsim.notify.config import notify_config_from_env

    try:
        config = notify_config_from_env()
    except Exception:  # noqa: BLE001 - a config typo must not lose a run
        return []
    if not config.enabled:
        return []
    if not config.hooks and config.email is None:
        return []

    results: list[DeliveryResult] = []
    if config.hooks:
        try:
            dispatcher = config.register_into(WebhookDispatcher())
            results.extend(dispatcher.dispatch(scorecard))
        except Exception:  # noqa: BLE001 - delivery must never break the run path
            pass
    if config.email is not None:
        results.extend(_dispatch_email(scorecard, config.email))
    return results


def _dispatch_email(scorecard: Scorecard, spec: "EmailSpec") -> list[DeliveryResult]:
    """Deliver *scorecard* over SMTP according to *spec*.

    Mirrors the webhook channel's contract: a severity-routed attempt that
    never raises and always reports a result, so a mail failure is visible
    rather than silent.

    The result is a :class:`DeliveryResult` rather than the email layer's own
    :class:`~validsim.notify.email.EmailDelivery` so both channels report
    through one type. The recipient list is reduced to a count in the
    ``url`` field: an address list is personally identifying, and a delivery
    result is the kind of thing that ends up in a log.
    """
    # Imported lazily: validsim.notify.email imports the engine layer, and the
    # dispatcher is imported by the engine pipeline.
    from validsim.notify.email import EmailNotifier

    name = "email"
    recipients = tuple(spec.to)
    try:
        payload = scorecard.to_dict()
        event_rank = _SEVERITY_RANK[severity_from_scorecard(payload)]
        if _SEVERITY_RANK[spec.min_severity] > event_rank:
            # This channel wants a higher severity than the event carries.
            return []
        notifier = EmailNotifier(dry_run=not spec.live)
        delivery = notifier.send_scorecard(
            to=list(recipients),
            subject=_email_subject(payload),
            scorecard=payload,
        )
    except Exception as exc:  # noqa: BLE001 - delivery must never raise
        return [DeliveryResult(name, f"mailto:{len(recipients)}-recipients", False, None, str(exc))]

    if delivery.ok:
        return [
            DeliveryResult(
                name,
                f"mailto:{len(recipients)}-recipients",
                True,
                None,
                dry_run=delivery.dry_run,
            )
        ]
    return [
        DeliveryResult(
            name,
            f"mailto:{len(recipients)}-recipients",
            False,
            None,
            delivery.error or "delivery failed",
            dry_run=delivery.dry_run,
        )
    ]


def _email_subject(payload: dict[str, Any]) -> str:
    """Build the mail subject line for a scorecard *payload*.

    Kept short and free of user-controlled text: the subject is a header, and
    the email layer refuses a subject containing CR/LF. The run id is
    store-assigned, but building it here rather than trusting it keeps a
    header-injection guard meaningful.
    """
    decision = str(payload.get("deploy_decision", "")).strip().upper()
    composite = payload.get("composite_score")
    score = f"{float(composite):.1f}" if isinstance(composite, (int, float)) else "?"
    run_id = str(payload.get("run_id", "")).strip()
    suffix = f" [{run_id}]" if run_id else ""
    return f"ValidSim {decision} - composite {score}{suffix}"
