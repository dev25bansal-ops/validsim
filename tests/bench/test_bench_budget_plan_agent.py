"""The benchmark wall-clock budget must be a ceiling, not a suggestion.

Motivation
----------
``PER_BENCHMARK_BUDGET_S`` is documented as a per-benchmark wall-clock budget
("an expensive one is sampled the minimum number of times rather than blowing
the CI budget"). The implementation contradicted that: it computed
``max(MIN_ROUNDS, min(rounds, budget // single))``, which has **no ceiling**.
When a single call already cost more than the whole budget, ``budget // single``
is 0 and ``max(MIN_ROUNDS, 0)`` is still 3, so the benchmark ran 3 measured
calls *plus* its warmups -- a 5x overrun with no upper bound.

This was not theoretical. The 100k-episode bootstrap costs ~12s per call, so a
nominal 3s budget became ~60s for that one benchmark; the "fast" tier took many
minutes, no ``baseline.json`` was ever generated, and the compare gate was
permanently stuck at "cannot measure" -- the exact "a missing signal read as a
clean one" failure the suite exists to prevent.

What is asserted
----------------
``plan_rounds`` is pure (numbers in, numbers out), so the policy is pinned here
without a clock, a sleep, or a single measured benchmark:

* a cheap call keeps full statistical confidence (all rounds + all warmups);
* an expensive call never exceeds the budget by more than the one call it must
  take to be measured at all;
* warmups are sacrificed before the measured count drops below ``MIN_ROUNDS``;
* the measured count never reaches zero.
"""

from __future__ import annotations

import json

import pytest

from .conftest import (
    DEFAULT_ROUNDS,
    DEFAULT_WARMUP,
    MIN_ROUNDS,
    PER_BENCHMARK_BUDGET_S,
    plan_rounds,
)

BUDGET = PER_BENCHMARK_BUDGET_S


def _plan(single_s: float, *, rounds: int = DEFAULT_ROUNDS, warmup: int = DEFAULT_WARMUP):
    return plan_rounds(single_s, rounds, warmup, BUDGET)


class TestCheapBenchmarkKeepsFullConfidence:
    def test_a_fast_call_gets_every_round_and_warmup(self) -> None:
        warm, measured = _plan(0.001)
        assert (warm, measured) == (DEFAULT_WARMUP, DEFAULT_ROUNDS)

    def test_a_call_that_fits_comfortably_still_takes_min_rounds(self) -> None:
        # 0.1s x (2 warmup + 5 measured) = 0.7s, well inside a 3s budget.
        warm, measured = _plan(0.1)
        assert warm == DEFAULT_WARMUP
        assert measured == DEFAULT_ROUNDS


class TestExpensiveBenchmarkIsBounded:
    def test_a_call_larger_than_the_budget_runs_once(self) -> None:
        """12s per call against a 3s budget: measure it, do not multiply it."""
        warm, measured = _plan(12.0)
        assert (warm, measured) == (0, 1)

    def test_total_calls_never_exceed_the_affordable_count(self) -> None:
        """The old bug: total calls could be 5x the affordable count."""
        for single_s in (0.05, 0.2, 0.5, 0.9, 1.5, 2.9, 12.0):
            warm, measured = _plan(single_s)
            affordable = max(1, int(BUDGET / single_s))
            assert warm + measured <= affordable, (
                f"single={single_s}s planned {warm + measured} calls but only "
                f"{affordable} fit the {BUDGET}s budget"
            )

    def test_an_over_budget_call_is_still_measured(self) -> None:
        warm, measured = _plan(BUDGET * 4)
        assert measured >= 1

    def test_wall_clock_stays_near_the_budget(self) -> None:
        """Total planned time must not blow the budget beyond one call."""
        for single_s in (0.3, 0.7, 1.1, 2.5):
            warm, measured = _plan(single_s)
            planned_s = (warm + measured) * single_s
            assert planned_s <= BUDGET + single_s, (
                f"single={single_s}s planned {planned_s:.2f}s against a {BUDGET}s budget"
            )


