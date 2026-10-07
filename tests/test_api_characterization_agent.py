"""Characterization tests for behavioural gaps in the /api/v1 surface.

These are **characterization** tests: every one of them asserts what the code
does *today*. Where that behaviour is believed to be wrong, the test is marked
``SUSPECTED-WRONG`` in a comment on the assertion itself, so the team gets a
safety net before anyone changes semantics and an explicit list of the places
where a change is probably warranted.

Gaps closed here (all previously untested at the HTTP layer)
------------------------------------------------------------
1. ``GET /api/v1/regressions`` was only ever exercised against the *mock*
   backend with identical seeds, which by construction never regresses, so the
   existing assertion is ``isinstance(response.json(), list)`` -- true for an
   empty list. The whole non-empty code path (``run.regression.has_regressions``
   filtering, the ``regression`` key being merged into each row) had **zero**
   coverage. :class:`TestRegressionsNonEmpty` exercises it with a synthetic
   ``StoredRun``.

2. ``GET /api/v1/models`` has no coverage of a *single* checkpoint's row shape
   beyond ``tests/test_api_pagination.py``'s ordering checks, and nothing
   covered the fact that a checkpoint's ``latest_*`` fields track the newest
   run rather than the first.

3. ``GET /api/v1/validations/{run_id}/failures`` was only tested through the
   real pipeline, where the taxonomy/episodes relationship holds by
   construction. Nothing covered a run with **zero** failures, where
   ``episodes`` must be ``[]`` and the taxonomy ``{}`` -- the shape a
   dashboard renders as "no failures".

4. ``GET /api/v1/models/{checkpoint_id}/history`` does not 404 for a
   *malformed but well-formed-length* id, nor does any route distinguish
   "unknown id" from "malformed id" -- all 404 with the same detail shape.

5. ``GET /api/v1/health`` when a store is injected whose class name does not
   follow the ``*ValidationStore`` convention: ``_store_backend_name`` derives
   the label purely from the class name, so an unexpected class name is
   echoed verbatim into the probe. That is a real (minor) information
   disclosure and is pinned here as SUSPECTED-WRONG.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionItem, RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore

#: Summary key set served by every compact ``StoredRun.summary()`` view.
_SUMMARY_KEYS = {
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
    "baseline_run_id",
    "composite_score",
    "deploy_decision",
    "episode_count",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scorecard(run_id: str, created_at: str, decision: str = "APPROVE") -> Scorecard:
    """A minimal scorecard with controlled identity and verdict."""
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0 if decision == "APPROVE" else 42.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )


def _regressed_run(run_id: str, created_at: str) -> StoredRun:
    """A stored run whose regression report *does* flag a regression.

    ``RegressionReport.has_regressions`` requires at least one item that is
    both ``significant`` and of severity above ``"info"``, so the synthetic
    item below sets both flags. This is the only way to reach the non-empty
    branch of ``GET /api/v1/regressions`` without a backend that actually
    degrades, because the mock backend is deterministic in
    ``stable_seed(checkpoint_id, task_id)`` and two runs of the same
    checkpoint/task pair produce identical episodes.
    """
    return StoredRun(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        created_at=created_at,
        scorecard=_scorecard(run_id, created_at, "BLOCK"),
        evaluation=EvaluationResult(
            total_episodes=100, success_count=50, success_rate=0.5
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem(
                    metric="success_rate",
                    before=0.9,
                    after=0.5,
                    delta=-0.4,
                    p_value=0.001,
                    significant=True,
                    severity="critical",
                )
            ]
        ),
    )


def _clean_run(run_id: str, created_at: str) -> StoredRun:
    """A stored run whose regression report exists but flags nothing."""
    return StoredRun(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        created_at=created_at,
        scorecard=_scorecard(run_id, created_at),
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
        baseline_run_id="vrun-base0001",
        regression=RegressionReport(
            items=[
                RegressionItem(
                    metric="success_rate",
                    before=0.9,
                    after=0.9,
                    delta=0.0,
                    p_value=0.9,
                    significant=False,
                    severity="info",
                )
            ]
        ),
    )


def _no_regression_run(run_id: str, created_at: str) -> StoredRun:
    """A stored run with no regression report at all (no baseline supplied)."""
    run = _clean_run(run_id, created_at)
    return StoredRun(
        run_id=run.run_id,
        checkpoint_id=run.checkpoint_id,
        task_id=run.task_id,
        created_at=run.created_at,
        scorecard=run.scorecard,
        evaluation=run.evaluation,
        safety=run.safety,
        baseline_run_id=None,
        regression=None,
    )


def _store_with(*runs: StoredRun) -> ValidationStore:
    """A store pre-populated with ``runs`` (oldest first by ``created_at``)."""
    store = ValidationStore()
    for run in runs:
        store.save(run)
    return store


# ---------------------------------------------------------------------------
# GET /api/v1/regressions -- the non-empty path
# ---------------------------------------------------------------------------


class TestRegressionsNonEmpty:
    """The one branch of ``/regressions`` that was never exercised.

    Previously the route was only called with the deterministic mock backend,
    which never regresses against itself, so the existing test asserted
    ``isinstance(response.json(), list)`` -- satisfied by an empty list. These
    tests inject a synthetic regressed run so the filtering logic and the
    merged ``regression`` payload are actually covered.
    """

    def test_regressed_run_appears(self) -> None:
        # CORRECT-AS-DOCUMENTED: a run whose stored comparison flagged a
        # significant regression must be listed.
        client = TestClient(
            create_app(_store_with(_regressed_run("vrun-00000001", "2026-01-01T00:00:00+00:00")))
        )
        rows = client.get("/api/v1/regressions").json()
        assert [r["run_id"] for r in rows] == ["vrun-00000001"]

    def test_clean_regression_is_excluded(self) -> None:
        # CORRECT-AS-DOCUMENTED: ``has_regressions`` filters on *significant*
        # items, so a present-but-clean report must not show up. A regression
        # report that exists is not the same as a regression.
        client = TestClient(
            create_app(_store_with(_clean_run("vrun-00000001", "2026-01-01T00:00:00+00:00")))
        )
        assert client.get("/api/v1/regressions").json() == []

    def test_run_without_baseline_is_excluded(self) -> None:
        # CORRECT-AS-DOCUMENTED: ``regression is not None`` is the first guard,
        # so a run validated without a baseline is absent rather than crashing.
        client = TestClient(
            create_app(
                _store_with(_no_regression_run("vrun-00000001", "2026-01-01T00:00:00+00:00"))
            )
        )
        assert client.get("/api/v1/regressions").json() == []

    def test_row_shape_is_summary_plus_regression(self) -> None:
        # CORRECT-AS-DOCUMENTED: the row is ``{**summary, "regression": ...}``,
        # so a client can parse it with the same parser the list endpoint uses
        # and additionally read the report.
        client = TestClient(
            create_app(_store_with(_regressed_run("vrun-00000001", "2026-01-01T00:00:00+00:00")))
        )
        row = client.get("/api/v1/regressions").json()[0]
        assert set(row) == _SUMMARY_KEYS | {"regression"}
        assert set(row["regression"]) == {"items", "significant_count", "worst_severity"}
        assert row["regression"]["significant_count"] == 1
        assert row["regression"]["worst_severity"] == "critical"

    def test_only_the_regressed_row_of_a_mixed_store(self) -> None:
        # CORRECT-AS-DOCUMENTED: the filter is per-run, so a store holding
        # both kinds returns exactly the regressed ones, oldest-first (the
        # route iterates ``history()`` without reversing).
        client = TestClient(
            create_app(
                _store_with(
                    _clean_run("vrun-00000001", "2026-01-01T00:00:00+00:00"),
                    _regressed_run("vrun-00000002", "2026-01-02T00:00:00+00:00"),
                    _no_regression_run("vrun-00000003", "2026-01-03T00:00:00+00:00"),
                )
            )
        )
        rows = client.get("/api/v1/regressions").json()
        assert [r["run_id"] for r in rows] == ["vrun-00000002"]

    def test_empty_store_returns_empty_list(self) -> None:
        # CORRECT-AS-DOCUMENTED: an empty store is ``[]``, not ``None`` or 404.
        assert TestClient(create_app(ValidationStore())).get("/api/v1/regressions").json() == []


# ---------------------------------------------------------------------------
# GET /api/v1/models -- latest-* semantics
# ---------------------------------------------------------------------------


class TestModelsLatestFields:
    """``latest_*`` must reflect the newest run of a checkpoint, not the first."""

    def test_latest_reflects_the_newest_run(self) -> None:
        # CORRECT-AS-DOCUMENTED: ``history()`` is oldest-first and the route
        # takes ``runs[-1]``, so the newest run's score/decision win even when
        # an earlier run scored higher.
        first = Scorecard(
            run_id="vrun-00000001",
            checkpoint_id="ckpt-1",
            task_id="pick-place",
            composite_score=99.0,
            success_rate=0.99,
            safety_score=99.0,
            robustness_score=100.0,
            regression_delta=None,
            confidence_interval=None,
            deploy_decision="APPROVE",
            created_at="2026-01-01T00:00:00+00:00",
            episode_count=100,
        )
        store = _store_with(
            StoredRun(
                run_id="vrun-00000001",
                checkpoint_id="ckpt-1",
                task_id="pick-place",
                created_at="2026-01-01T00:00:00+00:00",
                scorecard=first,
                evaluation=EvaluationResult(100, 99, 0.99),
                safety=SafetyResult(0.0, 0.0, None, 0.0, 99.0),
            ),
            StoredRun(
                run_id="vrun-00000002",
                checkpoint_id="ckpt-1",
                task_id="pick-place",
                created_at="2026-01-02T00:00:00+00:00",
                scorecard=_scorecard("vrun-00000002", "2026-01-02T00:00:00+00:00", "BLOCK"),
                evaluation=EvaluationResult(100, 50, 0.5),
                safety=SafetyResult(0.0, 0.0, None, 0.0, 40.0),
            ),
        )
        rows = TestClient(create_app(store)).get("/api/v1/models").json()
        assert len(rows) == 1
        assert rows[0]["runs"] == 2
        # Newest run won: BLOCK at 42.0, not the earlier APPROVE at 99.0.
        assert rows[0]["latest_decision"] == "BLOCK"
        assert rows[0]["latest_composite"] == 42.0
        assert rows[0]["last_validated"] == "2026-01-02T00:00:00+00:00"

    def test_checkpoint_order_is_first_validation_order(self) -> None:
        # CORRECT-AS-DOCUMENTED: checkpoints appear in the order they were
        # first validated (insertion order of the aggregation dict), *not*
        # alphabetically and not by latest timestamp.
        store = _store_with(
            _clean_run("vrun-00000001", "2026-01-01T00:00:00+00:00"),
            StoredRun(
                run_id="vrun-00000002",
                checkpoint_id="ckpt-2",
                task_id="pick-place",
                created_at="2026-01-02T00:00:00+00:00",
                scorecard=Scorecard(
                    run_id="vrun-00000002",
                    checkpoint_id="ckpt-2",
                    task_id="pick-place",
                    composite_score=70.0,
                    success_rate=0.7,
                    safety_score=70.0,
                    robustness_score=100.0,
                    regression_delta=None,
                    confidence_interval=None,
                    deploy_decision="BLOCK",
                    created_at="2026-01-02T00:00:00+00:00",
                    episode_count=100,
                ),
                evaluation=EvaluationResult(100, 70, 0.7),
                safety=SafetyResult(0.0, 0.0, None, 0.0, 70.0),
            ),
        )
        rows = TestClient(create_app(store)).get("/api/v1/models").json()
        assert [r["checkpoint_id"] for r in rows] == ["ckpt-1", "ckpt-2"]


# ---------------------------------------------------------------------------
# GET /api/v1/validations/{run_id}/failures -- the no-failure case
# ---------------------------------------------------------------------------


class TestFailuresEdgeCases:
    """The zero-failure shape, which the real pipeline cannot produce."""

    def test_no_failures_yields_empty_episodes_and_taxonomy(self) -> None:
        # CORRECT-AS-DOCUMENTED: a clean run returns ``episodes: []`` and
        # ``failure_taxonomy: {}`` -- not ``None`` -- so a dashboard can render
        # the empty state without a null check.
        run = _clean_run("vrun-00000001", "2026-01-01T00:00:00+00:00")
        client = TestClient(create_app(_store_with(run)))
        payload = client.get("/api/v1/validations/vrun-00000001/failures").json()
        assert payload == {
            "run_id": "vrun-00000001",
            "failure_taxonomy": {},
            "episodes": [],
        }

    def test_taxonomy_counts_match_returned_episodes(self) -> None:
        # CORRECT-AS-DOCUMENTED: the invariant the existing pipeline-based test
        # asserts still holds for an injected run -- the taxonomy is derived
        # from the same episode list the route returns.
        client = TestClient(create_app(ValidationStore()))
        run_id = client.post(
            "/api/v1/validations",
            json={
                "checkpoint_id": "ckpt-a",
                "task": {
                    "task_id": "pick-place",
                    "robot": {"name": "franka"},
                    "environment": {"name": "kitchen"},
                    "episodes": 60,
                    "adversarial_count": 20,
                },
            },
        ).json()["run_id"]
        payload = client.get(f"/api/v1/validations/{run_id}/failures").json()
        assert sum(payload["failure_taxonomy"].values()) == len(payload["episodes"])
        assert all(ep["success"] is False for ep in payload["episodes"])

    def test_unknown_run_404(self) -> None:
        # CORRECT-AS-DOCUMENTED: 404 before any payload work.
        client = TestClient(create_app(ValidationStore()))
        assert (
            client.get("/api/v1/validations/vrun-ffffffff/failures").status_code == 404
        )


# ---------------------------------------------------------------------------
# Unknown vs malformed ids
# ---------------------------------------------------------------------------


class TestUnknownVersusMalformedIds:
    """Routes treat every unusable id the same way: a bare 404."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/validations/{run_id}",
            "/api/v1/validations/{run_id}/scorecard",
            "/api/v1/validations/{run_id}/failures",
            "/api/v1/validations/{run_id}/scorecard.md",
            "/api/v1/validations/{run_id}/scorecard.html",
        ],
    )
    def test_malformed_id_is_404_not_422(self, path: str) -> None:
        # CORRECT-AS-DOCUMENTED: ``run_id`` is an unconstrained ``str`` path
        # param, so a malformed value is a lookup miss (404), not a validation
        # error. Only the jobs router validates its id shape (400), and that
        # asymmetry is deliberate -- see ``_validate_job_id``.
        client = TestClient(create_app(ValidationStore()))
        bad = path.format(run_id="not-a-valid-run-id")
        assert client.get(bad).status_code == 404

    def test_detail_names_the_offending_id(self) -> None:
        # CORRECT-AS-DOCUMENTED: the 404 detail echoes the requested id, which
        # is what makes the failure diagnosable from a client log.
        client = TestClient(create_app(ValidationStore()))
        body = client.get("/api/v1/validations/vrun-ffffffff").json()
        assert body["detail"] == "run vrun-ffffffff not found"

    def test_model_history_detail_names_the_checkpoint(self) -> None:
        # CORRECT-AS-DOCUMENTED: same convention on the model route.
        client = TestClient(create_app(ValidationStore()))
        body = client.get("/api/v1/models/ckpt-nope/history").json()
        assert body["detail"] == "checkpoint ckpt-nope not found"


