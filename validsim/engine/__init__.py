"""Validation engine: evaluation, safety, regression, statistics, scorecards."""

from __future__ import annotations

from validsim.engine.evaluation import EvaluationResult, evaluate
from validsim.engine.regression import RegressionItem, RegressionReport, compare
from validsim.engine.safety import SafetyResult, compute_safety
from validsim.engine.scorecard import Scorecard, build_scorecard
from validsim.engine.stats import bootstrap_ci, two_proportion_bootstrap_test

__all__ = [
    "EvaluationResult",
    "RegressionItem",
    "RegressionReport",
    "SafetyResult",
    "Scorecard",
    "bootstrap_ci",
    "build_scorecard",
    "compare",
    "compute_safety",
    "evaluate",
    "two_proportion_bootstrap_test",
]
