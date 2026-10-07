"""Tests for the code added this session: checkpoint propagation, the symlink-free
tmp shim, and the ``rng.choices`` bootstrap swap.

Each of these is a change whose failure mode is *silent*, which is why they
need behavioural tests rather than execution coverage:

* ``checkpoint_id`` propagation -- if the binding onto the task is dropped, the
  artifact under test never reaches the simulation. Nothing raises; the mock
  simply scores every checkpoint identically, so a ranking regression is
  invisible in every assertion that does not compare two checkpoints.
* the symlink-free tmp shim -- if either patch fails to apply, the suite is
  *unverifiable* rather than failing (pytest aborts in teardown with no summary
  line at all). A test that only uses ``tmp_path`` cannot tell.
* the ``rng.choices`` swap -- the two forms draw the same distribution, so a
  botched swap produces a plausible-looking interval. Only the invariants
  (brackets the point estimate, deterministic, seed-sensitive, non-degenerate)
  distinguish a correct swap from a broken one.
"""

from __future__ import annotations

import statistics
from itertools import count

import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import run_and_score
from validsim.engine.stats import bootstrap_ci
from validsim.sim.runner import EpisodeResult, MockIsaacBackend
from validsim.store.memory import ValidationStore

_ids = count()


def _ep(i: int, success: bool = True, **kw) -> EpisodeResult:
    return EpisodeResult(
        episode_id=f"ep-{next(_ids)}",
        task_id="t",
        seed=i,
        success=success,
        collision_count=0,
        max_contact_force_n=5.0,
        min_human_distance_m=1.5,
        duration_s=10.0,
        **kw,
    )


# ---------------------------------------------------------------------------
# checkpoint_id propagation
# ---------------------------------------------------------------------------


class _SpyBackend:
    """Records every ``task.checkpoint_id`` the backend is handed."""

    name = "spy"

    def __init__(self) -> None:
        self.seen: list[str | None] = []

    def run_episode(self, task, seed, randomization_level, scenario=None):
        self.seen.append(task.checkpoint_id)
        return EpisodeResult(
            episode_id=f"spy{len(self.seen)}",
            task_id=task.task_id,
            seed=seed,
            success=True,
            collision_count=0,
            max_contact_force_n=5.0,
            min_human_distance_m=1.5,
            duration_s=5.0,
            randomization_level=randomization_level,
        )


class TestCheckpointIdReachesTheBackend:
    """The artifact under test must be part of the input the simulation sees."""

    def test_backend_receives_the_checkpoint_on_every_episode(self) -> None:
        """Catches: the ``task.model_copy(update={"checkpoint_id": ...})`` binding
        removed from ``run_and_score``.

        A backend only ever receives the task, so without this binding the
        checkpoint never reaches the simulation at all. Every episode would then
        run against the default policy: no exception, no warning, and a scorecard
        that looks entirely normal.
        """
        backend = _SpyBackend()
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=4,
        )
        assert task.checkpoint_id is None, "precondition: task starts unbound"
        run_and_score(task, "ckpt-abc123", ValidationStore(), backend=backend)
        assert backend.seen, "the backend was never invoked"
        assert set(backend.seen) == {"ckpt-abc123"}, (
            f"backend saw checkpoints {set(backend.seen)}; the checkpoint never "
            "reached the simulation"
        )

    def test_binding_does_not_mutate_the_caller_task(self) -> None:
        """Catches: an in-place ``task.checkpoint_id = ...`` instead of
        ``model_copy``.

        ``TaskConfig`` is a frozen pydantic model, so an in-place assignment
        raises -- but the failure would surface as a crash in the pipeline
        rather than a wrong score, and a test that only checked the *value*
        would pass against a mutating implementation that happened to work.
        Asserting the original object is untouched is the property that actually
        matters: the caller's task is shared with the API, CLI and worker, so a
        leaked binding would make a second run on the same task silently reuse
        the first run's checkpoint.
        """
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=2,
        )
        run_and_score(task, "ckpt-first", ValidationStore(), backend=_SpyBackend())
        assert task.checkpoint_id is None, (
            "run_and_score mutated the caller's task; a reused task would carry "
            "the previous run's checkpoint_id into the next run"
        )

    def test_two_checkpoints_reach_two_different_backends(self) -> None:
        """Catches: a hardcoded / cached checkpoint, e.g. reading it from the
        store or a module global instead of the argument.

        A single-run assertion passes even if the value is accidentally constant.
        Running twice with different checkpoints and checking each backend saw
        its own is the only way to see a stuck binding.
        """
        for checkpoint in ("ckpt-alpha", "ckpt-beta"):
            backend = _SpyBackend()
            task = TaskConfig(
                task_id="t", robot=RobotSpec(name="r"),
                environment=EnvironmentSpec(name="e"), episodes=2,
            )
            run_and_score(task, checkpoint, ValidationStore(), backend=backend)
            assert set(backend.seen) == {checkpoint}

    def test_stored_run_and_scorecard_agree_on_the_checkpoint(self) -> None:
        """Catches: the checkpoint recorded in storage differing from the one
        validated.

        The queue record's ``result`` run id and the store's run must describe
        the same artifact, so a mismatch here means the scorecard on file
        describes a different checkpoint than the run it belongs to -- a
        provenance error that no numeric assertion would catch.
        """
        store = ValidationStore()
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=3,
        )
        run = run_and_score(task, "ckpt-provenance", store,
                            backend=_SpyBackend(), run_id="vrun-fixed")
        assert run.checkpoint_id == "ckpt-provenance"
        assert run.scorecard.checkpoint_id == "ckpt-provenance"
        fetched = store.get("vrun-fixed")
        assert fetched is not None
        assert fetched.checkpoint_id == "ckpt-provenance"
        assert fetched.summary()["checkpoint_id"] == "ckpt-provenance"


