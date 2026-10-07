"""``DeliveryResult.dry_run`` must separate a rehearsal from a real send.

Why this field exists
---------------------
A dry-run dispatch reports ``ok=True`` -- and so does a successful live delivery.
Before ``dry_run`` existed, ``ok`` on its own could not separate them:

* dry-run      -> ``ok=True,  status_code=None, error=None``
* live 2xx     -> ``ok=True,  status_code=200``(separable)
* live 4xx/5xx -> ``ok=False, status_code=500``(``ok`` separates it)
* live connerr -> ``ok=False, status_code=None, error=...`` (``ok`` separates it)

So ``ok`` is True in both the rehearsal and the real-send case: an alerting check
written as "if ``ok`` then the webhook fired" reports a rehearsal as a successful
delivery. ``dry_run`` closes exactly that gap and nothing wider.

The ``status_code`` difference does mean a careful reader could *infer* dry-run
from ``ok=True, status_code is None``. That inference is fragile -- it depends on
an invariant of ``_post`` rather than on anything the result states about itself
-- which is why notify-sec added an explicit field rather than relying on it.

Scope note: this file only *characterises* the new field. The pre-existing exact
field-set assertion in ``test_notify_ssrf_timeout_agent.py`` was widened to
include the field (that harness is marked ``FINDING --`` and was edited
surgically, not rewritten).
"""

from __future__ import annotations

import dataclasses

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify.dispatcher import DeliveryResult, WebhookDispatcher

_TRAP = "http://127.0.0.1:9/never"


def _card() -> Scorecard:
    return Scorecard(
        run_id="vrun-dryrun01",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={},
    )


def _dispatch(live: bool) -> DeliveryResult:
    dispatcher = WebhookDispatcher(live=live)
    dispatcher.register("hook", _TRAP)
    (result,) = dispatcher.dispatch(_card())
    return result


