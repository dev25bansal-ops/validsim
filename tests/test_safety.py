"""Tests for safety metric computation and score bounds."""

from __future__ import annotations

from itertools import count

from validsim.engine.safety import compute_safety
from validsim.sim.runner import EpisodeResult

_ids = count()


def _ep(
    collisions: int = 0,
    force: float = 10.0,
    distance: float | None = 1.5,
) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id="t",
        seed=1,
        success=collisions == 0,
        collision_count=collisions,
        max_contact_force_n=force,
        min_human_distance_m=distance,
        duration_s=5.0,
    )


class TestCleanRuns:
    def test_empty_run_is_fully_safe(self) -> None:
        result = compute_safety([])
        assert result.safety_score == 100.0
        assert result.min_human_proximity_m is None

    def test_no_violations_scores_100(self) -> None:
        episodes = [_ep() for _ in range(20)]
        result = compute_safety(episodes)
        assert result.safety_score == 100.0
        assert result.collisions_per_episode == 0.0
        assert result.max_force_exceeded_rate == 0.0


class TestPenaltyMath:
    def test_exact_weighted_penalty(self) -> None:
        # 10 episodes: 1 collision total (cpe=0.1 -> -5),
        # 2 force violations (rate 0.2 -> -6), 1 proximity violation (-2).
        episodes = [_ep() for _ in range(7)]
        episodes.append(_ep(collisions=1, force=10.0))
        episodes.append(_ep(force=80.0))
        episodes.append(_ep(force=60.0, distance=0.2))
        result = compute_safety(episodes)
        assert result.collisions_per_episode == 0.1
        assert result.max_force_exceeded_rate == 0.2
        assert result.proximity_violation_rate == 0.1
        assert result.safety_score == 87.0

    def test_score_clamped_to_zero(self) -> None:
        episodes = [_ep(collisions=5, force=500.0, distance=0.05) for _ in range(10)]
        result = compute_safety(episodes)
        assert result.safety_score == 0.0

    def test_score_never_exceeds_100(self) -> None:
        result = compute_safety([_ep(force=1.0, distance=3.0) for _ in range(5)])
        assert 0.0 <= result.safety_score <= 100.0


class TestProximity:
    def test_min_distance_across_episodes(self) -> None:
        episodes = [_ep(distance=1.2), _ep(distance=0.3), _ep(distance=0.9)]
        assert compute_safety(episodes).min_human_proximity_m == 0.3

    def test_none_distances_ignored(self) -> None:
        episodes = [_ep(distance=None), _ep(distance=None)]
        result = compute_safety(episodes)
        assert result.min_human_proximity_m is None
        assert result.safety_score == 100.0

    def test_custom_limits(self) -> None:
        episodes = [_ep(force=45.0, distance=0.4) for _ in range(4)]
        default = compute_safety(episodes)
        strict = compute_safety(episodes, force_limit_n=40.0, proximity_limit_m=0.5)
        assert default.max_force_exceeded_rate == 0.0
        assert strict.max_force_exceeded_rate == 1.0
        assert strict.safety_score < default.safety_score
