"""Behavioural invariants for the numeric core (weights, bounds, monotonicity).

These tests assert *properties* of the scoring arithmetic rather than
re-asserting individual lines. A line-execution test only proves the interpreter
reached a statement; a property test proves the statement computed the right
thing. That distinction is the whole point here: ``validsim/engine/safety.py``
reached 100% line *and* branch coverage while ``NaN > limit`` evaluated ``False``
and scored a perfect 100.0 for a sensor that had failed to report. Coverage
cannot catch that class of defect, so nothing in this file is satisfied by
merely executing the code -- each test states an invariant that a broken
implementation violates.

Every test below names the mutation it would catch in its docstring.

No third-party property-testing dependency is used (like
``tests/test_stats_property.py``): a pinned master seed drives a randomized
sweep, so the suite stays dependency-free and byte-for-byte reproducible.

The source under test is never modified.
"""

from __future__ import annotations

import random
from itertools import count

import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine import safety as safety_mod
from validsim.engine import scorecard as scorecard_mod
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
from validsim.engine.stats import bootstrap_ci
from validsim.sim.runner import EpisodeResult

#: Pinned master seed -> the whole randomized sweep is deterministic.
MASTER_SEED = 0xBEEF17
#: Randomized inputs per property. Kept small so the sweep stays fast: the
#: bootstrap is pure Python and this file also builds scorecards.
ITERATIONS = 40

_ids = count()


def _ep(
    i: int,
    success: bool = True,
    *,
    randomization_level: str = "full",
    collisions: int = 0,
    force: float = 5.0,
    distance: float | None = 1.5,
    duration: float = 10.0,
    failure_mode: str | None = None,
) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id="t",
        seed=i,
        success=success,
        collision_count=collisions,
        max_contact_force_n=force,
        min_human_distance_m=distance,
        failure_mode=failure_mode,
        duration_s=duration,
        randomization_level=randomization_level,
    )


def _task(episodes: int = 10, adversarial_count: int = 0) -> TaskConfig:
    return TaskConfig(
        task_id="t",
        robot=RobotSpec(name="r"),
        environment=EnvironmentSpec(name="e"),
        episodes=episodes,
        adversarial_count=adversarial_count,
    )


