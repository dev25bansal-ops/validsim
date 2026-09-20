"""Tests for DELETE /api/v1/validations/{run_id}.

Covers the happy path (204, empty body), the not-found path (404), idempotent
re-delete (404), isolation (other runs survive), and that a deleted run
disappears from the paginated history listing.
"""

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


def _create(client: TestClient, checkpoint: str = "ckpt-alpha") -> str:
    """Create one run and return its id."""
    response = client.post("/api/v1/validations", json=_request_body(checkpoint))
    assert response.status_code == 201
    return str(response.json()["run_id"])


class TestApiDelete:
    def test_delete_returns_204_then_404(self, client: TestClient) -> None:
        run_id = _create(client)
        assert client.get(f"/api/v1/validations/{run_id}").status_code == 200

        deleted = client.delete(f"/api/v1/validations/{run_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""  # no body on success

        # Gone: reads and re-deletes now report 404.
        assert client.get(f"/api/v1/validations/{run_id}").status_code == 404
        assert client.delete(f"/api/v1/validations/{run_id}").status_code == 404

    def test_deleted_run_absent_from_history(self, client: TestClient) -> None:
        ids = [_create(client) for _ in range(3)]
        assert client.get("/api/v1/validations").json()["total"] == 3

        assert client.delete(f"/api/v1/validations/{ids[1]}").status_code == 204

        payload = client.get("/api/v1/validations").json()
        assert payload["total"] == 2
        assert ids[1] not in [item["run_id"] for item in payload["items"]]
        assert set(item["run_id"] for item in payload["items"]) == {ids[0], ids[2]}

    def test_delete_unknown_run_returns_404(self, client: TestClient) -> None:
        response = client.delete("/api/v1/validations/vrun-missing1")
        assert response.status_code == 404

    def test_delete_does_not_affect_other_runs(self, client: TestClient) -> None:
        a = _create(client)
        b = _create(client)
        assert client.delete(f"/api/v1/validations/{a}").status_code == 204
        assert client.get(f"/api/v1/validations/{b}").status_code == 200


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
