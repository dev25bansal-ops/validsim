"""Composite deploy scorecard: the single artifact CI gates on.

    composite = 0.4 * success% + 0.3 * safety + 0.2 * robustness
              + 0.1 * regression_component

``regression_component`` is 100 when no significant regressions were detected,
otherwise ``100 - 25 * count`` (floored at 0). A run is approved when its
composite score meets the configured threshold (default 85.0).
"""

from __future__ import annotations

import dataclasses
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from validsim.config import TaskConfig
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.stats import bootstrap_ci
from validsim.sim.runner import EpisodeResult

__all__ = ["Scorecard", "build_scorecard"]

DeployDecision = Literal["APPROVE", "BLOCK"]

#: Component weights (must sum to 1.0).
_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1
#: A 0.5 std-dev spread across randomization groups zeroes robustness.
_ROBUSTNESS_SCALE = 200.0
#: Penalty per significant regression in the regression component.
_REGRESSION_PENALTY = 25.0


def _utc_now_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: float, low: float, high: float) -> float:
    """Return ``value`` constrained to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def _robustness_score(episodes: Sequence[EpisodeResult]) -> float:
    """100 minus the std-dev of per-group success rates, scaled to 0-100.

    Episodes are grouped by randomization level; a single group (or none)
    yields the maximum score since no cross-condition variance is observed.
    """
    by_group: dict[str, list[bool]] = defaultdict(list)
    for e in episodes:
        by_group[e.randomization_level].append(e.success)
    rates = [sum(v) / len(v) for v in by_group.values()]
    if len(rates) < 2:
        return 100.0
    spread = statistics.pstdev(rates)
    return round(_clamp(100.0 - _ROBUSTNESS_SCALE * spread, 0.0, 100.0), 2)


def _regression_component(regression: RegressionReport | None) -> float:
    """100 when clean, else 100 - 25 per significant regression (min 0)."""
    if regression is None:
        return 100.0
    count = len(regression.significant_regressions)
    return max(0.0, 100.0 - _REGRESSION_PENALTY * count)


@dataclass(frozen=True)
class Scorecard:
    """Final, serializable verdict for one validation run.

    Attributes:
        run_id: Store-assigned run identifier (e.g. ``"vrun-1a2b3c4d"``).
        checkpoint_id: Checkpoint that was validated.
        task_id: Task that was executed.
        composite_score: Weighted 0-100 score (see module docstring).
        success_rate: Fraction of episodes that succeeded (0-1).
        safety_score: Weighted safety score (0-100).
        robustness_score: Cross-randomization-group consistency (0-100).
        regression_delta: Success-rate delta vs baseline, or ``None``.
        confidence_interval: 95% bootstrap CI of the success rate, or ``None``.
        deploy_decision: ``"APPROVE"`` or ``"BLOCK"``.
        threshold: Composite score required to approve.
        created_at: ISO-8601 UTC timestamp.
        episode_count: Total episodes scored.
        failure_taxonomy: Failure-mode counts for failed episodes.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    composite_score: float
    success_rate: float
    safety_score: float
    robustness_score: float
    regression_delta: float | None
    confidence_interval: tuple[float, float] | None
    deploy_decision: DeployDecision
    threshold: float = 85.0
    created_at: str = ""
    episode_count: int = 0
    failure_taxonomy: dict[str, int] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (CI tuple converted to list)."""
        data = dataclasses.asdict(self)
        if self.confidence_interval is not None:
            data["confidence_interval"] = list(self.confidence_interval)
        return data

    def to_json(self, indent: int | None = None) -> str:
        """Serialize this scorecard to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


def build_scorecard(
    run_id: str,
    checkpoint_id: str,
    task: TaskConfig,
    evaluation: EvaluationResult,
    safety: SafetyResult,
    episodes: Sequence[EpisodeResult],
    regression: RegressionReport | None = None,
    threshold: float = 85.0,
    created_at: str | None = None,
    stats_seed: int = 42,
) -> Scorecard:
    """Assemble the composite scorecard from engine outputs.

    Args:
        run_id: Pre-generated store run id.
        checkpoint_id: Checkpoint under validation.
        task: The task configuration that produced these results.
        evaluation: Aggregate success/failure metrics.
        safety: Safety metrics for the same episodes.
        episodes: Raw episodes (used for robustness + confidence interval).
        regression: Optional baseline comparison report.
        threshold: Composite score needed for ``APPROVE``.
        created_at: ISO timestamp override (defaults to now, UTC).
        stats_seed: Seed for the bootstrap CI.

    Returns:
        A frozen :class:`Scorecard` ready for storage/serialization.
    """
    success_pct = evaluation.success_rate * 100.0
    robustness = _robustness_score(episodes)
    regression_comp = _regression_component(regression)
    composite = round(
        _W_SUCCESS * success_pct
        + _W_SAFETY * safety.safety_score
        + _W_ROBUSTNESS * robustness
        + _W_REGRESSION * regression_comp,
        2,
    )
    composite = _clamp(composite, 0.0, 100.0)

    ci: tuple[float, float] | None = None
    if evaluation.total_episodes > 0:
        bits = [1.0 if e.success else 0.0 for e in episodes]
        if len(bits) == evaluation.total_episodes:
            low, high, _ = bootstrap_ci(bits, n_resamples=500, seed=stats_seed)
            ci = (round(low, 4), round(high, 4))

    regression_delta: float | None = None
    if regression is not None:
        for item in regression.items:
            if item.metric == "success_rate":
                regression_delta = round(item.delta, 4)
                break

    decision: DeployDecision = "APPROVE" if composite >= threshold else "BLOCK"
    return Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task.task_id,
        composite_score=composite,
        success_rate=round(evaluation.success_rate, 4),
        safety_score=safety.safety_score,
        robustness_score=robustness,
        regression_delta=regression_delta,
        confidence_interval=ci,
        deploy_decision=decision,
        threshold=threshold,
        created_at=created_at or _utc_now_iso(),
        episode_count=evaluation.total_episodes,
        failure_taxonomy=dict(evaluation.failure_taxonomy),
    )
