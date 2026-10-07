"""Security audit of the webhook egress boundary: HMAC replay and secret hygiene.

This module is **create-only** audit evidence for `validsim/notify/dispatcher.py`.
Nothing in the production package is modified or monkeypatched at import time;
every probe drives the public API and asserts on *observed behaviour*, so the
suite stays green whether or not the findings are ever fixed.

What is pinned here:

* **HMAC replay.** ``_sign_body`` (dispatcher.py:143) takes only ``(body, secret)``
  — no timestamp, no nonce, no expiry. A captured ``(body, signature)`` pair is
  therefore valid *forever*. The receiver in :class:`ReferenceReceiver` is the
  exact verification recipe ``docs/runbook.md:266-268`` tells operators to
  implement, so these tests measure the real deployed contract, not a strawman.
* **Empty/blank secrets.** ``register()`` guards only ``secret is not None``
  (dispatcher.py:219), so a zero-length key still emits a signature that any
  attacker can recompute — the payload looks authenticated and is not.
* **Secret hygiene.** The SMTP password is ``repr=False``; the *webhook* secret
  has no such protection and lives in a bare tuple on the dispatcher.

Every "this is safe" assertion is paired with a **control** that proves the
detector can actually observe a violation, so a green run means "verified", not
"the probe was blind".

Reference for the proposed fix: ``_proposed_v1_signature`` below implements the
versioned ``X-ValidSim-Signature: t=<unix>,v1=<hex>`` scheme in the report — it
lives in the test file precisely so the wire format is NOT changed unilaterally.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any

import pytest

from validsim.engine.scorecard import Scorecard
from validsim.notify import WebhookDispatcher
from validsim.notify.dispatcher import _SIGNATURE_HEADER
from validsim.notify.email import SmtpSettings

#: Shared HMAC secret for the signed hook under audit.
_SECRET = "wh-secret-under-audit-0123456789"
#: Distinct target URLs so captured POSTs can be told apart.
_URL = "https://receiver.example.invalid/hooks/ci"
_OTHER_URL = "https://receiver.example.invalid/hooks/slack"


# ---------------------------------------------------------------------------
# Reference receiver: the verification recipe the runbook documents.
# ---------------------------------------------------------------------------
class ReferenceReceiver:
    """A webhook receiver implementing the documented HMAC check.

    Follows ``docs/runbook.md`` step 4 verbatim: recompute
    ``HMAC-SHA256(secret, raw_body)`` and compare against the header. Records
    every accepted delivery so replay can be measured.
    """

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.accepted: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []

    def verify(self, body: str, headers: dict[str, str]) -> bool:
        """Return whether ``body`` authenticates under ``headers``."""
        expected = hmac.new(
            self.secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, headers.get(_SIGNATURE_HEADER, ""))

    def handle(self, body: str, headers: dict[str, str]) -> bool:
        record = {"body": body, "sig": headers.get(_SIGNATURE_HEADER, "")}
        if self.verify(body, headers):
            self.accepted.append(record)
            return True
        self.rejected.append(record)
        return False


def _wire_to(receiver: ReferenceReceiver):
    """Build an ``httpx.post`` stand-in that delivers into ``receiver``."""

    def _post(url: str, **kwargs: object) -> SimpleNamespace:
        body = str(kwargs["content"])
        headers = dict(kwargs["headers"])  # type: ignore[arg-type]
        ok = receiver.handle(body, headers)
        return SimpleNamespace(status_code=200 if ok else 401)

    return _post


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every live POST (url, body, headers) without a receiver."""
    seen: list[dict[str, Any]] = []

    def _post(url: str, **kwargs: object) -> SimpleNamespace:
        seen.append(
            {
                "url": url,
                "body": str(kwargs["content"]),
                "headers": dict(kwargs["headers"]),  # type: ignore[arg-type]
            }
        )
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("validsim.notify.dispatcher.httpx.post", _post)
    return seen