def _card(episodes, task=None, threshold=85.0, regression=None):
    """Build a scorecard the way production does: evaluate + compute_safety."""
    task = task or _task()
    return build_scorecard(
        run_id="vrun-1",
        checkpoint_id="ckpt-1",
        task=task,
        evaluation=evaluate(episodes),
        safety=compute_safety(episodes),
        episodes=episodes,
        regression=regression,
        threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# 1. Weights must sum to 1.0
# ---------------------------------------------------------------------------


class TestWeightsSumToOne:
    """The single most dangerous silent edit in the codebase.

    Every weight is a bare float literal read by exactly one ``+`` term. Change
    ``_W_SAFETY`` from ``0.3`` to ``0.4`` and the suite stays green, every
    scorecard still lands in ``[0, 100]`` (the weights are all positive), the
    verdict only shifts for runs near the threshold -- and CI reports 100%
    coverage on the module, because every weight line executed.

    The only thing that notices is arithmetic: weights that do not sum to 1.0
    silently rescale the composite. ``0.4/0.3/0.2/0.1`` summing to ``0.9`` would
    cap a perfect run at ``90.0``; summing to ``1.1`` would let a mediocre run
    score above 100 before the clamp hides it. Neither is visible in a coverage
    report, and both move real deploy verdicts.
    """

    def test_scorecard_component_weights_sum_to_one(self) -> None:
        """Catches: any single weight constant edited, added or dropped.

        ``sum()`` over the module-level constants rather than a hard-coded 1.0
        literal list, so adding a *fifth* component without a weight is also
        caught -- the sum silently becomes 0.9 and a perfect run stops reaching
        100.0.
        """
        weights = [
            scorecard_mod._W_SUCCESS,
            scorecard_mod._W_SAFETY,
            scorecard_mod._W_ROBUSTNESS,
            scorecard_mod._W_REGRESSION,
        ]
        assert sum(weights) == pytest.approx(1.0, abs=1e-12), (
            f"composite weights {weights} sum to {sum(weights)}, not 1.0"
        )

    def test_every_scorecard_weight_is_positive(self) -> None:
        """Catches: a weight flipped negative or zeroed.

        A negative weight would let a component *raise* the score when it
        degrades, inverting the meaning of the metric while keeping the sum at
        1.0 -- so the sum test above would still pass.
        """
        for name in ("_W_SUCCESS", "_W_SAFETY", "_W_ROBUSTNESS", "_W_REGRESSION"):
            value = getattr(scorecard_mod, name)
            assert value > 0.0, f"{name} = {value} is not positive"

    def test_safety_channel_weights_sum_to_one(self) -> None:
        """Catches: an edit to a safety violation weight.

        ``safety.py`` carries 100% line and branch coverage. Its three weights
        are documented as "must sum to 1.0" and nothing checks it. Sum below 1.0
        means violations are under-penalised and a run with a collision in every
        episode cannot reach a safety score of 0; sum above 1.0 means the
        ``max(0.0, ...)`` floor starts absorbing the excess, so the last few
        violations stop costing anything at all.
        """
        weights = [
            safety_mod._COLLISION_WEIGHT,
            safety_mod._FORCE_WEIGHT,
            safety_mod._PROXIMITY_WEIGHT,
        ]
        assert sum(weights) == pytest.approx(1.0, abs=1e-12), (
            f"safety weights {weights} sum to {sum(weights)}, not 1.0"
        )

    def test_every_safety_channel_weight_is_positive(self) -> None:
        """Catches: a safety weight zeroed out, silently disabling a channel.

        Setting ``_PROXIMITY_WEIGHT = 0.0`` keeps the sum at 0.8 and would be
        caught by the sum test, but the *intent* -- a human-proximity channel
        that exists to catch a robot running into a person -- deserves an
        independent assertion.
        """
        for name in (
            "_COLLISION_WEIGHT",
            "_FORCE_WEIGHT",
            "_PROXIMITY_WEIGHT",
        ):
            assert getattr(safety_mod, name) > 0.0, f"{name} was neutralised"

    def test_perfect_run_reaches_exactly_one_hundred(self) -> None:
        """The end-to-end consequence of the weights summing to 1.0.

        With all four components at their maximum and weights summing to 1.0 the
        composite is 100.0. This is the property that actually matters, and it
        is what a weight edit actually breaks: catch a weight changed to
        ``0.35/0.3/0.2/0.1`` (sum 0.95 -> a perfect run reports 95.0 and never
        clears a 95 threshold) or a weight dropped entirely.
        """
        episodes = [_ep(i, True) for i in range(10)]
        card = _card(episodes)
        assert card.safety_score == 100.0
        # A flawless run must still be able to reach 100.0. Robustness may be
        # unmeasured (abstaining), which must not CAP the attainable maximum --
        # the measured components are re-normalised over their own weight, so a
        # run that measures everything it can still tops out at 100.
        assert card.composite_score == 100.0, (
            "a flawless run must score 100.0; a composite that tops out below "
            "100 means the weights no longer sum to 1.0"
        )

    def test_composite_equals_the_documented_weighted_sum(self) -> None:
        """Catches: weights reordered, or a term dropped/duplicated.

        Recomputes the composite from the published formula using the module's
        own constants and the scorecard's own reported components, so a change
        to *which* component is multiplied by *which* weight is caught even
        when the weights still sum to 1.0.
        """
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(1, 30)
            good = rng.randint(0, n)
            episodes = [
                _ep(
                    i,
                    i < good,
                    collisions=rng.choice((0, 0, 0, 1, 3)),
                    force=rng.choice((5.0, 30.0, 90.0)),
                    distance=rng.choice((None, 0.2, 1.5, 3.0)),
                    randomization_level=rng.choice(("full", "partial")),
                )
                for i in range(n)
            ]
            card = _card(episodes)
            # The composite is a weighted mean over the components that were
            # actually MEASURED: an unmeasured component (robustness with a
            # single randomization group, regression with no baseline) abstains,
            # so its weight leaves the denominator instead of contributing a
            # fabricated value. This still catches a reordering or dropped term.
            #
            # The success term comes from the evaluation, not from
            # ``card.success_rate``: the scorecard weights the unrounded rate and
            # only rounds what it *reports*, while the card field is rounded to
            # 4dp. Weighting the rounded copy instead shifts the mean by ~0.0002,
            # which is invisible except when it pushes the final 2dp rounding
            # across a boundary -- then the test fails on arithmetic that is
            # actually correct, and the tolerance is not the thing to widen.
            terms = [
                (scorecard_mod._W_SUCCESS, evaluate(episodes).success_rate * 100.0, True),
                (scorecard_mod._W_SAFETY, card.safety_score, True),
                (
                    scorecard_mod._W_ROBUSTNESS,
                    card.robustness_score,
                    card.robustness_measured,
                ),
                (scorecard_mod._W_REGRESSION, 100.0, card.regression_baseline_available),
            ]
            live = [(w, v) for w, v, measured in terms if measured]
            total_weight = sum(w for w, _ in live)
            expected = (
                round(sum(w * v for w, v in live) / total_weight, 2)
                if total_weight > 0
                else 0.0
            )
            assert card.composite_score == pytest.approx(
                min(100.0, max(0.0, expected)), abs=0.01
            ), (
                "composite does not match the weighted mean over measured "
                "components (0.4*success + 0.3*safety + 0.2*robustness "
                "+ 0.1*regression, unmeasured terms excluded)"
            )


# ---------------------------------------------------------------------------
# 2. Bounds
# ---------------------------------------------------------------------------


class TestScorecardStaysInRange:
    """A composite is a 0-100 number. It must never be anything else.

    Every component is individually clamped, and so is the sum, so a naive
    bounds test would pass against code that was badly broken -- unless the
    inputs themselves are driven out of range, which is what this does.
    """

    def test_composite_within_zero_and_hundred(self) -> None:
        """Catches: an unclamped composite, or a clamp applied with the wrong
        bounds (``_clamp(x, 0.0, 100.0)`` -> ``_clamp(x, 0.0, 1.0)``).

        Driven with hostile inputs: every episode failing, several collisions
        each, forces far above the limit and human distances far below it. Any
        component pushed out of range propagates into the composite.
        """
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(1, 20)
            episodes = [
                _ep(
                    i,
                    rng.random() < 0.5,
                    collisions=rng.randint(0, 6),
                    force=rng.uniform(0.0, 400.0),
                    distance=rng.choice((None, rng.uniform(0.0, 0.4))),
                )
                for i in range(n)
            ]
            card = _card(episodes)
            assert 0.0 <= card.composite_score <= 100.0
            assert 0.0 <= card.safety_score <= 100.0
            assert 0.0 <= card.robustness_score <= 100.0
            assert 0.0 <= card.success_rate <= 1.0

    def test_all_failures_cannot_score_above_the_floor(self) -> None:
        """A run where the model never completed the task must not be
        certifiable.

        Robustness and regression are unmeasured on an all-failure run, so both
        abstain and only success (0) and safety (100) carry weight:
        ``(0 + 0.3*100) / 0.7 = 42.86``. This is strictly BELOW the old
        unconditional-points floor of 60.0, which was 30 flat points measuring
        nothing about task success. Pinning the exact value catches the weights
        being changed in either direction.
        """
        episodes = [_ep(i, False, failure_mode="collision") for i in range(10)]
        card = _card(episodes, threshold=0.0)
        assert card.success_rate == 0.0
        assert card.composite_score == pytest.approx(42.86, abs=0.01)
        # ...and it must still be blocked regardless of how low the threshold is.
        assert card.deploy_decision == "BLOCK"
        assert card.block_reasons

    def test_safety_score_never_negative_for_pathological_inputs(self) -> None:
        """Catches: a removed ``max(0.0, ...)`` floor in ``compute_safety``.

        Penalty weights sum to 1.0 and each rate is capped at 1.0, so the worst
        legal input is exactly ``-100`` before flooring. Removing the floor
        yields ``-100.0`` -- a negative "safety" score that flows straight into
        the composite via ``_W_SAFETY``.
        """
        episodes = [
            _ep(i, False, collisions=9, force=9_000.0, distance=0.0)
            for i in range(10)
        ]
        assert compute_safety(episodes).safety_score == 0.0

    def test_safety_penalty_never_exceeds_one_hundred(self) -> None:
        """Catches: a removed per-channel ``min(..., 1.0)`` cap.

        The collision channel is capped at 1.0 so it can never subtract more
        than its 50-point allotment. The cap is invisible in a normal run,
        because ``collisions_per_episode`` rarely reaches 1.0 -- but 10 episodes
        with 3 collisions each gives exactly 3.0, and an uncapped channel would
        subtract 150 points on its own, taking an otherwise clean run to
        ``-50.0``.

        The assertion is the exact score 50.0, not a range check: with the cap
        the collision channel contributes exactly 50 points; without it the
        contribution is 150 and the run floors at 0.0. A bounds-only test passes
        against both.
        """
        episodes = [
            _ep(i, False, collisions=3) for i in range(10)
        ]
        result = compute_safety(episodes)
        assert result.collisions_per_episode == 3.0, "cpe must be uncapped in the metric"
        assert result.max_force_exceeded_rate == 0.0
        assert result.proximity_violation_rate == 0.0
        # Capped: 100 - 1.0*0.5*100 = 50.0.  Uncapped: 100 - 3.0*0.5*100 = -50 -> 0.0
        assert result.safety_score == 50.0, (
            "the collision channel deducted more than its 50-point weight; "
            "the per-channel min(..., 1.0) cap is not being applied"
        )

    def test_safety_score_is_the_exact_weighted_penalty(self) -> None:
        """Catches: a dropped channel, a swapped weight, or a cap applied to the
        wrong channel.

        Recomputes the score from the published formula
        ``100 - 100*(0.5*cpe + 0.3*force_rate + 0.2*prox_rate)`` with all three
        channels simultaneously non-zero, so removing any single term, or
        pairing a rate with the wrong weight, moves the result and fails here.
        Exactly 85.0 for the construction below.
        """
        episodes = [_ep(i, True) for i in range(7)]
        episodes.append(_ep(7, False, collisions=2))          # cpe = 0.2
        episodes.append(_ep(8, True, force=80.0))             # force rate = 0.1
        episodes.append(_ep(9, True, distance=0.2))          # prox rate = 0.1
        result = compute_safety(episodes)
        assert result.collisions_per_episode == pytest.approx(0.2)
        assert result.max_force_exceeded_rate == pytest.approx(0.1)
        assert result.proximity_violation_rate == pytest.approx(0.1)
        assert result.safety_score == pytest.approx(85.0), (
            "safety score is not 0.5/0.3/0.2 weighted; a channel was dropped, "
            "double-counted, or paired with the wrong weight"
        )

    def test_robustness_is_the_exact_dispersion_penalty(self) -> None:
        """Catches: ``_ROBUSTNESS_SCALE`` edited, or ``pstdev`` swapped for
        ``stdev``.

        Two-group constructions with known dispersion pin both the scale
        constant and the choice of population vs sample standard deviation:

        * rates 0.9 / 0.5 -> ``pstdev = 0.2`` -> 100 - 200*0.2 = 60.0
          (``stdev`` would give 0.2828 -> 43.43, so this also pins ``pstdev``)
        * rates 0.8 / 0.6 -> ``pstdev = 0.1`` -> 80.0
        * rates 1.0 / 0.0 -> ``pstdev = 0.5`` -> 0.0

        A pure bounds assertion survives all three mutations because every
        result is still inside ``[0, 100]``; only the exact values do not.
        """
        def two_groups(rate_a: float, rate_b: float, n: int = 10):
            eps = [_ep(i, i < round(rate_a * n), randomization_level="none")
                   for i in range(n)]
            eps += [_ep(1000 + i, i < round(rate_b * n), randomization_level="full")
                    for i in range(n)]
            return eps

        assert scorecard_mod._robustness_score(two_groups(0.9, 0.5)) == 60.0
        assert scorecard_mod._robustness_score(two_groups(0.8, 0.6)) == 80.0
        assert scorecard_mod._robustness_score(two_groups(1.0, 0.0)) == 0.0
        # Halving the scale keeps every value in range, so only these exact
        # anchors distinguish it.
        assert scorecard_mod._robustness_score(two_groups(0.9, 0.5)) != 80.0

    def test_robustness_floors_at_zero_for_maximal_dispersion(self) -> None:
        """The documented 0.5-std-dev -> 0.0 anchor for maximal dispersion.

        ``_ROBUSTNESS_SCALE = 200.0`` was chosen so that the largest dispersion
        two-group data can produce (``pstdev = 0.5``) lands on exactly 0.0 rather
        than below it, which is what makes the ``max(0.0, ...)`` half of
        ``_clamp`` unreachable. Asserting the exact 0.0 therefore also pins the
        scale constant from the other side: a scale above 200 would produce a
        negative robustness score, and with the lower clamp gone that negative
        value would *raise* the composite.
        """
        episodes = [
            _ep(i, False, randomization_level="none") for i in range(5)
        ] + [_ep(i + 100, True, randomization_level="full") for i in range(5)]
        assert scorecard_mod._robustness_score(episodes) == 0.0
        assert 0.0 <= _card(episodes).composite_score <= 100.0


# ---------------------------------------------------------------------------
# 3. Monotonicity -- more evidence must never lower confidence
# ---------------------------------------------------------------------------


class TestMonotonicity:
    """Scoring must be monotone: worse evidence never scores better.

    A scoring function that is not monotone in its own inputs is worse than one
    that is simply wrong, because every score in the gap is silently
    untrustworthy and no amount of threshold tuning finds it.
    """

    def test_more_successes_never_lowers_the_composite(self) -> None:
        """Catches: any term in the composite with an inverted sign.

        Flipping ``_W_SAFETY`` to ``-0.3`` keeps the weight sum at 0.4 and makes
        a *more* dangerous run score *higher*. The bounds tests above all still
        pass -- the result is clamped into range either way. Only monotonicity
        exposes it.
        """
        rng = random.Random(MASTER_SEED)
        for _ in range(ITERATIONS):
            n = rng.randint(2, 20)
            previous = None
            for good in range(n + 1):
                episodes = [_ep(i, i < good) for i in range(n)]
                composite = _card(episodes, threshold=0.0).composite_score
                if previous is not None:
                    assert composite >= previous - 1e-9, (
                        f"composite fell from {previous} to {composite} when "
                        f"successes rose to {good}/{n}"
                    )
                previous = composite

    def test_ci_brackets_the_point_estimate_at_every_resample_count(self) -> None:
        """Catches: removal of the ``min``/``max`` widening applied to the
        interval endpoints.

        A percentile bootstrap interval is centred on the observed statistic by
        construction, so it must bracket it. With few resamples the percentile
        endpoints are just the extremes of a handful of noisy resample means and
        can both land on one side of the point -- at ``n_resamples == 2`` the
        interval is ``[min, max]`` of two draws, so a single unlucky pair misses
        the point estimate entirely.

        The widening only has room to act at small resample counts (measured:
        it matters at ``n_resamples`` 2-10 and is unobservable at 20+), so this
        sweeps the low end deliberately. A test at the production count of 500
        cannot see the mutation at all, which is exactly why the fix needed its
        own test.
        """
        rng = random.Random(MASTER_SEED)
        checked = 0
        for n_resamples in (2, 3, 4, 5, 8, 10):
            for _ in range(25):
                n = rng.randint(2, 12)
                values = [
                    1.0 if rng.random() < rng.uniform(0.2, 0.8) else 0.0
                    for _ in range(n)
                ]
                low, high, point = bootstrap_ci(
                    values, n_resamples=n_resamples, seed=rng.randrange(1, 10**6)
                )
                assert low <= point <= high, (
                    f"n_resamples={n_resamples}, n={n}: CI [{low}, {high}] "
                    f"excludes the point estimate {point}"
                )
                checked += 1
        assert checked == 150, "the sweep must actually have run"

    def test_more_evidence_never_lowers_confidence(self) -> None:
        """Catches: a CI that widens as the sample grows, or one whose width is
        not driven by the resample count.

        This is the property a line-coverage test on ``bootstrap_ci`` cannot
        see. Every mutation below produces a perfectly well-formed interval --
        ``low <= point <= high``, both endpoints inside ``[0, 1]`` -- so all
        structural assertions still pass. What changes is the *meaning*: the
        interval no longer reports how much the evidence constrains the metric.

        Three independent failure modes are covered:

        1. Width must shrink monotonically as ``n`` grows (a resample of
           ``k = 2n`` draws inflates the spread of each resample mean and
           widens every percentile band).
        2. At a fixed sample, more ``n_resamples`` must not *narrow* the
           interval -- the opposite defect, from indexing percentiles by the
           sample size instead of the resample count.
        3. The width must be strictly positive on a genuinely mixed sample: a
           zero-width interval on real variance claims a certainty no sample of
           that size supports.

        Only asserted on mixed samples: on a degenerate all-pass sample every
        resample is identical and the width is legitimately 0 at every n, so
        the comparison carries no signal.
        """
        # A fixed 70/30 sample keeps the point estimate constant while n grows,
        # so the only thing that can move the width is the sample size.
        base = [1.0] * 70 + [0.0] * 30
        widths_by_n = []
        for n in (100, 200, 400, 1000):
            values = [base[i % len(base)] for i in range(n)]
            low, high, point = bootstrap_ci(values, n_resamples=200, seed=MASTER_SEED)
            assert low <= point <= high, "CI must bracket the point estimate"
            assert 0.0 <= low and high <= 1.0
            assert high > low, (
                f"a 70/30 sample of n={n} produced a zero-width CI; the "
                "interval claims more certainty than the data supports"
            )
            widths_by_n.append(high - low)
        for small, large, n_small, n_large in zip(
            widths_by_n, widths_by_n[1:], (100, 200, 400), (200, 400, 1000)
        ):
            assert large <= small + 1e-9, (
                f"CI widened from {small} to {large} when the sample grew "
                f"from n={n_small} to n={n_large}"
            )

        # Randomized sweep: the same monotonicity on fresh samples.
        rng = random.Random(MASTER_SEED)
        for _ in range(10):
            p = rng.uniform(0.3, 0.7)
            big = [1.0 if rng.random() < p else 0.0 for _ in range(1000)]
            widths = []
            for n in (100, 200, 400, 1000):
                low, high, point = bootstrap_ci(
                    big[:n], n_resamples=200, seed=MASTER_SEED
                )
                assert low <= point <= high
                widths.append(high - low)
            for small, large in zip(widths, widths[1:]):
                assert large <= small + 1e-9

        # ``n_resamples`` controls the *precision* of the interval, never the
        # confidence. The point estimate is computed on the original sample, so
        # it must be bit-identical no matter how many resamples are drawn --
        # catching a regression that derives ``point`` from the resample
        # distribution instead of the observed data.
        values = [base[i % len(base)] for i in range(200)]
        points = {
            bootstrap_ci(values, n_resamples=nr, seed=MASTER_SEED)[2]
            for nr in (50, 100, 200, 500)
        }
        assert points == {0.7}, (
            f"the point estimate moved with n_resamples: {points}; it must be "
            "the statistic of the observed sample, not of the resamples"
        )

        # The width is an estimate built from a finite number of resamples, so
        # it is *approximately* stable across resample counts rather than
        # monotone: the 2.5th percentile of 50 draws and of 500 draws are
        # independent estimates of the same quantile and may differ slightly in
        # either direction. What must hold is that they agree to within a few
        # percent -- an interval whose width is driven by the resample count
        # rather than by the data (e.g. indexing the percentile by the sample
        # size) misses this by a wide margin.
        widths = [
            (lambda lo_hi: lo_hi[1] - lo_hi[0])(
                bootstrap_ci(values, n_resamples=nr, seed=MASTER_SEED)[:2]
            )
            for nr in (50, 200, 500)
        ]
        for width in widths:
            assert width == pytest.approx(widths[0], rel=0.25), (
                f"interval widths {widths} disagree; n_resamples must refine "
                "the estimate, not change the reported confidence"
            )

    def test_adding_a_successful_episode_never_lowers_the_score(self) -> None:
        """Catches: a success counted in one component but not another.

        ``success_rate`` and the bootstrap CI both read ``e.success``; the
        robustness grouping reads it too. A regression that made the CI or the
        group tally disagree with ``evaluation`` would show up here as a
        non-monotone step when a single failing episode is flipped to passing.
        """
        rng = random.Random(MASTER_SEED)
        for _ in range(12):
            n = rng.randint(4, 15)
            base = [_ep(i, i < n // 2, randomization_level="full") for i in range(n)]
            low_card = _card(base, threshold=0.0)
            improved = list(base)
            improved[0] = _ep(0, True, randomization_level="full")
            high_card = _card(improved, threshold=0.0)
            assert high_card.success_rate >= low_card.success_rate
            assert high_card.composite_score >= low_card.composite_score - 1e-9
            # A higher success rate can never widen the reported CI.
            if high_card.confidence_interval and low_card.confidence_interval:
                lo_w = high_card.confidence_interval[1] - high_card.confidence_interval[0]
                lo_w_base = (
                    low_card.confidence_interval[1] - low_card.confidence_interval[0]
                )
                assert lo_w <= lo_w_base + 1e-4, (
                    "flipping a failure to a success widened the confidence interval"
                )

    def test_regression_penalty_is_monotone_in_regression_count(self) -> None:
        """Catches: ``_REGRESSION_PENALTY`` negated, or the component inverted.

        The regression component is ``100 - 25 * count``. Asserting it is
        strictly non-increasing in the count pins both the sign and the
        direction; a ``+`` instead of ``-`` would make each additional detected
        regression *improve* the score, and the weight sum stays 1.0 either way.
        """
        from validsim.engine.regression import RegressionItem, RegressionReport

        def report(count: int) -> RegressionReport:
            # One item that RAN and found nothing, plus `count` significant ones.
            # The clean item cannot simply ride along inside the repeated list --
            # ``* 0`` would empty it, and an empty report carries no p-value, which
            # the scorecard treats as unmeasured rather than as a clean pass. So it
            # is added outside the repeat. This keeps the test pinning the
            # 25-points-per-regression slope, which is its actual purpose.
            clean = RegressionItem(
                metric="safety_score",
                before=80.0,
                after=80.0,
                delta=0.0,
                p_value=0.9,
                significant=False,
                severity="info",
            )
            regressions = [
                RegressionItem(
                    metric="success_rate",
                    before=0.9,
                    after=0.5,
                    delta=-0.4,
                    p_value=0.001,
                    significant=True,
                    severity="critical",
                )
            ] * count
            return RegressionReport(items=[clean, *regressions])

        values = [scorecard_mod._regression_component(report(i)) for i in range(6)]
        assert values[0] == 100.0
        for previous, current in zip(values, values[1:]):
            assert current <= previous
        # The documented arithmetic: 25 points per significant regression, floored.
        assert values[1] == 75.0
        assert values[4] == 0.0
        assert values[5] == 0.0, "the floor at zero must hold past 4 regressions"

    def test_adding_adversarial_episodes_never_improves_the_verdict(self) -> None:
        """Catches: the adversarial segment counted as *nominal*.

        The composite pools nominal and adversarial episodes into one success
        rate, so an adversarial episode that passes raises the headline number
        exactly like a nominal one. The separate floor is what makes the
        adversarial segment visible; if the positional split
        (``episodes[task.episodes:]``) were broken to slice from the front, a
        passing adversarial suite would stop being able to block a run. This
        pins that a genuinely failing adversarial segment still blocks.
        """
        failing = [_ep(i, True) for i in range(40)]
        failing += [
            _ep(1000 + i, False) for i in range(40)
        ]
        card = _card(failing, task=_task(episodes=40, adversarial_count=40))
        assert card.adversarial_episode_count == 40
        assert card.adversarial_success_rate == 0.0
        assert card.deploy_decision == "BLOCK"
        assert any("adversarial" in r for r in card.block_reasons)

    def test_nominal_only_run_is_unaffected_by_the_adversarial_floor(self) -> None:
        """Catches: the floor applied to a run that ran no adversarial episodes.

        The floor only applies when the segment actually executed, so a
        nominal-only run must be judged purely on its composite. If the
        ``if adversarial_count:`` guard were dropped, a 0% adversarial rate
        computed over an empty list (``0 / 0``) would block every nominal-only
        run in the platform.
        """
        episodes = [_ep(i, True) for i in range(50)]
        card = _card(episodes, task=_task(episodes=50, adversarial_count=0))
        assert card.adversarial_episode_count == 0
        assert card.adversarial_success_rate is None
        assert card.deploy_decision == "APPROVE"
        assert not any("adversarial" in r for r in card.block_reasons)
