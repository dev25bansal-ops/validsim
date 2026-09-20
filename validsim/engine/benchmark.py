"""Head-to-head benchmark between two composite scorecards.

``compare_scorecards`` takes two scorecard dicts (as produced by
:meth:`validsim.engine.scorecard.Scorecard.to_dict`) and returns a frozen
:class:`BenchmarkResult` describing, for each scored metric, the value carried
by each side, the ``a - b`` delta, and which side wins. The overall verdict is
decided by the ``composite`` metric alone.

All four compared metrics are higher-is-better (composite, success rate,
safety, robustness), so a positive delta always favours ``a``. Missing, ``None``
or non-numeric keys are treated as unavailable: the side that carries a real
value wins that metric, and a metric absent on both sides is a tie.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from validsim.engine._coerce import as_float

__all__ = ["BenchmarkResult", "MetricComparison", "compare_scorecards"]

#: Which side wins a single metric (also used for the overall verdict).
Side = Literal["a", "b", "tie"]

#: Benchmark metric label -> scorecard dict key (see ``Scorecard.to_dict``).
#: Insertion order fixes the comparison order: composite, success_rate,
#: safety, robustness.
_METRIC_KEYS: dict[str, str] = {
    "composite": "composite_score",
    "success_rate": "success_rate",
    "safety": "safety_score",
    "robustness": "robustness_score",
}

#: Decimal places kept on per-metric deltas (suppresses float representation
#: noise such as ``0.95 - 0.80 == 0.15000000000000002``).
_DELTA_PRECISION = 4


def _metric_value(card: Mapping[str, Any], key: str) -> float | None:
    """Return ``card[key]`` coerced to ``float``.

    Absent keys, ``None`` values and non-numeric values all collapse to
    ``None`` so callers can treat them uniformly as "unavailable".
    """
    return as_float(card.get(key), default=None)


def _delta(a: float | None, b: float | None) -> float | None:
    """``a - b`` rounded, or ``None`` when either side is unavailable."""
    if a is None or b is None:
        return None
    return round(a - b, _DELTA_PRECISION)


def _winner(a: float | None, b: float | None) -> Side:
    """Decide the winning side for one metric.

    A real value beats a missing one; among two present values the larger
    wins (higher is better); equal or both-missing values are a tie.
    """
    if a is None and b is None:
        return "tie"
    if a is None:
        return "b"
    if b is None:
        return "a"
    if a > b:
        return "a"
    if b > a:
        return "b"
    return "tie"


@dataclass(frozen=True)
class MetricComparison:
    """One metric compared between two scorecards.

    Attributes:
        metric: Benchmark metric label (``composite`` / ``success_rate`` /
            ``safety`` / ``robustness``).
        a_value: Value from scorecard ``a``, or ``None`` when unavailable.
        b_value: Value from scorecard ``b``, or ``None`` when unavailable.
        delta: ``a_value - b_value`` (positive favours ``a``), or ``None``
            when either side is unavailable.
        winner: Which side wins this metric — ``"a"``, ``"b"`` or ``"tie"``.
    """

    metric: str
    a_value: float | None
    b_value: float | None
    delta: float | None
    winner: Side


@dataclass(frozen=True)
class BenchmarkResult:
    """Head-to-head outcome of comparing two scorecards.

    Attributes:
        comparisons: One :class:`MetricComparison` per benchmark metric, in
            the fixed order composite, success_rate, safety, robustness.
        overall: Verdict decided by the ``composite`` metric — ``"a"``,
            ``"b"`` or ``"tie"``.
    """

    comparisons: tuple[MetricComparison, ...]
    overall: Side

    @property
    def deltas(self) -> dict[str, float | None]:
        """Map of metric label -> ``a - b`` delta (``None`` when unavailable)."""
        return {c.metric: c.delta for c in self.comparisons}

    @property
    def winners(self) -> dict[str, Side]:
        """Map of metric label -> winning side."""
        return {c.metric: c.winner for c in self.comparisons}

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view of the full comparison.

        Surfaces the per-metric deltas, per-metric winners and the overall
        verdict as flat maps alongside the detailed ``comparisons`` list.
        """
        return {
            "comparisons": [dataclasses.asdict(c) for c in self.comparisons],
            "deltas": self.deltas,
            "winners": self.winners,
            "overall": self.overall,
        }


def compare_scorecards(a: dict[str, Any], b: dict[str, Any]) -> BenchmarkResult:
    """Compare two scorecard dicts head-to-head.

    Args:
        a: First scorecard dict (e.g. ``Scorecard.to_dict()``).
        b: Second scorecard dict.

    Returns:
        A frozen :class:`BenchmarkResult` with one :class:`MetricComparison`
        per metric and an overall verdict decided by ``composite``. Missing
        or non-numeric metrics are handled defensively (see module docstring).
    """
    comparisons: list[MetricComparison] = []
    for metric, key in _METRIC_KEYS.items():
        av = _metric_value(a, key)
        bv = _metric_value(b, key)
        comparisons.append(
            MetricComparison(
                metric=metric,
                a_value=av,
                b_value=bv,
                delta=_delta(av, bv),
                winner=_winner(av, bv),
            )
        )
    overall = next(
        (c.winner for c in comparisons if c.metric == "composite"), "tie"
    )
    return BenchmarkResult(comparisons=tuple(comparisons), overall=overall)
