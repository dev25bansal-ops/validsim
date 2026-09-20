"""Branch-coverage tests for engine edge cases not exercised elsewhere.

Covers:
- ``validsim/engine/evaluation.py``: failed episodes whose ``failure_mode``
  is empty (``None`` / ``""``) are excluded from the failure taxonomy.
- ``validsim/engine/regression.py``: the empty-run shortcut of
  ``_success_item`` (either run has zero episodes) and the ``before == 0``
  guard in ``_duration_item``.
- ``validsim/engine/stats.py``: parameter validation of
  ``two_proportion_bootstrap_test`` (``n_resamples`` / ``confidence``) and
  the single-element shortcut of ``_percentile``.
"""

from __future__ import annotations

from itertools import count

import pytest

from validsim.engine.evaluation import EvaluationResult, evaluate
from validsim.engine.regression import RegressionItem, compare
from validsim.engine.stats import _percentile, two_proportion_bootstrap_test
from validsim.sim.runner import EpisodeResult

SEED = 42

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


def _evaluation(
    total: int,
    success_count: int | None = None,
    mean_duration: float = 10.0,
) -> EvaluationResult:
    successes = total if success_count is None else success_count
    return EvaluationResult(
        total_episodes=total,
        success_count=successes,
        success_rate=(successes / total) if total else 0.0,
        mean_duration_s=mean_duration,
    )


def _item(report, metric: str) -> RegressionItem:
    return next(i for i in report.items if i.metric == metric)


class TestFailureTaxonomyFilter:
    """Failed episodes with an empty ``failure_mode`` stay out of the taxonomy."""

    def test_failed_episode_with_none_failure_mode_excluded(self) -> None:
        result = evaluate(
            [
                _ep(success=False, failure_mode=None),
                _ep(success=False, failure_mode="collision"),
            ]
        )
        assert result.total_episodes == 2
        assert result.success_count == 0
        assert result.failure_taxonomy == {"collision": 1}

    def test_failed_episode_with_empty_string_failure_mode_excluded(self) -> None:
        result = evaluate(
            [
                _ep(success=False, failure_mode=""),
                _ep(success=False, failure_mode="timeout"),
            ]
        )
        assert result.failure_taxonomy == {"timeout": 1}

    def test_only_unlabelled_failures_yield_empty_taxonomy(self) -> None:
        result = evaluate([_ep(success=False, failure_mode=None) for _ in range(3)])
        assert result.failure_taxonomy == {}
        assert result.success_rate == 0.0


class TestSuccessItemEmptyRun:
    """Either run having zero episodes skips the permutation test entirely."""

    def test_current_run_empty_skips_test(self) -> None:
        report = compare(_evaluation(0), _evaluation(2000, 1900), seed=SEED)
        item = _item(report, "success_rate")
        assert item.before == pytest.approx(0.95)
        assert item.after == 0.0
        assert item.delta == pytest.approx(-0.95)
        assert item.p_value is None
        assert not item.significant
        assert item.severity == "info"
        assert not report.has_regressions

    def test_baseline_run_empty_skips_test(self) -> None:
        report = compare(_evaluation(2000, 1900), _evaluation(0), seed=SEED)
        item = _item(report, "success_rate")
        assert item.before == 0.0
        assert item.after == pytest.approx(0.95)
        assert item.delta == pytest.approx(0.95)
        assert item.p_value is None
        assert not item.significant
        assert item.severity == "info"
        assert not report.has_regressions

    def test_both_runs_empty(self) -> None:
        report = compare(_evaluation(0), _evaluation(0), seed=SEED)
        item = _item(report, "success_rate")
        assert item.before == 0.0
        assert item.after == 0.0
        assert item.delta == 0.0
        assert item.p_value is None
        assert not item.significant
        assert item.severity == "info"


class TestDurationZeroBaselineGuard:
    """A zero baseline duration must not divide by zero nor flag a regression."""

    def test_zero_baseline_duration_is_info_not_critical(self) -> None:
        report = compare(
            _evaluation(50, 45, mean_duration=5.0),
            _evaluation(50, 45, mean_duration=0.0),
            seed=SEED,
        )
        item = _item(report, "mean_duration_s")
        assert item.before == 0.0
        assert item.after == 5.0
        assert item.delta == 5.0
        assert item.p_value is None
        assert item.severity == "info"
        assert not item.significant

    def test_same_increase_with_positive_baseline_is_flagged(self) -> None:
        # Contrast: the identical +5.0s delta against a positive baseline
        # (+50% relative) crosses the warning threshold, proving the
        # zero-baseline result comes from the guard, not the delta.
        report = compare(
            _evaluation(50, 45, mean_duration=15.0),
            _evaluation(50, 45, mean_duration=10.0),
            seed=SEED,
        )
        item = _item(report, "mean_duration_s")
        assert item.delta == 5.0
        assert item.severity == "warning"
        assert item.significant


class TestTwoProportionValidation:
    """Parameter validation of ``two_proportion_bootstrap_test``."""

    def test_rejects_too_few_resamples(self) -> None:
        samples = ([1.0, 0.0], [0.0, 1.0])
        for bad in (-5, 0, 1):
            with pytest.raises(ValueError, match="n_resamples must be >= 2"):
                two_proportion_bootstrap_test(*samples, n_resamples=bad)

    def test_rejects_confidence_outside_unit_interval(self) -> None:
        samples = ([1.0, 0.0], [0.0, 1.0])
        for bad in (0.0, -0.1, 1.0, 1.1, 2.0):
            with pytest.raises(ValueError, match="confidence must be within"):
                two_proportion_bootstrap_test(*samples, confidence=bad)


class TestPercentileSingleElement:
    """``_percentile`` short-circuits to the element for a length-1 input."""

    def test_single_element_returns_it_regardless_of_q(self) -> None:
        assert _percentile([3.14], 0.0) == 3.14
        assert _percentile([3.14], 0.37) == 3.14
        assert _percentile([3.14], 1.0) == 3.14

    def test_two_elements_interpolate_for_contrast(self) -> None:
        # The general path interpolates; the single-element path above
        # returns directly without touching the index math.
        assert _percentile([0.0, 10.0], 0.5) == pytest.approx(5.0)

    def test_empty_sequence_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _percentile([], 0.5)
