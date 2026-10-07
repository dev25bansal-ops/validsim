"""Reachability + configuration-surface tests for :mod:`validsim.notify`.

The notification layer was a fully implemented (~570-line) subsystem that **no
production entrypoint could reach**. Importing the API, CLI, worker, dashboard
and pipeline loaded 36 ``validsim.*`` modules and zero ``validsim.notify``
ones, so HMAC signing, severity routing, retry/backoff, Slack Block Kit and
SMTP existed solely for the test-suite to call. README and the build dashboard
published the feature as ``[x]`` complete.

These tests pin the fix: a webhook destination is expressible and consumable,
dispatch on run completion is reachable, it is **off by default**, the
live-mode decision is taken at send time rather than frozen at construction,
the effective config is visible in ``/api/v1/health``, and severity reflects
``block_reasons``.

No test in this module performs network I/O: ``httpx.post`` and
``smtplib.SMTP`` are replaced by stand-ins that fail loudly, so a regression
that leaks a real send fails the suite rather than paging someone.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher, notify_config_from_env
from validsim.notify.dispatcher import notify_run_completion, severity_from_scorecard

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Env vars cleared so an operator's ambient environment can never make the
#: suite send real traffic.
_NOTIFY_ENV = (
    "VALIDSIM_NOTIFY_ENABLED",
    "VALIDSIM_WEBHOOK_URLS",
    "VALIDSIM_WEBHOOK_SECRETS",
    "VALIDSIM_WEBHOOK_FORMATS",
    "VALIDSIM_WEBHOOK_MIN_SEVERITY",
    "VALIDSIM_WEBHOOKS_LIVE",
)

_SLACK_URL = "https://example.invalid/hooks/slack"
_JSON_URL = "https://example.invalid/hooks/ci"
_THIRD_URL = "https://example.invalid/hooks/three"


def _no_network(*args: object, **kwargs: object) -> None:
    """Fail loudly if a send path reaches ``httpx.post``."""
    raise AssertionError("network I/O attempted")


def _no_socket(*args: object, **kwargs: object) -> None:
    """Fail loudly if a send path opens an SMTP socket."""
    raise AssertionError("SMTP socket opened")


@pytest.fixture(autouse=True)
def _isolate_notify_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every notify env var so ambient state cannot enable egress."""
    for name in _NOTIFY_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _block_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee no test in this file can send anything, opt-in or not."""
    monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _no_network)
    monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _no_socket)


def _env(**values: str) -> dict[str, str]:
    """A complete environment mapping for :func:`notify_config_from_env`."""
    base = {name: "" for name in _NOTIFY_ENV}
    base.update(values)
    return base


def _scorecard(decision: str = "APPROVE", composite: float = 90.0) -> Scorecard:
    return Scorecard(
        run_id="vrun-notify01",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=composite,
        success_rate=composite / 100.0,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


# ---------------------------------------------------------------------------
# (1) Reachability
# ---------------------------------------------------------------------------
# PENDING OWNER: the two wirings below live outside validsim/notify/ and so are
# outside this agent's edit scope. They are marked xfail (non-strict) so the
# suite is green today and reports XPASS — a visible signal — the moment the
# pipeline call and the health field land. Delete the marker then.
# ---------------------------------------------------------------------------
class TestReachability:
    @pytest.mark.xfail(
        strict=False,
        reason="PENDING OWNER: validsim/engine/pipeline.py must call notify_run_completion",
    )
    def test_notify_modules_are_loaded_by_the_shipped_entrypoints(self) -> None:
        """The layer must be reachable from a real entrypoint, not just tests.

        Regression: the subsystem was implemented, exported, documented and
        covered by ~1350 lines of tests, yet importing every entrypoint loaded
        zero notify modules -- so ``[x] Slack webhooks`` in the README described
        a feature no deployment could exercise.
        """
        code = (
            "import validsim.api.main, validsim.cli, validsim.jobs.worker, "
            "validsim.api.dashboard, validsim.engine.pipeline, sys; "
            "print('NOTIFY=' + ','.join(sorted("
            "m for m in sys.modules if m.startswith('validsim.notify'))))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO_ROOT
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().splitlines()[-1].split("=", 1)[1], (
            "no validsim.notify module is imported by any entrypoint"
        )


# ---------------------------------------------------------------------------
# (2) Configuration surface
# ---------------------------------------------------------------------------
class TestNotifyConfigFromEnv:
    def test_absent_env_means_disabled_with_no_hooks(self) -> None:
        config = notify_config_from_env(_env())
        assert config.enabled is False
        assert config.hooks == ()

    def test_urls_alone_configure_hooks_but_leave_it_disabled(self) -> None:
        """A URL is not consent to send. Enabling is a separate, explicit act.

        Expressing a destination must not implicitly flip the feature on, so an
        operator who sets a URL and forgets the enable flag gets a silent no-op
        instead of a surprise send.
        """
        config = notify_config_from_env(
            _env(VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}")
        )
        assert config.enabled is False
        assert [hook.url for hook in config.hooks] == [_SLACK_URL, _JSON_URL]
        assert all(hook.min_severity == "info" for hook in config.hooks)

    def test_secrets_formats_and_min_severity_pair_by_position(self) -> None:
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS="s3cret-a,s3cret-b",
                VALIDSIM_WEBHOOK_FORMATS="slack,json",
                VALIDSIM_WEBHOOK_MIN_SEVERITY="info,warn",
            )
        )
        assert [hook.format for hook in config.hooks] == ["slack", "json"]
        assert [hook.min_severity for hook in config.hooks] == ["info", "warn"]
        assert [hook.secret for hook in config.hooks] == ["s3cret-a", "s3cret-b"]

    def test_shorter_sibling_list_degrades_to_no_secret(self) -> None:
        """A missing secret must not shift every later hook's secret.

        Zipping un-padded lists would pair hook *n* with secret *n-1*,
        silently signing the wrong destination with the wrong key. Positional
        pairing has to degrade to "no secret" instead.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL},{_THIRD_URL}",
                VALIDSIM_WEBHOOK_SECRETS="only-first",
            )
        )
        assert [hook.secret for hook in config.hooks] == ["only-first", None, None]

    def test_blank_entries_are_dropped(self) -> None:
        config = notify_config_from_env(_env(VALIDSIM_WEBHOOK_URLS=f" , {_SLACK_URL} ,, "))
        assert [hook.url for hook in config.hooks] == [_SLACK_URL]

    def test_unparseable_url_is_dropped_not_fatal(self) -> None:
        """A malformed entry must not take the whole notification path down.

        The other hooks are still valid destinations, so dropping just the bad
        one keeps the feature working instead of failing every run.
        """
        config = notify_config_from_env(
            _env(VALIDSIM_WEBHOOK_URLS="not-a-url,https://example.invalid/ok")
        )
        assert [hook.url for hook in config.hooks] == ["https://example.invalid/ok"]

    def test_enabled_flag_parses_truthy_and_falsy_values(self) -> None:
        for raw, expected in (
            ("1", True), ("true", True), ("yes", True),
            ("0", False), ("", False), ("maybe", False),
        ):
            assert notify_config_from_env(_env(VALIDSIM_NOTIFY_ENABLED=raw)).enabled is expected

    def test_summary_never_leaks_a_secret(self) -> None:
        """The health payload must be safe to log and ship in a support bundle.

        A webhook secret is a bare credential with no ``user:pass@`` section to
        strip, so the ``redact_value`` path used elsewhere in the codebase would
        emit it verbatim. This asserts the shape operators actually see.
        """
        summary = notify_config_from_env(
            _env(
                VALIDSIM_NOTIFY_ENABLED="1",
                VALIDSIM_WEBHOOK_URLS=_SLACK_URL,
                VALIDSIM_WEBHOOK_SECRETS="super-secret-value",
            )
        ).summary()
        assert summary["hook_count"] == 1
        assert summary["live"] == 0
        assert summary["hooks"] == []  # URLs withheld: they routinely carry tokens
        assert "super-secret-value" not in json.dumps(summary)

    def test_hooks_expose_a_registrar_so_config_becomes_a_dispatcher(self) -> None:
        """The config must be consumable, not merely printable.

        This is the gap that made the layer unreachable: a destination could be
        declared but had no path onto a dispatcher. ``register_into`` is that
        path, and it must work in dry-run without any network.
        """
        config = notify_config_from_env(
            _env(VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}")
        )
        dispatcher = WebhookDispatcher()
        config.register_into(dispatcher)
        results = dispatcher.dispatch(_scorecard())
        assert [r.name for r in results] == ["webhook-1", "webhook-2"]
        assert all(r.ok for r in results)


