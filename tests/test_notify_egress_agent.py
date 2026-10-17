"""The webhook egress policy must refuse non-public destinations (SEC-008).

Motivation
----------
``validsim/notify/config.py`` accepted any absolute http(s) URL as a webhook
destination, and ``WebhookDispatcher.register()`` accepted any string at all --
including ``file://`` and ``not-a-url``. An operator-supplied hook could
therefore name ``http://169.254.169.254/`` (cloud instance metadata, which hands
out credentials) or ``http://127.0.0.1:8500/`` and have the scorecard JSON POSTed
there from inside the deployment's network.

Both entry points now share one policy, :mod:`validsim.notify.egress`, so the
env-driven path and the programmatic API cannot drift -- a hardened config path
beside a permissive API is precisely the split that lets an SSRF through.

What is asserted
----------------
``forbidden_host_reason`` is pure (a string in, a reason or ``None`` out), so
the whole address taxonomy is pinned here with no DNS, no sockets and no
network. The integration tests at the bottom confirm the two call sites actually
apply it, and that the documented ``allow_private_hosts`` escape hatch is the
only way past it.

The honest limit is pinned too: a *DNS name* that resolves to a private address
is not caught, because judging it would require a lookup and this predicate is
deliberately pure. That is a documented residual, not an accident.
"""

from __future__ import annotations

import pytest

from validsim.notify.config import _valid_url, notify_config_from_env
from validsim.notify.dispatcher import WebhookDispatcher
from validsim.notify.egress import (
    ALLOWED_SCHEMES,
    forbidden_host_reason,
    is_deliverable_url,
    url_rejection_reason,
)


