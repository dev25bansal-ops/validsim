"""Tests for failure-mode anomaly (spike) detection."""

from __future__ import annotations

import json
import math
from decimal import Decimal

import pytest

from validsim.engine.anomaly import Anomaly, detect_anomalies


def _run(run_id: str, taxonomy: dict[str, int], total: int = 100) -> dict:
    """Build a per-run history record in the documented schema."""
    return {
        "run_id": run_id,
        "total_episodes": total,
        "failure_taxonomy": dict(taxonomy),
    }


def _stable_history(n: int = 8, count: int = 5) -> list[dict]:
    """``n`` identical runs each failing ``count``/total via one mode."""
    return [_run(f"vrun-{i}", {"perception_miss": count}) for i in range(1, n + 1)]


# --- Guard rails -----------------------------------------------------------


def test_empty_history_returns_empty() -> None:
    assert detect_anomalies([]) == []


def test_short_history_returns_empty() -> None:
    # Fewer than the minimum baseline runs -> no verdict is possible.
    history = _stable_history(3)
    assert detect_anomalies(history) == []


def test_current_run_without_episodes_returns_empty() -> None:
    history = _stable_history(7) + [_run("vrun-8", {"perception_miss": 50}, total=0)]
    assert detect_anomalies(history) == []


# --- Core behaviour --------------------------------------------------------


def test_stable_history_has_no_anomaly() -> None:
    # Last run identical to the baseline -> observed == expected -> z == 0.
    assert detect_anomalies(_stable_history(8)) == []


def test_big_spike_flagged_critical() -> None:
    # Baseline rate 0.05, current rate 0.50. With an identical baseline the
    # only spread is the current observation's binomial noise:
    #   sigma = sqrt(0.05 * 0.95 / 100) ~= 0.021794
    #   z     = (0.50 - 0.05) / sigma   ~= 20.65  -> well past the 2x band.
    history = _stable_history(8, count=5)[:-1] + [_run("vrun-8", {"perception_miss": 50})]
    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert isinstance(anomaly, Anomaly)
    assert anomaly.run_id == "vrun-8"
    assert anomaly.failure_mode == "perception_miss"
    assert anomaly.observed == 0.5
    assert anomaly.expected == 0.05
    assert anomaly.severity == "critical"
    assert anomaly.z_score > 4.0  # >= 2 * default z_threshold


def test_moderate_spike_flagged_warning() -> None:
    # Current rate 0.11 -> z ~= (0.11 - 0.05) / 0.021794 ~= 2.75, inside the
    # [threshold, 2*threshold) band -> "warning".
    history = _stable_history(8, count=5)[:-1] + [_run("vrun-8", {"perception_miss": 11})]
    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].severity == "warning"
    assert 2.0 <= anomalies[0].z_score < 4.0


def test_spike_over_varied_baseline_flagged_critical() -> None:
    # A noisy baseline exercises the dispersion + bootstrap-mean terms of sigma
    # while a large spike is still unambiguously critical.
    baseline_counts = [4, 5, 6, 5, 4, 6, 5, 5]
    history = [
        _run(f"vrun-{i}", {"perception_miss": c}) for i, c in enumerate(baseline_counts, start=1)
    ]
    history += [_run("vrun-9", {"perception_miss": 50})]
    anomalies = detect_anomalies(history)

    assert len(anomalies) == 1
    assert anomalies[0].severity == "critical"
    assert anomalies[0].z_score > 4.0


def test_decrease_is_not_flagged() -> None:
    # An improvement (rate drop) is not a "spike".
    history = _stable_history(8, count=50)[:-1] + [_run("vrun-8", {"perception_miss": 1})]
    assert detect_anomalies(history) == []


def test_higher_threshold_is_more_conservative() -> None:
    history = _stable_history(8, count=5)[:-1] + [_run("vrun-8", {"perception_miss": 11})]
    # z ~= 2.75: flagged at the default threshold, suppressed at 3.0.
    assert len(detect_anomalies(history, z_threshold=2.0)) == 1
    assert detect_anomalies(history, z_threshold=3.0) == []


def test_multiple_modes_sorted_by_z() -> None:
    baseline = [_run(f"vrun-{i}", {"perception_miss": 5, "planning_error": 5}) for i in range(1, 8)]
    history = baseline + [_run("vrun-8", {"perception_miss": 11, "planning_error": 50})]
    anomalies = detect_anomalies(history)

    assert [a.failure_mode for a in anomalies] == ["planning_error", "perception_miss"]
    zs = [a.z_score for a in anomalies]
    assert zs == sorted(zs, reverse=True)
    assert anomalies[0].severity == "critical"
    assert anomalies[1].severity == "warning"


# --- Determinism & serialization ------------------------------------------


