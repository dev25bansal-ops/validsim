"""Multi-run trend metrics from an ordered list of run summaries.

Each item in ``history`` is a compact, :meth:`~validsim.store.memory.StoredRun.summary`
style dict carrying at least ``composite_score``, ``deploy_decision``,
``created_at``, ``checkpoint_id`` and ``run_id``. The list is expected to be
in run order (oldest first), matching the store's
:meth:`~validsim.store.memory.ValidationStore.history` ordering; this module
does not re-sort the input.

The composite score is the same 0-100 value produced by the scorecard, so trend
metrics are directly comparable across runs of a checkpoint or task.
"""

from __future__ import annotations

import dataclasses
import statistics
from dataclasses import dataclass
from typing import Any, Literal

from validsim.engine._coerce import as_float, as_int

__all__ = ["TrendSummary", "compute_trends", "failure_mode_trends"]

#: Most-recent window used for the improving / volatility signals.
_WINDOW = 5
#: Moving-average window (number of most recent composite scores).
_MOVING_AVG_WINDOW = 3
#: Approval-rate window (number of most recent runs).
_APPROVAL_WINDOW = 10
#: Volatility is flagged when the recent window's std-dev exceeds this.
_VOLATILITY_THRESHOLD = 5.0
#: Decimal places kept on failure-mode rates and deltas.
_RATE_PRECISION = 6
#: A rounded delta within this band of zero is reported as ``"flat"``. This is
#: far below any meaningful rate change yet comfortably above double-precision
#: division noise, so it neither hides real trends nor mislabels rounding.
_FLAT_TOLERANCE = 1e-9

#: Direction label for a per-failure-mode rate trend.
Direction = Literal["rising", "falling", "flat"]


def _composite(run: dict[str, Any]) -> float:
    """Return a run's composite score coerced to ``float``.

    Delegates the numeric parsing to :func:`as_float`, so a stray ``None`` or
    non-numeric value degrades to ``0.0`` instead of raising; a genuinely
    absent ``composite_score`` key still raises ``KeyError`` as before (the
    documented run-summary contract guarantees the key is present).
    """
    return as_float(run["composite_score"], default=0.0)