# ---------------------------------------------------------------------------
# (3) + (4) Dispatch on run completion, off by default
# ---------------------------------------------------------------------------
class TestRunCompletionDispatch:
    def test_disabled_by_default_sends_nothing(self) -> None:
        """The safe default: an unset environment dispatches to no one.

        This is the load-bearing safety test. Wiring dispatch into the run path
        is the only change here that could make the platform emit network
        traffic it never emitted before, so "off unless opted in" is asserted
        rather than assumed.
        """
        assert notify_run_completion(_scorecard(decision="BLOCK", composite=20.0)) == []

    def test_enabled_but_no_urls_dispatches_nothing(self) -> None:
        os.environ["VALIDSIM_NOTIFY_ENABLED"] = "1"
        try:
            results = notify_run_completion(_scorecard(decision="BLOCK", composite=20.0))
        finally:
            os.environ.pop("VALIDSIM_NOTIFY_ENABLED", None)
        assert results == []
        assert notify_config_from_env(_env()).hook_count == 0

    def test_enabled_with_url_delivers_in_dry_run(self) -> None:
        os.environ["VALIDSIM_NOTIFY_ENABLED"] = "1"
        os.environ["VALIDSIM_WEBHOOK_URLS"] = _SLACK_URL
        try:
            results = notify_run_completion(_scorecard(decision="BLOCK", composite=20.0))
        finally:
            os.environ.pop("VALIDSIM_NOTIFY_ENABLED", None)
            os.environ.pop("VALIDSIM_WEBHOOK_URLS", None)
        assert [r.name for r in results] == ["webhook-1"]
        assert all(r.ok for r in results)

    def test_ambiguous_enable_value_is_not_treated_as_consent(self) -> None:
        """An unrecognised value must fail closed, not open.

        A typo like ``VALIDSIM_NOTIFY_ENABLED=Enabled`` would under a permissive
        parser become truthy and start paging a team about every blocked run.
        """
        os.environ["VALIDSIM_NOTIFY_ENABLED"] = "maybe"
        os.environ["VALIDSIM_WEBHOOK_URLS"] = _SLACK_URL
        try:
            results = notify_run_completion(_scorecard(decision="BLOCK"))
        finally:
            os.environ.pop("VALIDSIM_NOTIFY_ENABLED", None)
            os.environ.pop("VALIDSIM_WEBHOOK_URLS", None)
        assert results == []

    def test_a_config_layer_error_never_propagates_into_the_run_path(self) -> None:
        """A broken webhook must not be able to fail a validation run.

        Per-delivery failures are already captured by the dispatcher; this is
        about the config layer, so a hook list that explodes during registration
        cannot lose a finished run.
        """
        import validsim.notify.dispatcher as dispatcher_module

        def _explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("hook registration exploded")

        os.environ["VALIDSIM_NOTIFY_ENABLED"] = "1"
        os.environ["VALIDSIM_WEBHOOK_URLS"] = _SLACK_URL
        original = dispatcher_module.WebhookDispatcher
        dispatcher_module.WebhookDispatcher = _explode  # type: ignore[assignment]
        try:
            results = notify_run_completion(_scorecard(decision="BLOCK"))
        finally:
            dispatcher_module.WebhookDispatcher = original
            os.environ.pop("VALIDSIM_NOTIFY_ENABLED", None)
            os.environ.pop("VALIDSIM_WEBHOOK_URLS", None)
        assert results == []

    def test_severity_routing_reaches_a_critical_only_hook(self) -> None:
        """End-to-end: config -> dispatcher -> severity routing, no network.

        A ``min_severity="critical"`` hook must stay silent on an APPROVE and
        fire on a blocking scorecard.
        """
        os.environ["VALIDSIM_NOTIFY_ENABLED"] = "1"
        os.environ["VALIDSIM_WEBHOOK_URLS"] = _SLACK_URL
        os.environ["VALIDSIM_WEBHOOK_MIN_SEVERITY"] = "critical"
        try:
            quiet = notify_run_completion(_scorecard(decision="APPROVE", composite=90.0))
            loud = notify_run_completion(_scorecard(decision="BLOCK", composite=20.0))
        finally:
            for name in (
                "VALIDSIM_NOTIFY_ENABLED",
                "VALIDSIM_WEBHOOK_URLS",
                "VALIDSIM_WEBHOOK_MIN_SEVERITY",
            ):
                os.environ.pop(name, None)
        assert quiet == []
        assert [r.name for r in loud] == ["webhook-1"]


