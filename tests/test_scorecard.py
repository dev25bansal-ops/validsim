"""Tests for composite scorecard math, robustness, and deploy gating."""

from __future__ import annotations

import json
from itertools import count
from typing import Sequence

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard, build_scorecard
from validsim.sim.runner import EpisodeResult

_ids = count()


def _episodes(n_success: int, n_failure: int, level: str = "full") -> list[EpisodeResult]:
    eps = [
        EpisodeResult(
            episode_id=f"ep-{next(_ids)}", task_id="t", seed=i, success=True,
            duration_s=5.0, randomization_level=level,
        )
        for i in range(n_success)
    ]
    eps += [
        EpisodeResult(
            episode_id=f"ep-{next(_ids)}", task_id="t", seed=1000 + i, success=False,
            failure_mode="collision", duration_s=9.0, randomization_level=level,
        )
        for i in range(n_failure)
    ]
    return eps


def _task() -> TaskConfig:
    return TaskConfig(
        task_id="t", robot=RobotSpec(name="r"), environment=EnvironmentSpec(name="e"),
        episodes=10,
    )


def _safety(score: float) -> SafetyResult:
    return SafetyResult(0.0, 0.0, None, 0.0, score)


def _build(
    episodes: Sequence[EpisodeResult],
    safety_score: float = 100.0,
    regression: RegressionReport | None = None,
    threshold: float = 85.0,
) -> Scorecard:
    evaluation = evaluate(episodes)
    return build_scorecard(
        run_id="vrun-cafe1234",
        checkpoint_id="ckpt-1",
        task=_task(),
        evaluation=evaluation,
        safety=_safety(safety_score),
        episodes=episodes,
        regression=regression,
        threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
    )


class TestCompositeMath:
    def test_exact_composite_no_regression(self) -> None:
        # 90/100 success, safety 80, single group (robustness 100), no baseline.
        # composite = 0.4*90 + 0.3*80 + 0.2*100 + 0.1*100 = 36+24+20+10 = 90
        card = _build(_episodes(90, 10), safety_score=80.0)
        assert card.success_rate == 0.9
        assert card.robustness_score == 100.0
        assert card.composite_score == 90.0
        assert card.deploy_decision == "APPROVE"

    def test_regression_component(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.8, -0.1, 0.01, True, "critical"),
            RegressionItem("mean_duration_s", 10.0, 16.0, 6.0, None, True, "warning"),
        ])
        # 2 significant regressions -> component = 100 - 50 = 50
        # composite = 36 + 30 + 20 + 5 = 91? recompute: success 90/100 -> 0.4*90=36,
        # safety 100 -> 30, robustness 100 -> 20, regression 50 -> 5 => 91.0
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        assert card.composite_score == 91.0
        assert card.regression_delta == -0.1

    def test_many_regressions_floor_component(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"),
            RegressionItem("m2", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m3", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m4", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m5", 0, 0, 0, 0.01, True, "warning"),
        ])
        # component = max(0, 100 - 25*4) = 0 -> composite = 36+30+20+0 = 86? no:
        # 4 significant -> 100-100=0; composite = 0.4*90 + 0.3*100 + 0.2*100 + 0 = 86.0
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        assert card.composite_score == 86.0
        assert card.deploy_decision == "APPROVE"  # 86 >= 85

    def test_non_significant_items_do_not_penalize(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.89, -0.01, 0.4, False, "info"),
        ])
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        assert card.composite_score == 96.0  # 36+30+20+10


class TestRobustness:
    def test_group_spread_reduces_score(self) -> None:
        # Groups: none -> 10/10 (1.0), partial -> 5/10 (0.5); pstdev=0.25
        # robustness = 100 - 200*0.25 = 50; success 15/20=0.75
        # composite = 0.4*75 + 0.3*100 + 0.2*50 + 0.1*100 = 30+30+10+10 = 80 -> BLOCK
        episodes = _episodes(10, 0, level="none") + _episodes(5, 5, level="partial")
        card = _build(episodes, safety_score=100.0)
        assert card.robustness_score == 50.0
        assert card.composite_score == 80.0
        assert card.deploy_decision == "BLOCK"


class TestGatingAndSerialization:
    def test_decision_at_threshold_boundary(self) -> None:
        # composite exactly 85 -> APPROVE
        episodes = _episodes(70, 30)  # success 70 -> 28; safety 100 -> 30; rob 20; reg 10 = 88
        card = _build(episodes, safety_score=100.0, threshold=88.0)
        assert card.composite_score == 88.0
        assert card.deploy_decision == "APPROVE"
        strict = _build(episodes, safety_score=100.0, threshold=88.5)
        assert strict.deploy_decision == "BLOCK"

    def test_confidence_interval_contains_success_rate(self) -> None:
        card = _build(_episodes(70, 30))
        assert card.confidence_interval is not None
        low, high = card.confidence_interval
        assert low <= card.success_rate <= high

    def test_to_json_round_trip(self) -> None:
        card = _build(_episodes(90, 10))
        data = json.loads(card.to_json())
        assert data["run_id"] == "vrun-cafe1234"
        assert data["composite_score"] == 96.0
        assert data["failure_taxonomy"] == {"collision": 10}
        assert data["confidence_interval"] == list(card.confidence_interval or ())

    def test_defaults(self) -> None:
        card = _build(_episodes(90, 10))
        assert card.threshold == 85.0
        assert card.episode_count == 100
        assert card.regression_delta is None
        assert card.created_at == "2026-01-01T00:00:00+00:00"


class TestEvidenceSufficiency:
    def test_incomplete_run_cannot_approve(self) -> None:
        """A worker that under-delivers must not earn a deploy approval."""
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"), environment=EnvironmentSpec(name="e"),
            episodes=50,
        )
        episodes = _episodes(1, 0)
        card = build_scorecard(
            run_id="vrun-cafe1234",
            checkpoint_id="ckpt-1",
            task=task,
            evaluation=evaluate(episodes),
            safety=_safety(100.0),
            episodes=episodes,
            created_at="2026-01-01T00:00:00+00:00",
        )
        # The weighted math is perfect on the single episode it did see...
        assert card.composite_score == 100.0
        # ...but 1 of 50 requested episodes is not evidence to deploy on.
        assert card.deploy_decision == "BLOCK"