class TestPublicDestinationsAreAllowed:
    """The policy must not block the destinations operators actually use."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://hooks.slack.com/services/T00/B00/XXXX",
            "https://example.com/webhook",
            "https://example.com:8443/webhook?x=1",
            "http://example.com/plain-http",
            "https://8.8.8.8/dns",  # a genuinely global literal
            "https://[2606:4700:4700::1111]/v6",
        ],
    )
    def test_an_ordinary_public_url_is_deliverable(self, url: str) -> None:
        assert url_rejection_reason(url) is None
        assert is_deliverable_url(url) is True

    def test_only_http_and_https_are_schemes(self) -> None:
        assert ALLOWED_SCHEMES == frozenset({"http", "https"})


class TestNonHttpSchemesAreRefused:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "file:///c:/windows/win.ini",
            "gopher://example.com:11211/_stats",
            "ftp://example.com/x",
            "not-a-url",
            "",
            "//example.com/no-scheme",
        ],
    )
    def test_a_non_http_url_is_refused(self, url: str) -> None:
        assert url_rejection_reason(url) is not None

    def test_a_bare_hostname_has_no_scheme(self) -> None:
        assert url_rejection_reason("example.com/hook") is not None


class TestLoopbackIsRefused:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "localhost",
            "LOCALHOST",
            "localhost.",  # trailing dot is the same name to a resolver
            "app.localhost",
        ],
    )
    def test_loopback_is_refused(self, host: str) -> None:
        assert forbidden_host_reason(host) is not None


class TestLinkLocalAndMetadataAreRefused:
    @pytest.mark.parametrize(
        "host",
        [
            "169.254.169.254",  # AWS/GCP/Azure instance metadata
            "169.254.0.1",
            "fe80::1",
            "metadata.google.internal",
            "metadata.goog",
            "instance-data",
        ],
    )
    def test_link_local_and_metadata_are_refused(self, host: str) -> None:
        assert forbidden_host_reason(host) is not None


class TestPrivateRangesAreRefused:
    @pytest.mark.parametrize(
        "host",
        [
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "fd00::1",
            "fc00::1",
            "0.0.0.0",
            "::",
            "224.0.0.1",  # multicast
            "255.255.255.255",  # reserved
            "100.64.0.1",  # shared address space (CGNAT)
        ],
    )
    def test_a_private_range_is_refused(self, host: str) -> None:
        assert forbidden_host_reason(host) is not None

    def test_an_ipv4_mapped_ipv6_loopback_is_unwrapped(self) -> None:
        """``::ffff:127.0.0.1`` is loopback wearing a v6 costume."""
        assert forbidden_host_reason("::ffff:127.0.0.1") is not None

    def test_an_ipv4_mapped_ipv6_private_is_unwrapped(self) -> None:
        assert forbidden_host_reason("::ffff:10.0.0.1") is not None


class TestPrivateUseNamesAreRefused:
    @pytest.mark.parametrize(
        "host",
        [
            "service.internal",
            "printer.local",
            "gateway.home.arpa",
            "api.corp.internal",
        ],
    )
    def test_a_private_use_suffix_is_refused(self, host: str) -> None:
        assert forbidden_host_reason(host) is not None


class TestDocumentedLimits:
    """Pin the boundary honestly so the residual risk is visible, not assumed."""

    def test_a_plain_dns_name_is_not_resolved_here(self) -> None:
        """A name that *might* resolve privately is allowed -- no DNS is done.

        Closing this needs the resolved address pinned at connect time, which is
        a transport concern. Pinning the current behaviour keeps the limitation
        a deliberate, reviewable decision rather than a surprise.
        """
        assert forbidden_host_reason("internal-service.example.com") is None

    def test_a_missing_host_is_refused(self) -> None:
        assert forbidden_host_reason(None) is not None
        assert forbidden_host_reason("") is not None
        assert forbidden_host_reason(".") is not None


class TestConfigPathAppliesThePolicy:
    """``VALIDSIM_WEBHOOK_URLS`` must not accept a private destination."""

    def test_valid_url_rejects_a_private_host(self) -> None:
        assert _valid_url("http://127.0.0.1:9/hook") is False
        assert _valid_url("http://169.254.169.254/latest/meta-data/") is False

    def test_valid_url_accepts_a_public_host(self) -> None:
        assert _valid_url("https://hooks.example.com/x") is True

    def test_a_private_destination_is_dropped_from_the_resolved_config(self) -> None:
        config = notify_config_from_env(
            {
                "VALIDSIM_NOTIFY_ENABLED": "1",
                "VALIDSIM_WEBHOOK_URLS": (
                    "http://127.0.0.1:9/internal,https://hooks.example.com/ok"
                ),
            }
        )
        urls = [hook.url for hook in config.hooks]
        assert urls == ["https://hooks.example.com/ok"], (
            "the private destination must be dropped, not merely ordered last"
        )

    def test_every_destination_private_leaves_no_hooks(self) -> None:
        config = notify_config_from_env(
            {
                "VALIDSIM_NOTIFY_ENABLED": "1",
                "VALIDSIM_WEBHOOK_URLS": "http://127.0.0.1:9/a,http://10.0.0.1/b",
            }
        )
        assert config.hooks == ()


class TestDispatcherAppliesThePolicy:
    """``register()`` must refuse, not merely record, a bad destination."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9/hook",
            "http://localhost:9/hook",
            "http://[::1]:9/hook",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/hook",
            "file:///etc/passwd",
            "not-a-url",
        ],
    )
    def test_register_raises_for_a_refused_destination(self, url: str) -> None:
        dispatcher = WebhookDispatcher(live=True)
        with pytest.raises(ValueError, match="refusing webhook destination"):
            dispatcher.register("x", url)
        assert dispatcher.sent == []

    def test_register_accepts_a_public_destination(self) -> None:
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("x", "https://hooks.example.com/ok")
        assert dispatcher.sent == []

    def test_the_escape_hatch_restores_a_private_destination(self) -> None:
        """The documented opt-in is the ONLY way past the policy."""
        dispatcher = WebhookDispatcher(live=True, allow_private_hosts=True)
        dispatcher.register("local", "http://127.0.0.1:9/hook")  # must not raise

    def test_field_validation_still_runs_before_the_url_check(self) -> None:
        """A bad format must keep reporting the format, not the URL."""
        dispatcher = WebhookDispatcher(live=True)
        with pytest.raises(ValueError, match="unsupported hook format"):
            dispatcher.register("x", "http://127.0.0.1:9/h", format="xml")
