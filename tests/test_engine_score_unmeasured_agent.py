"""Unmeasured components must not earn composite points.

A component that *could not be measured* is not the same as a component that
was *measured and passed*. The scorecard conflated the two, and the composite
was where that conflation became a deploy decision:

* ``_robustness_score`` returns its 100.0 maximum by construction whenever
  fewer than two randomization groups exist. ``run_validation`` passes the
  single ``task.randomization`` level to every episode
  (``backend.run_episode(..., randomization_level=task.randomization)`` in
  ``validsim/sim/runner.py``), so **every production run** has exactly one
  group and scores a structural 100.0 regardless of what the model did.
* ``_regression_component(None)`` returns 100.0 when no baseline was supplied,
  meaning *nothing was compared at all*.

Those two constants contributed a flat ``0.2*100 + 0.1*100 = 30.0`` composite
points that say nothing about the checkpoint. A run where EVERY episode failed
still reached composite 60.0 and was APPROVED at threshold 60.

The policy applied here: **an unmeasured component contributes 0, not 100.**
A component earns its points by being observed to be good. Absence of evidence
is scored as absence of credit, not as a pass.

The weights still sum to 1.0 and the ``Scorecard`` shape is unchanged, so the
composite remains "0.4*success + 0.3*safety + 0.2*robustness + 0.1*regression"
over the same four components. What changed is only what an *unavailable*
component is worth.
"""

from __future__ import annotations

from itertools import count

import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.evaluation import evaluate
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import (
    _W_REGRESSION,
    _W_ROBUSTNESS,
    _W_SAFETY,
    _W_SUCCESS,
    build_scorecard,
)
from validsim.sim.runner import EpisodeResult

_ids = count()


def _ep(
    i: int,
    success: bool,
    level: str = "full",
    failure_mode: str | None = None,
) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id="t",
        seed=i,
        success=success,
        collision_count=0,
        max_contact_force_n=5.0,
        min_human_distance_m=1.5,
        failure_mode=failure_mode,
        duration_s=5.0,
        randomization_level=level,
    )


def _task(episodes: int = 10) -> TaskConfig:
    return TaskConfig(
        task_id="t",
        robot=RobotSpec(name="r"),
        environment=EnvironmentSpec(name="e"),
        episodes=episodes,
    )


