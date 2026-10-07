"""Tests for composite scorecard math, robustness, and deploy gating."""

from __future__ import annotations

import json
from itertools import count
from typing import Sequence

import pytest

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
        # 90/100 success, safety 80, single group (robustness UNMEASURED), no
        # baseline (regression UNMEASURED). An unmeasured component abstains: its
        # weight is excluded from the denominator rather than counted as 100.
        # measured = success 0.4*90=36, safety 0.3*80=24; weight 0.7 of 1.0
        # -> 60/0.7 = 85.71..., rounded to the scorecard's precision.
        card = _build(_episodes(90, 10), safety_score=80.0)
        assert card.success_rate == 0.9
        # Robustness must NOT be a fabricated 100 for a single randomization group.
        assert card.robustness_score == 0.0
        assert card.composite_score == pytest.approx(85.71, abs=0.01)
        assert card.deploy_decision == "APPROVE"  # 85.71 >= 85

    def test_regression_component(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.8, -0.1, 0.01, True, "critical"),
            RegressionItem("mean_duration_s", 10.0, 16.0, 6.0, None, True, "warning"),
        ])
        # 2 significant regressions -> component = 100 - 50 = 50.
        # Robustness is unmeasured (single group) and abstains, so the measured
        # weights are success 0.4 and safety 0.3 and regression 0.1 = 0.8.
        # (0.4*90 + 0.3*100 + 0.1*50) / 0.8 = (36+30+5)/0.8 = 88.75
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        assert card.composite_score == pytest.approx(88.75, abs=0.01)
        assert card.regression_delta == -0.1

    def test_many_regressions_floor_component(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"),
            RegressionItem("m2", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m3", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m4", 0, 0, 0, 0.01, True, "warning"),
            RegressionItem("m5", 0, 0, 0, 0.01, True, "warning"),
        ])
        # component = max(0, 100 - 25*4) = 0. Robustness abstains, so measured
        # weight is 0.4+0.3+0.1 = 0.8: (36 + 30 + 0) / 0.8 = 82.5
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        assert card.composite_score == pytest.approx(82.5, abs=0.01)
        # 82.5 < 85: four critical regressions must block, not approve.
        assert card.deploy_decision == "BLOCK"

    def test_non_significant_items_do_not_penalize(self) -> None:
        report = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.89, -0.01, 0.4, False, "info"),
        ])
        card = _build(_episodes(90, 10), safety_score=100.0, regression=report)
        # A non-significant item is a measured component scoring 100, but
        # robustness is still unmeasured (single group) and abstains, so the
        # measured weight is 0.4+0.3+0.1 = 0.8: (36+30+10)/0.8 = 95.0
        assert card.composite_score == pytest.approx(95.0, abs=0.01)


class TestRobustness:
    def test_group_spread_reduces_score(self) -> None:
        # Groups: none -> 10/10 (1.0), partial -> 5/10 (0.5); pstdev=0.25
        # robustness = 100 - 200*0.25 = 50; success 15/20=0.75.
        # Here robustness IS measured (two groups), so it keeps its 0.2 weight;
        # regression is unmeasured (no baseline) and abstains. Measured weight
        # is 0.4+0.3+0.2 = 0.9: (30 + 30 + 10)/0.9 = 77.78 -> BLOCK.
        episodes = _episodes(10, 0, level="none") + _episodes(5, 5, level="partial")
        card = _build(episodes, safety_score=100.0)
        assert card.robustness_score == 50.0
        assert card.composite_score == pytest.approx(77.78, abs=0.01)
        assert card.deploy_decision == "BLOCK"


