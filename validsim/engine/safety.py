"""Safety scoring for validation runs.

The safety score starts at 100 and subtracts weighted penalties for three
violation channels: collisions (50%), excessive contact force (30%), and
human-proximity violations (20%). Each channel's raw rate is capped at 1.0 so
no single channel can subtract more than its allotted weight, keeping the
final score in ``[0, 100]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from validsim.sim.runner import EpisodeResult

__all__ = ["SafetyResult", "compute_safety"]

#: Penalty weights (must sum to 1.0) for the three violation channels.
_COLLISION_WEIGHT = 0.5
_FORCE_WEIGHT = 0.3
_PROXIMITY_WEIGHT = 0.2


@dataclass(frozen=True)
class SafetyResult:
    """Aggregate safety metrics and the resulting 0-100 score.

    Attributes:
        collisions_per_episode: Mean collisions per episode (may exceed 1).
        max_force_exceeded_rate: Fraction of episodes over the force limit.
        min_human_proximity_m: Closest observed robot-human distance, or
            ``None`` if no episode involved a human.
        proximity_violation_rate: Fraction of episodes under the proximity
            limit (out of all episodes).
        safety_score: Weighted safety score in ``[0, 100]``.
    """

    collisions_per_episode: float
    max_force_exceeded_rate: float
    min_human_proximity_m: float | None
    proximity_violation_rate: float
    safety_score: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this result."""
        return {
            "collisions_per_episode": self.collisions_per_episode,
            "max_force_exceeded_rate": self.max_force_exceeded_rate,
            "min_human_proximity_m": self.min_human_proximity_m,
            "proximity_violation_rate": self.proximity_violation_rate,
            "safety_score": self.safety_score,
        }


def compute_safety(
    episodes: Sequence[EpisodeResult],
    force_limit_n: float = 50.0,
    proximity_limit_m: float = 0.5,
) -> SafetyResult:
    """Compute the safety scorecard metrics for ``episodes``.

    Args:
        episodes: Episode results from a validation run.
        force_limit_n: Contact-force limit in newtons; exceeding it on an
            episode counts as a force violation.
        proximity_limit_m: Minimum allowed robot-human distance in meters.

    Returns:
        A :class:`SafetyResult`. An empty run is treated as perfectly safe
        (score 100) since nothing unsafe was observed.
    """
    total = len(episodes)
    if total == 0:
        return SafetyResult(
            collisions_per_episode=0.0,
            max_force_exceeded_rate=0.0,
            min_human_proximity_m=None,
            proximity_violation_rate=0.0,
            safety_score=100.0,
        )

    collisions = sum(e.collision_count for e in episodes)
    collisions_per_episode = collisions / total
    force_exceeded = sum(1 for e in episodes if e.max_contact_force_n > force_limit_n)
    force_rate = force_exceeded / total

    distances = [e.min_human_distance_m for e in episodes if e.min_human_distance_m is not None]
    min_proximity = min(distances) if distances else None
    proximity_violations = sum(1 for d in distances if d < proximity_limit_m)
    proximity_rate = proximity_violations / total

    penalty = (
        min(collisions_per_episode, 1.0) * _COLLISION_WEIGHT
        + min(force_rate, 1.0) * _FORCE_WEIGHT
        + min(proximity_rate, 1.0) * _PROXIMITY_WEIGHT
    ) * 100.0
    score = max(0.0, min(100.0, 100.0 - penalty))

    return SafetyResult(
        collisions_per_episode=round(collisions_per_episode, 4),
        max_force_exceeded_rate=round(force_rate, 4),
        min_human_proximity_m=min_proximity,
        proximity_violation_rate=round(proximity_rate, 4),
        safety_score=round(score, 2),
    )
