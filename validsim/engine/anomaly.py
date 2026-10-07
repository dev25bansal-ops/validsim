"""Anomalous failure-mode spike detection across an ordered run history.

Each element of ``history`` is a compact per-run record carrying an ordered
failure-mode taxonomy plus the run size. The expected schema mirrors the
shape produced by :class:`validsim.engine.evaluation.EvaluationResult` and the
store summaries:

``{"run_id": str, "total_episodes": int, "failure_taxonomy": dict[str, int]}``

The most recent run (the last list element) is treated as the *current* run and
compared against the *baseline* formed by every preceding run. For each failure
mode we work in **rate** space (``count / total_episodes``) so runs of different
sizes are directly comparable. A mode is flagged when the current rate sits an
unusually large number of standard deviations above the historical expectation.

The standard-deviation estimate combines three sources of spread, which is what
makes the test robust to small baselines:

* the run-to-run dispersion of the baseline rates (``pstdev``);
* the uncertainty of the baseline mean, estimated with the existing
  :func:`validsim.engine.stats.bootstrap_ci` machinery ("where sensible" — a
  short/noisy baseline yields a wide bootstrap interval and therefore a wider,
  more conservative sigma);
* the intrinsic binomial sampling noise of the single current observation.

The input list is expected to be ordered oldest-first (matching the store's
:meth:`~validsim.store.memory.ValidationStore.history` ordering); this module
does not re-sort it.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from validsim.engine._coerce import as_int
from validsim.engine.stats import bootstrap_ci

__all__ = ["Anomaly", "detect_anomalies"]

Severity = Literal["warning", "critical"]

#: Minimum number of usable baseline runs required before we trust a spike call.
_MIN_BASELINE_RUNS = 3
#: Bootstrap resample count for the baseline-mean standard error.
_N_RESAMPLES = 500
#: Bootstrap interval coverage; the 95% z-multiplier is ``_Z_95``.
_CONFIDENCE = 0.95
_Z_95 = 1.96
#: Fixed RNG seed so results are fully deterministic across calls.
_SEED = 42
#: A z-score at or above ``z_threshold * _CRITICAL_MULTIPLIER`` is "critical".
_CRITICAL_MULTIPLIER = 2.0
#: Numerical floor below which sigma is treated as degenerate.
#:
#: NOTE (pre-existing, deliberately unchanged): this is an *absolute* floor
#: compared against a *rate-space* sigma. Sigma here is
#: ``sqrt(spread^2 + se^2 + p(1-p)/n)`` where ``p`` is a failure rate, so for a
#: very rare failure mode the binomial floor ``sqrt(p/n)`` drops below 1e-12
#: once ``p <~ 1e-6``, and the mode is skipped as "degenerate" even when the
#: z-score is enormous. Measured: a 1e-12 baseline rate spiking to 5e-10 yields
#: z = 499 yet reports nothing, while the same relative spike at a 1e-9 baseline
#: rate reports z = 1.6e7. Raising the floor, or making it relative to ``p``,
#: is a numerical-policy change rather than a correctness fix, so it is left
#: alone here and recorded instead. It is unreachable for realistic ValidSim
#: runs (an episode total of 1e6+ with a failure rate under 1e-6).
_EPS = 1e-12
#: z-score reported when the baseline has no variance but the current run does.
#: The true deviation is unbounded, so this is a display sentinel rather than a
#: computed value. It must stay finite -- ``math.inf`` serialises as a bare
#: ``Infinity`` token, which is not valid JSON and would corrupt every scorecard
#: export, so an unbounded z is never reported as infinity.
_UNBOUNDED_Z = 1e6
#: Largest denominator used in float division.
#:
#: ``float / int`` in CPython converts the int to a float first, which raises
#: ``OverflowError`` for a value beyond ``float`` range even though the
#: *quotient* would be a perfectly ordinary number (it underflows to ``0.0``).
#: Python ints are unbounded, so a single corrupt ``total_episodes`` of, say,
#: ``10 ** 400`` is otherwise enough to abort the whole report. Any
#: denominator this large contributes a binomial variance of exactly ``0.0``
#: to any double-precision sigma, so short-circuiting is exact, not an
#: approximation.
_MAX_SAFE_DENOMINATOR = 10 ** 300


@dataclass(frozen=True)
class Anomaly:
    """One statistically unusual failure-mode spike.

    Attributes:
        run_id: Identifier of the (current) run the spike was observed in.
        failure_mode: Name of the failure mode that spiked.
        observed: Current run's failure rate for this mode (``count / total``).
        expected: Baseline mean failure rate for this mode.
        z_score: Standardised deviation of ``observed`` from ``expected``.
        severity: ``"warning"`` or ``"critical"``.
    """

    run_id: str
    failure_mode: str
    observed: float
    expected: float
    z_score: float
    severity: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation of this anomaly."""
        return dataclasses.asdict(self)


def _run_id(run: dict[str, Any], index: int) -> str:
    """Return a run's id, falling back to a positional placeholder."""
    rid = run.get("run_id")
    return str(rid) if rid is not None else f"run-{index}"


def _total(run: dict[str, Any]) -> int:
    """Return a run's episode total (``total_episodes`` or ``episode_count``)."""
    value = run.get("total_episodes")
    if value is None:
        value = run.get("episode_count")
    return as_int(value, default=0)