class TestUnmeasuredComponentsAreVisible:
    """A component that was never measured must not read as a perfect score.

    ``run_validation`` applies a single ``task.randomization`` level to every
    episode, so every production run lands all of its episodes in ONE
    randomization group. ``_robustness_score`` then returns its maximum by
    construction -- the number is a *constant*, not a measurement. Likewise
    ``_regression_component(None)`` returns its maximum because no baseline was
    supplied, which is exactly the same "nothing was compared" state.

    Both therefore contribute a flat ``0.2*100 + 0.1*100 = 30.0`` composite
    points that say nothing about the model under test, and nothing in the
    serialized scorecard distinguishes them from genuinely earned scores.
    A reader (or a downstream consumer diffing two runs) sees "100.0 / 100" and
    concludes robustness was measured and passed.

    The policy decision here is deliberately NOT to zero these out -- that
    would silently move every existing verdict. Instead the scorecard must make
    the *absence* of evidence explicit and machine-readable, so the constant
    cannot be mistaken for a result.
    """

    def test_robustness_constant_is_flagged_as_unmeasured(self) -> None:
        card = _build(_episodes(90, 10))
        # A single randomization group carries no information about robustness,
        # so the component reports 0.0 and abstains from the composite rather
        # than contributing a fabricated perfect score.
        assert card.robustness_score == 0.0
        assert card.robustness_measured is False
        assert card.randomization_group_count == 1

    def test_multi_group_run_is_marked_measured(self) -> None:
        """CONTROL: a genuinely varied run must still be marked as measured."""
        episodes = _episodes(10, 0, level="none") + _episodes(5, 5, level="partial")
        card = _build(episodes)
        assert card.robustness_score == 50.0
        assert card.robustness_measured is True
        assert card.randomization_group_count == 2

    def test_empty_run_is_unmeasured_not_perfect(self) -> None:
        """Zero episodes is the strongest form of 'nothing was measured'."""
        card = _build([])
        assert card.robustness_score == 0.0
        assert card.robustness_measured is False
        assert card.randomization_group_count == 0

    def test_regression_without_baseline_is_flagged(self) -> None:
        card = _build(_episodes(90, 10))
        assert card.regression_baseline_available is False
        assert card.regression_delta is None  # already None, for a different reason

    def test_regression_with_clean_baseline_is_flagged_as_measured(self) -> None:
        """CONTROL: a real comparison that found nothing must be marked measured.

        This is the distinction that matters operationally: "we compared against
        a baseline and it is clean" and "there was no baseline to compare
        against" both score 100.0, and only the flag tells them apart.
        """
        clean = RegressionReport(items=[
            RegressionItem("success_rate", 0.9, 0.9, 0.0, 0.5, False, "info"),
        ])
        card = _build(_episodes(90, 10), regression=clean)
        assert card.regression_baseline_available is True
        assert card.regression_delta == 0.0

    def test_flags_survive_serialization(self) -> None:
        """The flags must reach the API/DB consumers, not just the dataclass.

        ``to_dict`` is what the store persists and what the API returns, so a
        flag that only exists on the Python object is invisible to every real
        consumer of the scorecard.
        """
        data = _build(_episodes(90, 10)).to_dict()
        assert data["robustness_measured"] is False
        assert data["regression_baseline_available"] is False
        assert data["randomization_group_count"] == 1

    def test_flags_default_to_measured_for_legacy_construction(self) -> None:
        """Old rows carry no flags, so the default must not invent unmeasured.

        A reconstructed legacy scorecard that stored no group information is
        treated as "measured" -- the opposite default would make every
        historical row claim it was unmeasured, which is a claim we cannot
        support with evidence.
        """
        sc = Scorecard(
            run_id="vrun-cafe1234", checkpoint_id="ckpt-1", task_id="t",
            composite_score=90.0, success_rate=0.9, safety_score=100.0,
            robustness_score=100.0, regression_delta=None, confidence_interval=None,
            deploy_decision="APPROVE",
        )
        assert sc.robustness_measured is True
        assert sc.regression_baseline_available is True


class TestGatingAndSerialization:
    def test_decision_at_threshold_boundary(self) -> None:
        # 70/100 success, safety 100, single group (robustness UNMEASURED), no
        # baseline (regression UNMEASURED). Both abstain, so the measured weight
        # is 0.4+0.3 = 0.7: (28 + 30)/0.7 = 82.857...
        episodes = _episodes(70, 30)
        card = _build(episodes, safety_score=100.0, threshold=82.0)
        assert card.composite_score == pytest.approx(82.86, abs=0.01)
        assert card.deploy_decision == "APPROVE"  # 82.86 >= 82
        strict = _build(episodes, safety_score=100.0, threshold=83.0)
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
        assert data["composite_score"] == pytest.approx(94.29, abs=0.01)
        assert data["failure_taxonomy"] == {"collision": 10}
        assert data["confidence_interval"] == list(card.confidence_interval or ())

    def test_defaults(self) -> None:
        card = _build(_episodes(90, 10))
        assert card.threshold == 85.0
        assert card.episode_count == 100
        assert card.regression_delta is None
        assert card.created_at == "2026-01-01T00:00:00+00:00"


