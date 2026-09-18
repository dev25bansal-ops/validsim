"""Tests for episode aggregation into EvaluationResult."""

from __future__ import annotations

from itertools import count

from validsim.engine.evaluation import evaluate
from validsim.sim.runner import EpisodeResult

_ids = count()


def _ep(
    task_id: str = "pick",
    success: bool = True,
    failure_mode: str | None = None,
    duration: float = 10.0,
) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id=task_id,
        seed=1,
        success=success,
        failure_mode=failure_mode,
        duration_s=duration,
    )


class TestEvaluate:
    def test_empty_run(self) -> None:
        result = evaluate([])
        assert result.total_episodes == 0
        assert result.success_rate == 0.0
        assert result.failure_taxonomy == {}
        assert result.mean_duration_s == 0.0

    def test_basic_rates_and_taxonomy(self) -> None:
        episodes = [
            _ep(success=True),
            _ep(success=True),
            _ep(success=False, failure_mode="collision"),
            _ep(success=False, failure_mode="timeout"),
            _ep(success=False, failure_mode="collision"),
        ]
        result = evaluate(episodes)
        assert result.total_episodes == 5
        assert result.success_count == 2
        assert result.success_rate == 0.4
        assert result.failure_taxonomy == {"collision": 2, "timeout": 1}

    def test_per_task_breakdown(self) -> None:
        episodes = [
            _ep(task_id="a", success=True),
            _ep(task_id="a", success=False, failure_mode="collision"),
            _ep(task_id="b", success=True),
            _ep(task_id="b", success=True),
        ]
        result = evaluate(episodes)
        assert result.per_task_success == {"a": 0.5, "b": 1.0}

    def test_mean_duration(self) -> None:
        episodes = [_ep(duration=4.0), _ep(duration=8.0), _ep(duration=12.0)]
        assert evaluate(episodes).mean_duration_s == 8.0

    def test_to_dict_round_trips(self) -> None:
        result = evaluate([_ep()])
        data = result.to_dict()
        assert data["total_episodes"] == 1
        assert data["success_rate"] == 1.0
