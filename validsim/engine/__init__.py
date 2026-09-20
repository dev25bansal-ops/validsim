"""Validation engine: evaluation, safety, regression, statistics, scorecards."""

from __future__ import annotations

from validsim.engine.anomaly import Anomaly, detect_anomalies
from validsim.engine.benchmark import (
    BenchmarkResult,
    MetricComparison,
    compare_scorecards,
)
from validsim.engine.evaluation import EvaluationResult, evaluate
from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
from validsim.engine.pdf import render_scorecard_pdf, scorecard_pdf_bytes
from validsim.engine.regression import RegressionItem, RegressionReport, compare
from validsim.engine.safety import SafetyResult, compute_safety
from validsim.engine.scorecard import Scorecard, build_scorecard
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test
from validsim.engine.trends import TrendSummary, compute_trends, failure_mode_trends

__all__ = [
    "Anomaly",
    "BenchmarkResult",
    "EvaluationResult",
    "MetricComparison",
    "RegressionItem",
    "RegressionReport",
    "SafetyResult",
    "Scorecard",
    "TrendSummary",
    "bootstrap_ci",
    "build_scorecard",
    "compare",
    "compare_scorecards",
    "compute_safety",
    "compute_trends",
    "detect_anomalies",
    "evaluate",
    "failure_mode_trends",
    "render_scorecard_pdf",
    "scorecard_pdf_bytes",
    "scorecard_to_html",
    "scorecard_to_markdown",
    "two_proportion_bootstrap_test",
]
