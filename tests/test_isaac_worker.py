"""Tests for the Isaac Sim GPU-worker adapter.

Every test drives :class:`~validsim.sim.isaac_worker.IsaacWorkerBackend` through
``httpx.MockTransport``, so the suite proves the wire contract in
``docs/isaac-worker.md`` without a GPU, a container, or a socket.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim import IsaacWorkerBackend, MockIsaacBackend, create_backend
from validsim.sim.isaac_worker import SimWorkerError
from validsim.sim.runner import (
    EpisodeResult,
    SimulationBackend,
    run_validation,
)

BASE_URL = "http://worker.test:8090"
SEED = 42


def _task(episodes: int = 3, adversarial: int = 1) -> TaskConfig:
    return TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka_panda", urdf_path="robots/franka.urdf", dof=7),
        environment=EnvironmentSpec(name="kitchen", scene_usd="scenes/kitchen.usda"),
        episodes=episodes,
        randomization="full",
        adversarial_count=adversarial,
    )


def _scenario() -> AdversarialScenario:
    return AdversarialScenario(
        id="adv-pick-place-0000",
        category="human_proximity",
        name="Human Proximity #0000",
        params={"human_distance_m": 0.42, "human_speed_mps": 1.1, "crossing": True},
        difficulty=0.6,
    )


def _episode_json(**overrides: Any) -> dict[str, Any]:
    """A complete, contract-valid episode object as the worker would send it."""
    episode: dict[str, Any] = {
        "episode_id": "pick-place-seed0000000042",
        "task_id": "pick-place",
        "seed": SEED,
        "success": True,
        "collision_count": 0,
        "max_contact_force_n": 12.5,
        "min_human_distance_m": None,
        "failure_mode": None,
        "duration_s": 7.25,
        "joint_states_summary": {
            "position_rms": 0.4,
            "velocity_rms": 0.1,
            "effort_max": 33.0,
            "dof": 7,
        },
        "randomization_level": "full",
    }
    episode.update(overrides)
    return episode


Handler = Callable[[httpx.Request], httpx.Response]


def _backend(
    handler: Handler,
    *,
    base_url: str | None = BASE_URL,
    **kwargs: Any,
) -> tuple[IsaacWorkerBackend, list[httpx.Request]]:
    """Build a backend wired to ``handler``, plus the list recording its calls."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    backend = IsaacWorkerBackend(
        base_url=base_url, transport=httpx.MockTransport(recording), **kwargs
    )
    return backend, seen