def _taxonomy(run: dict[str, Any]) -> dict[str, int]:
    """Return a run's failure-mode taxonomy as a plain ``str -> int`` dict.

    Counts go through :func:`validsim.engine._coerce.as_int` so a malformed
    value (``None``, ``"abc"``, a float) degrades to ``0`` instead of raising --
    the same tolerant contract every other reader in this module honours.
    """
    tax = run.get("failure_taxonomy") or {}
    if not isinstance(tax, dict):
        return {}
    return {str(k): as_int(v, default=0) for k, v in tax.items()}


def _bootstrap_se(values: Sequence[float]) -> float:
    """Standard error of the mean of ``values`` via the bootstrap CI width."""
    if len(values) < 2:
        return 0.0
    low, high, _point = bootstrap_ci(
        list(values),
        n_resamples=_N_RESAMPLES,
        confidence=_CONFIDENCE,
        seed=_SEED,
    )
    return max(0.0, (high - low) / (2.0 * _Z_95))


def _baseline_rates(baseline: Sequence[dict[str, Any]], mode: str) -> list[float]:
    """Collect per-run rates for ``mode`` across baseline runs with episodes."""
    rates: list[float] = []
    for run in baseline:
        total = _total(run)
        if total <= 0:
            continue
        rates.append(_taxonomy(run).get(mode, 0) / total)
    return rates


def _classify(z_score: float, z_threshold: float) -> Severity:
    """Map a z-score (already ``>= z_threshold``) to a severity band."""
    if z_score >= z_threshold * _CRITICAL_MULTIPLIER:
        return "critical"
    return "warning"


def detect_anomalies(
    history: list[dict[str, Any]],
    z_threshold: float = 2.0,
) -> list[Anomaly]:
    """Detect failure-mode spikes in the latest run vs the historical baseline.

    Args:
        history: Per-run records ordered oldest-first. The last element is the
            current run; the rest form the baseline. See the module docstring
            for the expected per-run schema.
        z_threshold: Minimum standardised deviation to flag a spike. Higher is
            more conservative.

    Returns:
        A list of :class:`Anomaly` ordered by descending z-score (ties broken by
        failure-mode name). Empty when the history is empty or too short to
        establish a baseline (fewer than ``_MIN_BASELINE_RUNS`` usable runs),
        when the current run has no episodes, or when no mode exceeds the
        threshold.
    """
    if not history or len(history) <= _MIN_BASELINE_RUNS:
        return []

    current = history[-1]
    baseline = history[:-1]
    current_total = _total(current)
    if current_total <= 0:
        return []

    current_tax = _taxonomy(current)
    anomalies: list[Anomaly] = []
    for mode in sorted(current_tax):
        observed = current_tax[mode] / current_total
        rates = _baseline_rates(baseline, mode)
        if len(rates) < _MIN_BASELINE_RUNS:
            continue

        expected = statistics.fmean(rates)
        spread = statistics.pstdev(rates)
        se_mean = _bootstrap_se(rates)
        # ``expected`` is a rate derived from ``count / total_episodes``. If a
        # record is internally inconsistent (a failure-mode count exceeding the
        # run's own episode total -- possible with a truncated/merged history or
        # a hand-written record) the rate exceeds 1.0, ``expected * (1-expected)``
        # goes negative, and ``math.sqrt`` raises ValueError straight out of the
        # detector. The binomial term is a variance, so it is clamped at 0: an
        # impossible record must not crash the report, it must just carry no
        # extra sampling uncertainty.
        #
        # The division is guarded independently of the clamp above. Python
        # ints are arbitrary precision, so an absurd ``total_episodes`` parses
        # perfectly through ``as_int`` and then raises ``OverflowError`` on
        # ``float / huge_int`` -- a second, independent way for one corrupt
        # record to destroy the report. Python's own float semantics would
        # underflow this to 0.0, so short-circuiting the enormous denominator
        # reproduces that result without relying on it.
        binomial_var = (
            0.0
            if current_total > _MAX_SAFE_DENOMINATOR
            else max(0.0, expected * (1.0 - expected)) / current_total
        )
        sigma = math.sqrt(spread * spread + se_mean * se_mean + binomial_var)
        if sigma <= _EPS:
            # A flat baseline leaves z undefined, not absent. A brand-new
            # failure mode has the least history of any mode, so it lands
            # here with every baseline rate at zero -- and skipping it is
            # exactly backwards, because the first ever appearance of a
            # failure mode is the loudest signal this detector can emit.
            if observed > expected:
                anomalies.append(
                    Anomaly(
                        run_id=_run_id(current, len(history) - 1),
                        failure_mode=mode,
                        observed=round(observed, 6),
                        expected=round(expected, 6),
                        z_score=_UNBOUNDED_Z,
                        severity=_classify(_UNBOUNDED_Z, z_threshold),
                    )
                )
            continue

        z_score = (observed - expected) / sigma
        if z_score >= z_threshold:
            anomalies.append(
                Anomaly(
                    run_id=_run_id(current, len(history) - 1),
                    failure_mode=mode,
                    observed=round(observed, 6),
                    expected=round(expected, 6),
                    z_score=round(z_score, 4),
                    severity=_classify(z_score, z_threshold),
                )
            )

    anomalies.sort(key=lambda a: (-a.z_score, a.failure_mode))
    return anomalies