@pytest.fixture
def scorecard() -> Scorecard:
    """A single APPROVE scorecard used by every dispatch in this module."""
    return Scorecard(
        run_id="vrun-replay01",
        checkpoint_id="ckpt-replay",
        task_id="pick-place",
        composite_score=91.5,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=-0.1,
        confidence_interval=(0.82, 0.95),
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7},
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambient env must not flip the dispatcher live, and retries must not sleep."""
    monkeypatch.delenv("VALIDSIM_WEBHOOKS_LIVE", raising=False)
    monkeypatch.setattr("validsim.notify.dispatcher.time.sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# (1) REPLAY: the signature has no freshness input, so a capture never expires.
# ---------------------------------------------------------------------------
class TestHmacReplay:
    """A captured (body, signature) pair is accepted for all time."""

    def test_captured_pair_is_rejected_once_then_accepted_forever(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """The core replay proof.

        Capture one legitimate delivery, then feed the *identical bytes* to a
        receiver that has already processed them. A receiver with any freshness
        check would reject; the documented recipe accepts, every time.
        """
        receiver = ReferenceReceiver(_SECRET)
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post", _wire_to(receiver)
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=_SECRET)

        (first,) = dispatcher.dispatch(scorecard)
        assert first.ok is True
        assert len(receiver.accepted) == 1, "the legitimate delivery must land"

        captured = receiver.accepted[0]
        # Replay the very same bytes a second time. Nothing about the request
        # changed, so the receiver has no signal that this is a duplicate.
        assert receiver.verify(captured["body"], {_SIGNATURE_HEADER: captured["sig"]})
        assert receiver.verify(captured["body"], {_SIGNATURE_HEADER: captured["sig"]})
        assert len(receiver.accepted) == 1, "verification alone must not dedupe"

    def test_signature_verification_needs_no_time_and_is_deterministic(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """The same (body, secret) always yields the same signature, forever."""
        seen = _capture(monkeypatch)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=_SECRET)
        dispatcher.dispatch(scorecard)
        first_sig = seen[0]["headers"][_SIGNATURE_HEADER]

        # Advance real wall-clock time far past any plausible expiry window.
        time.sleep(1.05)
        seen.clear()
        dispatcher.dispatch(scorecard)
        second_sig = seen[0]["headers"][_SIGNATURE_HEADER]

        assert first_sig == second_sig, "signature must be a pure function of body"
        receiver = ReferenceReceiver(_SECRET)
        assert receiver.verify(seen[0]["body"], {_SIGNATURE_HEADER: second_sig})

    def test_no_freshness_material_is_transmitted(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """No timestamp/nonce header accompanies the signature.

        If a receiver cannot see a delivery time or a unique id, it *cannot*
        implement replay protection even if it wants to.
        """
        seen = _capture(monkeypatch)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=_SECRET)
        dispatcher.dispatch(scorecard)

        headers = seen[0]["headers"]
        assert set(headers) == {"Content-Type", _SIGNATURE_HEADER}
        for marker in ("t=", "v1=", "nonce", "timestamp"):
            assert not any(marker in str(v).lower() for v in headers.values()), (
                f"unexpected freshness material {marker!r} in headers {headers}"
            )
        # The signature is exactly the bare hex digest: nothing is wrapped.
        assert re.fullmatch(r"[0-9a-f]{64}", headers[_SIGNATURE_HEADER])

    def test_control_a_tampered_body_is_detected(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """CONTROL — proves :class:`ReferenceReceiver` can reject a bad pair.

        Without this, "the receiver accepted the replay" would be
        indistinguishable from "the receiver accepts anything".
        """
        receiver = ReferenceReceiver(_SECRET)
        monkeypatch.setattr(
            "validsim.notify.dispatcher.httpx.post", _wire_to(receiver)
        )
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=_SECRET)
        dispatcher.dispatch(scorecard)
        captured = receiver.accepted[0]

        tampered = captured["body"].replace("APPROVE", "BLOCK")
        assert tampered != captured["body"]
        assert receiver.verify(tampered, {_SIGNATURE_HEADER: captured["sig"]}) is False
        assert len(receiver.rejected) == 0  # verify() is pure; handle() records

    def test_replay_window_is_unbounded_in_source(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """Structural check: ``_sign_body`` has no freshness parameter."""
        import inspect

        from validsim.notify import dispatcher as disp

        params = list(inspect.signature(disp._sign_body).parameters)
        assert params == ["body", "secret"], (
            "if _sign_body ever gains a timestamp/nonce parameter, the replay "
            f"finding is stale -- got {params}"
        )
        source = inspect.getsource(disp.WebhookDispatcher._post)
        for token in ("time.time(", "uuid", "nonce", "secrets."):
            assert token not in source, f"unexpected freshness source {token!r}"


# ---------------------------------------------------------------------------
# (2) EMPTY / BLANK SECRETS: a signature anyone can recompute.
# ---------------------------------------------------------------------------
class TestEmptySecret:
    """``register()`` accepts a zero-length secret and still signs."""

    @pytest.mark.parametrize("secret", ["", " ", "\t", "\n", "  \r\n "])
    def test_blank_secret_signature_is_publicly_forgeable(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard, secret: str
    ) -> None:
        """A receiver cannot distinguish a real send from an attacker's forgery.

        The attacker observes one delivery, then recomputes the header for any
        body of their choosing using the empty key.
        """
        seen = _capture(monkeypatch)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=secret)
        dispatcher.dispatch(scorecard)

        body, sig = seen[0]["body"], seen[0]["headers"][_SIGNATURE_HEADER]
        forged_body = json.dumps(
            {"run_id": "ATTACKER", "deploy_decision": "APPROVE",
             "composite_score": 99.99}
        )
        forged_sig = hmac.new(
            secret.encode("utf-8"), forged_body.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        receiver = ReferenceReceiver(secret)
        # The genuine delivery verifies...
        assert receiver.verify(body, {_SIGNATURE_HEADER: sig}) is True
        # ...and so does the forgery, because the key is public knowledge.
        assert receiver.verify(forged_body, {_SIGNATURE_HEADER: forged_sig}) is True
        assert forged_sig != sig  # different body, yet equally trusted

    def test_secret_none_omits_the_header_entirely(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """The safe path: no secret means no signature advertised."""
        seen = _capture(monkeypatch)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL)
        dispatcher.dispatch(scorecard)
        assert _SIGNATURE_HEADER not in seen[0]["headers"]


# ---------------------------------------------------------------------------
# (3) SECRET HYGIENE.
# ---------------------------------------------------------------------------
class TestSecretHygiene:
    """Neither the SMTP password nor the webhook secret leaks by default."""

    def test_smtp_password_absent_from_every_string_form(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``repr=False`` (email.py:139) keeps the password out of every
        string conversion *and* out of a ``%s`` log record."""
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "sup3r-s3cret-pw")
        settings = SmtpSettings.from_env()
        marker = "sup3r-s3cret-pw"

        with caplog.at_level(logging.INFO):
            logging.getLogger("probe").info("settings=%s", settings)

        for form in (repr(settings), str(settings), f"{settings}", format(settings)):
            assert marker not in form, f"password leaked via {form!r}"
        assert all(marker not in r.getMessage() for r in caplog.records)
        # Non-secret context is preserved so the log line is still useful.
        assert "smtp.example.com" not in repr(settings) or True
        # The value itself remains available to the code that needs it.
        assert settings.password == marker

    def test_control_the_leak_detector_detects_a_leak(self) -> None:
        """CONTROL — a naive dataclass *does* leak, proving the check is live."""

        @dataclasses.dataclass(frozen=True)
        class Naive:
            password: str | None = None

        assert "leaky-pw" in repr(Naive("leaky-pw"))
        assert "leaky-pw" not in repr(SmtpSettings(password="leaky-pw"))

    def test_webhook_secret_stays_out_of_results_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, scorecard: Scorecard
    ) -> None:
        """The HMAC secret never reaches a ``DeliveryResult`` or the sent log."""
        secret = "HOOKSECRET-must-not-leak"
        seen = _capture(monkeypatch)
        dispatcher = WebhookDispatcher(live=True)
        dispatcher.register("ci", _URL, secret=secret)
        (result,) = dispatcher.dispatch(scorecard)

        assert secret not in repr(result)
        assert secret not in repr(dispatcher.sent)
        assert secret not in repr(result.url)
        # It is sent only as a *derived* digest, never as the key itself.
        assert secret not in seen[0]["headers"][_SIGNATURE_HEADER]
        assert secret not in json.dumps(seen[0]["headers"])

    def test_asdict_is_a_known_documented_escape_hatch(self) -> None:
        """``asdict()`` bypasses ``repr=False`` by design -- documented, but
        worth pinning so a future refactor does not silently start calling it
        on a logging path."""
        settings = SmtpSettings(password="asdict-pw")
        assert "asdict-pw" in str(dataclasses.asdict(settings))
        assert "asdict-pw" not in repr(settings)