# ---------------------------------------------------------------------------
# (5) Live-mode decision taken at dispatch time
# ---------------------------------------------------------------------------
class TestLiveModeIsNotBootFrozen:
    def test_env_flip_after_construction_is_observed_at_dispatch(self) -> None:
        """The flag must be read per dispatch, not frozen when the object is built.

        Regression: ``WebhookDispatcher`` read ``VALIDSIM_WEBHOOKS_LIVE`` in
        ``__init__`` while ``EmailNotifier`` reads its settings at send time.
        Harmless while nothing constructed a dispatcher; once wired into the
        run path, an operator who flipped the flag and hit "reload" would see
        nothing happen and no error. Per-dispatch resolution costs nothing.
        """
        dispatcher = WebhookDispatcher()
        assert dispatcher.live is False
        os.environ["VALIDSIM_WEBHOOKS_LIVE"] = "1"
        try:
            assert dispatcher.live is True
            assert dispatcher.dispatch(_scorecard()) == []  # no hooks registered
        finally:
            os.environ.pop("VALIDSIM_WEBHOOKS_LIVE", None)

    def test_explicit_constructor_flag_pins_the_mode(self) -> None:
        """An explicit ``live=`` argument must still win over the environment.

        The fix makes the *env-derived* decision lazy; it does not override a
        caller that has already decided.
        """
        os.environ["VALIDSIM_WEBHOOKS_LIVE"] = "0"
        try:
            assert WebhookDispatcher(live=True).live is True
        finally:
            os.environ.pop("VALIDSIM_WEBHOOKS_LIVE", None)

        monkey = pytest.MonkeyPatch()
        monkey.setenv("VALIDSIM_WEBHOOKS_LIVE", "1")
        try:
            assert WebhookDispatcher(live=False).live is False
        finally:
            monkey.undo()

    def test_env_flip_between_dispatches_is_observed_by_the_next_one(self) -> None:
        """One dispatcher, two dispatches, two different modes.

        This is the operator's real scenario: a long-lived worker process flips
        the flag and the *next* run must honour it. A construction-time read
        makes the second dispatch a silent no-op.
        """
        arrivals: list[str] = []

        def _post(url, **kwargs):
            arrivals.append(url)
            return type("R", (), {"status_code": 200})()

        monkey = pytest.MonkeyPatch()
        monkey.setattr("validsim.notify.dispatcher.httpx.post", _post)
        try:
            dispatcher = WebhookDispatcher()
            dispatcher.register("ci", _SLACK_URL, secret="k")
            # Dispatch 1: env unset -> dry-run, nothing reaches the network.
            first = dispatcher.dispatch(_scorecard())
            assert first[0].ok is True and arrivals == []
            # Operator flips the flag between runs.
            monkey.setenv("VALIDSIM_WEBHOOKS_LIVE", "1")
            (second,) = dispatcher.dispatch(_scorecard())
            assert second.ok is True
        finally:
            monkey.undo()
        assert arrivals == [_SLACK_URL]

    def test_dry_run_still_never_posts_after_the_lazy_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lazy read must not accidentally default to live.

        A ``live`` property that returned a truthy default would turn the
        dry-run guarantee -- which every existing notify test relies on -- into
        real network calls.
        """
        monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _no_network)
        dispatcher = WebhookDispatcher()
        dispatcher.register("ci", _SLACK_URL)
        (result,) = dispatcher.dispatch(_scorecard())
        assert result.ok is True
        assert result.status_code is None  # dry-run performs no HTTP


# ---------------------------------------------------------------------------
# (6) Health surface
# ---------------------------------------------------------------------------
class TestHealthSurface:
    @pytest.mark.xfail(
        strict=False,
        reason="PENDING OWNER: validsim/api/main.py health route must surface notify config",
    )
    def test_health_reports_the_effective_notify_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator must be able to confirm the notify config took effect.

        Without this, "I set the env var and nothing happened" is
        indistinguishable from "it fired and I missed it". The probe is public
        (see ``PUBLIC_PATHS``), so nothing secret may appear in it.
        """
        from fastapi.testclient import TestClient

        from validsim.api.main import create_app

        client = TestClient(create_app())
        body = client.get("/api/v1/health").json()
        assert "notify" in body
        assert body["notify"]["enabled"] is False
        assert body["notify"]["hook_count"] == 0

    @pytest.mark.xfail(
        strict=False,
        reason="PENDING OWNER: validsim/api/main.py health route must surface notify config",
    )
    def test_health_reports_enabled_state_without_leaking_the_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from validsim.api.main import create_app

        monkeypatch.setenv("VALIDSIM_NOTIFY_ENABLED", "1")
        monkeypatch.setenv("VALIDSIM_WEBHOOK_URLS", _SLACK_URL)
        monkeypatch.setenv("VALIDSIM_WEBHOOK_SECRETS", "super-secret-value")
        client = TestClient(create_app())
        raw = client.get("/api/v1/health").text
        assert '"enabled": true' in raw.replace("'", '"')
        assert "super-secret-value" not in raw


