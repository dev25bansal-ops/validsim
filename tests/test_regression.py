"""Tests for baseline-vs-current regression detection."""

from __future__ import annotations

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, compare

SEED = 42


def _evaluation(success_rate: float, total: int = 2000, mean_duration: float = 10.0) -> EvaluationResult:
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


class TestReportShape:
    def test_to_dict_serializable(self) -> None:
        report = compare(_evaluation(0.85), _evaluation(0.95), seed=SEED)
        data = report.to_dict()
        assert data["worst_severity"] == "critical"
        assert data["significant_count"] >= 1
        assert all({"metric", "before", "after", "delta", "p_value", "significant", "severity"} <= set(i) for i in data["items"])

    def test_empty_report_defaults(self) -> None:
        from validsim.engine.regression import RegressionReport

        report = RegressionReport(items=[])
        assert report.worst_severity == "info"
        assert not report.has_regressions
