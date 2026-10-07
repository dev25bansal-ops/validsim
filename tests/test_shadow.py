"""Tests for the shadow-run harness.

Every test drives :class:`~validsim.sim.shadow.ShadowRunner` through an
``httpx.MockTransport`` fake, so the suite proves the shadow comparison and the
contract-violation capture against the wire contract in
``docs/isaac-worker.md`` **without a GPU, a container, or a socket** — exactly
the point of a shadow run. The mock backend is the real
:class:`~validsim.sim.runner.MockIsaacBackend`, so the success-rate comparison
exercises genuine, seeded numbers.

The worker handlers read the batch shape (``episodes``, ``seed``,
``randomization_level``) straight from the request body and reply accordingly,
so each fake adapts to whatever batch the runner submits.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable

import httpx
import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import MockIsaacBackend
from validsim.sim.shadow import (
    ContractCase,
    ShadowReport,
    ShadowRunner,
    contract_violations_for,
    reference_contract_cases,
)
# Private, but it is the exact function that turns a wire request into the
# TaskConfig a backend receives -- the seam this test needs to assert on.
from validsim.sim.shadow import _task_from_request  # noqa: E402

BASE_URL = "http://worker.test:8090"
SEED = 42

Handler = Callable[[httpx.Request], httpx.Response]


# -- fixtures & task/scenario builders --------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient worker/backend configuration."""
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_URL", raising=False)
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_KEY", raising=False)
    monkeypatch.delenv("VALIDSIM_BACKEND", raising=False)


def _task(randomization: str = "none") -> TaskConfig:
    """A task for shadowing. ``episodes`` here is irrelevant: the runner uses
    its own (small) batch size, so keep the task minimal but valid (>= 1)."""
    return TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka_panda", urdf_path="robots/franka.urdf", dof=7),
        environment=EnvironmentSpec(name="kitchen", scene_usd="scenes/kitchen.usda"),
        episodes=1,
        randomization=randomization,  # type: ignore[arg-type]
        adversarial_count=0,
    )


def _scenario() -> AdversarialScenario:
    return AdversarialScenario(
        id="adv-pick-place-0000",
        category="human_proximity",
        name="Human Proximity #0000",
        params={"human_distance_m": 0.42, "human_speed_mps": 1.1, "crossing": True},
        difficulty=0.6,
    )


