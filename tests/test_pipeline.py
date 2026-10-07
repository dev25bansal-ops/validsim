"""End-to-end tests for the shared validation pipeline (:func:`run_and_score`).

:func:`validsim.engine.pipeline.run_and_score` is the single sequence the API,
CLI and job worker all delegate to: derive a seed, generate adversarial
scenarios, run episodes against a simulation backend, evaluate/score, optionally
compare to a baseline, and persist the finished :class:`StoredRun`.

These tests exercise that orchestration for real -- no HTTP, no CLI, no
monkeypatching of the engine internals -- by driving it with the deterministic
:class:`~validsim.sim.runner.MockIsaacBackend` and the in-memory
:class:`~validsim.store.memory.ValidationStore`. The guarantees pinned here are
the ones the refactor must not break:

* the returned object is a fully-populated ``StoredRun`` (scorecard /
  evaluation / safety all present);
* a fixed ``(checkpoint_id, task)`` seed makes the composite score
  reproducible across calls;
* requesting adversarial scenarios actually adds those episodes;
* the finished run is persisted to (and retrievable from) the store;
* baseline lookup, backend-factory injection and the approve/block threshold
  behave as documented.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
from validsim.engine.pipeline import (
    DEFAULT_THRESHOLD,
    BaselineNotFoundError,
    run_and_score,
)
from validsim.scenarios.generator import AdversarialScenario
from validsim.sim.runner import (
    EpisodeResult,
    MockIsaacBackend,
    SimulationBackend,
)
from validsim.sim.runner import run_validation as _real_run_validation
from validsim.sim.runner import stable_seed
from validsim.store.memory import StoredRun, ValidationStore


def _task(episodes: int = 20, adversarial: int = 4) -> TaskConfig:
    """Build a small, fast task config for pipeline tests."""
    return TaskConfig(
        task_id="pick-place",
        robot=RobotSpec(name="franka"),
        environment=EnvironmentSpec(name="kitchen"),
        episodes=episodes,
        adversarial_count=adversarial,
    )


def _mock_backend() -> MockIsaacBackend:
    """A fresh, default (base_success_rate=0.9) deterministic mock backend."""
    return MockIsaacBackend()


@pytest.fixture
def store() -> ValidationStore:
    """An empty in-memory validation store."""
    return ValidationStore()


# ---------------------------------------------------------------------------
# 1. Shape of the returned run
# ---------------------------------------------------------------------------


class TestRunShape:
    def test_returns_stored_run(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-shape", store, backend=_mock_backend())
        assert isinstance(run, StoredRun)

    def test_scorecard_is_present(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-shape", store, backend=_mock_backend())
        assert run.scorecard is not None
        assert run.scorecard.composite_score is not None

    def test_evaluation_is_present(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-shape", store, backend=_mock_backend())
        assert run.evaluation is not None
        assert run.evaluation.total_episodes > 0

    def test_safety_is_present(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-shape", store, backend=_mock_backend())
        assert run.safety is not None
        assert 0.0 <= run.safety.safety_score <= 100.0

    def test_metadata_is_wired_through(self, store: ValidationStore) -> None:
        task = _task()
        run = run_and_score(task, "ckpt-meta", store, backend=_mock_backend())
        assert run.checkpoint_id == "ckpt-meta"
        assert run.task_id == task.task_id
        assert run.scorecard.checkpoint_id == "ckpt-meta"
        assert run.scorecard.task_id == task.task_id

    def test_run_id_is_generated_when_omitted(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-id", store, backend=_mock_backend())
        assert run.run_id.startswith("vrun-")

    def test_explicit_run_id_is_honored(self, store: ValidationStore) -> None:
        run = run_and_score(
            _task(), "ckpt-id", store, backend=_mock_backend(), run_id="vrun-fixed01"
        )
        assert run.run_id == "vrun-fixed01"
        assert run.scorecard.run_id == "vrun-fixed01"


# ---------------------------------------------------------------------------
# 2. Determinism for a fixed seed
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_composite_is_identical_across_two_calls(
        self, store: ValidationStore
    ) -> None:
        # Same (checkpoint_id, task) -> same derived seed -> same episodes ->
        # byte-identical composite, even though the two runs are distinct.
        first = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        second = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        assert first.scorecard.composite_score == second.scorecard.composite_score

    def test_all_scored_components_are_reproducible(
        self, store: ValidationStore
    ) -> None:
        a = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        b = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        assert a.scorecard.success_rate == b.scorecard.success_rate
        assert a.scorecard.safety_score == b.scorecard.safety_score
        assert a.scorecard.robustness_score == b.scorecard.robustness_score
        assert a.scorecard.failure_taxonomy == b.scorecard.failure_taxonomy
        assert a.evaluation.to_dict() == b.evaluation.to_dict()
        assert a.safety.to_dict() == b.safety.to_dict()

    def test_distinct_runs_despite_identical_scores(
        self, store: ValidationStore
    ) -> None:
        # Reproducibility of the score must not collapse the two runs into one
        # record -- the auto-generated run ids still differ.
        a = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        b = run_and_score(_task(), "ckpt-det", store, backend=_mock_backend())
        assert a.run_id != b.run_id

    def test_seed_is_derived_from_checkpoint_and_task(self) -> None:
        # The pipeline seeds scenario generation from stable_seed(checkpoint,
        # task); a different checkpoint therefore drives a different episode
        # stream. (The composite may coincidentally match, so we assert on the
        # seed contract the pipeline relies on rather than the score.)
        assert stable_seed("ckpt-a", "pick-place") != stable_seed(
            "ckpt-b", "pick-place"
        )


# ---------------------------------------------------------------------------
# 3. Adversarial scenarios are included when requested
# ---------------------------------------------------------------------------


class TestAdversarialScenarios:
    def test_adversarial_count_adds_episodes(self, store: ValidationStore) -> None:
        task = _task(episodes=20, adversarial=4)
        run = run_and_score(task, "ckpt-adv", store, backend=_mock_backend())
        # Nominal episodes + one per adversarial scenario.
        assert run.scorecard.episode_count == 24
        assert run.evaluation.total_episodes == 24
        assert len(run.episodes) == 24

    def test_zero_adversarial_runs_only_nominal(
        self, store: ValidationStore
    ) -> None:
        task = _task(episodes=15, adversarial=0)
        run = run_and_score(task, "ckpt-noadv", store, backend=_mock_backend())
        assert run.scorecard.episode_count == 15
        assert len(run.episodes) == 15

    def test_more_adversarial_scales_episode_count(
        self, store: ValidationStore
    ) -> None:
        task = _task(episodes=10, adversarial=12)
        run = run_and_score(task, "ckpt-manyadv", store, backend=_mock_backend())
        assert run.evaluation.total_episodes == 22

    def test_adversarial_episodes_are_unique(self, store: ValidationStore) -> None:
        # The trailing adversarial episodes are seeded after the nominal block,
        # so the episode-id space reflects the full run_validation contract.
        task = _task(episodes=8, adversarial=3)
        run = run_and_score(task, "ckpt-adv-ids", store, backend=_mock_backend())
        episode_ids = {e.episode_id for e in run.episodes}
        assert len(episode_ids) == 11  # 8 nominal + 3 adversarial, all unique


# ---------------------------------------------------------------------------
# 4. Persistence to the store
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_run_is_retrievable_from_store(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-persist", store, backend=_mock_backend())
        fetched = store.get(run.run_id)
        # The store snapshots on write, so the record is equal but detached --
        # handing back the caller's own object would let a later mutation of
        # its episode list rewrite what was stored.
        assert fetched == run
        assert fetched.run_id == run.run_id

    def test_store_count_increments(self, store: ValidationStore) -> None:
        assert store.count() == 0
        run_and_score(_task(), "ckpt-persist", store, backend=_mock_backend())
        assert store.count() == 1
        run_and_score(_task(), "ckpt-persist", store, backend=_mock_backend())
        assert store.count() == 2

    def test_run_appears_in_checkpoint_history(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-hist", store, backend=_mock_backend())
        history = store.list_for_checkpoint("ckpt-hist")
        assert [r.run_id for r in history] == [run.run_id]


# ---------------------------------------------------------------------------
# 5. Baseline / regression handling
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_missing_baseline_raises(self, store: ValidationStore) -> None:
        with pytest.raises(BaselineNotFoundError) as exc:
            run_and_score(
                _task(),
                "ckpt-base",
                store,
                backend=_mock_backend(),
                baseline_run_id="vrun-nonexistent",
            )
        assert "vrun-nonexistent" in str(exc.value)
        assert exc.value.baseline_run_id == "vrun-nonexistent"

    def test_missing_baseline_is_not_persisted(self, store: ValidationStore) -> None:
        with pytest.raises(BaselineNotFoundError):
            run_and_score(
                _task(),
                "ckpt-base",
                store,
                backend=_mock_backend(),
                baseline_run_id="vrun-ghost",
            )
        # The error fires before anything is saved.
        assert store.count() == 0

    def test_valid_baseline_produces_regression(self, store: ValidationStore) -> None:
        baseline = run_and_score(
            _task(), "ckpt-base-src", store, backend=_mock_backend()
        )
        current = run_and_score(
            _task(),
            "ckpt-base-cur",
            store,
            backend=_mock_backend(),
            baseline_run_id=baseline.run_id,
        )
        assert current.baseline_run_id == baseline.run_id
        assert current.regression is not None
        assert current.scorecard.regression_delta is not None

    def test_no_baseline_means_no_regression(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-nobase", store, backend=_mock_backend())
        assert run.baseline_run_id is None
        assert run.regression is None


# ---------------------------------------------------------------------------
# 6. Backend / factory injection
# ---------------------------------------------------------------------------


class TestBackendInjection:
    def test_explicit_backend_skips_factory(self, store: ValidationStore) -> None:
        calls: list[int] = []

        def _factory() -> SimulationBackend:
            calls.append(1)
            return _mock_backend()

        run_and_score(
            _task(), "ckpt-inj", store, backend=_mock_backend(), create_backend=_factory
        )
        assert calls == []  # backend supplied -> factory never consulted

    def test_factory_used_when_no_backend(self, store: ValidationStore) -> None:
        calls: list[int] = []

        def _factory() -> SimulationBackend:
            calls.append(1)
            return _mock_backend()

        run = run_and_score(_task(), "ckpt-inj", store, create_backend=_factory)
        assert calls == [1]
        assert isinstance(run, StoredRun)

    def test_injected_run_validation_is_called_with_derived_seed(
        self, store: ValidationStore
    ) -> None:
        seen: dict[str, Any] = {}

        def _spy(
            task: TaskConfig,
            backend: SimulationBackend,
            scenarios: Sequence[AdversarialScenario],
            seed: int = 42,
        ) -> list[EpisodeResult]:
            seen["seed"] = seed
            seen["n_scenarios"] = len(scenarios)
            return _real_run_validation(task, _mock_backend(), scenarios, seed=seed)

        run_and_score(
            _task(episodes=6, adversarial=2),
            "ckpt-rv",
            store,
            backend=_mock_backend(),
            run_validation=_spy,
        )
        assert seen["seed"] == stable_seed("ckpt-rv", "pick-place")
        assert seen["n_scenarios"] == 2


# ---------------------------------------------------------------------------
# 7. Threshold / deploy decision
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_default_threshold_is_recorded(self, store: ValidationStore) -> None:
        run = run_and_score(_task(), "ckpt-thr", store, backend=_mock_backend())
        assert run.scorecard.threshold == DEFAULT_THRESHOLD
        assert run.scorecard.deploy_decision in {"APPROVE", "BLOCK"}

    def test_zero_threshold_always_approves(self, store: ValidationStore) -> None:
        run = run_and_score(
            _task(), "ckpt-thr", store, backend=_mock_backend(), threshold=0.0
        )
        # Composite is clamped to [0, 100], so it always meets a zero bar.
        assert run.scorecard.deploy_decision == "APPROVE"

    def test_impossible_threshold_always_blocks(self, store: ValidationStore) -> None:
        run = run_and_score(
            _task(), "ckpt-thr", store, backend=_mock_backend(), threshold=1000.0
        )
        assert run.scorecard.deploy_decision == "BLOCK"

    def test_decision_matches_threshold_against_composite(
        self, store: ValidationStore
    ) -> None:
        run = run_and_score(_task(), "ckpt-thr", store, backend=_mock_backend())
        expected = (
            "APPROVE"
            if run.scorecard.composite_score >= run.scorecard.threshold
            else "BLOCK"
        )
        assert run.scorecard.deploy_decision == expected
