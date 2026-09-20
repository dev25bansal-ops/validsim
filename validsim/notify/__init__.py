"""Notification integrations (webhook dispatch and SMTP email of scorecards)."""

from __future__ import annotations

from validsim.notify.dispatcher import (
    DeliveryResult,
    WebhookDispatcher,
    format_slack_blocks,
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
    "EmailDelivery",
    "EmailNotifier",
    "SmtpSettings",
    "WebhookDispatcher",
    "format_slack_blocks",
    "scorecard_email_body",
    "scorecard_text_body",
    "severity_from_scorecard",
]