# -- canned worker handlers --------------------------------------------------


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
    """A handler that replies with one episode per requested slot.

    ``build(seed, index, level)`` produces the episode object; the count and
    seeds mirror the request, so a well-formed build never trips the count or
    seed-echo checks.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        count = int(body["episodes"]) + len(body["scenarios"])
        base = int(body["seed"])
        level = str(body["randomization_level"])
        episodes = [build(base + i, i, level) for i in range(count)]
        return httpx.Response(200, json={"episodes": episodes})

    return handler


def _succeeding_handler() -> Handler:
    return _reply(lambda seed, i, level: _episode_json(seed, success=True, level=level))


def _failing_handler() -> Handler:
    return _reply(lambda seed, i, level: _episode_json(seed, success=False, level=level))


def _malformed_handler() -> Handler:
    """Every episode valid except the first, which drops a mandatory field."""

    def build(seed: int, i: int, level: str) -> dict[str, Any]:
        episode = _episode_json(seed, success=True, level=level)
        if i == 0:
            del episode["max_contact_force_n"]
        return episode

    return _reply(build)


def _http500_handler() -> Handler:
    return lambda request: httpx.Response(500, json={"error": "CUDA out of memory"})


def _unreachable_handler() -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return handler


def _recording(inner: Handler, seen: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return inner(request)

    return handler


def _runner(handler: Handler, **kwargs: Any) -> ShadowRunner:
    """A ShadowRunner wired to ``handler`` via a MockTransport (no network)."""
    return ShadowRunner(BASE_URL, transport=httpx.MockTransport(handler), **kwargs)


# -- the report shape --------------------------------------------------------


class TestShadowReportShape:
    def test_is_frozen_with_documented_fields(self) -> None:
        report = ShadowReport(
            worker_ok=True,
            mock_success_rate=0.5,
            worker_success_rate=0.6,
            delta=0.1,
            contract_violations=[],
            passed=True,
        )
        assert dataclasses.is_dataclass(report)
        names = tuple(f.name for f in dataclasses.fields(report))
        assert names == (
            "worker_ok",
            "mock_success_rate",
            "worker_success_rate",
            "delta",
            "contract_violations",
            "passed",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.passed = False  # type: ignore[misc]

    def test_defaults_for_optional_fields(self) -> None:
        report = ShadowReport(
            worker_ok=False, mock_success_rate=0.0, worker_success_rate=0.0, delta=0.0
        )
        assert report.contract_violations == []
        assert report.passed is False


# -- the tolerance comparison ------------------------------------------------


class TestWithinTolerance:
    def test_worker_matching_mock_band_passes(self) -> None:
        # Worker reports all-success; the mock (base 0.9, "none") sits well
        # inside a 0.5 band, so the shadow run passes.
        runner = _runner(_succeeding_handler(), episodes=12, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        assert report.worker_ok is True
        assert report.contract_violations == []
        assert report.worker_success_rate == pytest.approx(1.0)
        assert 0.5 <= report.mock_success_rate <= 1.0
        assert report.delta == pytest.approx(
            report.worker_success_rate - report.mock_success_rate
        )
        assert abs(report.delta) <= 0.5
        assert report.passed is True


class TestOutOfTolerance:
    def test_worker_far_from_mock_fails(self) -> None:
        # Worker reports all-failure (rate 0.0); the deterministic mock is far
        # from 0.0, so with a zero-width band the run does not pass — yet the
        # worker itself answered cleanly, so it is not a contract violation.
        runner = _runner(_failing_handler(), episodes=12, tolerance=0.0)
        report = runner.run(_task(), base_seed=SEED)
        assert report.worker_ok is True
        assert report.worker_success_rate == pytest.approx(0.0)
        assert report.contract_violations == []
        assert report.mock_success_rate > 0.0
        assert report.delta == pytest.approx(0.0 - report.mock_success_rate)
        assert report.passed is False

    def test_tolerance_boundary_is_inclusive(self) -> None:
        # Discover the exact drift, then straddle it: just below fails, at/above
        # passes (the comparison is ``abs(delta) <= tolerance``).
        probe = _runner(_failing_handler(), episodes=12, tolerance=1.0).run(
            _task(), base_seed=SEED
        )
        drift = abs(probe.delta)
        assert drift > 0.0
        below = _runner(_failing_handler(), episodes=12, tolerance=drift - 0.01)
        assert below.run(_task(), base_seed=SEED).passed is False
        above = _runner(_failing_handler(), episodes=12, tolerance=drift + 0.01)
        assert above.run(_task(), base_seed=SEED).passed is True


# -- contract violations are captured, never raised --------------------------


class TestContractViolations:
    def test_malformed_response_is_recorded(self) -> None:
        runner = _runner(_malformed_handler(), episodes=12, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        assert report.worker_ok is False
        assert report.passed is False
        assert report.worker_success_rate == 0.0
        assert report.contract_violations
        assert "missing required field" in report.contract_violations[0]
        assert "max_contact_force_n" in report.contract_violations[0]

    def test_http_error_status_is_recorded(self) -> None:
        runner = _runner(_http500_handler(), episodes=5, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        assert report.worker_ok is False
        assert report.passed is False
        assert any("HTTP 500" in v for v in report.contract_violations)

    def test_unreachable_worker_is_recorded(self) -> None:
        runner = _runner(_unreachable_handler(), episodes=5, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        assert report.worker_ok is False
        assert report.passed is False
        assert any("unreachable" in v for v in report.contract_violations)

    def test_clean_run_has_no_violations(self) -> None:
        runner = _runner(_succeeding_handler(), episodes=6, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        assert report.contract_violations == []
        assert report.worker_ok is True


# -- batch plumbing ----------------------------------------------------------


class TestBatchPlumbing:
    def test_runs_batch_in_a_single_round_trip(self) -> None:
        seen: list[httpx.Request] = []
        runner = _runner(
            _recording(_succeeding_handler(), seen), episodes=7, tolerance=0.5
        )
        runner.run(_task(), base_seed=100)
        assert len(seen) == 1  # the whole batch travels in one POST
        request = seen[0]
        assert request.method == "POST"
        assert str(request.url) == f"{BASE_URL}/episodes/run"
        body = json.loads(request.content)
        assert body["episodes"] == 7
        assert body["seed"] == 100
        assert body["randomization_level"] == "none"
        assert body["scenarios"] == []

    def test_auth_header_is_forwarded(self) -> None:
        seen: list[httpx.Request] = []
        runner = ShadowRunner(
            BASE_URL,
            api_key="sekret",
            transport=httpx.MockTransport(_recording(_succeeding_handler(), seen)),
            episodes=3,
            tolerance=0.5,
        )
        runner.run(_task(), base_seed=SEED)
        assert seen[0].headers["authorization"] == "Bearer sekret"

    def test_scenarios_are_sent_and_compared(self) -> None:
        seen: list[httpx.Request] = []
        runner = _runner(
            _recording(_succeeding_handler(), seen), episodes=4, tolerance=0.5
        )
        report = runner.run(_task(), base_seed=SEED, scenarios=[_scenario()])
        body = json.loads(seen[0].content)
        assert body["episodes"] == 4
        assert len(body["scenarios"]) == 1
        assert body["scenarios"][0]["category"] == "human_proximity"
        # 4 nominal + 1 adversarial all succeeded on the worker.
        assert report.worker_ok is True
        assert report.worker_success_rate == pytest.approx(1.0)

    def test_injected_mock_backend_is_used(self) -> None:
        injected = MockIsaacBackend(base_success_rate=0.5)
        runner = ShadowRunner(
            BASE_URL,
            transport=httpx.MockTransport(_succeeding_handler()),
            mock=injected,
            episodes=6,
            tolerance=0.5,
        )
        assert runner._mock is injected


# -- reference contract fixtures --------------------------------------------


class TestReferenceContractCases:
    def test_cases_ship_and_are_contract_valid(self) -> None:
        cases = reference_contract_cases()
        assert len(cases) >= 2
        for case in cases:
            assert isinstance(case, ContractCase)
            assert case.name
            assert set(case.request) == {
                "task_id", "robot", "environment", "checkpoint_id", "seed",
                "episodes", "randomization_level", "scenarios",
            }
            # Replaying a canned pair through the real parser yields no violations.
            assert contract_violations_for(case) == []

    def test_fixtures_carry_a_checkpoint(self) -> None:
        """Every fixture must identify the policy under test.

        A fixture set with no ``checkpoint_id`` would pass against a contract
        incapable of expressing the product's core input: a worker could be
        fully conformant, clear the promotion gate, and still be structurally
        unable to validate a checkpoint. Conformance would certify the defect.
        """
        for case in reference_contract_cases():
            assert case.request.get("checkpoint_id"), (
                f"fixture {case.name!r} carries no checkpoint_id"
            )

    def test_checkpoint_survives_into_the_reconstructed_task(self) -> None:
        """The request field must reach the TaskConfig a backend receives."""
        for case in reference_contract_cases():
            task = _task_from_request(case.request)
            assert task.checkpoint_id == case.request["checkpoint_id"]

    def test_null_checkpoint_is_accepted(self) -> None:
        """``null`` means "no checkpoint supplied" and must stay valid.

        The client sends ``None`` when the caller did not identify a checkpoint,
        so rejecting it would break the CLI's default path.
        """
        case = reference_contract_cases()[0]
        nulled = ContractCase(
            name=case.name,
            request={**case.request, "checkpoint_id": None},
            response=case.response,
        )
        assert contract_violations_for(nulled) == []
        assert _task_from_request(nulled.request).checkpoint_id is None

    def test_detects_unknown_field(self) -> None:
        case = reference_contract_cases()[0]
        broken = ContractCase(
            name=case.name,
            request=case.request,
            response={
                "episodes": [dict(ep, teleop_score=0.5) for ep in case.response["episodes"]]
            },
        )
        violations = contract_violations_for(broken)
        assert violations
        assert "unknown field" in violations[0]

    def test_detects_misaligned_seed_echo(self) -> None:
        case = reference_contract_cases()[0]
        broken = ContractCase(
            name=case.name,
            request=case.request,
            response={
                "episodes": [dict(ep, seed=ep["seed"] + 999) for ep in case.response["episodes"]]
            },
        )
        violations = contract_violations_for(broken)
        assert any("echoed seed" in v for v in violations)

    def test_detects_wrong_episode_count(self) -> None:
        case = reference_contract_cases()[2]  # the 3-episode batch case
        broken = ContractCase(
            name=case.name,
            request=case.request,
            response={"episodes": case.response["episodes"][:2]},
        )
        violations = contract_violations_for(broken)
        assert any("returned 2 episode" in v for v in violations)


# -- configuration & lifecycle ----------------------------------------------


class TestConfiguration:
    def test_empty_worker_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="worker_url"):
            ShadowRunner("   ")

    def test_trailing_slash_normalized(self) -> None:
        assert ShadowRunner(f"{BASE_URL}/").worker_url == BASE_URL

    def test_zero_batch_rejected(self) -> None:
        with pytest.raises(ValueError, match="episodes"):
            ShadowRunner(BASE_URL, episodes=0)

    def test_negative_tolerance_rejected(self) -> None:
        with pytest.raises(ValueError, match="tolerance"):
            ShadowRunner(BASE_URL, tolerance=-0.1)

    def test_per_run_episodes_override_rejects_zero(self) -> None:
        runner = _runner(_succeeding_handler(), episodes=5)
        with pytest.raises(ValueError, match="episodes"):
            runner.run(_task(), episodes=0)

    def test_worker_url_property(self) -> None:
        assert _runner(_succeeding_handler()).worker_url == BASE_URL
        assert _runner(_succeeding_handler(), tolerance=0.3).tolerance == pytest.approx(0.3)


# -- serialization -----------------------------------------------------------


class TestSerialization:
    def test_to_dict_and_to_json(self) -> None:
        runner = _runner(_malformed_handler(), episodes=6, tolerance=0.5)
        report = runner.run(_task(), base_seed=SEED)
        data = report.to_dict()
        assert set(data) == {
            "worker_ok", "mock_success_rate", "worker_success_rate",
            "delta", "contract_violations", "passed",
        }
        assert data["worker_ok"] is False
        assert len(data["contract_violations"]) == 1
        restored = json.loads(report.to_json())
        assert restored["passed"] is False
        assert restored["worker_success_rate"] == pytest.approx(report.worker_success_rate)
        assert restored["contract_violations"] == report.contract_violations
