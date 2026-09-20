"""Tests for multi-run trend metrics."""

from __future__ import annotations

import json

from validsim.engine.trends import TrendSummary, compute_trends


def _run(
    run_id: str,
    composite: float,
    decision: str = "APPROVE",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "run_id": run_id,
        "checkpoint_id": "ckpt-1",
        "created_at": created_at,
        "composite_score": composite,
        "deploy_decision": decision,
    }


def test_empty_history_returns_neutral_defaults() -> None:
    summary = compute_trends([])
    assert summary == TrendSummary(
        n_runs=0,
        latest_composite=0.0,
        delta_last=0.0,
        moving_average_3=0.0,
        approval_rate_10=0.0,
        improving=False,
        volatile=False,
    )


def test_single_run_returns_neutral_signals() -> None:
    summary = compute_trends([_run("vrun-1", 88.0)])
    assert summary.n_runs == 1
    assert summary.latest_composite == 88.0
    assert summary.delta_last == 0.0
    assert summary.moving_average_3 == 88.0
    assert summary.approval_rate_10 == 1.0
    assert summary.improving is False
    assert summary.volatile is False


def test_monotonic_improving() -> None:
    history = [
        _run(f"vrun-{i}", score)
        for i, score in enumerate([80.0, 82.0, 84.0, 86.0, 88.0], start=1)
    ]
    summary = compute_trends(history)
    assert summary.improving is True
    # Small steady climbs stay under the volatility threshold.
    assert summary.volatile is False


def test_volatile() -> None:
    history = [
        _run(f"vrun-{i}", score)
        for i, score in enumerate([100.0, 60.0, 90.0, 70.0, 80.0], start=1)
    ]
    summary = compute_trends(history)
    assert summary.volatile is True
    assert summary.improving is False


def test_delta_last() -> None:
    summary = compute_trends([_run("vrun-1", 80.0), _run("vrun-2", 95.0)])
    assert summary.latest_composite == 95.0
    assert summary.delta_last == 15.0


def test_approval_rate_uses_last_10() -> None:
    decisions = ["APPROVE", "BLOCK", "BLOCK", "APPROVE", "BLOCK",
                 "APPROVE", "BLOCK", "BLOCK", "APPROVE", "BLOCK"]
    history = [_run(f"vrun-{i}", 90.0, d) for i, d in enumerate(decisions, start=1)]
    summary = compute_trends(history)
    # 4 APPROVE out of 10.
    assert summary.approval_rate_10 == 0.4

    # An early APPROVE outside the last-10 window must not be counted.
    history = [_run("vrun-earlier", 90.0, "APPROVE")] + history
    assert compute_trends(history).approval_rate_10 == 0.4


def test_moving_average_3() -> None:
    history = [
        _run("vrun-1", 70.0),
        _run("vrun-2", 80.0),
        _run("vrun-3", 90.0),
    ]
    summary = compute_trends(history)
    assert summary.moving_average_3 == 80.0


def test_to_dict() -> None:
    history = [
        _run("vrun-1", 80.0),
        _run("vrun-2", 82.0),
        _run("vrun-3", 84.0, "BLOCK"),
    ]
    data = compute_trends(history).to_dict()
    assert data == {
        "n_runs": 3,
        "latest_composite": 84.0,
        "delta_last": 2.0,
        "moving_average_3": 82.0,
        "approval_rate_10": round(2 / 3, 4),
        "improving": True,
        "volatile": False,
    }
    # The result must be JSON serializable.
    json.dumps(data)