def _ok(*episodes: dict[str, Any]) -> Handler:
    """Handler replying with a well-formed ``{"episodes": [...]}`` envelope."""
    return lambda request: httpx.Response(200, json={"episodes": list(episodes)})


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ambient worker/backend configuration."""
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_URL", raising=False)
    monkeypatch.delenv("VALIDSIM_ISAAC_WORKER_KEY", raising=False)
    monkeypatch.delenv("VALIDSIM_BACKEND", raising=False)


class TestHappyPath:
    def test_maps_every_episode_result_field(self) -> None:
        backend, _ = _backend(_ok(_episode_json()))
        result = backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert isinstance(result, EpisodeResult)
        assert result.episode_id == "pick-place-seed0000000042"
        assert result.task_id == "pick-place"
        assert result.seed == SEED
        assert result.success is True
        assert result.collision_count == 0
        assert result.max_contact_force_n == pytest.approx(12.5)
        assert result.min_human_distance_m is None
        assert result.failure_mode is None
        assert result.duration_s == pytest.approx(7.25)
        assert result.joint_states_summary == {
            "position_rms": 0.4,
            "velocity_rms": 0.1,
            "effort_max": 33.0,
            "dof": 7.0,
        }
        assert result.randomization_level == "full"

    def test_maps_adversarial_fields(self) -> None:
        episode = _episode_json(
            success=False, failure_mode="collision", collision_count=2,
            max_contact_force_n=140.0, min_human_distance_m=0.31, duration_s=9.0,
            randomization_level="partial",
        )
        backend, _ = _backend(_ok(episode))
        result = backend.run_episode(
            _task(), seed=SEED, randomization_level="partial", scenario=_scenario()
        )
        assert (result.success, result.failure_mode, result.collision_count) == (
            False, "collision", 2,
        )
        assert result.min_human_distance_m == pytest.approx(0.31)
        assert result.randomization_level == "partial"

    def test_request_body_matches_documented_contract(self) -> None:
        backend, seen = _backend(_ok(_episode_json()))
        backend.run_episode(_task(episodes=5, adversarial=1), seed=SEED,
                            randomization_level="full", scenario=_scenario())
        assert len(seen) == 1
        request = seen[0]
        assert request.method == "POST"
        assert str(request.url) == f"{BASE_URL}/episodes/run"
        assert request.headers["content-type"] == "application/json"
        body = _body(request)
        assert set(body) == {
            "task_id", "robot", "environment", "checkpoint_id", "seed", "episodes",
            "randomization_level", "scenarios",
        }
        assert body["task_id"] == "pick-place"
        assert body["robot"] == {
            "name": "franka_panda", "urdf_path": "robots/franka.urdf", "dof": 7,
        }
        assert body["environment"] == {
            "name": "kitchen", "scene_usd": "scenes/kitchen.usda",
        }
        assert body["seed"] == SEED
        # The scenario *is* the episode, so zero nominal ones are requested:
        # the worker returns episodes + len(scenarios) == 1 result.
        assert body["episodes"] == 0
        assert body["randomization_level"] == "full"
        assert body["scenarios"] == [
            {
                "id": "adv-pick-place-0000",
                "category": "human_proximity",
                "params": {
                    "human_distance_m": 0.42, "human_speed_mps": 1.1, "crossing": True,
                },
                "difficulty": 0.6,
            }
        ]

    def test_nominal_request_sends_no_scenarios(self) -> None:
        backend, seen = _backend(_ok(_episode_json(randomization_level="none")))
        backend.run_episode(_task(), seed=SEED, randomization_level="none")
        body = _body(seen[0])
        assert body["scenarios"] == []
        assert body["episodes"] == 1

    def test_batch_reply_maps_seeded_run(self) -> None:
        """The batched form of the contract: episodes + scenarios, seeds in order."""
        episodes = [
            _episode_json(seed=seed, episode_id=f"pick-place-seed{seed:010d}")
            for seed in (7, 8, 9)
        ]
        backend, seen = _backend(_ok(*episodes))
        payload = backend._build_payload(
            _task(), 7, "full", [{"id": "a", "category": "sensor_degradation",
                                  "params": {}, "difficulty": 0.5}], episodes=2,
        )
        results = backend._post_run(payload)
        assert [r.seed for r in results] == [7, 8, 9]
        assert _body(seen[0])["episodes"] == 2
        assert len(results) == 3

    def test_works_unchanged_with_run_validation(self) -> None:
        """The whole point: run_validation drives the worker like the mock."""

        def handler(request: httpx.Request) -> httpx.Response:
            seed = _body(request)["seed"]
            return httpx.Response(
                200,
                json={
                    "episodes": [
                        _episode_json(
                            seed=seed,
                            episode_id=f"pick-place-seed{seed:010d}",
                            success=seed % 4 != 0,
                            failure_mode=None if seed % 4 == 0 else "collision",
                        )
                    ]
                },
            )

        backend, seen = _backend(handler)
        task = _task(episodes=3, adversarial=1)
        results = run_validation(task, backend, [_scenario()], seed=100)
        assert len(results) == 4
        assert [r.seed for r in results] == [100, 101, 102, 103]
        assert len(seen) == 4  # one POST per episode, as the protocol implies
        assert [r.success for r in results] == [False, True, True, True]

    def test_satisfies_backend_protocol(self) -> None:
        backend, _ = _backend(_ok(_episode_json()))
        assert isinstance(backend, SimulationBackend)
        assert backend.name == "isaac-worker"


class TestAuth:
    def test_bearer_header_when_key_given(self) -> None:
        backend, seen = _backend(_ok(_episode_json()), api_key="sekret")
        backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert seen[0].headers["authorization"] == "Bearer sekret"
        assert backend.has_api_key is True

    def test_bearer_header_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_KEY", "from-env")
        backend, seen = _backend(_ok(_episode_json()))
        backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert seen[0].headers["authorization"] == "Bearer from-env"

    def test_no_auth_header_without_key(self) -> None:
        backend, seen = _backend(_ok(_episode_json()))
        backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert "authorization" not in seen[0].headers
        assert backend.has_api_key is False


class TestConfiguration:
    def test_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", BASE_URL)
        backend, seen = _backend(_ok(_episode_json()), base_url=None)
        backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert str(seen[0].url) == f"{BASE_URL}/episodes/run"

    def test_trailing_slash_is_normalized(self) -> None:
        backend, seen = _backend(_ok(_episode_json()), base_url=f"{BASE_URL}/")
        backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert str(seen[0].url) == f"{BASE_URL}/episodes/run"

    def test_missing_url_fails_on_use_not_construction(self) -> None:
        backend, seen = _backend(_ok(_episode_json()), base_url=None)
        assert backend.base_url is None  # constructing is harmless...
        with pytest.raises(ValueError, match="VALIDSIM_ISAAC_WORKER_URL"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert seen == []  # ...and we never reached the network
        assert backend._client is None

    def test_context_manager_closes_client(self) -> None:
        backend, _ = _backend(_ok(_episode_json()))
        with backend:
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
            assert backend._client is not None
        assert backend._client is None


class TestContractMismatch:
    def test_missing_field_raises(self) -> None:
        episode = _episode_json()
        del episode["max_contact_force_n"]
        backend, _ = _backend(_ok(episode))
        with pytest.raises(SimWorkerError, match="missing required field") as exc_info:
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert "max_contact_force_n" in str(exc_info.value)
        assert exc_info.value.status_code is None

    def test_unknown_field_raises(self) -> None:
        backend, _ = _backend(_ok(_episode_json(teleop_score=0.5)))
        with pytest.raises(SimWorkerError, match="unknown field"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_wrong_type_raises(self) -> None:
        backend, _ = _backend(_ok(_episode_json(success="yes")))
        with pytest.raises(SimWorkerError, match="'success' must be bool"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_bool_is_not_a_number(self) -> None:
        """JSON ``true`` must not quietly satisfy an int field (``True == 1``)."""
        backend, _ = _backend(_ok(_episode_json(collision_count=True)))
        with pytest.raises(SimWorkerError, match="collision_count"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_seed_must_be_echoed(self) -> None:
        backend, _ = _backend(_ok(_episode_json(seed=999)))
        with pytest.raises(SimWorkerError, match="echoed seed"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_unknown_failure_mode_raises(self) -> None:
        episode = _episode_json(success=False, failure_mode="exploded")
        backend, _ = _backend(_ok(episode))
        with pytest.raises(SimWorkerError, match="taxonomy"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_unknown_randomization_level_raises(self) -> None:
        backend, _ = _backend(_ok(_episode_json(randomization_level="extreme")))
        with pytest.raises(SimWorkerError, match="randomization_level"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_randomization_level_must_be_echoed(self) -> None:
        """A worker quietly running "none" when "full" was asked would skew robustness."""
        backend, _ = _backend(_ok(_episode_json(randomization_level="none")))
        with pytest.raises(SimWorkerError, match="although 'full' was requested"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_missing_envelope_key_raises(self) -> None:
        backend, _ = _backend(lambda request: httpx.Response(200, json={"results": []}))
        with pytest.raises(SimWorkerError, match="no top-level 'episodes'"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_wrong_episode_count_raises(self) -> None:
        backend, _ = _backend(_ok(_episode_json(), _episode_json(seed=43)))
        with pytest.raises(SimWorkerError, match="returned 2 episode"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")

    def test_non_json_reply_raises(self) -> None:
        backend, _ = _backend(lambda request: httpx.Response(200, text="<html>502</html>"))
        with pytest.raises(SimWorkerError, match="non-JSON"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")


class TestTransport:
    def test_http_500_raises_without_retry(self) -> None:
        backend, seen = _backend(
            lambda request: httpx.Response(500, json={"error": "CUDA out of memory"})
        )
        with pytest.raises(SimWorkerError, match="HTTP 500") as exc_info:
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert exc_info.value.status_code == 500
        assert "CUDA out of memory" in str(exc_info.value)
        assert len(seen) == 1  # a deterministic server answer is never retried

    def test_transport_error_is_retried_then_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        backend, seen = _backend(handler)
        with pytest.raises(SimWorkerError, match="unreachable") as exc_info:
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert len(seen) == 2  # exactly two attempts, no more
        assert exc_info.value.status_code is None
        assert isinstance(exc_info.value.__cause__, httpx.TransportError)

    def test_transport_error_then_success(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow worker", request=request)
            return httpx.Response(200, json={"episodes": [_episode_json()]})

        backend, seen = _backend(handler)
        result = backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert result.success is True
        assert len(seen) == 2

    def test_http_4xx_raises(self) -> None:
        backend, seen = _backend(
            lambda request: httpx.Response(401, text="missing bearer token")
        )
        with pytest.raises(SimWorkerError) as exc_info:
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert exc_info.value.status_code == 401
        assert len(seen) == 1


class TestHealthProbe:
    def test_is_alive_true_on_200(self) -> None:
        backend, seen = _backend(lambda request: httpx.Response(200, json={"status": "ok"}))
        assert backend.is_alive() is True
        assert str(seen[0].url) == f"{BASE_URL}/health"
        assert seen[0].method == "GET"

    def test_is_alive_sends_credentials(self) -> None:
        backend, seen = _backend(
            lambda request: httpx.Response(204), api_key="sekret"
        )
        assert backend.is_alive() is True
        assert seen[0].headers["authorization"] == "Bearer sekret"

    def test_is_alive_false_on_error_status(self) -> None:
        backend, _ = _backend(lambda request: httpx.Response(503, text="gpu busy"))
        assert backend.is_alive() is False

    def test_is_alive_false_on_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        backend, _ = _backend(handler)
        assert backend.is_alive() is False

    def test_is_alive_without_configuration(self) -> None:
        backend, _ = _backend(lambda request: httpx.Response(200), base_url=None)
        with pytest.raises(ValueError, match="VALIDSIM_ISAAC_WORKER_URL"):
            backend.is_alive()


class TestFactory:
    def test_defaults_to_mock(self) -> None:
        backend = create_backend()
        assert isinstance(backend, MockIsaacBackend)
        assert not isinstance(backend, IsaacWorkerBackend)

    def test_explicit_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "mock")
        assert type(create_backend()) is MockIsaacBackend

    def test_selects_isaac_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        monkeypatch.setenv("VALIDSIM_ISAAC_WORKER_URL", BASE_URL)
        backend = create_backend()
        assert isinstance(backend, IsaacWorkerBackend)
        assert backend.base_url == BASE_URL

    def test_name_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", " Isaac ")
        assert isinstance(create_backend(), IsaacWorkerBackend)

    def test_unknown_name_falls_back_to_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_BACKEND", "mujoco")
        assert isinstance(create_backend(), MockIsaacBackend)

    def test_isaac_branch_needs_no_gpu_at_import_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Selecting "isaac" without a URL must not explode until an episode runs."""
        monkeypatch.setenv("VALIDSIM_BACKEND", "isaac")
        backend = create_backend()
        assert isinstance(backend, IsaacWorkerBackend)
        with pytest.raises(ValueError, match="VALIDSIM_ISAAC_WORKER_URL"):
            backend.run_episode(_task(), seed=SEED, randomization_level="full")