def test_is_deterministic() -> None:
    baseline_counts = [4, 5, 6, 5, 4, 6, 5, 5]
    history = [
        _run(f"vrun-{i}", {"perception_miss": c}) for i, c in enumerate(baseline_counts, start=1)
    ] + [_run("vrun-9", {"perception_miss": 50})]

    first = detect_anomalies(history)
    second = detect_anomalies(history)
    assert first == second
    assert [a.to_dict() for a in first] == [a.to_dict() for a in second]


def test_anomaly_to_dict_is_json_serializable() -> None:
    history = _stable_history(8, count=5)[:-1] + [_run("vrun-8", {"perception_miss": 50})]
    anomaly = detect_anomalies(history)[0]

    data = anomaly.to_dict()
    assert data == {
        "run_id": "vrun-8",
        "failure_mode": "perception_miss",
        "observed": 0.5,
        "expected": 0.05,
        "z_score": round(anomaly.z_score, 4),
        "severity": "critical",
    }
    # Must round-trip through JSON without inf/NaN.
    json.dumps(data)


class TestImpossibleRatesDoNotCrash:
    """A count exceeding the run's own episode total must degrade, not raise.

    ``expected`` is a rate (``count / total_episodes``). An internally
    inconsistent record -- a failure-mode count larger than the run's episode
    total, which a truncated/merged history or a hand-written record can produce
    -- pushes the rate above 1.0, so ``expected * (1 - expected)`` goes negative
    and ``math.sqrt`` raised ``ValueError`` straight out of
    :func:`detect_anomalies`, breaking the "degrade gracefully, never raise
    mid-report" contract the rest of the engine relies on.
    """

    @pytest.mark.parametrize("count,total", [(6, 3), (10, 1), (1, 0), (99, 2)])
    def test_inconsistent_taxonomy_does_not_raise(self, count: int, total: int) -> None:
        history = [_run(f"vrun-b{i}", {"collision": count}, total=total) for i in range(3)]
        history.append(_run("vrun-cur", {"collision": count}, total=total))
        # The only requirement is that it returns rather than raising.
        assert isinstance(detect_anomalies(history), list)

    def test_legitimate_spike_is_still_detected(self) -> None:
        """Control: the clamp must not neuter real detection."""
        history = [_run(f"vrun-b{i}", {"collision": 1, "timeout": 1}) for i in range(3)]
        history.append(_run("vrun-cur", {"collision": 50, "timeout": 1}))
        anomalies = detect_anomalies(history)
        assert [a.failure_mode for a in anomalies] == ["collision"]
        assert anomalies[0].severity == "critical"


class TestOverflowingEpisodeTotalsDoNotCrash:
    """A non-integer ``total_episodes`` must degrade, not raise ``OverflowError``.

    ``_total`` routes through :func:`~validsim.engine._coerce.as_int`, which
    caught only ``TypeError`` / ``ValueError``. ``int(float('inf'))`` raises
    ``OverflowError`` -- neither of those -- so a single corrupt record with an
    infinite (or merely enormous) episode total raised straight out of
    :func:`detect_anomalies` and took down the whole anomaly report.

    This is reachable from real data, not just a synthetic literal:
    ``json.dumps(float('inf'))`` emits the bare token ``Infinity`` and
    ``json.loads("1e400")`` parses to ``inf``, so any store row or API payload
    carrying either one lands here.

    The sibling value NaN was already handled (``int(nan)`` raises
    ``ValueError``), which is what makes the gap a genuine asymmetry rather
    than an intentional one.
    """

    @pytest.mark.parametrize(
        "total", [math.inf, -math.inf, 10**400, Decimal("1e400"), "inf", "1e400"]
    )
    def test_overflowing_baseline_total_does_not_raise(self, total: object) -> None:
        history = [
            _run(f"vrun-b{i}", {"perception_miss": 5}, total=total)  # type: ignore[arg-type]
            for i in range(3)
        ]
        history.append(_run("vrun-cur", {"perception_miss": 50}))
        assert isinstance(detect_anomalies(history), list)

    @pytest.mark.parametrize("total", [math.inf, -math.inf, 10**400, "inf"])
    def test_overflowing_current_total_does_not_raise(self, total: object) -> None:
        """CONTROL: the current run's total takes a different code path.

        ``detect_anomalies`` checks ``_total(current) <= 0`` before doing any
        rate arithmetic, so this exercises a separate branch from the baseline
        sweep above -- both must degrade.
        """
        history = _stable_history(3)
        history.append(_run("vrun-cur", {"perception_miss": 50}, total=total))  # type: ignore[arg-type]
        assert isinstance(detect_anomalies(history), list)

    def test_control_int_totals_still_detect_the_spike(self) -> None:
        """The guard must not swallow ordinary integer history.

        Without this control, "returns a list" would be satisfied by a
        detector that never reports anything.
        """
        history = _stable_history(3) + [_run("vrun-cur", {"perception_miss": 50})]
        anomalies = detect_anomalies(history)
        assert [a.failure_mode for a in anomalies] == ["perception_miss"]
        assert anomalies[0].severity == "critical"

    def test_control_nan_total_already_degraded(self) -> None:
        """CONTROL: the sibling non-finite value was never broken.

        ``int(nan)`` raises ``ValueError``, which the original ``except``
        clause already caught. Pinning it shows the fix is a narrow widening of
        the tolerated set rather than a behavioural change.
        """
        history = [
            _run(f"vrun-b{i}", {"perception_miss": 5}, total=math.nan)  # type: ignore[arg-type]
            for i in range(3)
        ]
        history.append(_run("vrun-cur", {"perception_miss": 50}))
        assert isinstance(detect_anomalies(history), list)