class TestAdversarialGate:
    """The gate must see WHICH segment failed, not just the pooled rate.

    ``composite`` folds nominal and adversarial episodes into one success rate,
    so a checkpoint that is perfect nominally and fails every adversarial episode
    scored identically to one that is mediocre nominally and passes every
    adversarial episode. The headline feature -- adversarial testing -- was
    invisible to the deploy gate, and because the adversarial share is
    operator-configurable (up to 1,000 scenarios against 100,000 nominal
    episodes) a suite could configure itself out of the score entirely.
    """

    @staticmethod
    def _split(nominal_total: int, nominal_ok: int, adv_total: int, adv_ok: int):
        """Build a run with an explicit nominal-then-adversarial layout.

        ``run_validation`` emits nominal episodes first and appends one per
        scenario, so the positional split the engine relies on is reproduced
        here exactly.
        """
        episodes = [
            EpisodeResult(
                episode_id=f"n{i}", task_id="t", seed=i, success=i < nominal_ok,
                duration_s=5.0, randomization_level="full",
                failure_mode=None if i < nominal_ok else "collision",
            )
            for i in range(nominal_total)
        ]
        episodes += [
            EpisodeResult(
                episode_id=f"a{j}", task_id="t", seed=1000 + j, success=j < adv_ok,
                duration_s=7.0, randomization_level="full",
                failure_mode=None if j < adv_ok else "collision",
            )
            for j in range(adv_total)
        ]
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=nominal_total,
        )
        return build_scorecard(
            run_id="vrun-cafe1234",
            checkpoint_id="ckpt-1",
            task=task,
            evaluation=evaluate(episodes),
            safety=_safety(100.0),
            episodes=episodes,
            created_at="2026-01-01T00:00:00+00:00",
        )

    def test_identical_composite_opposite_verdicts(self) -> None:
        """The core counterexample: same pooled rate, opposite verdicts."""
        # 900 nominal + 100 adversarial. Case A passes 900 nominal and 0
        # adversarial; case B passes 800 nominal and 100 adversarial. Both total
        # 900/1000 = 0.90 pooled success, which clears the 85 composite gate, so
        # the verdict difference is attributable to the adversarial segment and
        # not merely to a low score. The composite cannot separate them.
        fails_every = self._split(900, 900, 100, 0)     # 900 +   0 = 900/1000
        passes_every = self._split(900, 800, 100, 100)  # 800 + 100 = 900/1000

        # The composite genuinely cannot tell them apart...
        assert fails_every.composite_score == passes_every.composite_score
        assert fails_every.success_rate == passes_every.success_rate
        # ...so the separate gate must.
        assert fails_every.deploy_decision == "BLOCK"
        assert passes_every.deploy_decision == "APPROVE"

    def test_block_reason_is_specific_and_legible(self) -> None:
        card = self._split(400, 400, 100, 5)
        assert card.deploy_decision == "BLOCK"
        assert any("adversarial" in reason for reason in card.block_reasons)
        assert card.adversarial_success_rate == 0.05
        assert card.adversarial_episode_count == 100

    def test_healthy_adversarial_segment_still_approves(self) -> None:
        """The gate must not become a blanket reject."""
        for adv_ok in (70, 85, 100):
            card = self._split(400, 400, 100, adv_ok)
            assert card.deploy_decision == "APPROVE", f"blocked a healthy run ({adv_ok}/100)"

    def test_small_adversarial_samples_are_reported_but_not_gated(self) -> None:
        """A 2-of-4 sample is a coin flip, not evidence of failure.

        Gating on it would block runs for a reason the data does not support,
        which trains operators to ignore the gate. The rate is still reported.
        """
        for adv_total, adv_ok in ((4, 2), (12, 7), (20, 12)):
            card = self._split(400, 400, adv_total, adv_ok)
            assert not any("adversarial" in r for r in card.block_reasons), (
                f"gated a {adv_ok}/{adv_total} sample on noise"
            )
            assert card.adversarial_success_rate is not None  # still visible

    def test_nominal_only_run_is_unaffected(self) -> None:
        card = self._split(400, 400, 0, 0)
        assert card.adversarial_episode_count == 0
        assert card.adversarial_success_rate is None
        assert card.deploy_decision == "APPROVE"
        assert card.block_reasons == ()


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

    def test_total_task_failure_can_never_approve(self) -> None:
        """A run where EVERY episode failed must not reach APPROVE at any threshold.

        This is the sharpest form of the unconditional-points defect. With a
        single randomization group, robustness scores a structural 100.0; with
        no baseline supplied, the regression component is a structural 100.0 as
        well. Those two contribute a flat
        ``0.2*100 + 0.1*100 = 30.0`` composite points regardless of whether the
        model accomplished anything, so the composite floors at 60.0 whenever
        safety is perfect and success is 0.0 -- and a threshold at or below
        that floor approves a run with a 0% success rate.

        A deploy gate that certifies "this model never completed the task" is
        worse than no gate, because it converts a total failure into a signed
        off approval. The evidence rule must therefore require positive task
        success, independent of where the threshold happens to sit.

        The composite itself is deliberately left at 60.0 -- that is a
        *description* of the scoring model, not itself the defect. What must
        change is the verdict, so only the verdict is asserted.
        """
        episodes = _episodes(0, 100)  # every single episode fails
        card = _build(episodes, safety_score=100.0, threshold=60.0)

        assert card.success_rate == 0.0
        # Robustness and regression are both unmeasured and abstain, so only
        # success (0) and safety (100) count: 30/0.7 = 42.86 -- not the old
        # unconditional-points floor of 60.0.
        assert card.composite_score == pytest.approx(42.86, abs=0.01)
        # The gate must refuse a run that achieved nothing, whatever the gate.
        assert card.deploy_decision == "BLOCK"

    def test_zero_success_rate_blocks_regardless_of_threshold(self) -> None:
        """The control: the fix must hold at *every* threshold, not just the floor.

        Asserting the verdict is stable across the whole threshold range proves
        the protection comes from the evidence rule rather than from an
        accidental threshold interaction.
        """
        episodes = _episodes(0, 100)
        for threshold in (0.0, 30.0, 60.0, 85.0, 100.0):
            card = _build(episodes, safety_score=100.0, threshold=threshold)
            assert card.deploy_decision == "BLOCK", (
                f"zero-success run approved at threshold {threshold}"
            )


