"""Tests for per-failure-mode rate trends (:func:`failure_mode_trends`)."""

from __future__ import annotations

import json

from validsim.engine.trends import failure_mode_trends


def _run(
    run_id: str,
    episode_count: int,
    taxonomy: dict[str, int] | None = None,
) -> dict:
    """Build a minimal run summary carrying a failure taxonomy + episode count."""
    run: dict = {"run_id": run_id, "episode_count": episode_count}
    if taxonomy is not None:
        run["failure_taxonomy"] = taxonomy
    return run


# --- Guard clauses -----------------------------------------------------------


def test_empty_history_returns_empty_dict() -> None:
    assert failure_mode_trends([]) == {}


def test_history_without_episodes_returns_empty_dict() -> None:
    # Every run has zero (or missing) episodes -> no rates are well-defined.
    history = [
        _run("vrun-1", 0, {"stuck": 3}),
        _run("vrun-2", 0, {"stuck": 5}),
        {"run_id": "vrun-3"},  # no episode_count / taxonomy at all
    ]
    assert failure_mode_trends(history) == {}


def test_runs_without_taxonomy_are_ignored() -> None:
    # No run carries a failure_taxonomy -> nothing to report.
    history = [_run("vrun-1", 10), _run("vrun-2", 10)]
    assert failure_mode_trends(history) == {}


# --- Direction detection -----------------------------------------------------


def test_rising_mode() -> None:
    history = [
        _run("vrun-1", 20, {"stuck": 2}),  # 0.10
        _run("vrun-2", 20, {"stuck": 6}),  # 0.30
    ]
    trends = failure_mode_trends(history)
    assert trends["stuck"]["direction"] == "rising"
    assert trends["stuck"]["first_rate"] == 0.1
    assert trends["stuck"]["last_rate"] == 0.3
    assert trends["stuck"]["delta"] == 0.2


def test_falling_mode() -> None:
    history = [
        _run("vrun-1", 10, {"drift": 5}),  # 0.50
        _run("vrun-2", 10, {"drift": 1}),  # 0.10
    ]
    trends = failure_mode_trends(history)
    assert trends["drift"]["direction"] == "falling"
    assert trends["drift"]["delta"] == -0.4


def test_flat_mode() -> None:
    history = [
        _run("vrun-1", 10, {"slip": 3}),  # 0.30
        _run("vrun-2", 10, {"slip": 3}),  # 0.30
    ]
    trends = failure_mode_trends(history)
    assert trends["slip"]["direction"] == "flat"
    assert trends["slip"]["delta"] == 0.0


def test_single_run_is_flat() -> None:
    trends = failure_mode_trends([_run("vrun-1", 8, {"stuck": 2})])
    assert trends["stuck"] == {
        "first_rate": 0.25,
        "last_rate": 0.25,
        "delta": 0.0,
        "direction": "flat",
    }


# --- Rate normalisation across differing episode counts ----------------------


def test_rates_normalise_by_episode_count() -> None:
    # Counts grow but the *rate* is identical once normalised by episodes.
    history = [
        _run("vrun-1", 10, {"stuck": 1}),   # 0.10
        _run("vrun-2", 100, {"stuck": 10}),  # 0.10
    ]
    trends = failure_mode_trends(history)
    assert trends["stuck"]["first_rate"] == 0.1
    assert trends["stuck"]["last_rate"] == 0.1
    assert trends["stuck"]["delta"] == 0.0
    assert trends["stuck"]["direction"] == "flat"


def test_only_first_and_last_usable_runs_are_compared() -> None:
    # The middle run must not influence first/last rates.
    history = [
        _run("vrun-1", 10, {"stuck": 0}),   # 0.00 (first)
        _run("vrun-2", 10, {"stuck": 9}),   # ignored middle
        _run("vrun-3", 10, {"stuck": 4}),   # 0.40 (last)
    ]
    trends = failure_mode_trends(history)
    assert trends["stuck"]["first_rate"] == 0.0
    assert trends["stuck"]["last_rate"] == 0.4
    assert trends["stuck"]["direction"] == "rising"


