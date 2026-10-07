"""Batching-path tests for the Isaac Sim GPU-worker adapter.

These exercise :meth:`~validsim.sim.isaac_worker.IsaacWorkerBackend.run_episodes`,
the multi-episode counterpart of ``run_episode``: several episodes travel in a
single ``POST /episodes/run`` and the reply is split back into per-episode
results, reusing the exact contract checks pinned in ``docs/isaac-worker.md``.

Like ``tests/test_isaac_worker.py``, every test runs against
``httpx.MockTransport`` so the suite proves the wire contract without a GPU, a
container, or a socket. The helpers are deliberately mirrored from that file
rather than imported, so the two suites stay independent.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim import IsaacWorkerBackend
from validsim.sim.isaac_worker import SimWorkerError
from validsim.sim.runner import EpisodeResult

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


def _scenario(tag: str = "0000") -> AdversarialScenario:
    return AdversarialScenario(
        id=f"adv-pick-place-{tag}",
        category="human_proximity",
        name=f"Human Proximity #{tag}",
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


def _seeded_batch(base_seed: int, count: int, **overrides: Any) -> list[dict[str, Any]]:
    """``count`` contract-valid episodes echoing ``base_seed + i`` in order."""
    return [
        _episode_json(
            seed=base_seed + i,
            episode_id=f"pick-place-seed{base_seed + i:010d}",
            **overrides,
        )
        for i in range(count)
    ]


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


class TestBatchHappyPath:
    def test_batch_of_n_returns_n_results_in_one_round_trip(self) -> None:
        episodes = _seeded_batch(SEED, 5)
        backend, seen = _backend(_ok(*episodes))
        results = backend.run_episodes(_task(), base_seed=SEED, episodes=5,
                                       randomization_level="full")
        assert len(seen) == 1  # the whole point: a single POST for the batch
        assert str(seen[0].url) == f"{BASE_URL}/episodes/run"
        assert seen[0].method == "POST"
        assert len(results) == 5
        assert all(isinstance(r, EpisodeResult) for r in results)

    def test_results_are_correctly_seeded_in_order(self) -> None:
        base = 1000
        backend, _ = _backend(_ok(*_seeded_batch(base, 4)))
        results = backend.run_episodes(_task(), base_seed=base, episodes=4,
                                       randomization_level="full")
        assert [r.seed for r in results] == [base, base + 1, base + 2, base + 3]
        assert [r.episode_id for r in results] == [
            f"pick-place-seed{base + i:010d}" for i in range(4)
        ]

    def test_request_body_matches_batched_contract(self) -> None:
        backend, seen = _backend(_ok(*_seeded_batch(SEED, 3)))
        backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                             randomization_level="full")
        body = _body(seen[0])
        assert set(body) == {
            "task_id", "robot", "environment", "checkpoint_id", "seed", "episodes",
            "randomization_level", "scenarios",
        }
        assert body["seed"] == SEED
        assert body["episodes"] == 3
        assert body["scenarios"] == []
        assert body["randomization_level"] == "full"

    def test_batch_with_scenarios_returns_episodes_plus_scenarios(self) -> None:
        """Nominal episodes come first, then one result per adversarial scenario."""
        scenarios = [_scenario("0000"), _scenario("0001")]
        episodes = _seeded_batch(SEED, 4)  # 2 nominal + 2 adversarial = 4 results
        backend, seen = _backend(_ok(*episodes))
        results = backend.run_episodes(
            _task(), base_seed=SEED, episodes=2,
            randomization_level="full", scenarios=scenarios,
        )
        assert len(results) == 4
        assert [r.seed for r in results] == [SEED, SEED + 1, SEED + 2, SEED + 3]
        body = _body(seen[0])
        assert body["episodes"] == 2
        assert [s["id"] for s in body["scenarios"]] == [
            "adv-pick-place-0000", "adv-pick-place-0001",
        ]
        # Each scenario serializes with the identical single-scenario wire shape.
        assert set(body["scenarios"][0]) == {"id", "category", "params", "difficulty"}

    def test_scenario_only_batch(self) -> None:
        """episodes=0 with scenarios is valid: run only the injected scenarios."""
        backend, seen = _backend(_ok(*_seeded_batch(SEED, 2, randomization_level="partial")))
        results = backend.run_episodes(
            _task(), base_seed=SEED, episodes=0,
            randomization_level="partial", scenarios=[_scenario("0000"), _scenario("0001")],
        )
        assert [r.seed for r in results] == [SEED, SEED + 1]
        body = _body(seen[0])
        assert body["episodes"] == 0
        assert len(body["scenarios"]) == 2

    def test_batch_carries_auth_header(self) -> None:
        backend, seen = _backend(_ok(*_seeded_batch(SEED, 2)), api_key="sekret")
        backend.run_episodes(_task(), base_seed=SEED, episodes=2,
                             randomization_level="full")
        assert seen[0].headers["authorization"] == "Bearer sekret"


class TestBatchContractMismatch:
    def test_wrong_count_raises(self) -> None:
        """A batch that returns fewer episodes than requested is a contract break."""
        backend, _ = _backend(_ok(*_seeded_batch(SEED, 2)))
        with pytest.raises(SimWorkerError, match="returned 2 episode"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                                 randomization_level="full")

    def test_misaligned_seed_echo_raises(self) -> None:
        """Position 2 must echo base_seed+2; a swapped seed is rejected."""
        episodes = _seeded_batch(SEED, 3)
        episodes[2]["seed"] = SEED + 99  # wrong echo at the last slot
        backend, _ = _backend(_ok(*episodes))
        with pytest.raises(SimWorkerError, match="echoed seed"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                                 randomization_level="full")

    def test_bad_field_in_one_batch_member_raises(self) -> None:
        episodes = _seeded_batch(SEED, 3)
        del episodes[1]["max_contact_force_n"]  # missing mandatory field mid-batch
        backend, _ = _backend(_ok(*episodes))
        with pytest.raises(SimWorkerError, match="missing required field") as exc_info:
            backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                                 randomization_level="full")
        assert "episodes[1]" in str(exc_info.value)
        assert "max_contact_force_n" in str(exc_info.value)

    def test_unknown_failure_mode_raises(self) -> None:
        episodes = _seeded_batch(SEED, 3)
        episodes[0].update(success=False, failure_mode="exploded")
        backend, _ = _backend(_ok(*episodes))
        with pytest.raises(SimWorkerError, match="taxonomy"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                                 randomization_level="full")

    def test_randomization_level_must_be_echoed(self) -> None:
        """Every batch member must echo the requested level or the run is rejected."""
        episodes = _seeded_batch(SEED, 3, randomization_level="none")
        backend, _ = _backend(_ok(*episodes))
        with pytest.raises(SimWorkerError, match="although 'full' was requested"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=3,
                                 randomization_level="full")

    def test_non_json_reply_raises(self) -> None:
        backend, _ = _backend(lambda request: httpx.Response(200, text="<html>502</html>"))
        with pytest.raises(SimWorkerError, match="non-JSON"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=2,
                                 randomization_level="full")


class TestEmptyBatch:
    def test_empty_batch_no_ops_without_network(self) -> None:
        backend, seen = _backend(_ok())
        results = backend.run_episodes(_task(), base_seed=SEED, episodes=0,
                                       randomization_level="full")
        assert results == []
        assert seen == []  # never touched the transport
        assert backend._client is None  # and never even built a client

    def test_empty_scenario_list_is_also_a_no_op(self) -> None:
        backend, seen = _backend(_ok())
        results = backend.run_episodes(
            _task(), base_seed=SEED, episodes=0,
            randomization_level="full", scenarios=[],
        )
        assert results == []
        assert seen == []

    def test_negative_episodes_raises_value_error(self) -> None:
        backend, seen = _backend(_ok())
        with pytest.raises(ValueError, match="episodes must be >= 0"):
            backend.run_episodes(_task(), base_seed=SEED, episodes=-1,
                                 randomization_level="full")
        assert seen == []


class TestSingleEpisodeUnchanged:
    def test_run_episode_still_works(self) -> None:
        """The batched addition must not disturb the original single-episode path."""
        backend, seen = _backend(_ok(_episode_json()))
        result = backend.run_episode(_task(), seed=SEED, randomization_level="full")
        assert isinstance(result, EpisodeResult)
        assert result.seed == SEED
        assert len(seen) == 1
        assert _body(seen[0])["episodes"] == 1
