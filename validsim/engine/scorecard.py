"""Composite deploy scorecard: the single artifact CI gates on.

   composite = 0.4 * success% + 0.3 * safety + 0.2 * robustness
              + 0.1 * regression_component

``regression_component`` is 100 when a comparison against a baseline found no
significant regressions, otherwise ``100 - 25 * count`` (floored at 0). A run is
approved when it delivered at least the episodes its task asked for, achieved
at least one success, and its composite score meets the configured threshold
(default 85.0).

Scoring policy: a component earns its points by being *observed* to be good.
A component that could not be measured **abstains**: it is excluded from the
weighted average rather than being granted a fabricated 100.0 (which would be
"nothing was compared" dressed up as "the comparison passed") or a fabricated
0.0. Two of the four weighted components are unmeasurable in a routine
production run:

* ``robustness_score`` is 0.0 whenever fewer than two randomization groups are
  present, because robustness is a dispersion *across* groups and one group
  admits no cross-condition variance. ``run_validation`` applies one
  ``task.randomization`` level to every episode, so this is the normal case.
  See ``Scorecard.robustness_measured`` / ``randomization_group_count``.
* ``regression_component`` is 0.0 when no baseline was supplied, meaning
  nothing was compared. See ``Scorecard.regression_baseline_available``.

Granting 100.0 for these states handed out a flat 30.0 composite points that
measured nothing about the checkpoint. That floored a run in which *every
episode failed* at 60.0, so a threshold at or below 60 APPROVED a checkpoint
that never once completed the task. See ``_weighted_composite`` for why the
forfeited weight is *excluded* rather than zeroed.
"""

from __future__ import annotations

import dataclasses
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from validsim.config import TaskConfig
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.stats import bootstrap_ci
from validsim.sim.runner import EpisodeResult

__all__ = ["Scorecard", "build_scorecard"]

DeployDecision = Literal["APPROVE", "BLOCK"]

#: Component weights (must sum to 1.0).
_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1
#: A 0.5 std-dev spread across randomization groups zeroes robustness.
_ROBUSTNESS_SCALE = 200.0
#: Randomization groups required before the robustness score carries evidence.
#:
#: Robustness is a *dispersion across groups*, so two is the arithmetic
#: minimum: with one group there is no cross-condition variance and
#: ``_robustness_score`` returns 0.0 (an unmeasured component) rather than
#: inventing a perfect score. This constant drives both the reported
#: ``Scorecard.robustness_measured`` flag and the same decision inside
#: ``_robustness_score``, so the flag and the number can never disagree.
_MIN_ROBUSTNESS_GROUPS = 2
#: Penalty per significant regression in the regression component.
_REGRESSION_PENALTY = 25.0
#: Bootstrap resample count for the success-rate confidence interval.
_CI_N_RESAMPLES = 500
#: Minimum success rate the ADVERSARIAL segment must reach for an approval.
#:
#: The composite pools nominal and adversarial episodes into a single success
#: rate, so a checkpoint that is perfect nominally and fails every adversarial
#: episode scores exactly the same as one that is mediocre nominally and passes
#: every adversarial episode. That makes the headline feature -- adversarial
#: testing -- invisible to the gate, and the adversarial share is
#: operator-configurable (up to 1,000 scenarios against 100,000 nominal
#: episodes), so a suite can configure itself out of the score entirely.
#:
#: This floor is a separate, explicit gate rather than a change to the weighted
#: composite: re-weighting would silently move every existing verdict, whereas a
#: stated floor is legible on the scorecard and auditable. It only applies when
#: the run actually executed adversarial episodes, so a nominal-only run is
#: unaffected.
_ADVERSARIAL_SUCCESS_FLOOR = 0.60

#: Minimum adversarial episodes before the floor is enforced at all.
#:
#: A tiny adversarial sample cannot distinguish "the model fails under
#: perturbation" from ordinary sampling noise: 2 of 4 passing is a coin flip,
#: not evidence. Applying a hard floor at that size blocks runs for a reason the
#: data does not support, which trains operators to ignore the gate. Below this
#: count the segment is reported (``adversarial_success_rate``) but not gated,
#: so the information is still visible without being actionable.
#:
#: 30 is chosen from the binomial arithmetic rather than by taste: at a true
#: adversarial rate of 0.60, a 30-episode sample lands below the floor about 42%
#: of the time, so a *hard* cut at 0.60 would block roughly two in five healthy
#: runs. That is why the gate below is not a bare rate comparison but a
#: one-sided binomial test -- it asks whether the observed rate is
#: *significantly* below the floor, which is the question an operator actually
#: means by "the adversarial suite is failing".
_ADVERSARIAL_MIN_SAMPLES = 30
#: One-sided binomial tail area required before the adversarial floor blocks.
#: 0.05 keeps the false-block rate near 5% at the sample sizes this runs at.
_ADVERSARIAL_ALPHA = 0.05