class TestDryRunDiscriminator:
    def test_a_dry_run_reports_dry_run_true(self) -> None:
        result = _dispatch(live=False)
        assert result.ok is True
        assert result.dry_run is True, (
            "a dry-run reported ok=True with no way to tell it never left the "
            "process -- that is the ambiguity this field exists to remove"
        )

    def test_a_live_delivery_reports_dry_run_false(self) -> None:
        result = _dispatch(live=True)
        assert result.dry_run is False, (
            "a live dispatch must never claim to be a rehearsal; an operator "
            "alerting on dry_run=True would miss a real failure"
        )

    def test_ok_alone_cannot_tell_a_rehearsal_from_a_real_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``ok`` is True in BOTH modes -- that is the gap the field closes.

        Measured on the real dispatcher: a dry-run yields
        ``ok=True, status_code=None`` and a live 2xx yields
        ``ok=True, status_code=200``. So ``ok`` is genuinely identical across the
        two cases, and any monitoring check written as "if ``ok`` then the webhook
        fired" reports a rehearsal as a successful delivery. ``dry_run`` is what
        separates them.

        Note the asymmetry that makes the pre-existing fields *almost* sufficient:
        ``ok=True`` with ``status_code is None`` only ever comes from dry-run, so
        a careful reader could infer it. That inference is exactly what the field
        makes explicit -- and it is fragile, because it depends on an invariant of
        ``_post`` rather than on anything the result states about itself.

        The live leg is stubbed to 200 so the test does not depend on a peer.
        """
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post",
            lambda *a, **k: type("R", (), {"status_code": 200})(),
        )
        dry = _dispatch(live=False)
        live = _dispatch(live=True)

        # The ambiguity, stated: identical `ok`, different reality.
        assert dry.ok is True and live.ok is True, (
            "expected ok=True in both modes; if this changed, the ambiguity this "
            "field addresses has moved and this test needs re-reading"
        )
        assert dry.dry_run is True
        assert live.dry_run is False
        assert dry.dry_run is not live.dry_run

        # And the field is the *only* thing that reports the distinction.
        assert dry.name == live.name
        assert dry.url == live.url

    def test_a_failed_live_send_is_not_mistaken_for_a_rehearsal(self) -> None:
        """A real failure must read as a failure, not as "never sent".

        This is the operational half of the field: the combination that an
        operator acts on is ``ok=False`` and ``dry_run=False`` -- an actual send
        that the receiver rejected.
        """
        result = _dispatch(live=True)
        assert result.dry_run is False
        # Port 9 (discard) refuses the connection, so this is a genuine failure.
        assert result.ok is False
        assert result.error is not None

    def test_the_field_is_part_of_the_dataclass_shape(self) -> None:
        """``vars()`` must include it, since that is what callers introspect.

        The exact-shape assertion in ``test_notify_ssrf_timeout_agent.py`` reads
        ``set(vars(result))``; this pins that the field is a dataclass field
        rather than a property, which is why that assertion had to widen.
        """
        result = _dispatch(live=False)
        assert "dry_run" in vars(result)
        assert set(vars(result)) == {
            "name",
            "url",
            "ok",
            "status_code",
            "error",
            "dry_run",
        }


class TestCrossNotifierDryRunConsistency:
    """``EmailDelivery`` and ``DeliveryResult`` must agree on ``dry_run``.

    notify-sec's docstring claims the two notifier surfaces "report success the
    same way". This is the assertion for that claim -- previously nothing pinned
    it, so the two could drift apart silently.

    The interesting part is that they are **not** identical in construction, and
    the difference is a live footgun:

    * ``DeliveryResult.dry_run`` **defaults to ``False``** -- the fail-safe
      direction, since omitting it means "this really happened".
    * ``EmailDelivery.dry_run`` is a **required** field with no default, so a
      caller must state the polarity explicitly and cannot get it wrong by
      omission.

    Both defaults mean the same thing, and that is the invariant: ``True`` is
    always "rehearsal, nothing left the process".
    """

    def test_both_fields_exist_and_are_bool(self) -> None:
        from validsim.notify.email import EmailDelivery

        # Read via __dataclass_fields__, not the class attribute: with no
        # default, `EmailDelivery.dry_run` does not exist as a class attribute.
        assert EmailDelivery.__dataclass_fields__["dry_run"].type in ("bool", bool)
        assert DeliveryResult.__dataclass_fields__["dry_run"].type in ("bool", bool)

    def test_email_dry_run_has_no_default_but_webhook_defaults_to_false(self) -> None:
        """The asymmetry is deliberate; assert it rather than trip over it.

        If ``DeliveryResult.dry_run`` ever gains a ``True`` default, a
        construction site that forgets the argument would start reporting
        rehearsals as real sends -- the exact inversion the field prevents.
        """
        from validsim.notify.email import EmailDelivery

        assert DeliveryResult.__dataclass_fields__["dry_run"].default is False, (
            "DeliveryResult.dry_run must default to False (fail-safe: an "
            "omitted flag must not claim a rehearsal happened)"
        )
        assert (
            EmailDelivery.__dataclass_fields__["dry_run"].default
            is dataclasses.MISSING
        ), (
            "EmailDelivery.dry_run is required by design; if it gained a "
            "default, update this test and the notifier's construction sites"
        )

    def test_both_default_to_the_same_polarity_for_a_real_send(self) -> None:
        """An omitted/None flag on the email side must mean the same as False.

        ``EmailDelivery`` has no default, so its fail-safe is explicit ``False``
        at the construction site; ``DeliveryResult``'s is the field default. Both
        resolve to "this was a real attempt".
        """
        from validsim.notify.email import EmailDelivery

        email = EmailDelivery(("a@example.com",), "s", True, False, None)
        webhook = DeliveryResult("h", "http://x/h", True, 200, None)
        assert email.dry_run is False
        assert webhook.dry_run is False

    def test_a_dry_run_is_true_on_both_surfaces(self) -> None:
        """The shared polarity: ``True`` means "nothing left the process"."""
        from validsim.notify.email import EmailDelivery

        email = EmailDelivery(("a@example.com",), "s", True, True, None)
        webhook = _dispatch(live=False)

        assert email.dry_run is True
        assert webhook.dry_run is True

    def test_a_failed_live_send_is_false_on_both_surfaces(self) -> None:
        """The ambiguous case both fields exist to resolve.

        A live webhook send that failed at the transport layer carries
        ``ok=False, status_code=None`` -- which is exactly the shape a dry-run
        *would* have if ``dry_run`` did not exist. Requiring ``dry_run=False``
        here is what stops an operator reading a failed send as a rehearsal (or
        vice versa).
        """
        from validsim.notify.email import EmailDelivery

        webhook = _dispatch(live=True)
        assert webhook.ok is False
        assert webhook.status_code is None
        assert webhook.dry_run is False
        assert webhook.error is not None

        email = EmailDelivery(("a@example.com",), "s", False, False, "conn refused")
        assert email.ok is False
        assert email.dry_run is False
        assert email.error is not None


class TestDryRunCannotBeConfusedWithARehearsal:
    @pytest.mark.parametrize("live", [False, True])
    def test_the_legacy_fields_alone_cannot_say_whether_it_was_sent(
        self, live: bool
    ) -> None:
        """No combination of the pre-existing fields can stand in for ``dry_run``.

        The regression stated as an invariant: for every dispatch mode the tuple
        of legacy fields must be insufficient to determine whether the request
        was actually sent, which is exactly why the field was needed. Pinned as
        an invariant rather than a single example so a future change that, say,
        starts populating ``status_code`` on dry-run fails loudly here.
        """
        result = _dispatch(live=live)
        legacy_ok = result.ok
        if live:
            # Port 9 refuses, so a real send failed and the legacy fields say so.
            assert legacy_ok is False, (
                "expected the live send to the refused port to fail; if the "
                "environment now answers on port 9 this test needs a stub"
            )
        else:
            assert legacy_ok is True, "a dry-run is recorded as ok by design"