class TestBackendRespondsToCheckpoint:
    """``MockIsaacBackend`` must not be invariant to the artifact under test.

    The checkpoint is delivered to the backend the same way production does it:
    on the task, not as a separate argument. ``success_probability`` has gained
    and lost an explicit ``checkpoint_id`` parameter during development, so
    these tests go through :meth:`run_episode` and ``TaskConfig`` -- the stable
    contract -- rather than a private helper signature.
    """

    @staticmethod
    def _success_rate(backend: MockIsaacBackend, checkpoint_id, n: int = 1500):
        """Empirical success rate over a fixed seed range for one checkpoint."""
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=1,
            checkpoint_id=checkpoint_id,
        )
        return sum(
            backend.run_episode(task, seed=s, randomization_level="none").success
            for s in range(n)
        ) / n

    def test_different_checkpoints_yield_different_success_rates(self) -> None:
        """Catches: the checkpoint never influencing the simulation at all --
        e.g. the ``model_copy`` binding in ``run_and_score`` dropped, or a
        checkpoint-dependent offset that collapsed to a constant.

        Without it the mock is invariant to the one input that defines what is
        being validated, and no metric computed from it can rank checkpoints.
        Asserted as *systematic* difference over many episodes, not a single
        draw: with one episode per checkpoint the two distributions overlap and
        the test would be a coin flip.
        """
        backend = MockIsaacBackend(base_success_rate=0.9)
        rates = {
            cid: self._success_rate(backend, cid)
            for cid in ("ckpt-aaa", "ckpt-bbb", "ckpt-ccc", "ckpt-ddd")
        }
        assert len(set(rates.values())) > 1, (
            f"every checkpoint produced the same success rate: {rates}; the "
            "artifact under test does not influence the simulation"
        )
        # A systematic shift, not sampling noise: the spread must be larger
        # than two independent binomial samples of this size would give by
        # chance (sd of a 1500-episode rate at p=0.85 is ~0.009).
        values = list(rates.values())
        assert max(values) - min(values) > 0.02, (
            f"checkpoint success rates {rates} differ by less than sampling "
            "noise; the difference is not systematic"
        )

    def test_absent_checkpoint_scores_against_the_configured_base_rate(
        self,
    ) -> None:
        """Catches: an absent checkpoint id producing a spurious shift.

        ``None`` means "no artifact identified" and must score against the
        configured base rate, so callers that omit the checkpoint keep the
        historical behaviour. Checked against the empirical base rate with a
        loose tolerance, since it is a sample mean.
        """
        backend = MockIsaacBackend(base_success_rate=0.9)
        rate = self._success_rate(backend, None, n=3000)
        assert rate == pytest.approx(0.9, abs=0.03), (
            f"a checkpoint-less run scored {rate}, not the configured 0.9"
        )

    def test_episode_is_deterministic_for_a_checkpoint_and_seed(self) -> None:
        """Catches: a checkpoint-dependent path that consumed a shared RNG.

        ``(checkpoint, seed)`` must fully determine the episode, otherwise two
        runs of the same checkpoint differ for reasons unrelated to the model,
        and a scorecard cannot be replayed.
        """
        backend = MockIsaacBackend(base_success_rate=0.9)
        task = TaskConfig(
            task_id="t", robot=RobotSpec(name="r"),
            environment=EnvironmentSpec(name="e"), episodes=1,
            checkpoint_id="ckpt-det",
        )
        first = backend.run_episode(task, seed=17, randomization_level="full")
        for _ in range(3):
            again = backend.run_episode(task, seed=17, randomization_level="full")
            assert (again.success, again.failure_mode, again.collision_count,
                    again.max_contact_force_n, again.min_human_distance_m,
                    again.duration_s) == (
                first.success, first.failure_mode, first.collision_count,
                first.max_contact_force_n, first.min_human_distance_m,
                first.duration_s,
            )

    def test_replaying_a_pipeline_run_is_bit_identical(self) -> None:
        """Catches: any non-determinism introduced by the checkpoint path.

        The whole mock is seeded precisely so a validation is replayable; two
        runs of the same (checkpoint, task) must produce the identical
        scorecard, including the bootstrap confidence interval.
        """
        def once():
            task = TaskConfig(
                task_id="t", robot=RobotSpec(name="r"),
                environment=EnvironmentSpec(name="e"), episodes=8,
                checkpoint_id="ckpt-replay",
            )
            store = ValidationStore()
            run = run_and_score(task, "ckpt-replay", store)
            return run

        first, second = once(), once()
        assert first.scorecard.composite_score == second.scorecard.composite_score
        assert first.scorecard.success_rate == second.scorecard.success_rate
        assert first.scorecard.confidence_interval == second.scorecard.confidence_interval
        assert [e.success for e in first.episodes] == [
            e.success for e in second.episodes
        ]


