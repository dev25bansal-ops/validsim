"""Notification integrations (webhook dispatch and SMTP email of scorecards).

This package is **implemented, configurable, tested and wired into the
shipped run path**. :func:`validsim.notify.dispatcher.notify_run_completion`
is the seam: :func:`validsim.engine.pipeline.run_and_score` calls it after a
run is newly persisted, so a finished run fans its scorecard out to every
configured webhook and to the SMTP channel.

Dispatch is **off by default** and stays inert until an operator sets
``VALIDSIM_NOTIFY_ENABLED=1`` *and* at least one destination — a
``VALIDSIM_WEBHOOK_URLS`` entry or a ``VALIDSIM_SMTP_TO`` recipient. A
destination alone is deliberately not consent to send. See
:mod:`validsim.notify.config` for the full variable reference and
:func:`validsim.notify.config.NotifyConfig.summary` for the secret-free view
surfaced on ``/api/v1/health``.

Delivery is additionally **dry-run by default** even once enabled: a
dispatcher records what it *would* send in
:data:`WebhookDispatcher.sent` until ``VALIDSIM_WEBHOOKS_LIVE=1`` is set, and
the email channel stays dry-run until ``VALIDSIM_SMTP_LIVE=1`` is set, so
turning on notifications never, by itself, starts sending network traffic.
"""

from __future__ import annotations

from validsim.notify.config import (
    ENV_ENABLED,
    ENV_EMAIL_LIVE,
    ENV_EMAIL_MIN_SEVERITY,
    ENV_EMAIL_TO,
    ENV_FORMATS,
    ENV_MIN_SEVERITY,
    ENV_SECRETS,
    ENV_URLS,
    EmailSpec,
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
    "ENV_EMAIL_LIVE",
    "ENV_EMAIL_MIN_SEVERITY",
    "ENV_EMAIL_TO",
    "ENV_ENABLED",
    "ENV_FORMATS",
    "ENV_MIN_SEVERITY",
    "ENV_SECRETS",
    "ENV_URLS",
    "EmailDelivery",
    "EmailNotifier",
    "EmailSpec",
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