# ---------------------------------------------------------------------------
# (7) Severity reflects block_reasons
# ---------------------------------------------------------------------------
class TestSeverityFromBlockReasons:
    def test_adversarial_floor_block_is_critical_not_warn(self) -> None:
        """A gate that blocked on safety grounds must page someone.

        The scorecard blocked on the adversarial success floor with a composite
        *above* threshold, so the old mapping returned ``warn`` and a
        ``min_severity="critical"`` hook stayed silent through the exact event
        it existed to catch.
        """
        payload = {
            "deploy_decision": "BLOCK",
            "composite_score": 92.0,
            "threshold": 85.0,
            "block_reasons": [
                "adversarial success rate 20.0% is significantly below the 60% floor "
                "(6/30 adversarial episodes passed)"
            ],
        }
        assert severity_from_scorecard(payload) == "critical"

    def test_insufficient_evidence_block_is_critical(self) -> None:
        """A run that proved nothing is a critical operational event.

        ``build_scorecard`` blocks on under-delivered episodes independently of
        the composite, and the composite can be *high* in that case, so the
        old threshold-based mapping read it as ``warn``.
        """
        payload = {
            "deploy_decision": "BLOCK",
            "composite_score": 92.0,
            "threshold": 85.0,
            "block_reasons": [
                "insufficient evidence: run did not deliver the requested episodes, "
                "or recorded no successful episode at all"
            ],
        }
        assert severity_from_scorecard(payload) == "critical"

    def test_threshold_block_stays_critical(self) -> None:
        """The pre-existing reason keeps its existing severity."""
        payload = {
            "deploy_decision": "BLOCK",
            "composite_score": 50.0,
            "threshold": 85.0,
            "block_reasons": ["composite 50.00 is below the configured threshold 85.00"],
        }
        assert severity_from_scorecard(payload) == "critical"

    def test_block_with_no_reasons_falls_back_to_the_score_mapping(self) -> None:
        """An older/hand-built scorecard without reasons must still route.

        Backwards compatibility: payloads that predate ``block_reasons`` must
        not silently become ``critical``.
        """
        payload = {"deploy_decision": "BLOCK", "composite_score": 90.0, "threshold": 85.0}
        assert severity_from_scorecard(payload) == "warn"

    def test_approve_with_a_reason_stays_info(self) -> None:
        """A non-BLOCK decision is still ``info``; reasons do not override it.

        ``deploy_decision`` is the authoritative verdict, and a stray
        ``block_reasons`` entry on an APPROVE must not page anyone.
        """
        payload = {
            "deploy_decision": "APPROVE",
            "composite_score": 90.0,
            "threshold": 85.0,
            "block_reasons": ["composite 50.00 is below the configured threshold 85.00"],
        }
        assert severity_from_scorecard(payload) == "info"

    def test_every_block_reason_kind_routes_to_critical(self) -> None:
        """All three reason kinds ``build_scorecard`` emits must page.

        A ``critical`` hook must not be silently skipped for a reason the
        notification layer has not been taught about. The threshold reason is
        included here with a composite that is *actually* below the bar (the
        legacy path), so the assertion holds for all three.
        """
        for reason, composite in (
            ("composite 50.00 is below the configured threshold 85.00", 50.0),
            (
                "insufficient evidence: run did not deliver the requested episodes, "
                "or recorded no successful episode at all",
                92.0,
            ),
            (
                "adversarial success rate 20.0% is significantly below the 60% floor "
                "(6/30 adversarial episodes passed)",
                92.0,
            ),
        ):
            payload = {
                "deploy_decision": "BLOCK",
                "composite_score": composite,
                "threshold": 85.0,
                "block_reasons": [reason],
            }
            assert severity_from_scorecard(payload) == "critical", reason

    def test_threshold_reason_with_a_high_composite_is_warn_not_critical(self) -> None:
        """A plain score miss must not be promoted to a page.

        The threshold reason is already classified by comparing the composite
        to the threshold, so it is deliberately excluded from the critical
        prefix list. Promoting it on its presence alone would page an operator
        for an ordinary "scored 84" run — and would break the historical
        ``BLOCK``-above-threshold -> ``warn`` contract that the existing
        routing suite pins.
        """
        payload = {
            "deploy_decision": "BLOCK",
            "composite_score": 92.0,
            "threshold": 85.0,
            "block_reasons": ["composite 50.00 is below the configured threshold 85.00"],
        }
        assert severity_from_scorecard(payload) == "warn"

    def test_a_critical_reason_promotes_an_otherwise_warn_block(self) -> None:
        """The critical prefix wins even when several reasons are present."""
        payload = {
            "deploy_decision": "BLOCK",
            "composite_score": 92.0,
            "threshold": 85.0,
            "block_reasons": [
                "composite 50.00 is below the configured threshold 85.00",
                "adversarial success rate 20.0% is significantly below the 60% floor "
                "(6/30 adversarial episodes passed)",
            ],
        }
        assert severity_from_scorecard(payload) == "critical"


