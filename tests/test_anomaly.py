"""Tests for failure-mode anomaly (spike) detection."""

from __future__ import annotations

import json

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
    return [
        _run(f"vrun-{i}", {"perception_miss": count}) for i in range(1, n + 1)
    ]


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
        _run(f"vrun-{i}", {"perception_miss": c})
        for i, c in enumerate(baseline_counts, start=1)
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
    baseline = [
        _run(f"vrun-{i}", {"perception_miss": 5, "planning_error": 5})
        for i in range(1, 8)
    ]
    history = baseline + [
        _run("vrun-8", {"perception_miss": 11, "planning_error": 50})
    ]
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
        _run(f"vrun-{i}", {"perception_miss": c})
        for i, c in enumerate(baseline_counts, start=1)
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