# ---------------------------------------------------------------------------
# Health probe backend labels
# ---------------------------------------------------------------------------


class _OpaqueStore(ValidationStore):
    """A store whose class name does not end in ``ValidationStore``."""


class _CustomStore:
    """A duck-typed store whose class name leaks into the health payload."""

    def history(self, since: Any = None, until: Any = None) -> list[Any]:
        return []

    def get(self, run_id: str) -> None:
        return None

    def count(self) -> int:
        return 0


class TestHealthBackendLabels:
    """``_store_backend_name`` derives its label from the class name alone."""

    def test_conventional_subclass_reports_its_prefix(self) -> None:
        # CORRECT-AS-DOCUMENTED: ``_OpaqueStore`` + suffix strip -> the class
        # name lower-cased minus the trailing ``ValidationStore``. Note the
        # leading underscore survives: the helper strips only the shared
        # suffix, not Python's private-name marker, so a module-private
        # subclass reports ``"_opaquestore"`` rather than ``"opaque"``.
        client = TestClient(create_app(_OpaqueStore()))
        assert client.get("/api/v1/health").json()["store_backend"] == "_opaquestore"

    def test_unconventional_class_name_is_echoed_verbatim(self) -> None:
        # SUSPECTED-WRONG: the label is derived purely from
        # ``type(obj).__name__.lower()`` with no allow-list, so an arbitrary
        # injected backend's class name is reflected straight into an
        # unauthenticated endpoint. ``_store_backend_name`` is reached from
        # ``/api/v1/health``, which is deliberately in ``PUBLIC_PATHS``, so
        # this is reachable without a credential. Low severity (class names are
        # not secrets) but it is unauthenticated reflection of internal type
        # names, and a deployment that injects a bespoke store leaks its
        # implementation detail on every health probe.
        client = TestClient(create_app(_CustomStore()))  # type: ignore[arg-type]
        assert client.get("/api/v1/health").json()["store_backend"] == "_customstore"

    def test_missing_store_reports_unknown(self) -> None:
        # CORRECT-AS-DOCUMENTED: ``_backend_label(None, ...)`` -> ``"unknown"``,
        # so a partially-wired app still answers the probe instead of 500ing.
        app = create_app(ValidationStore())
        app.state.store = None
        assert TestClient(app).get("/api/v1/health").json()["store_backend"] == "unknown"