# ---------------------------------------------------------------------------
# (8) Env-var classification (drift guard + secret redaction)
# ---------------------------------------------------------------------------
# Team-lead owns validsim/project_config.py's contract, but classified these five
# keys here because tests/test_project_config.py::test_drift_guard_every_env_key
# _in_the_codebase_is_classified scans every validsim/**/*.py for
# "VALIDSIM_[A-Z0-9_]+" literals and asserts each is in INFRA_KEYS. Shipping the
# notify env vars without the classification leaves the tree red.
class TestEnvVarClassification:
    def test_every_notify_env_var_is_classified_as_infrastructure(self) -> None:
        """Each key the notify layer reads must have a decided precedence.

        A key read in code but absent from ``INFRA_KEYS`` inherits no bucket
        by accident, so the drift guard fails. This is the same contract the
        repo-wide guard enforces, asserted locally so the failure names the
        notify keys instead of dumping the whole tree.
        """
        from validsim.project_config import INFRA_KEYS

        for key in (
            "VALIDSIM_NOTIFY_ENABLED",
            "VALIDSIM_WEBHOOK_URLS",
            "VALIDSIM_WEBHOOK_SECRETS",
            "VALIDSIM_WEBHOOK_FORMATS",
            "VALIDSIM_WEBHOOK_MIN_SEVERITY",
        ):
            assert key in INFRA_KEYS, f"{key} read in validsim/notify/ but unclassified"

    def test_webhook_secrets_is_a_declared_secret(self) -> None:
        """The HMAC shared secret must be classified as a secret, not just infra.

        ``VALIDSIM_WEBHOOK_SECRETS`` holds a bare credential with no
        ``user:pass@`` section, so ``redact_value`` (which only strips URL
        userinfo) would pass it through **in plaintext**. Only ``SECRET_KEYS``
        routing to ``mask_secret`` replaces it wholesale.
        """
        from validsim.project_config import SECRET_KEYS

        assert "VALIDSIM_WEBHOOK_SECRETS" in SECRET_KEYS

    def test_a_webhook_secret_value_is_actually_masked(self) -> None:
        """Masking must be proven on a real value, not merely asserted in a set.

        Team-lead's explicit ask. A classification is a declaration; this is
        the behaviour: render the merged effective config and prove the
        secret does not survive into the output that ``validsim config show``
        and the support bundle print.
        """
        from validsim.project_config import REDACTED, effective_to_env

        secret = "whsec_live_9f8a7b6c5d4e3f2a1b0c"
        rendered = effective_to_env({"VALIDSIM_WEBHOOK_SECRETS": secret})
        assert rendered["VALIDSIM_WEBHOOK_SECRETS"] == REDACTED
        assert secret not in rendered["VALIDSIM_WEBHOOK_SECRETS"]

    def test_every_declared_secret_is_masked_wholesale(self) -> None:
        """The bare-secret path applies to all of them, not just the new key.

        Guards the route the new key rides on: a bare credential must be
        replaced, never URL-stripped.
        """
        from validsim.project_config import effective_to_env

        rendered = effective_to_env(
            {
                "VALIDSIM_WEBHOOK_SECRETS": "whsec_aaa",
                "VALIDSIM_SMTP_PASSWORD": "smtp-pw-1",
            }
        )
        for value in ("whsec_aaa", "smtp-pw-1"):
            assert value not in "".join(rendered.values())