class TestWarmupsAreSacrificedFirst:
    def test_min_rounds_survive_by_dropping_warmups(self) -> None:
        # 0.75s per call: only 4 calls fit the 3s budget. The default plan wants
        # 2 warmups + 5 measured = 7 calls (5.25s), which does not fit, so the
        # warmups give way first and MIN_ROUNDS measured samples are kept.
        warm, measured = _plan(0.75)
        assert measured >= MIN_ROUNDS
        assert warm < DEFAULT_WARMUP

    def test_warmups_drop_to_zero_before_measured_drops_below_min(self) -> None:
        # 0.9s per call: 3 calls fit the 3s budget -- exactly MIN_ROUNDS. No
        # room is left for a warmup, and the measured count stays at the floor.
        warm, measured = _plan(0.9)
        assert (warm, measured) == (0, MIN_ROUNDS)

    def test_measured_count_never_reaches_zero(self) -> None:
        for single_s in (0.0, 1e-9, 1.0, 100.0, 1e9):
            _, measured = _plan(single_s)
            assert measured >= 1, f"single={single_s}s produced zero measured samples"

    def test_rounds_cap_is_respected_when_the_budget_is_huge(self) -> None:
        warm, measured = _plan(1e-6)
        assert measured == DEFAULT_ROUNDS
        assert warm == DEFAULT_WARMUP


class TestDegenerateInputs:
    def test_zero_cost_falls_back_to_the_requested_plan(self) -> None:
        """A call that measures as zero seconds must not divide by zero."""
        warm, measured = _plan(0.0)
        assert (warm, measured) == (DEFAULT_WARMUP, DEFAULT_ROUNDS)

    def test_a_zero_cost_does_not_divide_by_zero_in_the_regression_gate(
        self, tmp_path, monkeypatch
    ) -> None:
        """BUG-003: a ``0.0`` baseline made the gate raise, not report.

        ``memstore_count_n*`` measures below timer resolution, so the committed
        baseline holds literal zeros. A later run that measured a real
        regression there reached ``delta / prior`` with ``prior == 0.0`` and
        died with ``ZeroDivisionError`` -- killing the whole job instead of
        reporting one regression. It must report.
        """
        from . import conftest as bench_conftest

        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps({"benchmarks": {"memstore_count_n1000": 0.0}}), encoding="utf-8"
        )
        monkeypatch.setattr(bench_conftest, "BASELINE_PATH", baseline)
        monkeypatch.setattr(
            bench_conftest, "_RESULTS", {"memstore_count_n1000": 500.0}
        )

        failures = bench_conftest.compare_to_baseline()  # must not raise

        assert len(failures) == 1
        assert "memstore_count_n1000" in failures[0]

    def test_a_zero_baseline_still_tolerates_sub_floor_noise(
        self, tmp_path, monkeypatch
    ) -> None:
        """With no ratio to normalise by, the absolute floor is the only guard."""
        from . import conftest as bench_conftest

        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps({"benchmarks": {"memstore_count_n1000": 0.0}}), encoding="utf-8"
        )
        monkeypatch.setattr(bench_conftest, "BASELINE_PATH", baseline)
        # Below ABSOLUTE_FLOOR_MS (20.0) -> noise, not a regression.
        monkeypatch.setattr(bench_conftest, "_RESULTS", {"memstore_count_n1000": 5.0})

        assert bench_conftest.compare_to_baseline() == []

    @pytest.mark.parametrize("budget", [0.0, -1.0])
    def test_a_non_positive_budget_still_measures_once(self, budget: float) -> None:
        warm, measured = plan_rounds(0.5, DEFAULT_ROUNDS, DEFAULT_WARMUP, budget)
        assert measured >= 1
        assert warm >= 0
