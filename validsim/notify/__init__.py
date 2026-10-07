"""Notification integrations (webhook dispatch and SMTP email of scorecards).

This package is **implemented, configurable and tested, but not yet wired into
the shipped run path**. :func:`validsim.notify.dispatcher.notify_run_completion`
is the intended seam and would fan a completed run's scorecard out to
configured webhooks, but :func:`validsim.engine.pipeline.run_and_score` does
not call it yet — its only callers are in ``tests/``. So no run, CLI command or
API request currently reaches this layer at all. The pending patch is written
up in ``docs/notify_wiring_patches.md`` and tracked as ``PENDING OWNER`` in
``tests/test_notify_wiring_agent.py``.

Dispatch is **off by default** and stays inert until an operator sets both
``VALIDSIM_NOTIFY_ENABLED=1`` and at least one ``VALIDSIM_WEBHOOK_URLS``
entry — and even once that call site lands, still inert until the wiring
exists. See :mod:`validsim.notify.config` for the full variable reference and
:func:`validsim.notify.config.NotifyConfig.summary` for the secret-free view
surfaced on ``/api/v1/health``.

Delivery is additionally **dry-run by default** even once enabled: a
dispatcher records what it *would* send in
:data:`WebhookDispatcher.sent` until ``VALIDSIM_WEBHOOKS_LIVE=1`` is set, so
turning on notifications never, by itself, starts sending network traffic.
"""

from __future__ import annotations

from validsim.notify.config import (
    ENV_ENABLED,
    ENV_FORMATS,
    ENV_MIN_SEVERITY,
    ENV_SECRETS,
    ENV_URLS,
    HookSpec,
    NotifyConfig,
    notify_config_from_env,
)
from validsim.notify.dispatcher import (
    DeliveryResult,
    HookFormat,
    Severity,
    WebhookDispatcher,
    format_slack_blocks,
    notify_run_completion,
    severity_from_scorecard,
)
from validsim.notify.email import (
    EmailDelivery,
    EmailNotifier,
    SmtpSettings,
    scorecard_email_body,
    scorecard_text_body,
)

__all__ = [
    "DeliveryResult",
    "ENV_ENABLED",
    "ENV_FORMATS",
    "ENV_MIN_SEVERITY",
    "ENV_SECRETS",
    "ENV_URLS",
    "EmailDelivery",
    "EmailNotifier",
    "HookFormat",
    "HookSpec",
    "NotifyConfig",
    "Severity",
    "SmtpSettings",
    "WebhookDispatcher",
    "format_slack_blocks",
    "notify_config_from_env",
    "notify_run_completion",
    "scorecard_email_body",
    "scorecard_text_body",
    "severity_from_scorecard",
]
