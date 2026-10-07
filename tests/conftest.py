"""Suite-wide isolation and shared validation-store test data.

``create_app`` refuses to start when ``VALIDSIM_ENV=production`` and no API key
is configured, so an inherited shell value would otherwise fail every test that
builds an app. Tests that want a deployment mode set it explicitly (their
``monkeypatch`` runs after this fixture), and the apps they build are discarded
afterwards.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

#: Env vars cleared before every test so ambient shell config cannot leak in.
_ISOLATED_ENV = ("VALIDSIM_ENV", "VALIDSIM_STORE", "VALIDSIM_SQLITE_PATH")

DEFAULT_RUN_ID = "vrun-cafe1234"
DEFAULT_CHECKPOINT_ID = "ckpt-1"
DEFAULT_TASK_ID = "pick-place"
DEFAULT_CREATED_AT = "2026-01-01T00:00:00+00:00"


def make_scorecard(
    run_id: str = DEFAULT_RUN_ID,
    checkpoint_id: str = DEFAULT_CHECKPOINT_ID,
    created_at: str = DEFAULT_CREATED_AT,
    *,
    regression_delta: float | None = None,
    confidence_interval: tuple[float, float] | None = None,
    failure_taxonomy: dict[str, int] | None = None,
    **overrides: object,
) -> Scorecard:
    """Build the baseline scorecard shared by store contract tests."""
    base: dict[str, object] = {
        "run_id": run_id,
        "checkpoint_id": checkpoint_id,
        "task_id": DEFAULT_TASK_ID,
        "composite_score": 90.0,
        "success_rate": 0.9,
        "safety_score": 80.0,
        "robustness_score": 100.0,
        "regression_delta": regression_delta,
        "confidence_interval": confidence_interval,
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": created_at,
        "episode_count": 100,
        "failure_taxonomy": dict(failure_taxonomy or {}),
    }
    base.update(overrides)
    return Scorecard(**base)  # type: ignore[arg-type]


def make_persisted_scorecard(
    run_id: str = DEFAULT_RUN_ID,
    checkpoint_id: str = DEFAULT_CHECKPOINT_ID,
    created_at: str = DEFAULT_CREATED_AT,
    **overrides: object,
) -> Scorecard:
    """Build the non-default scorecard used by persistence round trips."""
    profile: dict[str, object] = {
        "regression_delta": -0.1,
        "confidence_interval": (0.82, 0.95),
        "failure_taxonomy": {"collision": 7, "timeout": 3},
    }
    profile.update(overrides)
    return make_scorecard(
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        **profile,
    )


def make_stored_run(
    scorecard: Scorecard | None = None,
    *,
    run_id: str = DEFAULT_RUN_ID,
    checkpoint_id: str = DEFAULT_CHECKPOINT_ID,
    created_at: str = DEFAULT_CREATED_AT,
) -> StoredRun:
    """Build a minimal StoredRun, optionally around a supplied scorecard."""
    sc = scorecard
    if sc is None:
        sc = make_scorecard(
            run_id=run_id,
            checkpoint_id=checkpoint_id,
            created_at=created_at,
        )
    return StoredRun(
        run_id=sc.run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=sc.created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=sc.episode_count,
            success_count=90,
            success_rate=sc.success_rate,
            failure_taxonomy=dict(sc.failure_taxonomy),
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, sc.safety_score),
    )


def make_detailed_stored_run(scorecard: Scorecard) -> StoredRun:
    """Build a StoredRun exercising every persisted detail field."""
    episodes = [
        EpisodeResult(
            episode_id="pick-place-seed0000000042",
            task_id=DEFAULT_TASK_ID,
            seed=42,
            success=True,
            collision_count=0,
            max_contact_force_n=12.5,
            min_human_distance_m=1.2,
            failure_mode=None,
            duration_s=8.25,
            joint_states_summary={"position_rms": 0.4, "dof": 7.0},
            randomization_level="full",
        ),
        EpisodeResult(
            episode_id="pick-place-seed0000000043",
            task_id=DEFAULT_TASK_ID,
            seed=43,
            success=False,
            collision_count=2,
            max_contact_force_n=98.75,
            min_human_distance_m=None,
            failure_mode="collision",
            duration_s=15.5,
            joint_states_summary={},
            randomization_level="partial",
        ),
    ]
    return StoredRun(
        run_id=scorecard.run_id,
        checkpoint_id=scorecard.checkpoint_id,
        task_id=scorecard.task_id,
        created_at=scorecard.created_at,
        scorecard=scorecard,
        evaluation=EvaluationResult(
            total_episodes=2,
            success_count=1,
            success_rate=0.5,
            per_task_success={DEFAULT_TASK_ID: 0.5},
            failure_taxonomy={"collision": 1},
            mean_duration_s=11.875,
        ),
        safety=SafetyResult(1.0, 0.5, 1.2, 0.0, 42.5),
        episodes=episodes,
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem(
                    "success_rate", 0.9, 0.5, -0.4, 0.001, True, "critical"
                ),
                RegressionItem(
                    "mean_duration_s", 8.0, 11.875, 3.875, None, False, "info"
                ),
            ]
        ),
    )


@pytest.fixture(autouse=True)
def _isolate_deployment_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise inherited deployment config for every test."""
    for name in _ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def durable_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Configure the sqlite store and return its path.

    Needed by any test of ``validsim gate``, which refuses to decide from the
    per-process in-memory store. Tests that tamper with a stored record use the
    returned path to reopen the same database through the store's own API.
    """
    db = tmp_path / "test-store.db"
    monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
    monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
    return db


@pytest.fixture
def make_store(
    tmp_path: Path,
) -> Iterator[Callable[[str], SqliteValidationStore]]:
    """Return a factory for temporary SQLite stores and close them on exit."""
    created: list[SqliteValidationStore] = []

    def _make(name: str = "store.db") -> SqliteValidationStore:
        store = SqliteValidationStore(tmp_path / name)
        created.append(store)
        return store

    yield _make
    for store in created:
        store.close()


# ---------------------------------------------------------------------------
# Cross-backend store contract harness
# ---------------------------------------------------------------------------
#
# ``store_backends`` yields the *real* backends — the in-memory store and a
# temporary SQLite database — behind one factory API, so a single test can
# assert the same contract on every backend instead of restating it three
# times. The PostgreSQL backend is absent by design: it needs a live server and
# a psycopg driver, so it is covered by its own fake-connection suites in
# ``test_store_postgres.py``. The harness is deliberately honest about that:
# ``store_backend_ids`` names exactly the backends the shared suite runs
# against, so nobody mistakes "cross-backend" for "all three" by accident.

#: Backend identifiers yielded by :func:`store_backends`, in a stable order.
store_backend_ids: tuple[str, ...] = ("memory", "sqlite")


@pytest.fixture
def store_backends(
    tmp_path: Path,
) -> Iterator[Callable[[str], ValidationStore]]:
    """Yield a ``backend_id -> ValidationStore`` factory for the real backends.

    Every store handed out is closed on teardown, so a test may request as many
    as it needs. ``create`` returns a store that starts empty and is safe to
    write the same ``run_id`` into twice.
    """
    created: list[ValidationStore] = []

    def _create(backend_id: str) -> ValidationStore:
        store: ValidationStore
        if backend_id == "memory":
            store = ValidationStore()
        elif backend_id == "sqlite":
            store = SqliteValidationStore(
                tmp_path / f"contract-{len(created)}.db"
            )
        else:  # pragma: no cover - guards a typo in a test's parametrization
            raise AssertionError(f"unknown store backend id {backend_id!r}")
        created.append(store)
        return store

    yield _create
    for store in created:
        store.close()


def make_adversarial_scorecard(run_id: str = DEFAULT_RUN_ID) -> Scorecard:
    """Build a scorecard with *every* field set to a non-default value.

    The baseline helpers in this module leave the adversarial-segment fields at
    their dataclass defaults, so a round trip through them cannot prove those
    fields survive persistence. This builder sets all three, plus the tuple- and
    container-typed fields (``confidence_interval``, ``failure_taxonomy``,
    ``block_reasons``) whose JSON round trip is the lossy part of the contract.
    """
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-adversarial",
        task_id="adversarial-pick-place",
        composite_score=61.5,
        success_rate=0.1234,
        safety_score=37.89,
        robustness_score=2.5,
        regression_delta=-0.4567,
        confidence_interval=(0.1111, 0.9999),
        deploy_decision="BLOCK",
        threshold=61.5,
        created_at="2026-02-29T23:59:59+00:00",
        episode_count=4242,
        failure_taxonomy={"collision": 11, "timeout": 2, "perception_error": 0},
        adversarial_episode_count=97,
        adversarial_success_rate=0.0,
        block_reasons=(
            "adversarial success rate 0.0% is significantly below the 60% floor",
            "composite 61.50 is below the configured threshold 61.50",
        ),
        robustness_measured=False,
        randomization_group_count=4,
        regression_baseline_available=False,
    )

