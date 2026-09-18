"""Integration tests for the ValidSim FastAPI service."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.engine.regression import compare
from validsim.sim.runner import stable_seed
from validsim.store.memory import ValidationStore


def _request_body(
    checkpoint: str = "ckpt-alpha",
    episodes: int = 60,
    adversarial: int = 12,
    baseline: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": episodes,
            "adversarial_count": adversarial,
        },
    }
    if baseline is not None:
        body["baseline_run_id"] = baseline
    return body


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


class TestBasics:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_cors_allows_origins(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_invalid_body_rejected(self, client: TestClient) -> None:
        bad = _request_body()
        bad["task"]["episodes"] = 0
        assert client.post("/api/v1/validations", json=bad).status_code == 422


class TestValidationLifecycle:
    def test_create_returns_scorecard(self, client: TestClient) -> None:
        response = client.post("/api/v1/validations", json=_request_body())
        assert response.status_code == 201
        card = response.json()
        assert card["run_id"].startswith("vrun-")
        assert card["checkpoint_id"] == "ckpt-alpha"
        assert card["episode_count"] == 72  # 60 nominal + 12 adversarial
        assert 0.0 <= card["composite_score"] <= 100.0
        assert card["deploy_decision"] in {"APPROVE", "BLOCK"}
        assert card["threshold"] == 85.0

    def test_get_and_404(self, client: TestClient) -> None:
        run_id = client.post("/api/v1/validations", json=_request_body()).json()["run_id"]
        summary = client.get(f"/api/v1/validations/{run_id}")
        assert summary.status_code == 200
        assert summary.json()["run_id"] == run_id
        assert summary.json()["task_id"] == "pick-place"
        assert client.get("/api/v1/validations/vrun-ffffffff").status_code == 404

    def test_scorecard_endpoint(self, client: TestClient) -> None:
        run_id = client.post("/api/v1/validations", json=_request_body()).json()["run_id"]
        response = client.get(f"/api/v1/validations/{run_id}/scorecard")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["run_id"] == run_id
        assert "confidence_interval" in response.json()

    def test_failures_endpoint(self, client: TestClient) -> None:
        run_id = client.post("/api/v1/validations", json=_request_body()).json()["run_id"]
        payload = client.get(f"/api/v1/validations/{run_id}/failures").json()
        assert payload["run_id"] == run_id
        assert isinstance(payload["failure_taxonomy"], dict)
        assert sum(payload["failure_taxonomy"].values()) == len(payload["episodes"])
        assert all(ep["success"] is False for ep in payload["episodes"])


class TestCompareAndRegressions:
    def test_compare_endpoint(self, client: TestClient) -> None:
        base = client.post("/api/v1/validations", json=_request_body("ckpt-base")).json()["run_id"]
        cur = client.post("/api/v1/validations", json=_request_body("ckpt-new")).json()["run_id"]
        response = client.post(
            f"/api/v1/validations/{cur}/compare", json={"baseline_id": base}
        )
        assert response.status_code == 200
        report = response.json()
        assert report["run_id"] == cur and report["baseline_id"] == base
        metrics = {item["metric"] for item in report["items"]}
        assert {"success_rate", "mean_duration_s"} <= metrics

    def test_compare_unknown_baseline_404(self, client: TestClient) -> None:
        cur = client.post("/api/v1/validations", json=_request_body()).json()["run_id"]
        response = client.post(
            f"/api/v1/validations/{cur}/compare", json={"baseline_id": "vrun-00000000"}
        )
        assert response.status_code == 404

    def test_creation_with_missing_baseline_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/validations", json=_request_body(baseline="vrun-00000000")
        )
        assert response.status_code == 404

    def test_regressions_list(self, client: TestClient) -> None:
        base = client.post("/api/v1/validations", json=_request_body("ckpt-base")).json()["run_id"]
        client.post(
            "/api/v1/validations", json=_request_body("ckpt-new", baseline=base)
        )
        response = client.get("/api/v1/regressions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)  # same mock seeds -> no regression expected

    def test_compare_is_reproducible_for_same_pair(self, client: TestClient) -> None:
        base = client.post("/api/v1/validations", json=_request_body("ckpt-base")).json()["run_id"]
        cur = client.post("/api/v1/validations", json=_request_body("ckpt-new")).json()["run_id"]
        first = client.post(
            f"/api/v1/validations/{cur}/compare", json={"baseline_id": base}
        ).json()
        second = client.post(
            f"/api/v1/validations/{cur}/compare", json={"baseline_id": base}
        ).json()
        assert first == second

    def test_compare_seed_derived_from_run_pair(self) -> None:
        """The permutation seed must be ``stable_seed(run_id, baseline_id)``."""
        store = ValidationStore()
        client = TestClient(create_app(store))
        base = client.post("/api/v1/validations", json=_request_body("ckpt-base")).json()["run_id"]
        cur = client.post("/api/v1/validations", json=_request_body("ckpt-new")).json()["run_id"]
        report = client.post(
            f"/api/v1/validations/{cur}/compare", json={"baseline_id": base}
        ).json()
        expected = compare(
            store.get(cur).evaluation,  # type: ignore[union-attr]
            store.get(base).evaluation,  # type: ignore[union-attr]
            seed=stable_seed(cur, base),
        )
        assert report["items"] == expected.to_dict()["items"]