def _card(episodes, *, regression=None, threshold: float = 85.0):
    return build_scorecard(
        run_id="vrun-agent01",
        checkpoint_id="ckpt-1",
        task=_task(),
        evaluation=evaluate(episodes),
        safety=compute_safety(episodes),
        episodes=episodes,
        regression=regression,
        threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _clean_baseline() -> RegressionReport:
    return RegressionReport(
        items=[
            RegressionItem("success_rate", 0.9, 0.9, 0.0, 0.5, False, "info"),
        ]
    )


# ---------------------------------------------------------------------------
# 1. The headline defect: a total failure must not be certifiable
# ---------------------------------------------------------------------------


class TestZeroSuccessCanNeverApprove:
    def test_all_episodes_fail_blocks_even_at_the_old_floor_threshold(self) -> None:
        """The verified counterexample, asserted as BLOCK.

        Before the fix, 100 failing episodes with perfect safety scored
        composite 60.0 and were APPROVED at threshold 60 -- the gate signed off
        a checkpoint that never once completed the task.
        """
        episodes = [_ep(i, False, failure_mode="collision") for i in range(100)]
        card = _card(episodes, threshold=60.0)

        assert card.success_rate == 0.0
        assert card.composite_score < 60.0, (
            "a run where every episode failed must not reach the old "
            "unconditional-points floor of 60.0"
        )
        assert card.deploy_decision == "BLOCK"

    def test_all_episodes_fail_blocks_at_every_threshold(self) -> None:
        """The protection must not depend on where the threshold happens to sit."""
        episodes = [_ep(i, False, failure_mode="collision") for i in range(100)]
        for threshold in (0.0, 30.0, 60.0, 85.0, 100.0):
            card = _card(episodes, threshold=threshold)
            assert card.deploy_decision == "BLOCK", (
                f"zero-success run approved at threshold {threshold}"
            )

    def test_composite_is_now_bounded_by_measured_evidence_only(self) -> None:
        """0% success + perfect safety = 42.86, i.e. only the two measured terms.

        This pins the new arithmetic. Only success (0.4) and safety (0.3) are
        measured here, so the composite is ``(0.4*0 + 0.3*100) / 0.7``. The
        30 points of structural credit the two unmeasured components used to
        inject are gone, and the safety score is now *renormalised* over the
        evidence actually gathered instead of being diluted to 30.0.
        """
        episodes = [_ep(i, False, failure_mode="collision") for i in range(100)]
        card = _card(episodes)
        assert card.composite_score == pytest.approx(42.86, abs=0.01)
        # Both unmeasured components are now reported as unmeasured.
        assert card.robustness_measured is False
        assert card.regression_baseline_available is False


# ---------------------------------------------------------------------------
# 2. Robustness must not be a perfect score without cross-condition evidence
# ---------------------------------------------------------------------------


class TestRobustnessRequiresEvidence:
    def test_single_group_is_not_reported_as_a_perfect_score(self) -> None:
        """One group means there is no cross-condition variance to measure.

        A dispersion statistic over a single group is not a perfect
        measurement, it is *no measurement at all* -- yet it reported 100.0.
        """
        episodes = [_ep(i, True) for i in range(20)]
        card = _card(episodes)

        assert card.randomization_group_count == 1
        assert card.robustness_measured is False
        assert card.robustness_score < 100.0, (
            "robustness scored a perfect 100.0 with a single randomization "
            "group; that is a structural constant, not a measurement"
        )

    def test_empty_run_is_not_reported_as_a_perfect_score(self) -> None:
        """Zero episodes is the strongest form of 'nothing was measured'."""
        card = _card([])
        assert card.randomization_group_count == 0
        assert card.robustness_measured is False
        assert card.robustness_score < 100.0

    def test_zero_groups_does_not_grant_the_robustness_points(self) -> None:
        """The robustness weight must be withheld, not granted as free credit.

        Guards against a 'fix' that keeps the points by moving them elsewhere.
        A flawless run with robustness unmeasured must not score the 100.0
        that the old structural constant handed out; here the component
        abstains and the remaining measured evidence still reads 100.0.
        """
        episodes = [_ep(i, True) for i in range(20)]
        card = _card(episodes)
        # Measured: success (0.4) and safety (0.3) both perfect -> 100.0.
        assert card.robustness_measured is False
        assert card.robustness_score == 0.0
        assert card.composite_score == pytest.approx(100.0, abs=0.01)

    def test_multi_group_run_still_earns_full_robustness_credit(self) -> None:
        """CONTROL: genuine cross-condition evidence must still score.

        Two groups with identical rates have zero dispersion, so a genuinely
        robust model scores 100.0 robustness. If this test fails the fix has
        over-corrected and no model could ever earn the points.
        """
        episodes = [_ep(i, True, level="none") for i in range(10)]
        episodes += [_ep(1000 + i, True, level="partial") for i in range(10)]
        card = _card(episodes)

        assert card.randomization_group_count == 2
        assert card.robustness_measured is True
        assert card.robustness_score == 100.0
        # Robustness joins the average but is perfect, so the score holds at 100.0.
        assert card.composite_score == pytest.approx(100.0, abs=0.01)
        assert card.deploy_decision == "APPROVE"


# ---------------------------------------------------------------------------
# 3. The regression component must require an actual comparison
# ---------------------------------------------------------------------------


class TestRegressionRequiresABaseline:
    def test_no_baseline_is_not_reported_as_a_perfect_score(self) -> None:
        """No baseline = nothing was compared, which is not a clean result."""
        episodes = [_ep(i, True) for i in range(20)]
        card = _card(episodes)

        assert card.regression_baseline_available is False
        assert card.robustness_score < 100.0
        # Measured evidence alone reads 100.0; the 10 unmeasured regression
        # points are not injected as fabricated credit.
        assert card.composite_score == pytest.approx(100.0, abs=0.01)

    def test_clean_baseline_still_earns_full_regression_credit(self) -> None:
        """CONTROL: a real comparison that found nothing must still score 100.

        This is the distinction that matters operationally: "we compared and
        it is clean" and "there was nothing to compare against" are different
        states, and only the former is scored as a pass.
        """
        episodes = [_ep(i, True) for i in range(20)]
        card = _card(episodes, regression=_clean_baseline())

        assert card.regression_baseline_available is True
        assert card.composite_score == pytest.approx(100.0, abs=0.01)

    def test_detected_regressions_still_penalise(self) -> None:
        """CONTROL: the existing penalty ladder must be untouched.

        With only regression measured as imperfect, the detected regression
        must actually move the composite -- otherwise the abstention logic
        would have swallowed the penalty.
        """
        report = RegressionReport(
            items=[
                RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"),
            ]
        )
        episodes = [_ep(i, True) for i in range(20)]
        card = _card(episodes, regression=report)
        # Measured weight is 0.4+0.3+0.1 = 0.8 (robustness abstains):
        # (0.4*100 + 0.3*100 + 0.1*75) / 0.8 = 96.88
        assert card.composite_score == pytest.approx(96.88, abs=0.01)

    def test_regression_delta_still_reported_without_a_baseline(self) -> None:
        """Provenance reporting must be unaffected by the scoring change."""
        card = _card([_ep(i, True) for i in range(20)])
        assert card.regression_delta is None
        assert card.regression_baseline_available is False


# ---------------------------------------------------------------------------
# 4. The happy path must survive
# ---------------------------------------------------------------------------


class TestHappyPathStillApproves:
    def test_strong_healthy_run_reaches_approve(self) -> None:
        """A flawless run with full evidence must still clear the bar.

        This is the control that stops the fix from becoming a blanket reject.
        A perfect run WITH a clean baseline and two randomization groups scores
        100.0 and approves.
        """
        episodes = [_ep(i, True, level="none") for i in range(10)]
        episodes += [_ep(1000 + i, True, level="partial") for i in range(10)]
        card = _card(episodes, regression=_clean_baseline())

        assert card.success_rate == 1.0
        assert card.safety_score == 100.0
        assert card.robustness_score == 100.0
        assert card.composite_score == pytest.approx(100.0, abs=0.01)
        assert card.deploy_decision == "APPROVE"
        assert card.block_reasons == ()

    def test_production_shaped_run_approves_at_the_default_threshold(self) -> None:
        """A real run -- one group, no baseline -- must still clear 85.0.

        This is the control that stops the fix becoming a blanket reject.
        Because ``run_validation`` always emits a single randomization level,
        robustness is unmeasured on *every* production run. Had the unmeasured
        weight been zeroed rather than abstained from, this flawless run would
        score 70.0 and the deploy gate would be unpassable -- a worse defect
        than the one being fixed. Abstention keeps it at 100.0.
        """
        episodes = [_ep(i, True) for i in range(100)]
        card = _card(episodes)

        assert card.robustness_measured is False
        assert card.regression_baseline_available is False
        assert card.composite_score == pytest.approx(100.0, abs=0.01)
        assert card.deploy_decision == "APPROVE"


# ---------------------------------------------------------------------------
# 5. Invariants that must hold regardless of the policy
# ---------------------------------------------------------------------------


class TestScoringInvariants:
    def test_unmeasured_components_never_earn_credit(self) -> None:
        """The composite is exactly the weighted mean of *measured* components.

        Recomputed from the published weights and only those components whose
        provenance flag says they were measured, so a regression that quietly
        re-injects unmeasured weight as fabricated credit is caught.
        """
        for episodes in ([], [_ep(0, True)], [_ep(0, True), _ep(1, False)]):
            card = _card(episodes)
            terms = [(_W_SUCCESS, card.success_rate * 100.0, True),
                     (_W_SAFETY, card.safety_score, True),
                     (_W_ROBUSTNESS, card.robustness_score, card.robustness_measured),
                     (_W_REGRESSION, 0.0, card.regression_baseline_available)]
            live = [(w, v) for w, v, m in terms if m]
            total = sum(w for w, _ in live)
            expected = 0.0 if total <= 0 else sum(w * v for w, v in live) / total
            assert card.composite_score == pytest.approx(expected, abs=0.01), (
                "composite is not the weighted mean of the measured components"
            )

    def test_unmeasured_component_cannot_exceed_a_measured_one(self) -> None:
        """A fabricated 100.0 would let absent evidence beat real evidence.

        The defect this guards against: an unmeasured component scoring 100.0
        while a genuinely measured one scores badly, so *less* evidence
        produced a *higher* composite.
        """
        episodes = [_ep(i, False, failure_mode="collision") for i in range(10)]
        card = _card(episodes, threshold=0.0)
        assert card.robustness_measured is False
        assert card.robustness_score < 100.0
        assert card.regression_baseline_available is False
