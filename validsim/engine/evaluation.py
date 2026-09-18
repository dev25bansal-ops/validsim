"""Aggregation of raw episode results into evaluation metrics."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from validsim.sim.runner import EpisodeResult

__all__ = ["EvaluationResult", "evaluate"]


@dataclass(frozen=True)
class EvaluationResult:
    """Summary metrics for one validation run.

    Attributes:
        total_episodes: Number of episodes aggregated.
        success_count: Episodes that achieved their goal.
        success_rate: ``success_count / total_episodes`` (0.0 when empty).
        per_task_success: Success rate keyed by ``task_id``.
        failure_taxonomy: Failure-mode name keyed by occurrence count.
        mean_duration_s: Mean episode duration in seconds.
    """

    total_episodes: int
    success_count: int
    success_rate: float
    per_task_success: dict[str, float] = field(default_factory=dict)
    failure_taxonomy: dict[str, int] = field(default_factory=dict)
    mean_duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this result."""
        return {
            "total_episodes": self.total_episodes,
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "per_task_success": dict(self.per_task_success),
            "failure_taxonomy": dict(self.failure_taxonomy),
            "mean_duration_s": self.mean_duration_s,
        }


def evaluate(episodes: Sequence[EpisodeResult]) -> EvaluationResult:
    """Compute aggregate success/failure/duration metrics for ``episodes``.

    Args:
        episodes: Episode results from a validation run.

    Returns:
        An :class:`EvaluationResult`; all-zero counts for an empty sequence.
    """
    total = len(episodes)
    if total == 0:
        return EvaluationResult(
            total_episodes=0,
            success_count=0,
            success_rate=0.0,
            per_task_success={},
            failure_taxonomy={},
            mean_duration_s=0.0,
        )

    success_count = sum(1 for e in episodes if e.success)
    by_task: dict[str, list[bool]] = defaultdict(list)
    for e in episodes:
        by_task[e.task_id].append(e.success)
    taxonomy: Counter[str] = Counter(
        e.failure_mode for e in episodes if not e.success and e.failure_mode
    )

    return EvaluationResult(
        total_episodes=total,
        success_count=success_count,
        success_rate=success_count / total,
        per_task_success={
            task: sum(vals) / len(vals) for task, vals in sorted(by_task.items())
        },
        failure_taxonomy=dict(taxonomy.most_common()),
        mean_duration_s=statistics.fmean(e.duration_s for e in episodes),
    )
