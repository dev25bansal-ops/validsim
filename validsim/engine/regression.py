"""Regression detection between a current run and a baseline run.

The success-rate metric is compared with a permutation (two-proportion
bootstrap) test so small-sample noise is not flagged as a regression.
Duration is compared descriptively against relative-change thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.stats import two_proportion_bootstrap_test

__all__ = ["RegressionItem", "RegressionReport", "compare"]

Severity = Literal["info", "warning", "critical"]

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

#: Success-rate drops (percentage points) that trigger each severity band.
_CRITICAL_DROP = 0.05
_WARNING_DROP = 0.02
#: Relative duration increases that trigger each severity band.
_DURATION_WARNING = 0.20
_DURATION_CRITICAL = 0.50


@dataclass(frozen=True)
class RegressionItem:
    """One metric compared between baseline and current runs.

    Attributes:
        metric: Metric name (e.g. ``"success_rate"``).
        before: Baseline value.
        after: Current value.
        delta: ``after - before`` (negative = worse for success rate).
        p_value: Test p-value estimate, or ``None`` for descriptive metrics.
        significant: Whether the change crosses its significance gate.
        severity: One of ``info`` / ``warning`` / ``critical``.
    """

    metric: str
    before: float
    after: float
    delta: float
    p_value: float | None
    significant: bool
    severity: Severity


@dataclass(frozen=True)
class RegressionReport:
    """Collection of per-metric regression items plus helpers."""

    items: list[RegressionItem] = field(default_factory=list)

    @property
    def significant_regressions(self) -> list[RegressionItem]:
        """Items flagged significant and worse than ``info`` severity."""
        return [i for i in self.items if i.significant and i.severity != "info"]

    @property
    def has_regressions(self) -> bool:
        """True when at least one significant regression was detected."""
        return bool(self.significant_regressions)

    @property
    def worst_severity(self) -> Severity:
        """Maximum severity across all items (``"info"`` when empty)."""
        if not self.items:
            return "info"
        return max((i.severity for i in self.items), key=lambda s: _SEVERITY_ORDER[s])  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this report."""
        return {
            "items": [asdict(i) for i in self.items],
            "significant_count": len(self.significant_regressions),
            "worst_severity": self.worst_severity,
        }


def _binary_sample(success_count: int, total: int) -> list[float]:
    """Reconstruct a 0/1 success sample from aggregate counts."""
    return [1.0] * success_count + [0.0] * (total - success_count)


def _success_item(current: EvaluationResult, baseline: EvaluationResult, seed: int) -> RegressionItem:
    """Compare success rates with the two-proportion permutation test."""
    if current.total_episodes == 0 or baseline.total_episodes == 0:
        delta = current.success_rate - baseline.success_rate
        return RegressionItem("success_rate", baseline.success_rate, current.success_rate,
                              delta, None, False, "info")
    delta, p_value, significant = two_proportion_bootstrap_test(
        _binary_sample(current.success_count, current.total_episodes),
        _binary_sample(baseline.success_count, baseline.total_episodes),
        seed=seed,
    )
    severity: Severity = "info"
    if significant and delta <= -_CRITICAL_DROP:
        severity = "critical"
    elif significant and delta <= -_WARNING_DROP:
        severity = "warning"
    return RegressionItem(
        metric="success_rate",
        before=baseline.success_rate,
        after=current.success_rate,
        delta=delta,
        p_value=p_value,
        significant=significant,
        severity=severity,
    )


def _duration_item(current: EvaluationResult, baseline: EvaluationResult) -> RegressionItem:
    """Compare mean durations descriptively (relative-change thresholds)."""
    before, after = baseline.mean_duration_s, current.mean_duration_s
    delta = after - before
    relative = delta / before if before > 0 else 0.0
    severity: Severity = "info"
    if relative > _DURATION_CRITICAL:
        severity = "critical"
    elif relative > _DURATION_WARNING:
        severity = "warning"
    return RegressionItem(
        metric="mean_duration_s",
        before=before,
        after=after,
        delta=delta,
        p_value=None,
        significant=severity != "info",
        severity=severity,
    )


def compare(
    current: EvaluationResult,
    baseline: EvaluationResult,
    seed: int = 42,
) -> RegressionReport:
    """Compare ``current`` against ``baseline`` across scored metrics.

    Args:
        current: Evaluation of the checkpoint under test.
        baseline: Evaluation of the previously approved run.
        seed: Seed for the permutation test.

    Returns:
        A :class:`RegressionReport` with one item per compared metric.
    """
    items: Sequence[RegressionItem] = (
        [_success_item(current, baseline, seed), _duration_item(current, baseline)]
    )
    return RegressionReport(items=list(items))
