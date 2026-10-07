"""Operator-facing configuration surface for the notification layer.

This module is what makes :mod:`validsim.notify` *expressible*: before it,
a webhook destination could be declared nowhere, so the dispatcher had no
inputs and no entrypoint constructed one. It is deliberately a pure
function of a ``str -> str`` mapping (defaulting to :data:`os.environ`) so
it is testable with no ambient state, exactly like
:mod:`validsim.project_config`.

Enabling
--------

Notification dispatch on run completion is **off unless explicitly enabled**.
Two independent switches exist, and both must agree:

* ``VALIDSIM_NOTIFY_ENABLED`` — the master switch. Absent, blank or
  unrecognised means **off**.
* ``VALIDSIM_WEBHOOK_URLS`` — the destinations. Configuring a destination is
  deliberately *not* consent to send: a URL can be present for documentation,
  for a later cut-over, or by a copy-paste, and none of those should page a
  team. An operator sets the URL and then turns the feature on.

This fail-closed pairing is the whole point. An unrecognised value
(``maybe``) is treated as **off**, not truthy, so a typo cannot start
egress that nobody asked for.

Shape of the env vars
---------------------

``VALIDSIM_WEBHOOK_URLS`` is a comma-separated list; the three sibling
variables are positional companions that pair by index:

===================  ======================================================
Variable             Meaning
===================  ======================================================
``..._URLS``         destination URLs (the list that defines the hooks)
``..._SECRETS``      HMAC-SHA256 shared secrets, one per URL
``..._FORMATS``      ``json`` or ``slack`` payload format
``..._MIN_SEVERITY`` ``info`` / ``warn`` / ``critical`` routing threshold
===================  ======================================================

A shorter companion list pads with the safe default (no secret, ``json``,
``info``) rather than shifting values, so a single-URL setup never
accidentally signs with a neighbour's key. Blank entries are **positional**:
``VALIDSIM_WEBHOOK_SECRETS=",s2"`` leaves hook 1 unsigned and signs hook 2
with ``s2``, because a companion list pairs with :data:`ENV_URLS` by index.
A URL entry that is blank or unparseable is dropped instead, so one typo must
not disable every other destination — but because dropping a URL re-indexes
the survivors, an operator who drops a URL should keep the remaining
companion entries in their original positions.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from validsim.notify.dispatcher import (
    HookFormat,
    Severity,
    WebhookDispatcher,
    _LIVE_ENV,
)

__all__ = [
    "HookSpec",
    "NotifyConfig",
    "notify_config_from_env",
]

#: Master switch. Only an explicitly truthy value turns dispatch on.
ENV_ENABLED = "VALIDSIM_NOTIFY_ENABLED"
#: Comma-separated destination URLs.
ENV_URLS = "VALIDSIM_WEBHOOK_URLS"
#: Comma-separated HMAC secrets, positionally paired with :data:`ENV_URLS`.
ENV_SECRETS = "VALIDSIM_WEBHOOK_SECRETS"
#: Comma-separated payload formats, positionally paired with :data:`ENV_URLS`.
ENV_FORMATS = "VALIDSIM_WEBHOOK_FORMATS"
#: Comma-separated per-hook severity thresholds, paired with :data:`ENV_URLS`.
ENV_MIN_SEVERITY = "VALIDSIM_WEBHOOK_MIN_SEVERITY"

#: Values accepted as "on" for :data:`ENV_ENABLED`. Anything else is off.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Name given to the *n*-th hook, so a delivery result identifies its source
#: without the operator having to name every destination.
_HOOK_NAME_TEMPLATE = "webhook-{index}"


@dataclass(frozen=True)
class HookSpec:
    """One configured webhook destination.

    Attributes:
        name: Generated hook name (``webhook-1``, ``webhook-2``, ...).
        url: Destination URL.
        format: ``"json"`` (raw scorecard) or ``"slack"`` (Block Kit).
        secret: Optional HMAC-SHA256 shared secret. ``None`` means the body is
            sent unsigned.
        min_severity: Lowest severity this hook wants (``info`` receives all).
    """

    name: str
    url: str
    format: HookFormat = "json"
    # repr=False keeps the secret out of repr()/str(), so logging the config
    # (e.g. in a health payload or a support bundle) cannot leak it.
    secret: str | None = field(default=None, repr=False)
    min_severity: Severity = "info"

    def register(self, dispatcher: WebhookDispatcher) -> None:
        """Register this hook on *dispatcher*."""
        dispatcher.register(
            self.name,
            self.url,
            format=self.format,
            secret=self.secret,
            min_severity=self.min_severity,
        )


@dataclass(frozen=True)
class NotifyConfig:
    """The effective notification configuration, resolved from the environment.

    Attributes:
        enabled: Whether run-completion dispatch may fire at all.
        hooks: Configured destinations, in declaration order.
    """

    enabled: bool = False
    hooks: tuple[HookSpec, ...] = ()

    @property
    def hook_count(self) -> int:
        """Number of configured destinations."""
        return len(self.hooks)

    def register_into(self, dispatcher: WebhookDispatcher) -> WebhookDispatcher:
        """Register every configured hook onto *dispatcher* and return it.

        This is the missing link that made the layer unreachable: it turns a
        declaration into actual dispatcher state, so the same environment that
        documents a destination is the one that delivers to it.
        """
        for hook in self.hooks:
            hook.register(dispatcher)
        return dispatcher

    def summary(self) -> dict[str, object]:
        """A JSON-safe, **secret-free** view for ``/api/v1/health``.

        Secret values are never included — they are bare credentials with no
        ``user:pass@`` section, so the URL-only redaction used elsewhere in the
        codebase would emit them verbatim. Destination URLs are withheld too:
        Slack and most webhook providers carry a secret token *inside* the URL
        path, so printing it on a public probe would leak it. The counts and
        flags below are what an operator actually needs to confirm that a
        configuration took effect.
        """
        return {
            "enabled": self.enabled,
            "hook_count": self.hook_count,
            "live": 1 if _live_from_env() else 0,
            "hooks": [],
        }


def _split(raw: str) -> list[str]:
    """Split a comma-separated env value into trimmed, non-empty entries.

    Used for :data:`ENV_URLS`, where a blank entry is simply a padding artefact
    and must not become a destination.
    """
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_positional(raw: str) -> list[str]:
    """Split a *positional companion* env value, keeping blank placeholders.

    The companion lists (:data:`ENV_SECRETS`, :data:`ENV_FORMATS`,
    :data:`ENV_MIN_SEVERITY`) pair with :data:`ENV_URLS` **by index**, so an
    entry must keep its position even when it is blank. A list such as
    ``",s2"`` means "hook 1 has no secret, hook 2 signs with ``s2``"; dropping
    the blank would slide ``s2`` onto hook 1 and sign the wrong destination with
    the wrong key.

    Returns a list at least as long as the number of comma-separated fields, so
    index *n* of the result always corresponds to URL *n*. A wholly absent value
    still yields ``[]``, which :func:`_at` pads with the safe default.
    """
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",")]


def _at(items: list[str], index: int, default: str) -> str:
    """Return ``items[index]`` or *default* when the list is shorter."""
    return items[index] if index < len(items) else default


def _live_from_env() -> bool:
    """Whether live (non-dry-run) delivery is currently enabled.

    Reads the canonical :data:`validsim.notify.dispatcher._LIVE_ENV` rather than
    repeating the literal, so there is exactly one place that names the switch.
    """
    return os.environ.get(_LIVE_ENV, "").strip() == "1"


def _valid_url(url: str) -> bool:
    """Whether *url* is an absolute http(s) URL safe to POST a scorecard to.

    Only ``http``/``https`` are accepted. Anything else (``file:``, ``ftp:``,
    a bare hostname) is a misconfiguration, and POSTing a run's composite score
    to an arbitrary scheme handler is not something a config typo should be
    able to do.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def notify_config_from_env(environ: Mapping[str, str] | None = None) -> NotifyConfig:
    """Resolve the effective notification configuration from *environ*.

    Args:
        environ: Environment mapping to read (defaults to :data:`os.environ`).
            Accepted as a parameter so configuration can be tested — and
            audited — with no ambient state at all.

    Returns:
        A :class:`NotifyConfig`. With no environment set it is
        ``NotifyConfig(enabled=False, hooks=())``: nothing is enabled and
        nothing is registered, which is the platform's shipped default.
    """
    if environ is None:
        environ = os.environ

    raw_urls = environ.get(ENV_URLS, "")
    urls = _split(raw_urls)
    # Companions are positional, so blanks must be PRESERVED as placeholders;
    # dropping them would shift a later hook onto an earlier hook's slot.
    secrets = _split_positional(environ.get(ENV_SECRETS, ""))
    formats = _split_positional(environ.get(ENV_FORMATS, ""))
    severities = _split_positional(environ.get(ENV_MIN_SEVERITY, ""))

    hooks: list[HookSpec] = []
    for index, url in enumerate(urls):
        if not _valid_url(url):
            # Drop rather than raise: one malformed destination must not
            # disable every other one.
            continue
        fmt = _at(formats, index, "json")
        severity = _at(severities, index, "info")
        if fmt not in ("json", "slack"):
            fmt = "json"
        if severity not in ("info", "warn", "critical"):
            severity = "info"
        hooks.append(
            HookSpec(
                name=_HOOK_NAME_TEMPLATE.format(index=index + 1),
                url=url,
                format=fmt,  # type: ignore[arg-type]
                secret=_at(secrets, index, "") or None,
                min_severity=severity,  # type: ignore[arg-type]
            )
        )

    enabled = environ.get(ENV_ENABLED, "").strip().lower() in _TRUTHY
    return NotifyConfig(enabled=enabled, hooks=tuple(hooks))
