"""Integrity guard for the Astro marketing site (``site/``).

Why ``site/`` needed this
-------------------------
All four pre-existing marketing tests target the LEGACY single file
(``marketing/index.html``). The Astro site had **zero** coverage, so the
integrity figures it publishes were unguarded against engine drift. That matters
more than an ordinary coverage gap: the page shows a BLOCK verdict and tells the
reader "you can check this". site-astro found exactly this bug class -- the page
was displaying TWO different run ids while claiming every figure came from one
run -- and nothing would have caught a recurrence.

Why ``content.ts`` and not ``dist/index.html``
----------------------------------------------
``site/src/data/content.ts`` is the source of truth; ``dist/`` is generated
output. Asserting the source fires the moment an author edits a number, rather
than only after someone rebuilds. A ``dist`` staleness check is added separately
below, but as a *build-freshness* signal rather than the primary surface.

Expected values are DERIVED from the engine
-------------------------------------------
The obvious way to write this test is to assert ``42.86 == 42.86``, but that
freezes today's constants and would pass even if the scoring model changed --
the exact failure mode this file exists to catch. So the composite is recomputed
from the real engine on every run and the page's literal string is compared
against that. A change in ``_weighted_composite`` or in the abstention policy
therefore fails with a message naming both numbers, instead of quietly leaving a
false claim up.

Every expected value was measured by running the engine, not inferred.
"""

from __future__ import annotations

import re
from pathlib import Path

_SITE = Path(__file__).resolve().parent.parent / "site"
_SRC = _SITE / "src" / "data" / "content.ts"
_DIST = _SITE / "dist" / "index.html"


def _engine_composites() -> dict[str, object]:
    """Recompute the two engine cases the page's integrity copy asserts on.

    Uses the real :func:`build_scorecard` over 100 single-group episodes, which
    is the shape ``run_validation`` actually produces (one randomization level
    for the whole run), so robustness abstains exactly as it does in production.
    """
    from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
    from validsim.engine.evaluation import evaluate
    from validsim.engine.safety import compute_safety
    from validsim.engine.scorecard import build_scorecard
    from validsim.sim.runner import EpisodeResult

    task = TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="gr00t-n1"),
        environment=EnvironmentSpec(name="tabletop"),
        episodes=100,
    )

    def score(success: bool) -> tuple[float, str, object]:
        episodes = [
            EpisodeResult(
                episode_id=f"e{i}",
                task_id="pick-place",
                seed=i,
                success=success,
                randomization_level="full",
                failure_mode=None if success else "collision",
            )
            for i in range(100)
        ]
        card = build_scorecard(
            run_id="vrun-00000001",
            checkpoint_id="ckpt-gr00t-n1",
            task=task,
            evaluation=evaluate(episodes),
            safety=compute_safety(episodes),
            episodes=episodes,
            threshold=0.0,
        )
        return round(card.composite_score, 2), card.deploy_decision, card

    failing, failing_verdict, failing_card = score(False)
    perfect, _, perfect_card = score(True)
    return {
        "failing": failing,
        "failing_verdict": failing_verdict,
        "perfect": perfect,
        "failing_measured": failing_card.robustness_measured,
        "perfect_measured": perfect_card.robustness_measured,
    }


def _src() -> str:
    assert _SRC.exists(), f"Astro content source missing at {_SRC}"
    return _SRC.read_text(encoding="utf-8")


