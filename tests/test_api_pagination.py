"""Pagination tests for GET /api/v1/models and GET /api/v1/validations."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore


def _request_body(checkpoint: str = "ckpt-alpha") -> dict[str, object]:
    """Minimal valid validation request body."""
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": 5,
            "adversarial_count": 1,
        },
    }


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


def _create_n(client: TestClient, n: int, checkpoint: str = "ckpt-alpha") -> list[str]:
    """Create ``n`` runs sequentially; return run ids in creation order."""
    ids: list[str] = []
    for _ in range(n):
        response = client.post("/api/v1/validations", json=_request_body(checkpoint))
        assert response.status_code == 201
        ids.append(response.json()["run_id"])
    return ids


class TestValidationsListPagination:
    def test_default_pagination_shape(self, client: TestClient) -> None:
        ids = _create_n(client, 3)
        payload = client.get("/api/v1/validations").json()
        assert payload["total"] == 3
        assert payload["limit"] == 100
        assert payload["offset"] == 0
        assert [item["run_id"] for item in payload["items"]] == ids[::-1]  # newest first

    def test_limit_slices_newest_first(self, client: TestClient) -> None:
        ids = _create_n(client, 4)
        payload = client.get("/api/v1/validations", params={"limit": 2}).json()
        assert payload["total"] == 4
        assert payload["limit"] == 2
        assert [item["run_id"] for item in payload["items"]] == ids[::-1][:2]

    def test_offset_skips_newest(self, client: TestClient) -> None:
        ids = _create_n(client, 4)
        payload = client.get("/api/v1/validations", params={"limit": 2, "offset": 2}).json()
        assert payload["total"] == 4
        assert [item["run_id"] for item in payload["items"]] == ids[::-1][2:4]

    def test_items_are_compact_summaries(self, client: TestClient) -> None:
        _create_n(client, 1)
        item = client.get("/api/v1/validations", params={"limit": 1}).json()["items"][0]
        assert {"run_id", "checkpoint_id", "task_id", "created_at", "composite_score",
                "deploy_decision"} <= set(item)
        assert "episodes" not in item  # summary only, no episode payloads

    def test_empty_store(self, client: TestClient) -> None:
        payload = client.get("/api/v1/validations").json()
        assert payload == {"total": 0, "limit": 100, "offset": 0, "items": []}

    @pytest.mark.parametrize(
        "params", [{"limit": 0}, {"limit": 501}, {"limit": -1}, {"offset": -1}]
    )
    def test_invalid_params_422(self, client: TestClient, params: dict[str, int]) -> None:
        assert client.get("/api/v1/validations", params=params).status_code == 422


class TestModelsPagination:
    def test_models_default_unchanged(self, client: TestClient) -> None:
        _create_n(client, 2, "ckpt-alpha")
        _create_n(client, 1, "ckpt-beta")
        models = client.get("/api/v1/models")
        assert models.status_code == 200
        assert [m["checkpoint_id"] for m in models.json()] == ["ckpt-alpha", "ckpt-beta"]

    def test_models_limit_offset(self, client: TestClient) -> None:
        for name in ("ckpt-a", "ckpt-b", "ckpt-c", "ckpt-d"):
            _create_n(client, 1, name)
        page = client.get("/api/v1/models", params={"limit": 2, "offset": 1}).json()
        assert [m["checkpoint_id"] for m in page] == ["ckpt-b", "ckpt-c"]

    def test_models_offset_beyond_end(self, client: TestClient) -> None:
        _create_n(client, 1, "ckpt-a")
        assert client.get("/api/v1/models", params={"offset": 5}).json() == []

    @pytest.mark.parametrize(
        "params", [{"limit": 0}, {"limit": 501}, {"limit": -1}, {"offset": -1}]
    )
    def test_invalid_params_422(self, client: TestClient, params: dict[str, int]) -> None:
        assert client.get("/api/v1/models", params=params).status_code == 422
