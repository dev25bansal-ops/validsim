"""Tests for head-to-head scorecard benchmarking."""

from __future__ import annotations

import dataclasses
import json

import pytest

from validsim.engine.benchmark import (
    BenchmarkResult,
    MetricComparison,
    compare_scorecards,
)


def _card(
    composite: float | None = 90.0,
    success_rate: float | None = 0.9,
    safety: float | None = 80.0,
    robustness: float | None = 100.0,
) -> dict:
    """Build a minimal scorecard dict, omitting any metric passed as ``None``."""
    card: dict = {}
    if composite is not None:
        card["composite_score"] = composite
    if success_rate is not None:
        card["success_rate"] = success_rate
    if safety is not None:
        card["safety_score"] = safety
    if robustness is not None:
        card["robustness_score"] = robustness
    return card


def _cmp(result: BenchmarkResult, metric: str) -> MetricComparison:
    return next(c for c in result.comparisons if c.metric == metric)


def test_a_wins_every_metric() -> None:
    a = _card(composite=95.0, success_rate=0.95, safety=90.0, robustness=100.0)
    b = _card(composite=80.0, success_rate=0.80, safety=70.0, robustness=90.0)
    result = compare_scorecards(a, b)
    assert result.overall == "a"
    assert set(result.winners.values()) == {"a"}
    assert result.deltas["composite"] == 15.0
    assert result.deltas["success_rate"] == 0.15  # float noise rounded away
    assert result.deltas["safety"] == 20.0
    assert result.deltas["robustness"] == 10.0


def test_b_wins() -> None:
    a = _card(composite=70.0)
    b = _card(composite=88.0)
    result = compare_scorecards(a, b)
    assert result.overall == "b"
    assert result.winners["composite"] == "b"
    assert result.deltas["composite"] == -18.0


def test_tie_identical_cards() -> None:
    card = _card(composite=85.0, success_rate=0.85, safety=80.0, robustness=90.0)
    result = compare_scorecards(card, dict(card))
    assert result.overall == "tie"
    assert all(w == "tie" for w in result.winners.values())
    assert all(d == 0.0 for d in result.deltas.values())


def test_overall_decided_by_composite_only() -> None:
    # b wins success_rate/safety/robustness but a wins composite -> overall a.
    a = _card(composite=90.0, success_rate=0.5, safety=10.0, robustness=10.0)
    b = _card(composite=80.0, success_rate=0.9, safety=99.0, robustness=99.0)
    result = compare_scorecards(a, b)
    assert result.overall == "a"
    assert result.winners["composite"] == "a"
    assert result.winners["success_rate"] == "b"
    assert result.winners["safety"] == "b"


def test_missing_keys_defensive() -> None:
    a = {"composite_score": 90.0}  # only composite present
    b = {"composite_score": 80.0, "safety_score": 50.0}
    result = compare_scorecards(a, b)
    # success_rate and robustness absent on both sides -> tie, delta None.
    assert result.winners["success_rate"] == "tie"
    assert result.deltas["success_rate"] is None
    assert result.winners["robustness"] == "tie"
    assert result.deltas["robustness"] is None
    # safety present only on b -> b wins that metric, delta None.
    assert result.winners["safety"] == "b"
    assert result.deltas["safety"] is None
    assert result.overall == "a"


def test_none_values_treated_as_missing() -> None:
    a = {"composite_score": None, "success_rate": 0.5}
    b = {"composite_score": 70.0, "success_rate": 0.5}
    result = compare_scorecards(a, b)
    assert result.winners["composite"] == "b"  # a's composite is None
    assert result.deltas["composite"] is None
    assert result.overall == "b"
    # success_rate present and equal on both -> tie with a zero delta.
    assert result.winners["success_rate"] == "tie"
    assert result.deltas["success_rate"] == 0.0


def test_non_numeric_value_treated_as_missing() -> None:
    a = {"composite_score": "oops", "success_rate": 0.5}
    b = {"composite_score": 70.0, "success_rate": 0.5}
    result = compare_scorecards(a, b)
    comp = _cmp(result, "composite")
    assert comp.a_value is None
    assert comp.b_value == 70.0
    assert comp.winner == "b"
    assert comp.delta is None


def test_empty_cards_all_tie() -> None:
    result = compare_scorecards({}, {})
    assert result.overall == "tie"
    assert all(w == "tie" for w in result.winners.values())
    assert all(d is None for d in result.deltas.values())


def test_comparisons_are_ordered_tuple() -> None:
    result = compare_scorecards(_card(), _card())
    assert isinstance(result, BenchmarkResult)
    assert isinstance(result.comparisons, tuple)
    assert [c.metric for c in result.comparisons] == [
        "composite",
        "success_rate",
        "safety",
        "robustness",
    ]
    assert all(isinstance(c, MetricComparison) for c in result.comparisons)


def test_to_dict_shape() -> None:
    a = _card(composite=95.0, success_rate=0.95, safety=90.0, robustness=100.0)
    b = _card(composite=80.0, success_rate=0.80, safety=70.0, robustness=90.0)
    data = compare_scorecards(a, b).to_dict()
    assert set(data) == {"comparisons", "deltas", "winners", "overall"}
    assert data["overall"] == "a"
    assert [c["metric"] for c in data["comparisons"]] == [
        "composite",
        "success_rate",
        "safety",
        "robustness",
    ]
    assert data["deltas"]["composite"] == 15.0
    assert data["winners"]["composite"] == "a"
    first = data["comparisons"][0]
    assert set(first) == {"metric", "a_value", "b_value", "delta", "winner"}
    assert first["a_value"] == 95.0
    assert first["b_value"] == 80.0
    # The result must be JSON serializable.
    json.dumps(data)


def test_result_is_frozen() -> None:
    result = compare_scorecards(_card(composite=95.0), _card(composite=80.0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.overall = "b"  # type: ignore[misc]


def test_metric_comparison_is_frozen() -> None:
    comp = MetricComparison("composite", 1.0, 2.0, -1.0, "b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        comp.delta = 5.0  # type: ignore[misc]
