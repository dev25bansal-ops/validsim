"""Behavioural tests for the adversarial binomial gate and its boundaries.

The gate answers the question an operator actually means by "the adversarial
suite is failing": *is the observed adversarial success rate significantly below
the floor, by more than sampling noise explains?* That is a one-sided exact
binomial test, not a bare ``rate < floor`` comparison, and the distinction is
the whole reason the code exists -- a hard comparison at 0.60 rejects roughly
40% of the time at 30 episodes, blocking two in five *healthy* runs and
training operators to ignore the gate.

Every test here asserts behaviour a deliberately broken implementation fails:

* a bare ``rate < floor`` comparison (no significance test);
* a two-sided test instead of one-sided;
* a flipped tail (``P(X >= k)`` instead of ``P(X <= k)``);
* the wrong direction of ``alpha`` (``p > alpha``);
* an off-by-one in the summation range, which shifts every p-value;
* the sample-size floor ignored, so tiny samples gate and block spuriously.

The implementation is cross-checked against an independent closed-form
evaluation of the same CDF (``math.lgamma``-based, no shared code with the
module under test) so a mutation of the summation itself is caught rather than
merely a change of verdict.
"""

from __future__ import annotations

import math
import random
from itertools import count

import pytest

from validsim.engine import scorecard as scorecard_mod
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard

FLOOR = scorecard_mod._ADVERSARIAL_SUCCESS_FLOOR
ALPHA = scorecard_mod._ADVERSARIAL_ALPHA
MIN_SAMPLES = scorecard_mod._ADVERSARIAL_MIN_SAMPLES

_ids = count()


def _ep(i: int, success: bool, *, level: str = "full") -> object:
    from validsim.sim.runner import EpisodeResult

    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id="t",
        seed=i,
        success=success,
        collision_count=0,
        max_contact_force_n=5.0,
        min_human_distance_m=1.5,
        failure_mode=None if success else "grasp_failure",
        duration_s=10.0,
        randomization_level=level,
    )


def _lgamma_binom_pmf(k: int, n: int, p: float) -> float:
    """Binomial PMF via lgamma, sharing no code with the module under test.

    Used as an independent oracle: the module computes each term with
    ``math.comb(n, k) * p**k * (1-p)**(n-k)``, which is fine at these sizes but
    shares its failure modes (an off-by-one range, a swapped exponent) with any
    reimplementation of the same expression. This form is different enough to be
    an independent check while remaining exact enough to compare at 1e-9.
    """
    if k < 0 or k > n:
        return 0.0
    log_pmf = (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )
    return math.exp(log_pmf)


def _exact_cdf(k: int, n: int, p: float) -> float:
    """``P(X <= k)`` under ``Binomial(n, p)``, summed independently."""
    return math.fsum(_lgamma_binom_pmf(i, n, p) for i in range(k + 1))


# ---------------------------------------------------------------------------
# The gate predicate itself
# ---------------------------------------------------------------------------


