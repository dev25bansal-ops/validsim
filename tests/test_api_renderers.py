"""API tests for the Markdown and HTML scorecard render endpoints.

These mirror the PDF export tests: 200 + correct content-type for an existing
run, 404 for an unknown run, and light content checks (the Markdown carries the
verdict, the HTML parses as a document).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore


def _request_body(checkpoint: str = "ckpt-alpha") -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": 60,
            "adversarial_count": 12,
        },
    }


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


@pytest.fixture()
def run_id(client: TestClient) -> str:
    """Create one validation run and return its id."""
    return client.post("/api/v1/validations", json=_request_body()).json()["run_id"]


class _RecordingParser(HTMLParser):
    """Minimal HTMLParser that records tags; raises on malformed input."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self.tags.append(tag)


class TestScorecardMarkdown:
    def test_ok_content_type(self, client: TestClient, run_id: str) -> None:
        response = client.get(f"/api/v1/validations/{run_id}/scorecard.md")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")

    def test_contains_verdict(self, client: TestClient, run_id: str) -> None:
        decision = client.get(f"/api/v1/validations/{run_id}/scorecard").json()[
            "deploy_decision"
        ]
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.md").text
        assert "## Verdict:" in body
        assert decision in body

    def test_unknown_run_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/validations/vrun-ffffffff/scorecard.md")
        assert response.status_code == 404


class TestScorecardHtml:
    def test_ok_content_type(self, client: TestClient, run_id: str) -> None:
        response = client.get(f"/api/v1/validations/{run_id}/scorecard.html")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_parses_as_document(self, client: TestClient, run_id: str) -> None:
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.html").text
        parser = _RecordingParser()
        parser.feed(body)  # must not raise
        parser.close()
        assert "html" in parser.tags
        assert "<!DOCTYPE html>" in body

    def test_unknown_run_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/validations/vrun-ffffffff/scorecard.html")
        assert response.status_code == 404
