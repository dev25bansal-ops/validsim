"""Tests for episode aggregation into EvaluationResult."""

from __future__ import annotations

import json
import math
from itertools import count

from validsim.engine.evaluation import evaluate
from validsim.engine.regression import compare
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


class TestNonFiniteDurations:
    """A backend that fails to measure must not poison the run's aggregates.

    ``duration_s`` is whatever the ``SimulationBackend`` reports; nothing in
    :class:`~validsim.sim.runner.EpisodeResult` constrains it to be finite.
    These tests pin the three properties downstream consumers depend on:
    the mean stays finite, the recorded shape stays JSON-strict, and a
    poisoned run cannot break a later baseline comparison.
    """

    def test_nan_duration_excluded_from_mean(self) -> None:
        # Only the two real readings count; the NaN one is a failed reading.
        episodes = [
            _ep(duration=8.0),
            _ep(duration=float("nan")),
            _ep(duration=12.0),
        ]
        result = evaluate(episodes)
        assert math.isfinite(result.mean_duration_s)
        assert result.mean_duration_s == 10.0

    def test_infinite_durations_excluded_from_mean(self) -> None:
        episodes = [
            _ep(duration=6.0),
            _ep(duration=float("inf")),
            _ep(duration=float("-inf")),
            _ep(duration=14.0),
        ]
        result = evaluate(episodes)
        assert result.mean_duration_s == 10.0

    def test_all_non_finite_records_no_duration(self) -> None:
        # Nothing measurable -> 0.0, the documented "no duration recorded"
        # shape that regression._duration_item already special-cases.
        episodes = [_ep(duration=float("nan")), _ep(duration=float("inf"))]
        result = evaluate(episodes)
        assert result.mean_duration_s == 0.0

    def test_non_finite_duration_does_not_disturb_other_metrics(self) -> None:
        # Success/failure accounting is independent of duration readings.
        episodes = [
            _ep(success=True),
            _ep(success=False, failure_mode="collision"),
            _ep(success=True, duration=float("nan")),
        ]
        result = evaluate(episodes)
        assert result.total_episodes == 3
        assert result.success_count == 2
        assert result.success_rate == 2 / 3
        assert result.failure_taxonomy == {"collision": 1}

    def test_output_is_strict_json(self) -> None:
        # A NaN token is a Python extension, not JSON: strict parsers reject
        # it, and the store writes this dict verbatim into evaluation_json.
        episodes = [_ep(duration=float("nan")), _ep(duration=4.0)]
        payload = evaluate(episodes).to_dict()
        json.dumps(payload, allow_nan=False)  # raises on any NaN/inf

    def test_poisoned_run_cannot_break_a_baseline_comparison(self) -> None:
        # Regression compares this run's mean against a healthy baseline's;
        # a NaN before/after would emit NaN tokens into the report.
        poisoned = evaluate([_ep(duration=float("nan"))])
        healthy = evaluate([_ep(duration=5.0)])
        for current, baseline in ((poisoned, healthy), (healthy, poisoned)):
            report = compare(current, baseline, seed=7)
            json.dumps(report.to_dict(), allow_nan=False)