class TestAdversarialSignificanceMatchesExactBinomial:
    """``_adversarial_significant`` must be the one-sided exact binomial test."""

    def test_matches_independent_cdf_on_randomised_inputs(self) -> None:
        """Catches: any change to the p-value itself -- an off-by-one in the
        summation range, a swapped exponent, ``comb(n, k)`` replaced by
        ``comb(n, n - k)``, or a tail flipped to ``P(X >= k)``.

        Each of those still returns a plausible-looking boolean for some inputs,
        so comparing against a *value* oracle over a randomised sweep is the
        only way to see them. Verdicts coincide with an independent CDF to
        1e-9 across the whole input space the gate can be called with.
        """
        rng = random.Random(0x5EED)
        for _ in range(300):
            n = rng.randint(1, 400)
            k = rng.randint(0, n)
            p = round(rng.uniform(0.05, 0.95), 2)
            alpha = round(rng.uniform(0.01, 0.2), 3)
            expected_p = _exact_cdf(k, n, p)
            assert (expected_p < alpha) == scorecard_mod._adversarial_significant(
                k, n, p, alpha
            ), (
                f"verdict disagrees with the exact binomial CDF for "
                f"k={k}, n={n}, p={p}, alpha={alpha} "
                f"(exact p={expected_p:.6g})"
            )

    def test_cdf_is_monotone_decreasing_in_observed_successes(self) -> None:
        """Catches: a p-value that does not fall as evidence of success rises.

        A flipped comparison (``p > alpha``) inverts the gate: a checkpoint that
        passes every adversarial episode would be blocked and one that fails
        them all would be approved. Monotonicity exposes that immediately, and
        the endpoint assertions below pin the direction explicitly.
        """
        for n in (30, 100, 500):
            verdicts = [
                scorecard_mod._adversarial_significant(k, n, FLOOR, ALPHA)
                for k in range(n + 1)
            ]
            # ``P(X <= k)`` decreases in ``k``, so the gate fires on a
            # contiguous prefix of small counts and stops firing for good.
            first_false = next((i for i, v in enumerate(verdicts) if not v), None)
            assert first_false is not None, f"n={n}: nothing ever stops blocking"
            assert verdicts[0] is True, f"n={n}: a total wipeout did not block"
            assert verdicts[n] is False, f"n={n}: a perfect segment blocked"
            for k in range(first_false):
                assert verdicts[k] is True, (
                    f"n={n}: k={k} blocks but so does a larger k, so the "
                    "p-value is not monotone in the number of successes"
                )
            for k in range(first_false, n + 1):
                assert verdicts[k] is False, (
                    f"n={n}: k={k} does not block but so does a smaller k, so "
                    "the p-value is not monotone in the number of successes"
                )

    def test_never_blocks_a_run_at_or_above_the_floor(self) -> None:
        """The operator-meaning check, and the single most important property.

        A sample whose observed rate is at or above the floor has, by
        definition, not demonstrated a failure. If the gate blocks there, it is
        punishing healthy checkpoints. Swept exhaustively over a wide band of
        sample sizes rather than sampled, because the defect is a boundary
        condition and sampled testing walks straight past it.
        """
        for n in range(1, 401):
            start = math.ceil(FLOOR * n)
            for k in range(start, n + 1):
                assert not scorecard_mod._adversarial_significant(
                    k, n, FLOOR, ALPHA
                ), (
                    f"{k}/{n} (rate {k / n:.3f} >= floor {FLOOR}) was blocked; "
                    "the gate is punishing a checkpoint that met the floor"
                )

    def test_blocks_a_run_far_below_the_floor(self) -> None:
        """The converse: a genuinely failing suite must always be caught.

        Asymmetric with the test above on purpose. A gate that never blocks and a
        gate that always blocks are both broken, and only asserting one of them
        leaves the other undetected.
        """
        for n in (30, 100, 500):
            for k in (0, 1, max(1, n // 20)):
                assert scorecard_mod._adversarial_significant(
                    k, n, FLOOR, ALPHA
                ), f"{k}/{n} is far below the {FLOOR:.0%} floor but was not blocked"

    def test_degenerate_counts_never_block(self) -> None:
        """Catches: the ``if total <= 0`` guard removed or weakened.

        The guard is what stops an empty (or nonsensical) segment from being
        treated as a total wipeout. Without it, ``total == 0`` falls through to
        the summation, and ``range(observed_ok + 1)`` with ``observed_ok == 0``
        yields exactly one term -- ``comb(0, 0) * floor**0 * (1-floor)**0 ==
        1.0`` -- so the p-value comes out as 1.0 and the empty segment would
        *pass*. Worse, a negative ``total`` reaches ``math.comb`` and raises
        ``ValueError``, turning a nominal-only run into a crash.

        Asserting the return value alone is not enough: the negative cases
        additionally assert that no exception escapes.
        """
        for total in (0, -1, -5):
            for observed_ok in (-3, -1, 0, 1):
                assert scorecard_mod._adversarial_significant(
                    observed_ok, total, FLOOR, ALPHA
                ) is False, (
                    f"total={total}, observed_ok={observed_ok} did not short-"
                    "circuit; an empty segment must never be read as a failure"
                )

    def test_degenerate_counts_do_not_raise(self) -> None:
        """The crash half of the same defect, asserted independently.

        ``math.comb`` rejects a negative count, so without the guard a
        nonsensical input propagates ``ValueError`` out of ``build_scorecard``
        and fails an entire validation run. A ``try``/``except`` that would
        swallow the error would also pass a return-value-only assertion, so the
        absence of an exception is checked on its own.
        """
        for total in (0, -1, -5):
            try:
                scorecard_mod._adversarial_significant(0, total, FLOOR, ALPHA)
            except (ValueError, TypeError, ZeroDivisionError) as exc:  # pragma: no cover
                pytest.fail(
                    f"total={total} raised {exc!r} instead of short-circuiting"
                )

    def test_alpha_is_one_sided_and_directionally_correct(self) -> None:
        """Catches: ``p > alpha``, or a two-sided ``1 - p < alpha``.

        A symmetric decision rule around the same alpha would agree with the
        one-sided rule in the middle of the distribution (where the p-value is
        near 0.5) and disagree deep in the *upper* tail, so the cases below walk
        the lower tail around a genuine near-boundary p-value and confirm the
        decision switches exactly where the exact CDF crosses alpha. A two-sided
        rule never blocks there, because a two-sided test only rejects when the
        observation is far from the null in *either* direction.
        """
        n, k = 100, 47           # rate 0.47, exact one-sided p = 0.0058
        exact = _exact_cdf(k, n, FLOOR)
        assert 0.005 < exact < 0.09, (
            f"pivot p={exact} must be a genuine near-boundary case"
        )
        # Just inside alpha -> blocks.
        assert scorecard_mod._adversarial_significant(k, n, FLOOR, exact * 1.01)
        # Just outside alpha -> does not block.
        assert not scorecard_mod._adversarial_significant(k, n, FLOOR, exact * 0.99)
        # A two-sided rule would additionally reject the *upper* tail, which
        # this one-sided gate must never do.
        for upper_k in (95, 100):
            assert not scorecard_mod._adversarial_significant(
                upper_k, n, FLOOR, 0.05
            ), (
                f"{upper_k}/{n} is far ABOVE the floor but was blocked; the "
                "test is being applied two-sided"
            )

    def test_verdict_is_deterministic(self) -> None:
        """Catches: accidental dependence on a shared/global RNG.

        The gate is called once per run inside ``build_scorecard``. If it drew
        from ``random.random()`` rather than computing an exact tail, two
        identical runs could disagree -- and an intermittent deploy verdict is
        the worst possible failure mode for a gate.
        """
        for n, k in ((100, 40), (100, 60), (500, 250), (30, 5)):
            first = [
                scorecard_mod._adversarial_significant(k, n, FLOOR, ALPHA)
                for _ in range(20)
            ]
            assert len(set(first)) == 1, f"n={n}, k={k} gave {set(first)}"


# ---------------------------------------------------------------------------
# The gate's wiring into the verdict
# ---------------------------------------------------------------------------


def _card(nominal_pass: int, nominal_total: int, adv_pass: int, adv_total: int,
          threshold: float = 85.0):
    """Build a scorecard with a controlled nominal and adversarial segment."""
    from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig

    episodes = [_ep(i, i < nominal_pass) for i in range(nominal_total)]
    episodes += [
        _ep(10_000 + i, i < adv_pass) for i in range(adv_total)
    ]
    task = TaskConfig(
        task_id="t",
        robot=RobotSpec(name="r"),
        environment=EnvironmentSpec(name="e"),
        episodes=nominal_total,
        adversarial_count=adv_total,
    )
    return build_scorecard(
        run_id="vrun-1",
        checkpoint_id="ckpt-1",
        task=task,
        evaluation=evaluate(episodes),
        safety=compute_safety(episodes),
        episodes=episodes,
        threshold=threshold,
        created_at="2026-01-01T00:00:00+00:00",
    )


class TestAdversarialGateBoundaries:
    """The sample-size boundary is where a bare rate comparison would misfire."""

    def test_min_samples_constant_matches_its_documented_derivation(self) -> None:
        """Catches: ``_ADVERSARIAL_MIN_SAMPLES`` retuned to any other value.

        30 is not a round number picked by taste: at a true adversarial rate of
        exactly the 0.60 floor, a 30-episode sample lands below the floor about
        42% of the time, which is why a *hard* cut at 0.60 would block roughly
        two in five healthy runs. The constant is the documented threshold at
        which the binomial test -- not the raw rate -- takes over as the
        decision rule, so lowering it re-admits exactly the small samples the
        design excludes, and raising it silently exempts real evidence.

        Asserting the literal (rather than only behaviour) is deliberate: the
        behaviour at the two adjacent sizes is already covered by the tests
        above and below, so what is left to pin is the constant itself.
        """
        assert MIN_SAMPLES == 30, (
            f"_ADVERSARIAL_MIN_SAMPLES is {MIN_SAMPLES}, not the documented 30; "
            "the value is derived from the 42%-below-floor rate at a true 0.60"
        )
        # The derivation the comment claims: at a true rate of 0.60, a
        # 30-episode sample lands below the floor about 42% of the time.
        # Simulated with a pinned seed so the figure is reproducible.
        rng = random.Random(0xB0A7)
        trials = 20_000
        hits = sum(
            1
            for _ in range(trials)
            if sum(1 for _ in range(MIN_SAMPLES) if rng.random() < FLOOR)
            / MIN_SAMPLES
            < FLOOR
        )
        assert 0.38 < hits / trials < 0.46, (
            f"simulated sub-floor rate {hits / trials:.3f} is far from the "
            "documented ~42%, so the constant's justification no longer holds"
        )

    def test_below_min_samples_is_reported_but_not_gated(self) -> None:
        """Catches: the ``>= _ADVERSARIAL_MIN_SAMPLES`` guard removed.

        4 of 4 adversarial episodes failing is a rate of 0.0 -- but 2 of 4 is a
        coin flip, not evidence. Blocking on a sample that small trains
        operators to ignore the gate, which is the failure mode the floor is
        meant to avoid. The information must still be *reported* so the segment
        is visible without being actionable.
        """
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=0, adv_total=4)
        assert card.adversarial_episode_count == 4
        assert card.adversarial_success_rate == 0.0
        assert not any("adversarial" in r for r in card.block_reasons), (
            "a 4-episode adversarial sample was gated; below the minimum "
            "sample size the rate is noise, not evidence"
        )

    def test_at_min_samples_a_significant_shortfall_blocks(self) -> None:
        """The first size at which the gate is allowed to act."""
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=0,
                     adv_total=MIN_SAMPLES)
        assert card.adversarial_episode_count == MIN_SAMPLES
        assert any("adversarial" in r for r in card.block_reasons)
        assert card.deploy_decision == "BLOCK"

    def test_one_below_min_samples_is_not_gated(self) -> None:
        """Pins the boundary exactly: ``n = MIN_SAMPLES - 1`` must not gate.

        An off-by-one in the comparison (``>`` for ``>=``) blocks one episode
        too many, or one too few. Both directions are asserted so neither
        survives.
        """
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=0,
                     adv_total=MIN_SAMPLES - 1)
        assert card.adversarial_episode_count == MIN_SAMPLES - 1
        assert card.adversarial_success_rate == 0.0
        assert not any("adversarial" in r for r in card.block_reasons)

    def test_significance_test_spares_runs_a_bare_rate_comparison_would_block(
        self,
    ) -> None:
        """The concrete justification for the binomial test over ``rate < floor``.

        This is the behaviour that distinguishes the two designs, and it is the
        reason the gate exists. At a true adversarial rate of exactly the floor,
        a hard ``rate < floor`` comparison rejects roughly 40% of the time at 30
        episodes -- it would block two in five *healthy* runs and train
        operators to ignore the gate.

        The cases below are all genuinely *below* the floor yet carry no
        significant evidence of failure, so the binomial test correctly spares
        them while a bare comparison would block. Asserting the spare explicitly
        is what makes the significance test load-bearing: delete it and reduce
        the gate to a bare comparison, and each of these runs flips to BLOCKED.
        """
        spared: list[tuple[int, int]] = []
        for n in (30, 50, 100, 200):
            for k in range(n + 1):
                if k / n >= FLOOR:
                    continue
                if not scorecard_mod._adversarial_significant(k, n, FLOOR, ALPHA):
                    spared.append((n, k))
        assert spared, (
            "no sub-floor sample was spared; the gate has degenerated into a "
            "bare `rate < floor` comparison"
        )
        # The smallest shortfall the gate tolerates, per sample size.
        for n in (30, 50, 100):
            tolerated = [k for nn, k in spared if nn == n]
            assert tolerated, f"n={n}: every sub-floor sample was blocked"
            worst = max(tolerated)
            assert worst / n < FLOOR, "sanity: the spared samples are sub-floor"
        # And the sparing is visible end-to-end, not just in the predicate.
        n = 100
        k = max(kk for nn, kk in spared if nn == n)
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=k, adv_total=n)
        assert card.adversarial_success_rate < FLOOR
        assert not any("adversarial" in r for r in card.block_reasons), (
            f"{k}/{n} is below the floor but was blocked; with no significance "
            "test this healthy run would fail the gate"
        )

    def test_floor_constant_is_load_bearing(self) -> None:
        """Catches: ``_ADVERSARIAL_SUCCESS_FLOOR`` edited or ignored.

        Retuning the floor from 0.60 moves the decision boundary for every
        large sample, because the floor is both the null rate of the binomial
        test and the explicit rate comparison. Lowering it to 0.40 makes a 50%
        adversarial run look acceptable; raising it makes a 70% run a failure.

        Note on the explicit ``adversarial_rate < _ADVERSARIAL_SUCCESS_FLOOR``
        comparison at the gate's call site: at the shipped ``alpha = 0.05`` it
        is **provably unreachable** -- exhaustively, for every ``(n, k)`` with
        ``1 <= n <= 5000``, ``P(X <= k) < 0.05`` already implies
        ``k / n < floor``, so no input reaches it and deleting the comparison
        is an *equivalent mutation* that no test can distinguish. It is
        retained as defence in depth for a retuned ``alpha`` (it does bind at
        ``alpha = 0.99``, where counts 601..635 of 1000 are "significant"
        despite clearing the floor). This test therefore pins the *constant*,
        which is reachable, rather than pretending the guard is exercised.
        """
        assert 0.0 < FLOOR < 1.0, "the floor must be a rate in (0, 1)"
        n = 1000
        # The floor is the null rate: a rate far below it is significant
        # evidence of failure, one far above it is not.
        assert scorecard_mod._adversarial_significant(
            int(0.50 * n), n, FLOOR, ALPHA
        ), "a 50% rate over 1000 episodes is significant evidence of failure"
        assert not scorecard_mod._adversarial_significant(
            int(0.70 * n), n, FLOOR, ALPHA
        ), "a 70% rate over 1000 episodes is not a failure"
        # Same observations, a lower floor -> the verdicts must move. At a 0.40
        # floor both 70% and 50% are *above* it, so both clear the bar and
        # neither is evidence of failing a 40% requirement.
        assert not scorecard_mod._adversarial_significant(
            int(0.70 * n), n, 0.40, ALPHA
        ), "a 70% rate is far above a 0.40 floor"
        assert not scorecard_mod._adversarial_significant(
            int(0.50 * n), n, 0.40, ALPHA
        ), "a 50% rate is above a 0.40 floor"
        # ...whereas at the shipped 0.60 floor the same 50% run is a failure.
        assert scorecard_mod._adversarial_significant(
            int(0.50 * n), n, FLOOR, ALPHA
        ), "the floor must decide the verdict, not the sample size"

    def test_nominal_only_run_is_never_gated(self) -> None:
        """Catches: the ``if adversarial_count:`` guard dropped.

        With the guard gone a nominal-only run reaches the gate with
        ``adversarial_count == 0``. The block condition requires
        ``adversarial_count >= _ADVERSARIAL_MIN_SAMPLES``, which ``0`` fails, so
        the run is not blocked -- but ``adversarial_rate`` is computed first, as
        ``adversarial_ok / adversarial_count`` -> ``0 / 0``. The
        ``ZeroDivisionError`` is the observable defect, and it takes down every
        nominal-only validation in the platform.

        The crash is asserted directly rather than inferred from a block reason:
        a hypothetical ``try``/``except`` that swallowed it would still produce a
        clean-looking "no adversarial block reason" result.
        """
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=0, adv_total=0)
        assert card.adversarial_episode_count == 0
        assert card.adversarial_success_rate is None, (
            "an unexecuted adversarial segment must report None, not a rate; a "
            "0/0 division would raise, and a 0.0 fallback would claim a "
            "measured 0% success"
        )
        assert not any("adversarial" in r for r in card.block_reasons)
        assert card.deploy_decision == "APPROVE"

    def test_nominal_only_run_never_raises(self) -> None:
        """The same defect asserted as an absence of exceptions.

        ``0 / 0`` raises ``ZeroDivisionError`` directly out of
        ``build_scorecard``. Asserting the field values alone would not notice
        if a future refactor guarded the division but still let the *gate* run,
        so the no-raise property is checked on its own.
        """
        for _ in range(3):
            try:
                card = _card(nominal_pass=10, nominal_total=10,
                             adv_pass=0, adv_total=0)
            except ZeroDivisionError:  # pragma: no cover - the regression itself
                pytest.fail(
                    "a nominal-only run raised ZeroDivisionError in the "
                    "adversarial gate; the `if adversarial_count:` guard is gone"
                )
            assert card.adversarial_success_rate is None

    def test_perfect_adversarial_segment_never_blocks(self) -> None:
        """The control for the whole class: 100% must be clean at any size."""
        for n in (1, 29, 30, 31, 500):
            card = _card(nominal_pass=50, nominal_total=50, adv_pass=n,
                         adv_total=n)
            assert card.adversarial_success_rate == 1.0
            assert not any("adversarial" in r for r in card.block_reasons)
            assert card.deploy_decision == "APPROVE"

    def test_failing_adversarial_segment_blocks_a_perfect_nominal_run(self) -> None:
        """The headline feature must be able to block on its own.

        The composite pools nominal and adversarial episodes into a single
        success rate, so a checkpoint that is flawless nominally and fails every
        adversarial episode scores exactly what a mediocre nominal run with a
        perfect adversarial suite scores. This is the concrete regression: if
        the gate stops firing, a 100%-nominal / 0%-adversarial run approves.
        """
        # Pooled success is identical (900/1000) and clears the 85 composite
        # gate, so the ONLY thing separating the two verdicts is the adversarial
        # segment. Were the composite to decide alone, both would approve.
        perfect_nominal_failing_adv = _card(
            nominal_pass=900, nominal_total=900, adv_pass=0, adv_total=100
        )
        mediocre_nominal_perfect_adv = _card(
            nominal_pass=800, nominal_total=900, adv_pass=100, adv_total=100
        )
        assert perfect_nominal_failing_adv.deploy_decision == "BLOCK"
        assert mediocre_nominal_perfect_adv.deploy_decision == "APPROVE"
        # ...and the composite cannot tell them apart, which is why the
        # separate gate exists.
        assert perfect_nominal_failing_adv.composite_score == pytest.approx(
            mediocre_nominal_perfect_adv.composite_score, abs=1.0
        )

    def test_block_reason_names_the_rate_and_the_floor(self) -> None:
        """Catches: a block reason that omits the numbers an operator needs.

        The reason is the only thing a human reads when a run is blocked. An
        empty or generic string still satisfies "block_reasons is non-empty",
        so the content is asserted rather than the presence.
        """
        card = _card(nominal_pass=50, nominal_total=50, adv_pass=5, adv_total=100)
        reasons = [r for r in card.block_reasons if "adversarial" in r]
        assert len(reasons) == 1
        reason = reasons[0]
        assert "5/100" in reason, f"reason does not state the tally: {reason!r}"
        assert "significantly" in reason, (
            f"reason must say the shortfall is statistically significant: {reason!r}"
        )
        assert f"{FLOOR:.0%}" in reason, (
            f"reason does not state the floor: {reason!r}"
        )

    def test_adversarial_segment_split_is_positional_and_fail_safe(self) -> None:
        """Catches: the ``episodes[task.episodes:]`` split sliced from the front,
        or an off-by-one that shifts the boundary by one episode.

        The split is positional because the runner appends one episode per
        scenario after the nominal block. Any extra episodes beyond
        ``task.episodes`` are treated as adversarial, which is the fail-safe
        direction: an unrecognised episode counts against the floor rather than
        being pooled into the nominal rate.
        """
        from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
        from validsim.engine.scorecard import _adversarial_segment

        episodes = [_ep(i, True) for i in range(10)]
        task = TaskConfig(task_id="t", robot=RobotSpec(name="r"),
                          environment=EnvironmentSpec(name="e"), episodes=10)
        assert len(_adversarial_segment(episodes, task)) == 0
        assert len(_adversarial_segment(episodes + [_ep(1, False)], task)) == 1
        # A task asking for more episodes than exist yields an empty segment
        # rather than a negative slice leaking the nominal episodes back in.
        greedy = task.model_copy(update={"episodes": 999})
        assert _adversarial_segment(episodes, greedy) == []
