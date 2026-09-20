"""Tests for the observability wave: structured logging, request-id, metrics.

Covers the three pieces wired into :func:`validsim.api.main.create_app`:

* :mod:`validsim.logging` — idempotent JSON logging configuration.
* The ASGI observability middleware — ``X-Request-ID`` echo/generation and the
  in-process HTTP request counter.
* :mod:`validsim.api.metrics` — the Prometheus text endpoint, its content type,
  metric names, store-derived gauges, and the auth-free ``/metrics`` mount.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim import __version__
from validsim.api.main import create_app
from validsim.api.metrics import HTTP_METRIC_NAME, Metrics, render_metrics
from validsim.logging import configure_logging
from validsim.store.memory import ValidationStore

#: Env vars ``create_app`` reads at build time; cleared before every test so
#: each case controls its own configuration deterministically.
_CONFIG_ENV = (
    "VALIDSIM_API_KEY",
    "VALIDSIM_CORS_ORIGINS",
    "VALIDSIM_RATE_LIMIT",
    "VALIDSIM_RATE_WINDOW_SECONDS",
    "VALIDSIM_JOB_QUEUE",
    "VALIDSIM_STORE",
    "VALIDSIM_LOG_LEVEL",
)

#: Metric names the scrape surface must always expose.
_METRIC_NAMES = (
    "validsim_runs_total",
    "validsim_approvals_total",
    "validsim_blocks_total",
    "validsim_composite_score",
    "validsim_build_info",
    HTTP_METRIC_NAME,
)


@pytest.fixture(autouse=True)
def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise deployment env so defaults are reproducible per test."""
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


def _validation_body(
    checkpoint: str = "ckpt-alpha",
    episodes: int = 60,
    adversarial: int = 12,
) -> dict[str, Any]:
    """Minimal valid ``POST /api/v1/validations`` payload."""
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


class TestRequestID:
    def test_inbound_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/health", headers={"X-Request-ID": "trace-me-123"}
        )
        assert response.status_code == 200
        assert response.headers.get("x-request-id") == "trace-me-123"

    def test_missing_request_id_is_generated_as_uuid(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        generated = response.headers.get("x-request-id")
        assert generated is not None and generated != ""
        # A fresh uuid4 is minted when the caller supplies none.
        assert str(uuid.UUID(generated)) == generated.lower()

    def test_request_ids_are_distinct_per_request(self, client: TestClient) -> None:
        first = client.get("/api/v1/health").headers.get("x-request-id")
        second = client.get("/api/v1/health").headers.get("x-request-id")
        assert first != second


class TestMetricsEndpoint:
    def test_metrics_returns_text_plain_with_names(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        for name in _METRIC_NAMES:
            assert name in body

    def test_build_info_carries_version(self, client: TestClient) -> None:
        body = client.get("/metrics").text
        assert f'validsim_build_info{{version="{__version__}"}} 1' in body

    def test_metrics_also_reachable_under_api_prefix(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics")
        assert response.status_code == 200
        assert "validsim_runs_total" in response.text

    def test_root_metrics_open_when_auth_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The scrape endpoint must stay reachable without a key even when the
        # rest of the API is gated; the /api/v1 copy keeps the gate.
        monkeypatch.setenv("VALIDSIM_API_KEY", "s3cret")
        client = TestClient(create_app(ValidationStore()))
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/v1/metrics").status_code == 401
        assert client.get("/api/v1/metrics", headers={"X-API-Key": "s3cret"}).status_code == 200

    def test_run_gauges_derive_from_store(self, client: TestClient) -> None:
        assert client.post("/api/v1/validations", json=_validation_body()).status_code == 201
        body = client.get("/metrics").text
        lines = {
            line.split(" ")[0]: line.split(" ")[-1]
            for line in body.splitlines()
            if line and not line.startswith("#")
        }
        assert lines["validsim_runs_total"] == "1"
        approvals = int(lines["validsim_approvals_total"])
        blocks = int(lines["validsim_blocks_total"])
        assert approvals + blocks == 1
        assert float(lines["validsim_composite_score"]) >= 0.0


class TestHTTPCounter:
    def test_request_counter_increments(self, client: TestClient) -> None:
        # Two completed 2xx requests precede the scrape; the scrape reports the
        # count observed *before* its own increment.
        client.get("/api/v1/health")
        client.get("/api/v1/health")
        body = client.get("/metrics").text
        counts = _parse_http_counts(body)
        assert counts["2xx"] >= 2

    def test_error_responses_counted_by_class(self, client: TestClient) -> None:
        client.get("/api/v1/validations/vrun-ffffffff")  # 404 -> 4xx
        body = client.get("/metrics").text
        counts = _parse_http_counts(body)
        assert counts["4xx"] >= 1

    def test_metrics_object_tracks_status_classes(self) -> None:
        metrics = Metrics()
        metrics.observe_status(200)
        metrics.observe_status(204)
        metrics.observe_status(404)
        metrics.observe_status(503)
        metrics.observe_status(100)  # informational: ignored
        snapshot = metrics.snapshot_http()
        assert snapshot["2xx"] == 2
        assert snapshot["4xx"] == 1
        assert snapshot["5xx"] == 1
        assert "1xx" not in snapshot


class TestRenderMetrics:
    def test_render_is_stable_on_empty_store(self) -> None:
        text = render_metrics(ValidationStore(), Metrics())
        assert "validsim_runs_total 0" in text
        assert "validsim_composite_score 0" in text
        assert text.endswith("\n")


class TestLoggingConfiguration:
    def test_configure_is_idempotent(self) -> None:
        logger = configure_logging()
        assert len(logger.handlers) >= 1
        count = len(logger.handlers)
        configure_logging()
        configure_logging()
        # Repeated calls never grow the handler set.
        assert len(logging.getLogger("validsim").handlers) == count

    def test_get_logger_namespaces_under_validsim(self) -> None:
        from validsim.logging import get_logger

        assert get_logger("api.access").name == "validsim.api.access"
        assert get_logger("validsim.engine").name == "validsim.engine"

    def test_json_formatter_emits_expected_fields(self) -> None:
        import io
        import json

        from validsim.logging import JsonLogFormatter

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        record = logging.LogRecord(
            name="validsim.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"
        handler.emit(record)

        payload = json.loads(stream.getvalue())
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "validsim.test"
        assert payload["request_id"] == "abc-123"
        assert "timestamp" in payload


def _parse_http_counts(body: str) -> dict[str, int]:
    """Extract ``validsim_http_requests_total{class=...}`` values from a scrape."""
    counts: dict[str, int] = {}
    for line in body.splitlines():
        if line.startswith(HTTP_METRIC_NAME + "{"):
            cls = line.split('class="')[1].split('"')[0]
            counts[cls] = int(line.rsplit(" ", 1)[1])
    return counts