class TestUnboundedDenominatorDoesNotCrash:
    """A huge *valid* int total must not crash the binomial-variance division.

    This is a genuinely different defect from ``TestOverflowingEpisodeTotals``:
    ``as_int(10 ** 400)`` succeeds, because Python ints are arbitrary
    precision, so the coercion layer is blameless. The crash comes one line
    further on, where ``binomial_var`` divides a float by that int -- CPython
    converts the int to a float first and raises ``OverflowError``, even though
    the quotient is a perfectly ordinary number (it underflows to ``0.0``).

    Pinning it separately matters because fixing only ``as_int`` leaves this
    path broken, and the two have different causes and different fixes.
    """

    def test_huge_int_total_in_the_current_run_does_not_raise(self) -> None:
        history = _stable_history(3)
        history.append(_run("vrun-cur", {"perception_miss": 50}, total=10**400))
        assert isinstance(detect_anomalies(history), list)

    def test_huge_int_total_in_the_baseline_does_not_raise(self) -> None:
        history = [_run(f"vrun-b{i}", {"perception_miss": 5}, total=10**400) for i in range(3)]
        history.append(_run("vrun-cur", {"perception_miss": 50}))
        assert isinstance(detect_anomalies(history), list)

    def test_control_ordinary_totals_still_detect_the_spike(self) -> None:
        """Without this, "returns a list" is satisfied by detecting nothing."""
        history = _stable_history(3) + [_run("vrun-cur", {"perception_miss": 50})]
        anomalies = detect_anomalies(history)
        assert [a.failure_mode for a in anomalies] == ["perception_miss"]
        assert anomalies[0].severity == "critical"

    def test_large_but_representable_total_still_divides_normally(self) -> None:
        """A total of 10**12 is legal and must still divide, not be skipped.

        It is far above any realistic episode count yet comfortably inside
        float range, so the short-circuit must not swallow it -- otherwise the
        guard would be a silent clamp on legitimate data. The counts are chosen
        so the baseline rate is 1e-9, well above the separate ``_EPS`` rate
        floor that this test is not about (see the module note on
        ``_EPS`` in ``anomaly.py``).
        """
        history = [_run(f"vrun-b{i}", {"perception_miss": 10**3}, total=10**12) for i in range(3)]
        history.append(_run("vrun-cur", {"perception_miss": 5 * 10**8}, total=10**12))
        anomalies = detect_anomalies(history)
        assert [a.failure_mode for a in anomalies] == ["perception_miss"]
        assert anomalies[0].z_score > 0.0


def test_first_ever_appearance_of_a_failure_mode_is_flagged() -> None:
    """A brand-new failure mode must be reported, not skipped.

    It has the least history of any mode, so every baseline rate for it is
    zero: expected, spread, standard error and the binomial term all collapse,
    sigma is 0, and the z-score is undefined rather than absent. Skipping
    that case means the single loudest signal this detector can emit -- a
    failure mode appearing for the first time, at scale -- is exactly the one
    thing it stays quiet about.
    """
    history = [_run(f"vrun-{i}", {"perception_miss": 5}) for i in range(1, 9)]
    history.append(_run("vrun-9", {"perception_miss": 5, "grasp_failure": 90}))

    anomalies = detect_anomalies(history)
    flagged = [a for a in anomalies if a.failure_mode == "grasp_failure"]
    assert len(flagged) == 1, f"unseen failure mode went unreported: {anomalies}"
    assert flagged[0].severity == "critical"
    assert flagged[0].expected == 0.0
    assert flagged[0].observed == 0.9

    # The unbounded z-score must stay finite: math.inf serialises as a bare
    # Infinity token, which is not valid JSON and would corrupt every export.
    json.dumps([a.__dict__ for a in anomalies])


def test_flat_baseline_on_its_own_reports_nothing() -> None:
    """The zero-variance path must not manufacture anomalies when nothing changed."""
    history = [_run(f"vrun-{i}", {"perception_miss": 5}) for i in range(1, 9)]
    history.append(_run("vrun-9", {"perception_miss": 5}))

    assert detect_anomalies(history) == []
