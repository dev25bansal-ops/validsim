"""Tests for currently-thin shadow-run and live-email paths.

This file deliberately targets edges the existing suites under-cover, without
touching the production code:

* :class:`~validsim.sim.shadow.ShadowRunner` when a worker's reply is missing a
  mandatory episode field — the violation must be *recorded*, never raised, and
  the run must not pass.
* The tolerance comparison at its exact boundary: ``abs(delta) == tolerance`` is
  inclusive (``<=``), so a drift sitting precisely on the band still passes.
* :func:`~validsim.sim.shadow.reference_contract_cases` shipping at least two
  canned pairs that each replay cleanly through the real client parser.
* :class:`~validsim.notify.email.EmailNotifier`'s **live** SMTP branch, driven by
  a monkeypatched ``smtplib.SMTP`` so no socket is ever opened: the built MIME
  message reaches the send call, STARTTLS fires when ``VALIDSIM_SMTP_TLS=1``,
  ``login`` fires when credentials are set, and the recipients/subject flow into
  the serialized message.
"""

from __future__ import annotations

import email as email_lib
import json
from typing import Any, Callable

import httpx
import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.notify import EmailNotifier
from validsim.notify import email as email_mod
from validsim.sim.shadow import (
    ContractCase,
    ShadowRunner,
    contract_violations_for,
    reference_contract_cases,
)

BASE_URL = "http://worker.test:8090"
SEED = 42

Handler = Callable[[httpx.Request], httpx.Response]

