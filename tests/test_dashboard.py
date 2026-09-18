"""Tests for the Week-7 ValidSim dashboard (page, static assets, data API)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore


def _request_body(
    checkpoint: str = "ckpt-alpha",
    episodes: int = 60,
    adversarial: int = 12,
) -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": episodes,
            "adversarial_count": adversarial,
        },
    }


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


class TestDashboardPage:
    def test_root_serves_dashboard_html(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "ValidSim" in response.text

    def test_router_alias_serves_dashboard(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard/")
        assert response.status_code == 200
        assert "ValidSim" in response.text

    def test_existing_endpoints_still_work(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").json()["status"] == "ok"

    def test_static_assets_served(self, client: TestClient) -> None:
        for asset in ("styles.css", "app.js", "index.html"):
            response = client.get(f"/static/{asset}")
            assert response.status_code == 200, asset
            assert len(response.content) > 0, asset

    def test_page_references_assets_and_chart_cdn(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "/static/styles.css" in html
        assert "/static/app.js" in html
        assert "https://cdn.jsdelivr.net/npm/chart.js" in html


class TestDashboardData:
    def test_summary_keys_after_one_run(self, client: TestClient) -> None:
        created = client.post("/api/v1/validations", json=_request_body()).json()
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        summary = response.json()
        assert set(summary) == {"total_runs", "approvals", "blocks", "avg_composite"}
        assert summary["total_runs"] == 1
        assert summary["approvals"] + summary["blocks"] == 1
        assert summary["avg_composite"] == created["composite_score"]

    def test_summary_empty_store(self, client: TestClient) -> None:
        summary = client.get("/api/v1/dashboard/summary").json()
        assert summary["total_runs"] == 0
        assert summary["approvals"] == 0
        assert summary["blocks"] == 0
        assert summary["avg_composite"] is None

    def test_history_lists_runs_newest_first(self, client: TestClient) -> None:
        first = client.post("/api/v1/validations", json=_request_body("ckpt-one")).json()["run_id"]
        second = client.post("/api/v1/validations", json=_request_body("ckpt-two")).json()["run_id"]
        response = client.get("/api/v1/dashboard/history")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 2
        assert rows[0]["run_id"] == second  # newest first
        assert rows[1]["run_id"] == first
        for row in rows:
            assert {
                "run_id",
                "checkpoint_id",
                "task_id",
                "composite_score",
                "deploy_decision",
                "created_at",
            } <= set(row)

    def test_history_shares_app_store(self, client: TestClient) -> None:
        """Dashboard reads the same store instance the API writes to."""
        card = client.post("/api/v1/validations", json=_request_body()).json()
        row = client.get("/api/v1/dashboard/history").json()[0]
        assert row["run_id"] == card["run_id"]
        assert row["composite_score"] == card["composite_score"]
        assert row["deploy_decision"] == card["deploy_decision"]

    def test_history_empty_store(self, client: TestClient) -> None:
        assert client.get("/api/v1/dashboard/history").json() == []