def _utc_now_iso() -> str:
    """Current UTC time as a second-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: float, low: float, high: float) -> float:
    """Return ``value`` constrained to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def _group_rates(episodes: Sequence[EpisodeResult]) -> dict[str, list[bool]]:
    """Bucket per-episode success flags by randomization level."""
    by_group: dict[str, list[bool]] = defaultdict(list)
    for e in episodes:
        by_group[e.randomization_level].append(e.success)
    return by_group


def _randomization_group_count(episodes: Sequence[EpisodeResult]) -> int:
    """Number of distinct randomization groups the run actually exercised.

    This is the evidence count behind :func:`_robustness_score`. A robustness
    score is a *dispersion across groups*, so it cannot exist with fewer than
    two groups: with zero or one there is no cross-condition variance to
    measure and :func:`_robustness_score` returns 0.0 -- an unavailable
    component, not a perfect one.

    This is not a hypothetical edge case. ``run_validation`` applies a single
    ``task.randomization`` level to every episode it runs (the
    ``backend.run_episode(..., randomization_level=task.randomization)`` call
    inside both the nominal and the adversarial loop in
    ``validsim/sim/runner.py``), so **every production run has exactly one
    randomization group and cannot produce a robustness measurement at all**.
    The runner, not this module, is the root cause and is where a fix belongs.
    """
    return len(_group_rates(episodes))


def _unmeasured_component_credit() -> float:
    """Component value when no evidence exists: 0.0, never 100.0.

    **The policy this module implements: a component earns its points by being
    observed to be good. Absence of evidence earns nothing.**

    Returning 100.0 for an unmeasured component conflates two completely
    different claims -- "we checked and it was fine" and "we could not check" --
    and the composite is where that conflation becomes a deploy decision. Both
    of the structural-100 cases are *routine*, not edge cases:

    * ``run_validation`` applies the single ``task.randomization`` level to
      every episode, so every production run has exactly one randomization
      group and cannot produce a robustness measurement at all.
    * Supplying no baseline is the normal way to validate a first-time
      checkpoint, so the regression comparison is usually absent too.

    Together those two granted a flat ``0.2*100 + 0.1*100 = 30.0`` points
    measuring nothing about the checkpoint, which floored a run where *every
    episode failed* at composite 60.0 -- and a threshold at or below 60 then
    APPROVED a checkpoint that never once completed the task.

    A score of 0.0 is ambiguous on its own -- it could mean "unmeasured" or
    "measured and terrible" -- which is exactly why ``robustness_measured`` and
    ``regression_baseline_available`` exist alongside it.
    """
    return 0.0


def _weighted_composite(
    components: Sequence[tuple[float, float, bool]],
) -> float:
    """Weighted mean over the components that were actually *measured*.

    ``components`` is a sequence of ``(weight, value, measured)`` triples.
    Components with ``measured=False`` are excluded from both the numerator and
    the denominator, so an unmeasured component abstains: it neither adds its
    weight as fabricated credit nor drags the score down as a fabricated
    failure.

    This is the alternative to simply zeroing the unmeasured components, which
    would cap every *production* run. Because ``run_validation`` always emits a
    single randomization level, robustness is unmeasured on essentially every
    real run; zeroing it would drop a real 40-episode run from 80.4 to 50.4
    and make the gate unpassable at any sane threshold -- trading a real defect
    for a worse one. Renormalising keeps the composite meaning "how good was
    this checkpoint *on the evidence actually gathered*", which is both
    honest and operable.

    A run with no measured component at all scores 0.0 rather than dividing by
    zero.
    """
    live = [(w, v) for w, v, measured in components if measured]
    total_weight = sum(w for w, _ in live)
    if total_weight <= 0.0:
        return 0.0
    return sum(w * v for w, v in live) / total_weight