# --- "Ignoring modes absent everywhere" --------------------------------------


def test_mode_absent_everywhere_is_ignored() -> None:
    history = [
        _run("vrun-1", 10, {"stuck": 2, "ghost": 0}),
        _run("vrun-2", 10, {"stuck": 3, "ghost": 0}),
    ]
    trends = failure_mode_trends(history)
    assert "stuck" in trends
    # "ghost" is present as a key but never has a non-zero count -> ignored.
    assert "ghost" not in trends


def test_mode_present_only_in_middle_run_is_reported() -> None:
    history = [
        _run("vrun-1", 10, {"stuck": 1}),
        _run("vrun-2", 10, {"transient": 5}),
        _run("vrun-3", 10, {"stuck": 2}),
    ]
    trends = failure_mode_trends(history)
    # transient is absent at both ends -> 0.0 -> 0.0, flat, but still reported.
    assert trends["transient"] == {
        "first_rate": 0.0,
        "last_rate": 0.0,
        "delta": 0.0,
        "direction": "flat",
    }


# --- Robustness / schema tolerance -------------------------------------------


def test_zero_episode_run_is_skipped_for_first_and_last() -> None:
    history = [
        _run("vrun-1", 0, {"stuck": 99}),   # unusable, ignored
        _run("vrun-2", 10, {"stuck": 1}),   # first usable -> 0.10
        _run("vrun-3", 10, {"stuck": 8}),   # last usable  -> 0.80
    ]
    trends = failure_mode_trends(history)
    assert trends["stuck"]["first_rate"] == 0.1
    assert trends["stuck"]["last_rate"] == 0.8
    assert trends["stuck"]["direction"] == "rising"


def test_total_episodes_fallback_is_accepted() -> None:
    # Store summaries use episode_count; EvaluationResult uses total_episodes.
    history = [
        {"run_id": "vrun-1", "total_episodes": 10, "failure_taxonomy": {"stuck": 1}},
        {"run_id": "vrun-2", "total_episodes": 10, "failure_taxonomy": {"stuck": 7}},
    ]
    trends = failure_mode_trends(history)
    assert trends["stuck"]["delta"] == 0.6
    assert trends["stuck"]["direction"] == "rising"


def test_malformed_taxonomy_and_counts_degrade_gracefully() -> None:
    history = [
        {"run_id": "vrun-1", "episode_count": 10, "failure_taxonomy": "not-a-dict"},
        {"run_id": "vrun-2", "episode_count": 10, "failure_taxonomy": None},
        {"run_id": "vrun-3", "episode_count": 10, "failure_taxonomy": {"stuck": "oops"}},
        {"run_id": "vrun-4", "episode_count": 10, "failure_taxonomy": {"stuck": 4}},
    ]
    trends = failure_mode_trends(history)
    # Only the well-formed non-zero count survives; the bad value -> 0 (ignored).
    assert set(trends) == {"stuck"}
    assert trends["stuck"]["first_rate"] == 0.0  # run-3 coerced "oops" -> 0
    assert trends["stuck"]["last_rate"] == 0.4
    assert trends["stuck"]["direction"] == "rising"


def test_multiple_modes_are_keyed_and_sorted() -> None:
    history = [
        _run("vrun-1", 10, {"zeta": 1, "alpha": 5}),
        _run("vrun-2", 10, {"zeta": 9, "alpha": 1}),
    ]
    trends = failure_mode_trends(history)
    assert list(trends) == ["alpha", "zeta"]  # insertion order is sorted by name
    assert trends["alpha"]["direction"] == "falling"
    assert trends["zeta"]["direction"] == "rising"


# --- Serialisation -----------------------------------------------------------


def test_result_is_json_serializable() -> None:
    history = [
        _run("vrun-1", 20, {"stuck": 2}),
        _run("vrun-2", 20, {"stuck": 6, "drift": 1}),
    ]
    payload = json.dumps(failure_mode_trends(history), sort_keys=True)
    assert json.loads(payload) == failure_mode_trends(history)
