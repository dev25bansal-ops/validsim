"""Destination-safety policy for webhook egress (SSRF defence).

A notification destination is *configuration*, not attacker input, but that is
exactly why it needs a check: a copy-pasted internal URL or a one-character typo
turns the dispatcher into a server-side request forgery primitive. The payload
carries run ids, verdicts and scores, and the request originates inside the
deployment's own network -- so a hook pointed at ``http://169.254.169.254/`` or
``http://127.0.0.1:8500/`` reaches infrastructure the operator never meant to
expose, from a process that already holds credentials.

This module is the single policy used by both entry points
(:func:`validsim.notify.config._valid_url` and
:meth:`validsim.notify.dispatcher.WebhookDispatcher.register`) so the two cannot
drift -- the failure mode where the env-driven path is hardened but the direct
API is not is the one this exists to prevent.

Scope and honest limits
-----------------------
Only the **URL text** is judged: scheme, and a *literal* IP address or the
reserved ``localhost`` name in the host position. No DNS is performed, which
keeps the check pure (no I/O, no ambient state, safe to call during config
parsing) but also means a hostname that *resolves* to a private address --
including a DNS-rebinding answer -- is not caught here. Closing that requires
pinning the resolved address at connect time, which belongs in the transport,
not in a pure predicate. What this does guarantee is that the overwhelmingly
common SSRF spellings (``127.0.0.1``, ``::1``, ``10.``, ``192.168.``,
``169.254.169.254``, ``0.0.0.0``, ``[fd00::1]``, ``localhost``) are refused
before any socket is opened.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

__all__ = [
    "ALLOWED_SCHEMES",
    "forbidden_host_reason",
    "is_deliverable_url",
    "url_rejection_reason",
]

#: The only URL schemes a webhook may use. Anything else (``file:``, ``gopher:``,
#: ``ftp:``, a bare hostname) is a misconfiguration; handing it to an HTTP client
#: is how a config typo reaches a non-HTTP protocol handler.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Names reserved for loopback by RFC 6761 that never need a DNS lookup to
#: classify.
_LOOPBACK_NAMES = frozenset({"localhost"})

#: Well-known cloud instance-metadata hostnames. The literal-address check
#: cannot see these -- they are DNS names -- but they are the single highest
#: value SSRF target on any cloud host, because they hand out instance
#: credentials to anything that can reach them. The set is small and stable, so
#: blocking by name costs nothing and closes the most damaging case.
_METADATA_NAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata",
    }
)

#: Suffixes that name non-public namespaces by convention, so a webhook should
#: never target one. ``.internal`` is reserved for private use (ICANN 2024),
#: ``.local`` is mDNS, and ``.localhost`` is loopback. A genuine third-party
#: webhook lives on a public domain, so refusing these cannot break a real
#: integration -- it only refuses the operator's own network.
_PRIVATE_SUFFIXES = (".internal", ".local", ".localhost", ".home.arpa")


def forbidden_host_reason(host: str | None) -> str | None:
    """Return why ``host`` must not be contacted, or ``None`` if it is allowed.

    ``host`` is a URL host as produced by :attr:`urllib.parse.ParseResult.hostname`
    (port and IPv6 brackets already stripped, lower-cased). Only literal
    addresses and reserved loopback names are classified; a genuine DNS name
    returns ``None`` because judging it would require a lookup.

    Args:
        host: Host component, or ``None`` when the URL had none.

    Returns:
        A short human-readable reason when the host is off-limits, else ``None``.
    """
    if not host:
        return "missing host"
    # A trailing dot is the same name to a resolver ("localhost." == "localhost")
    # but a different string to a naive comparison, so normalise it away first.
    name = host.strip().rstrip(".").lower()
    if not name:
        return "missing host"
    if name in _LOOPBACK_NAMES or name.endswith(".localhost"):
        return "loopback hostname"
    if name in _METADATA_NAMES:
        return "cloud instance-metadata hostname"
    if name.endswith(_PRIVATE_SUFFIXES):
        return "private-use hostname suffix"

    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        # Not an IP literal: a DNS name we deliberately do not resolve here.
        return None

    # IPv4-mapped IPv6 (``::ffff:127.0.0.1``) wraps a private IPv4 inside a v6
    # literal; unwrap it so the checks below see the real address class.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped

    if address.is_loopback:
        return "loopback address"
    if address.is_link_local:
        return "link-local address"
    if address.is_unspecified:
        return "unspecified address"
    if address.is_multicast:
        return "multicast address"
    if address.is_reserved:
        return "reserved address"
    if address.is_private:
        return "private address"
    if not address.is_global:
        # Catch-all for the ranges the named predicates miss (e.g. shared
        # address space, benchmarking nets). Failing closed is correct here:
        # an unrecognised non-global range is not a destination we can justify.
        return "non-global address"
    return None


def url_rejection_reason(url: str) -> str | None:
    """Return why ``url`` is not an acceptable webhook destination, else ``None``.

    Combines the scheme allowlist with :func:`forbidden_host_reason` so callers
    get one verdict and one message.

    Args:
        url: Candidate destination URL.

    Returns:
        A short reason string, or ``None`` when the URL may be contacted.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "unparseable URL"
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"unsupported scheme {parsed.scheme or '<none>'!r}"
    if not parsed.netloc:
        return "missing host"
    return forbidden_host_reason(parsed.hostname)


def is_deliverable_url(url: str) -> bool:
    """Whether ``url`` is an absolute http(s) URL with a routable host."""
    return url_rejection_reason(url) is None