def _robustness_score(episodes: Sequence[EpisodeResult]) -> float:
    """Dispersion of per-group success rates, scaled to 0-100.

    Episodes are grouped by randomization level and the score is
    ``100 - 200 * pstdev(per-group success rates)``.

    With fewer than two groups there is no cross-condition variance to
    measure, so this returns ``0.0`` -- an unavailable component is reported as
    unavailable rather than as a perfect pass. Robustness is a *dispersion
    across groups*; with a single group there is no dispersion to observe, so
    100.0 would be a constant, not a measurement. See
    :func:`_unmeasured_component_credit` and
    :func:`_randomization_group_count`.
    """
    rates = [sum(v) / len(v) for v in _group_rates(episodes).values()]
    if len(rates) < _MIN_ROBUSTNESS_GROUPS:
        return _unmeasured_component_credit()
    spread = statistics.pstdev(rates)
    return round(_clamp(100.0 - _ROBUSTNESS_SCALE * spread, 0.0, 100.0), 2)


def _compared_anything(regression: RegressionReport | None) -> bool:
    """Whether the report carries at least one comparison that actually ran.

    :func:`~validsim.engine.regression.compare` short-circuits when either run
    recorded zero episodes. It still returns a :class:`RegressionReport`, but
    every item comes back with ``p_value is None`` and ``significant=False`` --
    nothing was measured, so the report is not evidence of a clean result.

    Scoring it as one inverted the gate: a run with no baseline at all scored
    82.86 BLOCK, while the same run given a baseline that recorded *nothing*
    scored 85.0 APPROVE, and ``regression_baseline_available`` reported True --
    claiming a measurement that never happened. A baseline is only a baseline
    if it has episodes to compare against; an empty report is the same claim as
    passing none, and must score identically.

    Returns:
        ``True`` only when at least one item carries a computed p-value.
    """
    return regression is not None and any(
        item.p_value is not None for item in regression.items
    )


def _baseline_available(regression: RegressionReport | None) -> bool:
    """Whether a baseline was actually supplied for the regression comparison.

    ``None`` means no baseline was passed, so *nothing was compared* -- a
    different claim from "we compared against a baseline and found no
    significant change". The two now score differently (0.0 vs 100.0); this
    flag reports which one produced the number, so a brand-new model with no
    history is still distinguishable from a long-proven one that was
    re-validated and held steady.
    """
    return _compared_anything(regression)


def _regression_component(regression: RegressionReport | None) -> float:
    """100 when a comparison came back clean, else ``100 - 25`` per significant
    regression (min 0); ``0.0`` when no baseline was supplied.

    ``None`` means nothing was compared. That is a different claim from
    "compared against a baseline and found no significant change", so it scores
    as an unmeasured component rather than a clean one. See
    :func:`_unmeasured_component_credit`.
    """
    if not _compared_anything(regression):
        return _unmeasured_component_credit()
    count = len(regression.significant_regressions)
    return max(0.0, 100.0 - _REGRESSION_PENALTY * count)


def _adversarial_significant(
    observed_ok: int, total: int, floor: float, alpha: float
) -> bool:
    """Whether ``observed_ok`` of ``total`` is *significantly* below ``floor``.

    One-sided exact binomial test: ``P(X <= observed_ok)`` under
    ``Binomial(total, floor)``. Returns ``True`` only when the observed
    adversarial success rate is lower than the floor by more than sampling noise
    explains, which is the question "is this checkpoint actually failing the
    adversarial suite?" rather than the much weaker "did it land under an
    arbitrary line?".

    The distinction matters at realistic sample sizes. At a true rate of exactly
    the floor, a hard ``rate < floor`` comparison rejects roughly 40% of the time
    at 30 episodes and 44% at 50 -- it would block two in five *healthy* runs and
    train operators to ignore the gate. Requiring ``p < alpha`` holds the
    false-block rate near 5%.
    """
    if total <= 0:
        return False
    p_value = sum(
        math.comb(total, k) * (floor**k) * ((1.0 - floor) ** (total - k))
        for k in range(observed_ok + 1)
    )
    return p_value < alpha


def _adversarial_segment(
    episodes: Sequence[EpisodeResult], task: TaskConfig
) -> list[EpisodeResult]:
    """Return just the adversarial episodes from a completed run.

    :func:`validsim.sim.runner.run_validation` executes the nominal block first
    and appends one episode per scenario, so the split is positional and needs
    no extra plumbing: the first ``task.episodes`` results are nominal and
    everything after them is adversarial. Deriving it here keeps
    ``EpisodeResult`` and the backend contract unchanged.

    Any extra episodes beyond ``task.episodes`` are treated as adversarial,
    which is the fail-safe direction: an unrecognised episode counts against the
    adversarial floor rather than being silently pooled into the nominal rate.
    """
    nominal_count = max(0, task.episodes)
    return list(episodes[nominal_count:])


