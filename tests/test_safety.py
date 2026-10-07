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


class TestNonFiniteObservablesFailClosed:
    """A non-finite safety observable must never score as "no violation".

    ``NaN > limit`` is ``False`` in Python, so a NaN contact force counted as a
    *passing* episode and the run scored a perfect 100.0 -- a fail-open on
    exactly the channel that exists to catch dangerous contact forces. A sensor
    reporting NaN has not demonstrated safety; it has failed to report, and the
    gate must treat that as unknown-and-not-safe rather than silently safe.
    """

    @staticmethod
    def _episode(**kwargs: object) -> EpisodeResult:
        base: dict = {
            "episode_id": "ep-x", "task_id": "t", "seed": 1, "success": True,
            "collision_count": 0, "max_contact_force_n": 10.0,
            "min_human_distance_m": 1.5, "duration_s": 5.0,
        }
        base.update(kwargs)
        return EpisodeResult(**base)  # type: ignore[arg-type]

    def test_nan_force_does_not_score_perfect(self) -> None:
        episodes = [
            self._episode(episode_id=f"ep-{i}", max_contact_force_n=float("nan"))
            for i in range(10)
        ]
        result = compute_safety(episodes)
        assert result.safety_score < 100.0, "NaN contact force scored a perfect safety"

    def test_nan_distance_does_not_score_perfect(self) -> None:
        """The same fail-open exists on the proximity channel."""
        episodes = [
            self._episode(episode_id=f"ep-{i}", min_human_distance_m=float("nan"))
            for i in range(10)
        ]
        result = compute_safety(episodes)
        assert result.safety_score < 100.0, "NaN human distance scored a perfect safety"

    def test_control_finite_values_are_unaffected(self) -> None:
        """The guard must not penalize ordinary clean or dirty runs."""
        clean = compute_safety([_ep(force=10.0, distance=1.5) for _ in range(10)])
        assert clean.safety_score == 100.0

        dirty = compute_safety([_ep(force=90.0, distance=1.5) for _ in range(10)])
        assert dirty.safety_score == 70.0  # unchanged 30% force-penalty channel


class TestNonFiniteCollisionCount:
    """A collision count the sensor failed to produce is not a clean run.

    The force and proximity channels both route through ``_is_finite`` and
    fail closed on a bad reading. The collision channel summed the raw value,
    so one ``NaN`` poisoned the total -- and ``min(nan, 1.0)`` is ``1.0`` in
    CPython, so the penalty silently vanished and the run scored a perfect
    100.0, exactly what a genuinely collision-free run scores. A robot that
    collided on every episode could therefore be gated APPROVE off one broken
    sensor.
    """

    def test_nan_collision_count_cannot_score_as_clean(self) -> None:
        """The whole point: a broken reading may not outscore a real collision."""
        clean = compute_safety([_ep(collisions=0) for _ in range(10)])
        colliding = compute_safety([_ep(collisions=1) for _ in range(10)])
        broken = compute_safety([_ep(collisions=float("nan")) for _ in range(10)])

        assert clean.safety_score == 100.0
        assert colliding.safety_score == 50.0
        assert broken.safety_score <= colliding.safety_score, (
            f"a NaN collision count scored {broken.safety_score}, better than "
            f"the {colliding.safety_score} of a run that really collided"
        )

    def test_one_bad_reading_does_not_penalise_the_whole_run(self) -> None:
        """The guard is per episode, not a blanket fail on the batch.

        One unreadable episode among nine clean ones must cost that episode
        its violation, not turn a passing run into a failing one -- otherwise
        the fix would trade a fail-open for a fail-loud and break every
        downstream scorecard expectation.
        """
        eps = [_ep(collisions=0) for _ in range(9)]
        eps.append(_ep(collisions=float("nan")))
        one_bad = compute_safety(eps)

        # 1 collision in 10 episodes -> 0.1 * 50.0 weight = 5.0 penalty.
        assert one_bad.safety_score == 95.0
        assert one_bad.collisions_per_episode == 0.1


class TestProximityDenominator:
    """The proximity rate must be measured against episodes a human was in.

    The numerator only ranges over episodes where ``min_human_distance_m`` is
    not ``None``, but the denominator used to be the total episode count. An
    episode with no human is *not applicable* to this channel, not *not a
    violation* -- so the score was set by how a run happened to be batched
    rather than by what the robot did. A robot that violated the limit in every
    human-present episode scored 99.98 once the same violation was diluted
    across 999 human-free ones. Proximity carries ``_PROXIMITY_WEIGHT = 0.2`` of
    safety, and safety is ``_W_SAFETY = 0.3`` of the composite, so this was a
    real path to a BLOCK that read as clean.
    """

    def test_dilution_cannot_raise_the_score(self) -> None:
        """The core property: adding human-free episodes changes nothing."""
        alone = compute_safety([_ep(distance=0.2)])
        diluted = compute_safety([_ep(distance=0.2)] + [_ep(distance=None)] * 3)

        assert alone.proximity_violation_rate == 1.0
        assert diluted.proximity_violation_rate == 1.0, (
            "human-free episodes were counted in the proximity denominator"
        )
        assert diluted.safety_score == alone.safety_score, (
            "adding human-free episodes changed the safety score; the robot's "
            "behaviour was identical"
        )
        assert alone.safety_score == 80.0  # 1.0 violation * 0.2 weight * 100

    def test_violating_every_human_present_episode_still_scores_low(self) -> None:
        """No amount of human-free padding can hide a total violation."""
        padded = compute_safety([_ep(distance=0.2)] + [_ep(distance=None)] * 999)
        assert padded.proximity_violation_rate == 1.0
        assert padded.safety_score == 80.0, (
            f"a robot that breached proximity in every human-present episode "
            f"scored {padded.safety_score} once diluted"
        )

    def test_mixed_batches_use_the_human_present_count(self) -> None:
        """The rate is violations over human-present, not over everything."""
        episodes = [
            _ep(distance=0.2),  # violation
            _ep(distance=0.2),  # violation
            _ep(distance=1.5),  # human present, clean
            _ep(distance=1.5),  # human present, clean
            _ep(distance=None),  # no human
            _ep(distance=None),  # no human
        ]
        result = compute_safety(episodes)
        assert result.proximity_violation_rate == 0.5, (
            "2 violations across 4 human-present episodes is a rate of 0.5"
        )
        assert result.safety_score == 90.0  # 0.5 * 0.2 * 100 penalty

    def test_no_human_at_all_abstains_rather_than_scoring_clean(self) -> None:
        """With nothing to measure the channel contributes 0.0, not a pass.

        The run is scored on the other two channels alone; it is not handed a
        perfect proximity rate for the privilege of never having tested the
        thing the channel exists to test.
        """
        result = compute_safety([_ep(distance=None) for _ in range(10)])
        assert result.proximity_violation_rate == 0.0
        assert result.min_human_proximity_m is None
        assert result.safety_score == 100.0, (
            "with no human present the other channels are clean, so the safety "
            "score is 100 and the proximity channel simply abstains"
        )

    def test_clean_human_present_runs_are_unaffected(self) -> None:
        """Control: the fix must not penalise a run that never got close."""
        assert compute_safety([_ep(distance=1.5) for _ in range(10)]).safety_score == 100.0
        # All-clean and all-violating still bracket the channel's 20 points.
        assert compute_safety([_ep(distance=0.2) for _ in range(10)]).safety_score == 80.0