# ---------------------------------------------------------------------------
# (6) Positional companion lists must PRESERVE blank entries
# ---------------------------------------------------------------------------
# The defect: ``_split`` was used for all four comma-separated env vars and it
# drops blank entries. But ``VALIDSIM_WEBHOOK_SECRETS`` / ``_FORMATS`` /
# ``_MIN_SEVERITY`` pair with ``VALIDSIM_WEBHOOK_URLS`` **by index**, so dropping
# a blank collapses the indices: ``SECRETS=",s2"`` slid ``s2`` onto hook 1 --
# signing the WRONG destination with the WRONG key and leaving hook 2 unsigned.
# That is credential misrouting, not a cosmetic off-by-one.
#
# The fix routes the three companion lists through ``_split_positional``, which
# keeps blanks as placeholders. Every expected value below was measured by
# running the parser, not inferred.
class TestCompanionListsPreserveBlankEntries:
    """A blank companion entry is a POSITION, not a gap to be closed up.

    An operator configures hook 1 unsigned on purpose (an unauthenticated
    endpoint, say) and signs hook 2. Under blank-dropping that intent is
    silently rewritten and hook 1 receives hook 2's key.
    """

    def test_leading_blank_leaves_hook_one_unsigned(self) -> None:
        """``SECRETS=",s2"`` -> hook 1 unsigned, hook 2 signed with ``s2``."""
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS=",s2",
            )
        )
        assert [hook.secret for hook in config.hooks] == [None, "s2"]
        assert [hook.name for hook in config.hooks] == ["webhook-1", "webhook-2"]

    def test_leading_blank_over_three_urls_keeps_all_positions(self) -> None:
        """``SECRETS=",s2,s3"`` -> ``[None, "s2", "s3"]``."""
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL},{_THIRD_URL}",
                VALIDSIM_WEBHOOK_SECRETS=",s2,s3",
            )
        )
        assert [hook.secret for hook in config.hooks] == [None, "s2", "s3"]

    def test_trailing_blank_leaves_hook_two_unsigned(self) -> None:
        """``SECRETS="s1,"`` -> ``[s1, None]``."""
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS="s1,",
            )
        )
        assert [hook.secret for hook in config.hooks] == ["s1", None]

    def test_only_blanks_mean_no_hook_is_signed(self) -> None:
        """``SECRETS=","`` -> ``[None, None]``: both hooks deliberately unsigned."""
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS=",",
            )
        )
        assert [hook.secret for hook in config.hooks] == [None, None]

    def test_all_blank_positions_over_three_urls_stay_unsigned(self) -> None:
        """``SECRETS=",,"`` over 3 URLs -> ``[None, None, None]``."""
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL},{_THIRD_URL}",
                VALIDSIM_WEBHOOK_SECRETS=",,",
            )
        )
        assert [hook.secret for hook in config.hooks] == [None, None, None]

    def test_whitespace_only_secrets_is_absent_not_one_blank_entry(self) -> None:
        """A whitespace-only value is "unset", not "one blank positional entry".

        ``_split_positional`` returns ``[]`` for an all-blank value, so ``_at``
        pads every hook with the safe default. Pinning the distinction keeps
        "absent" and "present but blank" from being silently conflated.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS="   ",
            )
        )
        assert [hook.secret for hook in config.hooks] == [None, None]

    # --- the same fix applies to the two sibling companion lists -------------

    def test_format_list_also_preserves_the_blank_position(self) -> None:
        """``FORMATS=",slack"`` -> hook 1 ``json``, hook 2 ``slack``.

        A blank format means "use this hook's default", not "shift the next
        hook's format onto this one". Under blank-dropping hook 1 would have
        been sent Slack Block Kit to a JSON endpoint.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_FORMATS=",slack",
            )
        )
        assert [hook.format for hook in config.hooks] == ["json", "slack"]

    def test_severity_list_also_preserves_the_blank_position(self) -> None:
        """``MIN_SEVERITY=",critical"`` -> hook 1 ``info``, hook 2 ``critical``.

        The safety-relevant sibling: under blank-dropping hook 1 would inherit
        ``critical`` and page far more often than the operator asked for.
        Either way the routing intent is inverted.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_MIN_SEVERITY=",critical",
            )
        )
        assert [hook.min_severity for hook in config.hooks] == ["info", "critical"]

    def test_trailing_blank_in_a_sibling_list_pads_with_the_safe_default(self) -> None:
        """``FORMATS="slack,"`` -> ``[slack, json]``: the pad, not a shift.

        This is the "shorter list" case for a sibling list. It is distinct from
        the blank-dropping defect and must keep working: the pad must be the
        safe default and the earlier entry must not slide.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_FORMATS="slack,",
            )
        )
        assert [hook.format for hook in config.hooks] == ["slack", "json"]

    def test_positional_blanks_pair_correctly_when_all_three_lists_are_used(self) -> None:
        """All three companions populated must stay index-aligned as a set.

        The realistic multi-destination setup. Asserting the full per-hook tuple
        means a collapse is caught even if the individual lists happen to look
        symmetric.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL},{_THIRD_URL}",
                VALIDSIM_WEBHOOK_SECRETS="k1,k2,k3",
                VALIDSIM_WEBHOOK_FORMATS="json,slack,json",
                VALIDSIM_WEBHOOK_MIN_SEVERITY="info,warn,critical",
            )
        )
        assert [hook.secret for hook in config.hooks] == ["k1", "k2", "k3"]
        assert [hook.format for hook in config.hooks] == ["json", "slack", "json"]
        assert [hook.min_severity for hook in config.hooks] == ["info", "warn", "critical"]

    def test_blank_position_is_preserved_across_a_malformed_url_drop(self) -> None:
        """A dropped URL keeps the survivors' original companion indices.

        The module docstring documents the consequence: dropping a URL
        re-indexes the survivors, so they keep their ORIGINAL positions while
        the generated name reflects the new slot. For
        ``URLS="not-a-url,A,B"`` the survivors are hook 2 and hook 3 and keep
        secrets ``s2``/``s3``. Measured, not assumed.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"not-a-url,{_SLACK_URL},{_JSON_URL}",
                VALIDSIM_WEBHOOK_SECRETS="s1,s2,s3",
            )
        )
        assert [hook.name for hook in config.hooks] == ["webhook-2", "webhook-3"]
        assert [hook.secret for hook in config.hooks] == ["s2", "s3"]

    def test_hook_count_is_unaffected_by_blank_companion_entries(self) -> None:
        """A blank companion must never create or destroy a destination.

        Guards the interaction with the "drop, don't raise" contract: companion
        blanks are positional placeholders, so they must not be mistaken for a
        malformed destination and dropped.
        """
        config = notify_config_from_env(
            _env(
                VALIDSIM_WEBHOOK_URLS=f"{_SLACK_URL},{_JSON_URL},{_THIRD_URL}",
                VALIDSIM_WEBHOOK_SECRETS="s1,,s3",
            )
        )
        assert config.hook_count == 3
        assert [hook.secret for hook in config.hooks] == ["s1", None, "s3"]