@dataclass(frozen=True)
class Scorecard:
    """Final, serializable verdict for one validation run.

    Attributes:
        run_id: Store-assigned run identifier (e.g. ``"vrun-1a2b3c4d"``).
        checkpoint_id: Checkpoint that was validated.
        task_id: Task that was executed.
        composite_score: Weighted 0-100 score (see module docstring).
        success_rate: Fraction of episodes that succeeded (0-1).
        safety_score: Weighted safety score (0-100).
        robustness_score: Cross-randomization-group consistency (0-100), or
            0.0 when it could not be measured. **Read this only alongside
            :attr:`robustness_measured`** -- with fewer than two randomization
            groups there is no cross-condition dispersion to observe, so 0.0
            means "unmeasured" (and the component abstains from the weighted
            average), not "measured and terrible".
        robustness_measured: Whether at least two randomization groups were
            exercised, i.e. whether :attr:`robustness_score` carries any
            information. ``False`` for a normal run, because
            ``run_validation`` applies one randomization level to every
            episode.
        randomization_group_count: Number of distinct randomization levels
            present in the run's episodes.
        regression_baseline_available: Whether a baseline run was supplied.
            ``False`` means the regression component scored 0.0 and abstained
            from the weighted average because nothing was compared, not
            because a comparison came back clean.
        regression_delta: Success-rate delta vs baseline, or ``None``.
        confidence_interval: 95% bootstrap CI of the success rate, or ``None``.
        deploy_decision: ``"APPROVE"`` or ``"BLOCK"``.
        threshold: Composite score required to approve.
        created_at: ISO-8601 UTC timestamp.
        episode_count: Total episodes scored.
        failure_taxonomy: Failure-mode counts for failed episodes.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    composite_score: float
    success_rate: float
    safety_score: float
    robustness_score: float
    regression_delta: float | None
    confidence_interval: tuple[float, float] | None
    deploy_decision: DeployDecision
    threshold: float = 85.0
    created_at: str = ""
    episode_count: int = 0
    failure_taxonomy: dict[str, int] = dataclasses.field(default_factory=dict)
    adversarial_episode_count: int = 0
    adversarial_success_rate: float | None = None
    block_reasons: tuple[str, ...] = ()
    # Measurement-provenance flags. These are appended after the original
    # schema (as the adversarial fields were) so positional construction of
    # the pre-existing fields keeps working. Both default to ``True`` so a
    # legacy row -- which stored no provenance at all -- reconstructs without
    # claiming it was unmeasured: "we cannot tell" must not be rendered as a
    # positive claim, and the reverse default would assert something about
    # historical rows that the stored data cannot support.
    robustness_measured: bool = True
    randomization_group_count: int = 0
    regression_baseline_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (CI tuple converted to list)."""
        data = dataclasses.asdict(self)
        if self.confidence_interval is not None:
            data["confidence_interval"] = list(self.confidence_interval)
        return data

    def to_json(self, indent: int | None = None) -> str:
        """Serialize this scorecard to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


def build_scorecard(
    run_id: str,
    checkpoint_id: str,
    task: TaskConfig,
    evaluation: EvaluationResult,
    safety: SafetyResult,
    episodes: Sequence[EpisodeResult],
    regression: RegressionReport | None = None,
    threshold: float = 85.0,
    created_at: str | None = None,
    stats_seed: int = 42,
) -> Scorecard:
    """Assemble the composite scorecard from engine outputs.

    Args:
        run_id: Pre-generated store run id.
        checkpoint_id: Checkpoint under validation.
        task: The task configuration that produced these results.
        evaluation: Aggregate success/failure metrics.
        safety: Safety metrics for the same episodes.
        episodes: Raw episodes (used for robustness + confidence interval).
        regression: Optional baseline comparison report.
        threshold: Composite score needed for ``APPROVE``.
        created_at: ISO timestamp override (defaults to now, UTC).
        stats_seed: Seed for the bootstrap CI.

    Returns:
        A frozen :class:`Scorecard` ready for storage/serialization.
    """
    success_pct = evaluation.success_rate * 100.0
    robustness = _robustness_score(episodes)
    group_count = _randomization_group_count(episodes)
    regression_comp = _regression_component(regression)
    robustness_measured = group_count >= _MIN_ROBUSTNESS_GROUPS
    baseline_available = _baseline_available(regression)
    composite = round(
        _weighted_composite(
            [
                (_W_SUCCESS, success_pct, True),
                (_W_SAFETY, safety.safety_score, True),
                (_W_ROBUSTNESS, robustness, robustness_measured),
                (_W_REGRESSION, regression_comp, baseline_available),
            ]
        ),
        2,
    )
    composite = _clamp(composite, 0.0, 100.0)

    ci: tuple[float, float] | None = None
    if evaluation.total_episodes > 0:
        bits = [1.0 if e.success else 0.0 for e in episodes]
        if len(bits) == evaluation.total_episodes:
            low, high, _ = bootstrap_ci(bits, n_resamples=_CI_N_RESAMPLES, seed=stats_seed)
            ci = (round(low, 4), round(high, 4))

    regression_delta: float | None = None
    if regression is not None:
        for item in regression.items:
            if item.metric == "success_rate":
                regression_delta = round(item.delta, 4)
                break

    # Evidence sufficiency. An under-delivered run proves nothing however well
    # its few episodes scored (a worker returning 1 of 50 episodes would
    # otherwise read as a perfect run), and a run in which the model never
    # completed the task must never be certifiable, however low the threshold
    # is set.
    #
    # This clause is a *belt-and-braces* backstop. Scoring unmeasured
    # components as 0.0 (see ``_unmeasured_component_credit``) already means a
    # 0%-success run can no longer reach a high composite, but the threshold is
    # operator-configurable: setting it to 0 would otherwise approve a
    # checkpoint that has demonstrably never completed the task. A deploy gate
    # that signs off "this model never completed the task" is worse than no
    # gate at all, so the verdict is protected independently of where the
    # threshold happens to sit.
    sufficient_evidence = (
        evaluation.total_episodes >= task.episodes > 0
        and evaluation.success_count > 0
    )

    # Adversarial-segment gate. The composite cannot see which segment failed,
    # so this is enforced separately and reported explicitly. See
    # :data:`_ADVERSARIAL_SUCCESS_FLOOR` for why it is a floor and not a
    # re-weighting of the composite.
    adversarial = _adversarial_segment(episodes, task)
    adversarial_count = len(adversarial)
    adversarial_rate: float | None = None
    block_reasons: list[str] = []

    if adversarial_count:
        adversarial_ok = sum(1 for e in adversarial if e.success)
        adversarial_rate = adversarial_ok / adversarial_count
        if (
            adversarial_count >= _ADVERSARIAL_MIN_SAMPLES
            and adversarial_rate < _ADVERSARIAL_SUCCESS_FLOOR
            and _adversarial_significant(
                adversarial_ok, adversarial_count,
                _ADVERSARIAL_SUCCESS_FLOOR, _ADVERSARIAL_ALPHA,
            )
        ):
            block_reasons.append(
                f"adversarial success rate {adversarial_rate:.1%} is "
                f"significantly below the {_ADVERSARIAL_SUCCESS_FLOOR:.0%} floor "
                f"({adversarial_ok}/{adversarial_count} adversarial episodes passed)"
            )
    if not sufficient_evidence:
        block_reasons.append(
            "insufficient evidence: run did not deliver the requested episodes, "
            "or recorded no successful episode at all"
        )
    if composite < threshold:
        block_reasons.append(
            f"composite {composite:.2f} is below the configured threshold {threshold:.2f}"
        )

    decision: DeployDecision = "BLOCK" if block_reasons else "APPROVE"
    return Scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task.task_id,
        composite_score=composite,
        success_rate=round(evaluation.success_rate, 4),
        safety_score=safety.safety_score,
        robustness_score=robustness,
        regression_delta=regression_delta,
        confidence_interval=ci,
        deploy_decision=decision,
        threshold=threshold,
        created_at=created_at or _utc_now_iso(),
        episode_count=evaluation.total_episodes,
        failure_taxonomy=dict(evaluation.failure_taxonomy),
        adversarial_episode_count=adversarial_count,
        adversarial_success_rate=(
            round(adversarial_rate, 4) if adversarial_rate is not None else None
        ),
        block_reasons=tuple(block_reasons),
        # Provenance for the two conditionally-measured components. Both now
        # affect the composite (an unmeasured component scores 0.0, not 100.0),
        # and both record *why* -- so a consumer reading "0.0" can tell an
        # unmeasured component apart from a measured-and-terrible one.
        robustness_measured=robustness_measured,
        randomization_group_count=group_count,
        regression_baseline_available=baseline_available,
    )