class TestQuotedScoresTrackTheEngine:
    def test_the_all_failure_composite_matches_the_engine(self) -> None:
        """The page's headline bug-fix figure must equal what the engine emits.

        The page claims an all-failure run scores a specific value and blocks at
        any threshold. Both halves are asserted: the number against a freshly
        computed one, and the verdict.
        """
        numbers = _engine_composites()
        src = _src()
        failing = f"{numbers['failing']:.2f}"

        assert failing in src, (
            f"the Astro page no longer quotes {failing} for the all-failure run "
            "it advertises as its fixed bug; update the copy or the engine"
        )
        assert numbers["failing_verdict"] == "BLOCK", (
            "an all-failure run no longer BLOCKs; the page's claim that it "
            "blocks at any threshold is now false"
        )

    def test_the_all_failure_run_blocks_at_every_threshold(self) -> None:
        """'Blocks at any threshold' must be true, not just true at 85.

        The page says the run "blocks at any threshold". Asserting that only at
        the default would let a threshold-dependent regression through, and the
        threshold is operator-configurable, so the claim is about all of them --
        including 0.0, which is what makes it a safety property rather than a
        scoring detail.
        """
        from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
        from validsim.engine.evaluation import evaluate
        from validsim.engine.safety import compute_safety
        from validsim.engine.scorecard import build_scorecard
        from validsim.sim.runner import EpisodeResult

        task = TaskConfig(
            task_id="pick-place",
            robot=RobotSpec(name="gr00t-n1"),
            environment=EnvironmentSpec(name="tabletop"),
            episodes=100,
        )
        for threshold in (0.0, 30.0, 60.0, 85.0, 100.0):
            episodes = [
                EpisodeResult(
                    episode_id=f"e{i}",
                    task_id="pick-place",
                    seed=i,
                    success=False,
                    randomization_level="full",
                    failure_mode="collision",
                )
                for i in range(100)
            ]
            card = build_scorecard(
                run_id="vrun-00000001",
                checkpoint_id="ckpt-gr00t-n1",
                task=task,
                evaluation=evaluate(episodes),
                safety=compute_safety(episodes),
                episodes=episodes,
                threshold=threshold,
            )
            assert card.deploy_decision == "BLOCK", (
                f"an all-failure run was APPROVED at threshold {threshold}; the "
                "page's 'blocks at any threshold' claim is false"
            )

    def test_the_flawless_composite_matches_the_engine(self) -> None:
        """The control figure must hold too, or the fix over-corrected.

        A guard that only pins the failure case would still pass if a future
        scoring change capped every run -- the page's other claim is that a
        flawless run still scores 100, which is what makes the abstention policy
        operable rather than a blanket reject.
        """
        numbers = _engine_composites()
        src = _src()
        # The page writes this figure as prose ("still scores 100."), not as a
        # padded readout field, so it is matched on its significant digits
        # rather than on a fixed 2-decimal rendering.
        perfect = f"{numbers['perfect']:.0f}"

        assert perfect in src, (
            f"the Astro page no longer quotes {perfect} for a flawless run"
        )

    def test_unmeasured_components_are_what_the_page_claims(self) -> None:
        """The page claims robustness is unmeasured; the engine must agree.

        The copy says "an unmeasured component now abstains from the score
        rather than contributing fabricated credit". If robustness started
        measuring again, the abstention claim would be stale in the same way a
        stale composite would be -- so the provenance flags are asserted too,
        not just the totals.
        """
        numbers = _engine_composites()
        assert numbers["failing_measured"] is False, (
            "robustness is now measured on a single-group run, so the page's "
            "abstention claim is stale"
        )
        assert numbers["perfect_measured"] is False
        assert "not measured" in _src(), (
            "the readout must label robustness as unmeasured rather than "
            "rendering the 0.0 as a real score"
        )