@dataclass(frozen=True)
class TrendSummary:
    """Trend signals derived from an ordered run history.

    Attributes:
        n_runs: Number of runs that contributed to these metrics.
        latest_composite: Composite score of the most recent run.
        delta_last: ``latest_composite - previous_composite`` (0 for <2 runs).
        moving_average_3: Mean of the last 3 composite scores.
        approval_rate_10: Fraction of the last 10 runs marked ``APPROVE``.
        improving: ``True`` when the last 5 composite scores are monotonic
            non-decreasing.
        volatile: ``True`` when the population std-dev of the last 5 composite
            scores exceeds ``_VOLATILITY_THRESHOLD`` (5.0).
    """

    n_runs: int
    latest_composite: float
    delta_last: float
    moving_average_3: float
    approval_rate_10: float
    improving: bool
    volatile: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict of all trend fields."""
        return dataclasses.asdict(self)


def compute_trends(history: list[dict[str, Any]]) -> TrendSummary:
    """Compute multi-run trend metrics for an ordered run history.

    Args:
        history: Run summaries ordered oldest-first.

    Returns:
        A :class:`TrendSummary`. Empty and single-run histories return neutral
        defaults (zero-valued floats and ``False`` booleans) rather than
        raising.
    """
    n_runs = len(history)
    if n_runs == 0:
        return TrendSummary(
            n_runs=0,
            latest_composite=0.0,
            delta_last=0.0,
            moving_average_3=0.0,
            approval_rate_10=0.0,
            improving=False,
            volatile=False,
        )

    scores = [_composite(run) for run in history]
    latest = scores[-1]
    delta = latest - scores[-2] if n_runs >= 2 else 0.0

    moving_average = round(statistics.mean(scores[-_MOVING_AVG_WINDOW:]), 4)

    recent_decisions = [run["deploy_decision"] for run in history[-_APPROVAL_WINDOW:]]
    approval_rate = round(recent_decisions.count("APPROVE") / len(recent_decisions), 4)

    window_scores = scores[-_WINDOW:]
    improving = len(window_scores) >= 2 and all(
        window_scores[i] <= window_scores[i + 1]
        for i in range(len(window_scores) - 1)
    )
    volatile = (
        len(window_scores) >= 2
        and statistics.pstdev(window_scores) > _VOLATILITY_THRESHOLD
    )

    return TrendSummary(
        n_runs=n_runs,
        latest_composite=latest,
        delta_last=round(delta, 4),
        moving_average_3=moving_average,
        approval_rate_10=approval_rate,
        improving=improving,
        volatile=volatile,
    )


def _episode_total(run: dict[str, Any]) -> int:
    """Return a run's episode count (``episode_count`` or ``total_episodes``).

    The store summary carries ``episode_count``; :class:`EvaluationResult` uses
    ``total_episodes``. Accepting either keeps this helper compatible with both
    record shapes (mirroring :mod:`validsim.engine.anomaly`). Non-positive or
    unparseable values degrade to ``0`` so callers can skip the run.
    """
    value = run.get("episode_count")
    if value is None:
        value = run.get("total_episodes")
    return as_int(value, default=0)


def _failure_taxonomy(run: dict[str, Any]) -> dict[str, int]:
    """Return a run's failure-mode taxonomy as a plain ``str -> int`` dict.

    Missing, ``None`` or non-dict taxonomies degrade to ``{}``; individual counts
    are coerced with :func:`as_int` so a stray value never raises mid-report.
    """
    tax = run.get("failure_taxonomy") or {}
    if not isinstance(tax, dict):
        return {}
    return {str(k): as_int(v, default=0) for k, v in tax.items()}


def _direction(delta: float) -> Direction:
    """Map a rounded rate delta to a ``rising`` / ``falling`` / ``flat`` label."""
    if delta > _FLAT_TOLERANCE:
        return "rising"
    if delta < -_FLAT_TOLERANCE:
        return "falling"
    return "flat"


def failure_mode_trends(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute per-failure-mode rate trends across an ordered run history.

    Rates (``count / episode_count``) are compared between the earliest and the
    latest run that actually has episodes, so runs of different sizes stay
    directly comparable and empty/zero-episode runs never cause a divide-by-zero.
    A failure mode is reported only when it carries a non-zero count in at least
    one usable run; modes absent everywhere are ignored.

    Args:
        history: Run summaries ordered oldest-first (matching the store's
            :meth:`~validsim.store.memory.ValidationStore.history` ordering). Each
            run may optionally carry ``failure_taxonomy`` (``dict[str, int]``) and
            an episode count (``episode_count`` or ``total_episodes``). This module
            does not re-sort the input.

    Returns:
        A mapping keyed by failure-mode name (sorted) to a dict with:

        * ``first_rate``: the mode's rate in the first usable run (0.0 if absent);
        * ``last_rate``: the mode's rate in the last usable run (0.0 if absent);
        * ``delta``: ``last_rate - first_rate``;
        * ``direction``: ``"rising"``, ``"falling"`` or ``"flat"``.

        An empty history, or one with no run carrying episodes, yields ``{}``.
    """
    if not history:
        return {}

    usable = [run for run in history if _episode_total(run) > 0]
    if not usable:
        return {}

    first_run, last_run = usable[0], usable[-1]
    first_total = _episode_total(first_run)
    last_total = _episode_total(last_run)
    first_tax = _failure_taxonomy(first_run)
    last_tax = _failure_taxonomy(last_run)

    # Modes seen with a non-zero count anywhere in the usable window.
    modes = {
        mode
        for run in usable
        for mode, count in _failure_taxonomy(run).items()
        if count > 0
    }

    trends: dict[str, dict[str, Any]] = {}
    for mode in sorted(modes):
        first_rate = first_tax.get(mode, 0) / first_total
        last_rate = last_tax.get(mode, 0) / last_total
        delta = round(last_rate - first_rate, _RATE_PRECISION)
        trends[mode] = {
            "first_rate": round(first_rate, _RATE_PRECISION),
            "last_rate": round(last_rate, _RATE_PRECISION),
            "delta": delta,
            "direction": _direction(delta),
        }
    return trends