"""Tests for baseline-vs-current regression detection."""

from __future__ import annotations

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, compare

SEED = 42


def _evaluation(
    success_rate: float, total: int = 2000, mean_duration: float = 10.0
) -> EvaluationResult:
    count = int(total * success_rate)
    return EvaluationResult(
        total_episodes=total,
        success_count=count,
        success_rate=count / total,
        per_task_success={"t": count / total},
        failure_taxonomy={},
        mean_duration_s=mean_duration,
    )


def _success_item(report) -> RegressionItem:
    return next(i for i in report.items if i.metric == "success_rate")


class TestSuccessRegression:
    def test_injected_large_drop_is_critical(self) -> None:
        report = compare(_evaluation(0.85), _evaluation(0.95), seed=SEED)
        item = _success_item(report)
        assert item.delta < -0.05
        assert item.significant
        assert item.severity == "critical"
        assert report.has_regressions
        assert report.worst_severity == "critical"

    def test_medium_drop_is_warning(self) -> None:
        report = compare(_evaluation(0.92), _evaluation(0.95), seed=SEED)
        item = _success_item(report)
        assert item.significant
        assert item.severity == "warning"

    def test_identical_runs_show_no_regression(self) -> None:
        report = compare(_evaluation(0.9), _evaluation(0.9), seed=SEED)
        item = _success_item(report)
        assert not item.significant
        assert item.severity == "info"
        assert not report.has_regressions

    def test_improvement_is_info(self) -> None:
        report = compare(_evaluation(0.99), _evaluation(0.90), seed=SEED)
        item = _success_item(report)
        assert item.delta > 0
        assert item.severity == "info"
        assert not report.has_regressions

    def test_small_noise_not_flagged(self) -> None:
        report = compare(_evaluation(0.945), _evaluation(0.95), seed=SEED)
        assert _success_item(report).severity == "info"


class TestDurationRegression:
    def test_slowdown_flagged(self) -> None:
        report = compare(_evaluation(0.9, mean_duration=16.0), _evaluation(0.9, mean_duration=10.0))
        item = next(i for i in report.items if i.metric == "mean_duration_s")
        assert item.delta == 6.0
        assert item.severity == "critical"  # +60% > 50% threshold
        assert item.p_value is None

    def test_small_slowdown_is_info(self) -> None:
        report = compare(_evaluation(0.9, mean_duration=10.5), _evaluation(0.9, mean_duration=10.0))
        item = next(i for i in report.items if i.metric == "mean_duration_s")
        assert item.severity == "info"


class TestDurationZeroBaselineAbsoluteThreshold:
    """A zero baseline is graded by an ABSOLUTE threshold, not a relative one.

    ``before == 0.0`` leaves ``delta / before`` undefined, so the 20%/50% bands
    cannot scale the comparison -- the original ``else 0.0`` collapsed it and
    reported an unbounded slowdown as ``"info"``. The positive-baseline path
    therefore stays purely relative, and the zero-baseline path escalates only
    past an absolute rise that indicates a hang or a pathology.

    A modest rise over a duration-less baseline stays ``"info"``: ``0.0`` is
    also the ``EvaluationResult`` default, so a 0 -> few-seconds transition is
    ordinary for a newly populated baseline. ``tests/test_engine_branches.py::
    TestDurationZeroBaselineGuard`` pins that contract (0 -> 5.0 is silent);
    asserting otherwise here previously demanded 0 -> 1.0 be flagged, which is
    unsatisfiable against a 0 -> 5.0 "no signal" expectation in the same repo.
    """

    @staticmethod
    def _duration(report) -> RegressionItem:
        return next(i for i in report.items if i.metric == "mean_duration_s")

    @pytest.mark.parametrize("after", [100.0, 1e3, 1e6])
    def test_material_slowdown_from_a_zero_baseline_is_critical(self, after: float) -> None:
        item = self._duration(compare(_evaluation(0.9, mean_duration=after),
                                      _evaluation(0.9, mean_duration=0.0)))
        assert item.delta > 0
        assert item.severity == "critical"
        assert item.significant

    @pytest.mark.parametrize("after", [1e-9, 0.5, 5.0, 60.0])
    def test_benign_rise_from_a_zero_baseline_stays_info(self, after: float) -> None:
        item = self._duration(compare(_evaluation(0.9, mean_duration=after),
                                      _evaluation(0.9, mean_duration=0.0)))
        assert item.delta > 0
        assert item.severity == "info"
        assert not item.significant

    def test_zero_to_zero_stays_info(self) -> None:
        """CONTROL: 0 -> 0 is genuinely no change and must not be flagged."""
        item = self._duration(compare(_evaluation(0.9, mean_duration=0.0),
                                      _evaluation(0.9, mean_duration=0.0)))
        assert item.delta == 0.0
        assert item.severity == "info"
        assert not item.significant

    def test_a_shrink_toward_zero_is_not_a_regression(self) -> None:
        """CONTROL: going *faster* is an improvement, never a slowdown.

        ``delta`` is negative here, so an unguarded "before == 0 means
        critical" rule would wrongly flag a 100s -> 0s improvement.
        """
        item = self._duration(compare(_evaluation(0.9, mean_duration=0.0),
                                      _evaluation(0.9, mean_duration=100.0)))
        assert item.delta < 0
        assert item.severity == "info"
        assert not item.significant

    def test_control_normal_baseline_still_uses_relative_thresholds(self) -> None:
        """The non-zero path must keep its existing 20% / 50% bands."""
        for before, after, expected in (
            (10.0, 11.0, "info"),      # +10% -> below warning
            (10.0, 15.0, "warning"),   # +50% -> warning band
            (10.0, 20.0, "critical"),  # +100% -> critical
        ):
            item = self._duration(compare(_evaluation(0.9, mean_duration=after),
                                          _evaluation(0.9, mean_duration=before)))
            assert item.severity == expected, (before, after, item.severity)


class TestReportShape:
    def test_to_dict_serializable(self) -> None:
        report = compare(_evaluation(0.85), _evaluation(0.95), seed=SEED)
        data = report.to_dict()
        assert data["worst_severity"] == "critical"
        assert data["significant_count"] >= 1
        assert all(
            {"metric", "before", "after", "delta", "p_value", "significant", "severity"} <= set(i)
            for i in data["items"]
        )

    def test_empty_report_defaults(self) -> None:
        from validsim.engine.regression import RegressionReport

        report = RegressionReport(items=[])
        assert report.worst_severity == "info"
        assert not report.has_regressions