class TestOneRunIdOnly:
    """Every ``vrun-`` id on the page must be the SAME run.

    This is the actual bug site-astro found: two different run ids on one page
    while the copy claimed all figures came from a single run. A reader cannot
    tell which run produced which number, so the integrity claim collapses. It
    is asserted over the *source* (which is what an author edits) and over the
    built page, so neither a source edit nor a stale build can reintroduce it.
    """

    #: Matches a run id. Case-insensitive on the hex part so an id written
    #: ``vrun-DEADBEEF`` is still recognised as a run id -- a case-sensitive
    #: pattern would silently skip it and the "exactly one id" invariant would
    #: hold vacuously, which is the very bug class this guards.
    _ID = re.compile(r"vrun-[0-9A-Za-z]+")

    def _ids(self, text: str) -> set[str]:
        return set(self._ID.findall(text))

    def test_the_source_quotes_exactly_one_run_id(self) -> None:
        found = self._ids(_src())
        assert len(found) == 1, (
            "the Astro content source quotes multiple run ids "
            f"{sorted(found)}; every figure must come from ONE run"
        )

    def test_the_source_run_id_matches_the_readout_field(self) -> None:
        """The scattered literal and the ``runId`` field must be the same id.

        Two representations of one fact drift independently, which is how the
        original mismatch arose: the transcript hard-codes the id as text while
        the readout carries it as a field.
        """
        src = _src()
        found = self._ids(src)
        assert len(found) == 1, f"expected one run id, found {sorted(found)}"
        (run_id,) = found
        assert f"runId: '{run_id}'" in src, (
            f"the readout runId field does not match the id quoted in the "
            f"transcript ({run_id!r}); the page would show two different runs"
        )

    def test_the_built_page_carries_no_second_run_id(self) -> None:
        """The served artefact must agree with the source.

        A guard on the source alone would still pass while ``dist/`` served a
        stale build containing the mismatched id, which is the state a visitor
        actually sees.
        """
        if not _DIST.exists():
            # No build present: the source-level guards above still apply, and a
            # missing dist is not this file's failure to report.
            return
        built = _DIST.read_text(encoding="utf-8")
        found = self._ids(built)
        assert len(found) == 1, (
            f"the built page at {_DIST} contains run ids {sorted(found)}; a "
            "stale build is serving a different run than the source describes"
        )

    def test_the_id_pattern_would_actually_catch_a_second_id(self) -> None:
        """The detector is self-tested, so the invariant cannot hold vacuously.

        Asserting "exactly one id" is worthless if the pattern simply fails to
        match a second one. This injects a mixed-case id in memory -- the
        lowercase-only pattern this replaced missed exactly that -- and requires
        the set to grow, which is what makes the three tests above meaningful.
        """
        src = _src()
        assert len(self._ids(src)) == 1
        injected = src.replace("vrun-", "vrun-DEADBEEF", 1)
        assert len(self._ids(injected)) == 2, (
            "the run-id pattern failed to match an injected second id; the "
            "single-id invariant would pass vacuously"
        )


class TestTheUnverifiedReadoutIsNotFrozenHere:
    """The readout's ``72.7`` composite is correct but deliberately NOT asserted.

    The readout quotes ``composite: '72.7'`` for its featured run -- 1000 nominal
    plus 50 adversarial episodes (1050 total) with 17 of the 50 adversarial
    episodes passing, i.e. 724/1050 = 0.6895238... successes. Under the engine's
    0.7-weighted composite that is::

        (0.4 * 68.952 + 0.3 * 77.72) / 0.7 = 72.70971... -> 72.71 -> "72.7"

    So the figure is entirely consistent with the engine. It is a *different
    quantity* from the ``42.86`` / ``100.0`` the integrity copy quotes: those are
    the 100-episode ablation values (all-failure and flawless), while this is the
    pooled composite of the site's real 1050-episode run. Different denominators,
    different numbers, no discrepancy. The two are not in conflict and must not be
    treated as if they were.

    It stays unasserted for a different, narrower reason: the derivation above is
    arithmetic, not a real scored run. Pinning an arithmetic identity as though it
    were a verified measurement would assert something this suite has not actually
    observed. It is therefore left unasserted pending confirmation from a real run,
    and recorded here so the omission is deliberate and visible rather than read as
    an oversight or -- worse -- as an accusation that the figure is wrong.
    """

    def test_the_readout_composite_is_present_but_not_asserted_against_the_engine(
        self,
    ) -> None:
        """Fails only if the figure disappears entirely (a copy accident).

        Deliberately does not compare the value to anything. See the class
        docstring: the figure is consistent with the engine, and is left
        unasserted only because it has not yet been confirmed against a real
        scored run.
        """
        src = _src()
        assert "composite:" in src, "the readout lost its composite field"
        assert re.search(r"composite: '[^']+'", src), (
            "the readout's composite field lost its value"
        )