class TestUnmeasuredBaselineIsNotACleanOne:
    """A comparison that never ran must not score as one that passed.

    ``compare()`` short-circuits when either run recorded zero episodes and
    returns a report whose every item has ``p_value is None``. That report was
    scored as a clean 100.0 regression component with
    ``regression_baseline_available = True`` -- so passing an *empty* baseline
    beat passing none at all, and a run that BLOCKed at 82.86 with no baseline
    was APPROVEd at 85.0 with one. An empty baseline is the same claim as no
    baseline, and has to score the same.
    """

    def test_empty_baseline_scores_like_no_baseline(self) -> None:
        """The composite and the decision must be identical."""
        from validsim.engine.evaluation import EvaluationResult
        from validsim.engine.regression import compare

        current = EvaluationResult(14, 14, 0.7, {})
        empty = EvaluationResult(0, 0, 0.0, {})
        report = compare(current, empty, seed=1)

        assert report.items, "compare() should still emit placeholder items"
        assert all(i.p_value is None for i in report.items), (
            "expected the zero-episode short circuit"
        )
        assert report.significant_regressions == []

        episodes = _episodes(n_success=7, n_failure=3)
        none_card = _build(episodes, regression=None)
        empty_card = _build(episodes, regression=report)

        assert empty_card.composite_score == none_card.composite_score
        assert empty_card.deploy_decision == none_card.deploy_decision

    def test_empty_baseline_does_not_claim_it_was_measured(self) -> None:
        """The provenance flag must not assert a measurement that never ran."""
        from validsim.engine.evaluation import EvaluationResult
        from validsim.engine.regression import compare

        report = compare(EvaluationResult(14, 14, 0.7, {}), EvaluationResult(0, 0, 0.0, {}), seed=1)
        card = _build(_episodes(n_success=7, n_failure=3), regression=report)

        assert card.regression_baseline_available is False

    def test_a_real_baseline_still_reports_as_measured(self) -> None:
        """The fix must not disarm the flag for a comparison that did run."""
        from validsim.engine.evaluation import EvaluationResult
        from validsim.engine.regression import compare

        report = compare(
            EvaluationResult(20, 20, 1.0, {}), EvaluationResult(20, 20, 1.0, {}), seed=1
        )
        assert any(i.p_value is not None for i in report.items), (
            "a two-sided comparison should produce a real p-value"
        )
        card = _build(_episodes(n_success=7, n_failure=3), regression=report)
        assert card.regression_baseline_available is True