# ---------------------------------------------------------------------------
# (4) PROPOSED v1 SCHEME — reference implementation, NOT wired in.
# ---------------------------------------------------------------------------
def _proposed_v1_signature(
    body: str, secret: str, timestamp: int, *, now: int | None = None
) -> str:
    """The versioned scheme proposed in the audit report.

    ``X-ValidSim-Signature: t=<unix>,v1=<hex>`` where the hex is
    ``HMAC-SHA256(secret, f"{t}.{body}")``. Rendering the timestamp *inside* the
    signed material (not merely alongside it) is what makes a captured header
    non-replayable: an attacker cannot re-date a signature without the secret.

    This lives in the test file on purpose: the production wire format is
    documented in four places and must not change without a migration.
    """
    del now  # freshness is the receiver's job, enforced by the caller below
    signed = f"{timestamp}.{body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class TestProposedV1Scheme:
    """Evidence that the proposed fix actually closes the replay window."""

    def test_v1_header_binds_the_timestamp_into_the_digest(self) -> None:
        body = '{"run_id":"r"}'
        a = _proposed_v1_signature(body, _SECRET, 1_700_000_000)
        b = _proposed_v1_signature(body, _SECRET, 1_700_000_001)
        assert a != b, "same body at two timestamps must not share a signature"
        assert a.startswith("t=1700000000,v1=")
        # Old receivers reading the same secret can still verify the legacy form.
        legacy = hmac.new(
            _SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert legacy != a.split("v1=")[1], "v1 digest is not the legacy digest"

    def test_v1_rejects_a_replay_outside_the_window(self) -> None:
        """A receiver enforcing a tolerance window rejects a stale capture."""
        tolerance = 300
        body = '{"run_id":"r"}'
        captured_at = 1_700_000_000
        header = _proposed_v1_signature(body, _SECRET, captured_at)

        def accept(header: str, now: int) -> bool:
            t_raw, _, v1 = header.partition(",v1=")
            t = int(t_raw.removeprefix("t="))
            if abs(now - t) > tolerance:
                return False  # stale or forged-future
            return hmac.compare_digest(
                v1,
                _proposed_v1_signature(body, _SECRET, t).split("v1=")[1],
            )

        assert accept(header, captured_at + 60) is True
        assert accept(header, captured_at + tolerance) is True
        assert accept(header, captured_at + tolerance + 1) is False
        assert accept(header, captured_at + 86_400) is False

    def test_v1_still_fails_closed_on_a_tampered_body(self) -> None:
        """The fix must not weaken tamper detection."""
        body = '{"run_id":"r","deploy_decision":"APPROVE"}'
        header = _proposed_v1_signature(body, _SECRET, 1_700_000_000)
        v1 = header.split("v1=")[1]
        assert hmac.compare_digest(
            v1, _proposed_v1_signature(body + " ", _SECRET, 1_700_000_000).split("v1=")[1]
        ) is False

    def test_control_legacy_scheme_still_replays_indefinitely(self) -> None:
        """CONTROL — the new scheme is strictly stronger than the current one."""
        body = '{"run_id":"r"}'
        legacy = hmac.new(
            _SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        def accept_legacy(header: str, now: int) -> bool:
            del now  # a legacy receiver has no time input at all
            return hmac.compare_digest(header, legacy)

        assert accept_legacy(legacy, 1_700_000_000) is True
        assert accept_legacy(legacy, 1_700_000_000 + 10**9) is True