_SMTP_ENV = (
    "VALIDSIM_SMTP_HOST",
    "VALIDSIM_SMTP_PORT",
    "VALIDSIM_SMTP_USER",
    "VALIDSIM_SMTP_PASSWORD",
    "VALIDSIM_SMTP_FROM",
    "VALIDSIM_SMTP_TLS",
)
_WORKER_ENV = (
    "VALIDSIM_ISAAC_WORKER_URL",
    "VALIDSIM_ISAAC_WORKER_KEY",
    "VALIDSIM_BACKEND",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient SMTP and worker/backend configuration."""
    for var in _SMTP_ENV + _WORKER_ENV:
        monkeypatch.delenv(var, raising=False)


# ===========================================================================
# ShadowRunner — missing required field
# ===========================================================================


def _task(randomization: str = "none") -> TaskConfig:
    """A minimal but valid task; the runner supplies its own batch size."""
    return TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka_panda", urdf_path="robots/franka.urdf", dof=7),
        environment=EnvironmentSpec(name="kitchen", scene_usd="scenes/kitchen.usda"),
        episodes=1,
        randomization=randomization,  # type: ignore[arg-type]
        adversarial_count=0,
    )


def _episode_json(seed: int, *, success: bool, level: str, **overrides: Any) -> dict[str, Any]:
    """A complete, contract-valid episode object echoing ``seed`` and ``level``."""
    episode: dict[str, Any] = {
        "episode_id": f"pick-place-seed{seed:010d}",
        "task_id": "pick-place",
        "seed": seed,
        "success": success,
        "collision_count": 0 if success else 2,
        "max_contact_force_n": 12.5 if success else 140.0,
        "min_human_distance_m": None,
        "failure_mode": None if success else "collision",
        "duration_s": 7.25 if success else 9.0,
        "joint_states_summary": {
            "position_rms": 0.4,
            "velocity_rms": 0.1,
            "effort_max": 33.0,
            "dof": 7,
        },
        "randomization_level": level,
    }
    episode.update(overrides)
    return episode


def _reply(build: Callable[[int, int, str], dict[str, Any]]) -> Handler:
    """Reply with one episode per requested slot, seeds/count aligned to request."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        count = int(body["episodes"]) + len(body["scenarios"])
        base = int(body["seed"])
        level = str(body["randomization_level"])
        episodes = [build(base + i, i, level) for i in range(count)]
        return httpx.Response(200, json={"episodes": episodes})

    return handler


def _failing_handler() -> Handler:
    return _reply(lambda seed, i, level: _episode_json(seed, success=False, level=level))


def _missing_field_handler(field_name: str) -> Handler:
    """Every episode valid except the first, which drops a mandatory field."""

    def build(seed: int, i: int, level: str) -> dict[str, Any]:
        episode = _episode_json(seed, success=True, level=level)
        if i == 0:
            del episode[field_name]
        return episode

    return _reply(build)


def _runner(handler: Handler, **kwargs: Any) -> ShadowRunner:
    """A ShadowRunner wired to ``handler`` via a MockTransport (no network)."""
    return ShadowRunner(BASE_URL, transport=httpx.MockTransport(handler), **kwargs)


class TestShadowMissingRequiredField:
    def test_missing_field_is_recorded_and_blocks_pass(self) -> None:
        runner = _runner(_missing_field_handler("collision_count"), episodes=8, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)

        # A contract break is loud but never fatal: the worker is marked unhealthy,
        # the whole batch is discarded (rate 0.0), and the run cannot pass.
        assert report.worker_ok is False
        assert report.passed is False
        assert report.worker_success_rate == 0.0
        assert report.contract_violations, "expected at least one recorded violation"

        violation = report.contract_violations[0]
        assert "missing required field" in violation
        assert "collision_count" in violation

    def test_missing_field_violation_survives_serialization(self) -> None:
        runner = _runner(_missing_field_handler("duration_s"), episodes=6, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)

        data = report.to_dict()
        assert data["worker_ok"] is False
        assert data["passed"] is False
        assert len(data["contract_violations"]) == 1
        assert "duration_s" in data["contract_violations"][0]


# ===========================================================================
# ShadowRunner — tolerance boundary (delta exactly at tolerance)
# ===========================================================================


class TestShadowToleranceBoundary:
    def test_delta_exactly_at_tolerance_passes(self) -> None:
        # Discover the exact drift the failing worker produces against the mock.
        probe = _runner(_failing_handler(), episodes=12, tolerance=1.0).run(
            _task(), base_seed=SEED
        )
        drift = abs(probe.delta)
        assert drift > 0.0  # the worker genuinely diverges from the mock

        # Re-run with the band set *precisely* on the drift: the comparison is
        # ``abs(delta) <= tolerance``, so sitting exactly on the boundary passes.
        runner = _runner(_failing_handler(), episodes=12, tolerance=drift)
        report = runner.run(_task(), base_seed=SEED)

        assert report.worker_ok is True
        assert report.contract_violations == []
        assert abs(report.delta) == pytest.approx(drift)
        assert abs(report.delta) <= runner.tolerance  # tolerance == drift, inclusive
        assert report.passed is True

    def test_delta_just_under_tolerance_still_fails(self) -> None:
        # Complement of the boundary test: a hair below the drift must not pass,
        # proving the inclusive boundary is the tolerance value itself.
        probe = _runner(_failing_handler(), episodes=12, tolerance=1.0).run(
            _task(), base_seed=SEED
        )
        drift = abs(probe.delta)
        assert drift > 0.0

        below = _runner(_failing_handler(), episodes=12, tolerance=drift - 0.01)
        assert below.run(_task(), base_seed=SEED).passed is False


# ===========================================================================
# reference_contract_cases — self-validating fixtures
# ===========================================================================


class TestReferenceContractCasesSelfValid:
    def test_at_least_two_cases_pass_their_own_contract_check(self) -> None:
        cases = reference_contract_cases()
        assert len(cases) >= 2

        for case in cases:
            assert isinstance(case, ContractCase)
            assert case.name
            assert case.response.get("episodes"), "each canned response must carry episodes"
            # Replaying a shipped pair through the real client parser is clean.
            assert contract_violations_for(case) == []

    def test_case_names_are_unique(self) -> None:
        names = [case.name for case in reference_contract_cases()]
        assert len(names) == len(set(names))


# ===========================================================================
# EmailNotifier — LIVE send path (monkeypatched smtplib.SMTP, no socket)
# ===========================================================================


class _FakeSMTP:
    """Stand-in for :class:`smtplib.SMTP` that records calls and opens no socket."""

    def __init__(self, host: str, port: int, timeout: object = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_calls = 0
        self.login_calls: list[tuple[str, str]] = []
        self.sendmail_calls: list[tuple[str, list[str], str]] = []

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self, context: object = None) -> None:
        self.starttls_calls += 1
        self.tls_context = context

    def login(self, user: str, password: str) -> None:
        self.login_calls.append((user, password))

    def sendmail(self, from_addr: str, to_addrs: list, msg: str) -> None:
        self.sendmail_calls.append((from_addr, list(to_addrs), msg))


def _patch_smtp(monkeypatch: pytest.MonkeyPatch) -> list[_FakeSMTP]:
    """Replace ``smtplib.SMTP`` with a recording fake; return the created list."""
    created: list[_FakeSMTP] = []

    def _factory(*args: Any, **kwargs: Any) -> _FakeSMTP:
        smtp = _FakeSMTP(*args, **kwargs)
        created.append(smtp)
        return smtp

    monkeypatch.setattr("validsim.notify.email.smtplib.SMTP", _factory)
    return created


def _spy_built_messages(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Wrap ``_build_message`` to capture every MIME message it produces."""
    original = email_mod._build_message
    built: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        message = original(*args, **kwargs)
        built.append(message)
        return message

    monkeypatch.setattr("validsim.notify.email._build_message", _spy)
    return built


def _configure_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the minimum env a live send needs (host + sender)."""
    monkeypatch.setenv("VALIDSIM_SMTP_HOST", "mail.test")
    monkeypatch.setenv("VALIDSIM_SMTP_FROM", "sender@example.com")


class TestEmailLiveSendPath:
    def test_sendmail_receives_the_built_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = _patch_smtp(monkeypatch)
        built = _spy_built_messages(monkeypatch)
        _configure_live_env(monkeypatch)

        result = EmailNotifier(dry_run=False).send(
            to=["a@example.com", "b@example.com"],
            subject="Report",
            body_html="<h1>Hi</h1>",
            body_text="Hi",
        )

        assert result.ok is True and result.dry_run is False and result.error is None
        (smtp,) = created
        # The live path's SMTP send call (``sendmail``) fires exactly once with
        # the envelope sender, the vetted recipients, and the built MIME message.
        assert len(smtp.sendmail_calls) == 1
        from_addr, to_addrs, raw_msg = smtp.sendmail_calls[0]
        assert from_addr == "sender@example.com"
        assert to_addrs == ["a@example.com", "b@example.com"]

        assert len(built) == 1
        assert raw_msg == built[0].as_string()

    def test_starttls_invoked_when_tls_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = _patch_smtp(monkeypatch)
        _configure_live_env(monkeypatch)
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "1")

        EmailNotifier(dry_run=False).send(["a@example.com"], "Hi", body_text="body")

        (smtp,) = created
        assert smtp.starttls_calls == 1

    def test_login_invoked_when_credentials_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = _patch_smtp(monkeypatch)
        _configure_live_env(monkeypatch)
        monkeypatch.setenv("VALIDSIM_SMTP_USER", "alice")
        monkeypatch.setenv("VALIDSIM_SMTP_PASSWORD", "s3cr3t")
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "1")

        EmailNotifier(dry_run=False).send(["a@example.com"], "Hi", body_text="body")

        (smtp,) = created
        assert smtp.login_calls == [("alice", "s3cr3t")]

    def test_tls_and_login_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created = _patch_smtp(monkeypatch)
        _configure_live_env(monkeypatch)
        monkeypatch.setenv("VALIDSIM_SMTP_TLS", "0")  # no STARTTLS
        # user/password intentionally unset -> no login

        result = EmailNotifier(dry_run=False).send(["a@example.com"], "Hi", body_text="body")

        assert result.ok is True
        (smtp,) = created
        assert smtp.starttls_calls == 0
        assert smtp.login_calls == []
        assert len(smtp.sendmail_calls) == 1  # the send itself still happens

    def test_recipients_and_subject_flow_into_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = _patch_smtp(monkeypatch)
        _configure_live_env(monkeypatch)

        EmailNotifier(dry_run=False).send(
            to=["a@example.com", "b@example.com"],
            subject="Weekly report",
            body_html="<p>hello</p>",
            body_text="hello",
        )

        (smtp,) = created
        _from_addr, _to_addrs, raw_msg = smtp.sendmail_calls[0]
        parsed = email_lib.message_from_string(raw_msg)
        assert parsed["Subject"] == "Weekly report"
        assert parsed["From"] == "sender@example.com"
        assert parsed["To"] == "a@example.com, b@example.com"

        bodies = [
            part.get_payload(decode=True).decode("utf-8")
            for part in parsed.walk()
            if not part.is_multipart()
        ]
        assert any("<p>hello</p>" in body for body in bodies)
        assert any("hello" in body for body in bodies)