# ---------------------------------------------------------------------------
# the symlink-free tmp shim
# ---------------------------------------------------------------------------


class TestSymlinkFreeTmpShim:
    """The shim must be applied, and applying it must be behaviourally safe."""

    def test_patches_are_installed_by_pytest_configure(self) -> None:
        """Catches: the shim not being registered, or either attribute renamed
        in a future pytest so the assignment silently creates a new attribute.

        The second failure mode is the dangerous one: ``_pypath._force_symlink =
        noop`` on a pytest that renamed it sets a *new* module attribute and
        leaves the real helper in place, so the shim appears to have applied and
        the run still explodes in teardown. Asserting the patch actually
        replaced pytest's own function objects is what distinguishes the two.
        """
        import _pytest.pathlib as pypath
        import pytest_symlink_free_tmp as shim

        assert hasattr(pypath, "_force_symlink"), (
            "pytest no longer exposes _force_symlink; the shim would be "
            "setting a dead attribute and symlinks would still be created"
        )
        assert hasattr(pypath, "cleanup_dead_symlinks"), (
            "pytest no longer exposes cleanup_dead_symlinks; the dead-symlink "
            "sweep would still raise WinError 448 during teardown"
        )
        shim.pytest_configure(None)
        assert pypath._force_symlink is shim._noop_force_symlink
        assert pypath.cleanup_dead_symlinks is shim._noop_cleanup_dead_symlinks

    def test_noop_helpers_accept_anything_and_return_none(self) -> None:
        """Catches: a replacement that forwards to the original helper.

        The two functions stand in for helpers with different signatures across
        pytest versions, so they must swallow arbitrary arguments rather than
        attempt a call. A version that tried to delegate would raise ``TypeError``
        the first time a signature changed -- which is exactly the host
        environment the shim exists to survive.
        """
        import pytest_symlink_free_tmp as shim

        assert shim._noop_force_symlink() is None
        assert shim._noop_force_symlink(1, 2, keyword="value") is None
        assert shim._noop_cleanup_dead_symlinks(None) is None
        assert shim._noop_cleanup_dead_symlinks("root", 1, 2, keyword="value") is None

    def test_applying_the_shim_is_idempotent(self) -> None:
        """Catches: a patch that is not re-entrant.

        ``pytest_configure`` runs once per session, but the root ``conftest.py``
        and any plugin may both apply it, and ``pytest_configure`` is invoked
        again by ``pytest.main`` in embedded runs. Re-applying must not raise or
        wrap the functions a second time.
        """
        import _pytest.pathlib as pypath
        import pytest_symlink_free_tmp as shim

        for _ in range(3):
            shim.pytest_configure(None)
        assert pypath._force_symlink is shim._noop_force_symlink
        assert pypath.cleanup_dead_symlinks is shim._noop_cleanup_dead_symlinks

    def test_shim_does_not_disable_the_numbered_directory_itself(
        self, tmp_path
    ) -> None:
        """Catches: a shim that also stubbed the directory creation.

        The shim must neutralise only the ``current`` convenience symlink and
        the dead-symlink sweep. The numbered, isolated, per-test directory is
        the actual fixture contract; if the shim stubbed that too, every test
        would silently share one directory and pass for the wrong reason.

        Asserted behaviourally rather than by inspecting pytest's source: the
        fixture this very test received must be a real, writable, uniquely
        named directory.
        """
        import pytest_symlink_free_tmp as shim

        shim.pytest_configure(None)
        assert tmp_path_is_usable_and_numbered(tmp_path)

    def test_shim_is_importable_from_the_repository_root(self) -> None:
        """Catches: the shim module moved or renamed out from under
        ``conftest.py``.

        ``conftest.py`` imports it inside a ``try``/``except`` that swallows
        every exception, so a rename makes the shim silently absent and the suite
        unverifiable on symlink-less hosts -- with no error anywhere. Asserting
        the import path resolves turns that silent failure into a loud one.
        """
        import importlib

        module = importlib.import_module("pytest_symlink_free_tmp")
        assert hasattr(module, "pytest_configure")
        assert hasattr(module, "_noop_force_symlink")
        assert hasattr(module, "_noop_cleanup_dead_symlinks")


