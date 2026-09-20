"""Tests for optional ``since``/``until`` filtering on GET /api/v1/validations.

Runs are injected straight into the store with controlled ``created_at``
stamps (rather than POSTed, which would stamp them with "now"), so the
date-range behaviour is deterministic. The default (no-filter) listing must
stay byte-for-byte identical to the pre-feature behaviour so the existing
pagination tests keep passing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore


def _scorecard(run_id: str, created_at: str) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )


def _run(run_id: str, created_at: str) -> StoredRun:
    sc = _scorecard(run_id, created_at)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9,
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


_STAMPS: list[tuple[str, str]] = [
    ("vrun-00000001", "2026-01-01T00:00:00+00:00"),
    ("vrun-00000002", "2026-01-02T00:00:00+00:00"),
    ("vrun-00000003", "2026-01-03T00:00:00+00:00"),
]


@pytest.fixture()
def store() -> ValidationStore:
    """In-memory store pre-populated with three runs a day apart."""
    s = ValidationStore()
    for run_id, ts in _STAMPS:
        s.save(_run(run_id, ts))
    return s


@pytest.fixture()
def client(store: ValidationStore) -> TestClient:
    return TestClient(create_app(store))


def _run_ids(payload: dict[str, Any]) -> list[str]:
    return [item["run_id"] for item in payload["items"]]


class TestValidationsDateFilters:
    def test_default_returns_all_newest_first(self, client: TestClient) -> None:
        payload = client.get("/api/v1/validations").json()
        assert payload["total"] == 3
        assert _run_ids(payload) == ["vrun-00000003", "vrun-00000002", "vrun-00000001"]

    def test_since_drops_older_runs(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations", params={"since": "2026-01-02T00:00:00+00:00"}
        ).json()
        assert payload["total"] == 2
        assert _run_ids(payload) == ["vrun-00000003", "vrun-00000002"]

    def test_until_drops_newer_runs(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations", params={"until": "2026-01-02T00:00:00+00:00"}
        ).json()
        assert payload["total"] == 2
        assert _run_ids(payload) == ["vrun-00000002", "vrun-00000001"]

    def test_since_and_until_define_inclusive_window(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations",
            params={
                "since": "2026-01-02T00:00:00+00:00",
                "until": "2026-01-02T23:59:59+00:00",
            },
        ).json()
        assert payload["total"] == 1
        assert _run_ids(payload) == ["vrun-00000002"]

    def test_range_matching_nothing_is_empty(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations", params={"since": "2027-01-01T00:00:00+00:00"}
        ).json()
        assert payload == {"total": 0, "limit": 100, "offset": 0, "items": []}

    def test_total_reflects_filter_under_pagination(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations",
            params={"since": "2026-01-02T00:00:00+00:00", "limit": 1},
        ).json()
        assert payload["total"] == 2  # filtered count, not full store size
        assert payload["limit"] == 1
        assert _run_ids(payload) == ["vrun-00000003"]

    def test_z_suffix_is_accepted(self, client: TestClient) -> None:
        # A trailing Z is normalised to +00:00 so it aligns with stored stamps.
        payload = client.get(
            "/api/v1/validations", params={"since": "2026-01-02T00:00:00Z"}
        ).json()
        assert payload["total"] == 2
        assert _run_ids(payload) == ["vrun-00000003", "vrun-00000002"]

    def test_blank_bounds_treated_as_no_filter(self, client: TestClient) -> None:
        payload = client.get(
            "/api/v1/validations", params={"since": "", "until": "   "}
        ).json()
        assert payload["total"] == 3


class TestValidationsDateFilterValidation:
    @pytest.mark.parametrize(
        "bad",
        ["not-a-date", "2026-13-45", "2026/01/02T00:00:00+00:00", "01-02-2026", "2026-07"],
    )
    def test_malformed_since_422(self, client: TestClient, bad: str) -> None:
        assert client.get("/api/v1/validations", params={"since": bad}).status_code == 422

    @pytest.mark.parametrize("bad", ["nope", "2026-02-30T00:00:00+00:00"])
    def test_malformed_until_422(self, client: TestClient, bad: str) -> None:
        assert client.get("/api/v1/validations", params={"until": bad}).status_code == 422

    def test_date_only_bound_is_accepted(self, client: TestClient) -> None:
        # A bare date is valid ISO-8601; the endpoint must not reject it.
        assert client.get("/api/v1/validations", params={"since": "2026-01-01"}).status_code == 200


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