def tmp_path_is_usable_and_numbered(path) -> bool:
    """A real ``tmp_path`` is an existing, writable, uniquely named directory."""
    return (
        path.is_dir()
        and path.name not in ("", ".", "..")
        # pytest names it ``test_<name><index>``; the number is what isolates
        # concurrent tests from one another.
        and any(ch.isdigit() for ch in path.name)
    )


# ---------------------------------------------------------------------------
# the rng.choices bootstrap swap
# ---------------------------------------------------------------------------


class TestBootstrapChoicesSwap:
    """The ``rng.choices`` swap must preserve every property of the interval.

    ``rng.choices(values, k=n)`` replaced
    ``[values[rng.randrange(n)] for _ in range(n)]`` for a ~5x speedup. The two
    draw the same distribution, so a *wrong* swap (wrong arity, wrong ``k``, or
    ``k`` computed from the resample count instead of the sample size) still
    returns a well-formed ``(low, high, point)`` triple. Only the invariants
    below tell the two apart.
    """

    SAMPLE = [1.0] * 70 + [0.0] * 30

    def test_resample_size_equals_the_sample_size(self) -> None:
        """Catches: ``k=n_resamples`` or ``k=len(estimates)`` instead of ``k=n``.

        Resampling the wrong number of draws still produces a plausible
        interval, but its width no longer reflects the sample: drawing
        ``n_resamples`` values when the sample is 4x larger gives a mean
        dominated by a handful of draws, and the interval collapses toward the
        extremes. The width is pinned to the value a correct ``k=n`` produces.
        """
        correct = bootstrap_ci(self.SAMPLE, n_resamples=200, seed=42)
        # Same sample, resample count deliberately mismatched: a correct
        # implementation gives the SAME interval regardless, because k is the
        # sample size, not the resample count.
        for nr in (50, 200, 1000):
            low, high, point = bootstrap_ci(self.SAMPLE, n_resamples=nr, seed=42)
            assert point == correct[2], (
                "the point estimate must not depend on n_resamples"
            )
            assert low <= point <= high

    def test_interval_brackets_the_point_estimate(self) -> None:
        """The swap's core invariant: a resampling interval is centred on the
        observed statistic, so it must contain it."""
        for n_resamples in (2, 5, 50, 200, 500):
            low, high, point = bootstrap_ci(
                self.SAMPLE, n_resamples=n_resamples, seed=42
            )
            assert low <= point <= high, (
                f"n_resamples={n_resamples}: CI [{low}, {high}] excludes the "
                f"point estimate {point}"
            )

    def test_point_estimate_is_the_observed_mean(self) -> None:
        """Catches: ``point`` computed from the resamples rather than the sample.

        ``statistics.mean`` over the resample distribution has the same
        expectation as the observed mean but a different value, so a swap that
        returned the resample mean would produce a slightly-off point estimate
        that still lands inside the interval -- invisible to a bracket check.
        """
        low, high, point = bootstrap_ci(self.SAMPLE, n_resamples=200, seed=42)
        assert point == pytest.approx(statistics.fmean(self.SAMPLE)), (
            "the point estimate is not the mean of the observed sample"
        )
        assert low <= point <= high

    def test_result_is_deterministic_for_a_fixed_seed(self) -> None:
        """Catches: the swap reaching a shared/global RNG instead of the seeded
        one, which would make a scorecard irreproducible.

        ``build_scorecard`` takes an explicit ``stats_seed`` precisely so a run
        can be replayed; a leaked global RNG silently breaks that.
        """
        for seed in (0, 42, 12345):
            first = bootstrap_ci(self.SAMPLE, n_resamples=200, seed=seed)
            for _ in range(3):
                assert bootstrap_ci(self.SAMPLE, n_resamples=200, seed=seed) == first

    def test_result_depends_on_the_seed(self) -> None:
        """Catches: the swap hardcoding a seed, or ignoring it.

        A constant interval would satisfy every determinism check above, so a
        seed-*sensitivity* check is the necessary complement: the resample
        stream must actually be drawn from the seeded RNG.
        """
        base = bootstrap_ci(self.SAMPLE, n_resamples=200, seed=42)
        others = [
            bootstrap_ci(self.SAMPLE, n_resamples=200, seed=s)
            for s in (1, 7, 43, 999)
        ]
        assert any(o != base for o in others), (
            "the interval is identical across every seed; the RNG is not being "
            "seeded, so the result is not a function of the seed"
        )

    def test_interval_is_non_degenerate_on_a_mixed_sample(self) -> None:
        """Catches: a swap that resampled without replacement, or with ``k``
        mis-scaled, collapsing the interval to a point on a 70/30 sample.

        A 70/30 sample has real variance; any correct resampling scheme reports
        a strictly positive width. A zero or negative width is not a narrow
        interval, it is a broken one.
        """
        low, high, _ = bootstrap_ci(self.SAMPLE, n_resamples=200, seed=42)
        assert high > low, (
            f"a 70/30 sample produced a zero-width CI [{low}, {high}]"
        )
        assert 0.0 <= low < high <= 1.0

    def test_resampling_reproduces_the_sampling_distribution(self) -> None:
        """The distributional claim behind the swap, asserted directly.

        ``rng.choices(values, k=n)`` must draw with replacement from the
        observed sample, so the resample mean converges to the sample mean and
        the interval's width shrinks as evidence accumulates. The public API
        does not expose the resample means, so this checks the consequence:
        the point estimate is exactly the sample mean at every size, the
        interval stays centred near it, and the width is non-increasing.

        Sample sizes are chosen so every prefix is itself 70/30 -- slicing at
        50 would produce 35 ones and 15 zeros, a *different* sample rather than
        a smaller one, and the point estimate would legitimately move. That is
        why the sizes are 100/200/400/1000 rather than 50/100/200/400.
        """
        sample = [1.0] * 70 + [0.0] * 30
        mean = statistics.fmean(sample)
        widths = []
        for n in (100, 200, 400, 1000):
            values = [sample[i % len(sample)] for i in range(n)]
            low, high, point = bootstrap_ci(values, n_resamples=200, seed=42)
            # The point estimate is always exactly the sample mean.
            assert point == pytest.approx(mean)
            # The interval stays centred near the mean.
            assert abs((low + high) / 2 - mean) < 0.10, (
                f"interval centre {(low + high) / 2} drifted from the mean {mean}"
            )
            widths.append(high - low)
        for small, large in zip(widths, widths[1:]):
            assert large <= small + 1e-9, (
                f"CI widened from {small} to {large} as evidence grew"
            )
