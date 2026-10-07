"""Tests for env-driven, in-memory rate limiting of write routes.

``VALIDSIM_RATE_LIMIT`` (requests per window, default ``60``) and
``VALIDSIM_RATE_WINDOW_SECONDS`` (default ``60``) are read once at
:func:`create_app` time. Protection ships on: an absent, unparsable, or
negative limit falls back to the default rather than to disabled, and only an
explicit ``0`` turns it off. When enabled, only the write/sensitive routes
(``POST /api/v1/validations`` and ``POST .../compare``) are limited, keyed by
either the ``X-API-Key`` header or the client IP; exceeding the budget returns
``429`` with a ``Retry-After`` header. When disabled, behavior is unchanged.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import (
    API_KEY_HEADER,
    DEFAULT_RATE_LIMIT,
    RATE_LIMIT_ENV,
    RATE_WINDOW_ENV,
    create_app,
)
from validsim.store.memory import ValidationStore

ENV_VARS = (RATE_LIMIT_ENV, RATE_WINDOW_ENV)


def _request_body(checkpoint: str = "ckpt-alpha") -> dict[str, object]:
    """Minimal valid validation request body."""
    return {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": 2,
            "adversarial_count": 1,
        },
    }


@pytest.fixture()
def clean_env() -> None:
    """Ensure the rate-limit env vars don't leak between tests."""
    saved = {name: os.environ.get(name) for name in ENV_VARS}
    for name in ENV_VARS:
        os.environ.pop(name, None)
    yield  # type: ignore[misc]
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _client() -> TestClient:
    """Fresh TestClient over an app with its own in-memory store."""
    return TestClient(create_app(ValidationStore()))


class TestRateLimitDisabled:
    def test_default_env_enables_rate_limiting(self, clean_env: None) -> None:
        """No ``VALIDSIM_RATE_LIMIT`` must still ship protected, not wide open."""
        response = _client().get("/api/v1/health")
        assert response.status_code == 200, response.text
        config = response.json()["rate_limit"]
        assert config is not None
        assert config["requests"] == DEFAULT_RATE_LIMIT
        assert config["window_seconds"] > 0

    def test_default_actually_throttles_excess_writes(self, clean_env: None) -> None:
        """The default is reported AND enforced, not cosmetic."""
        os.environ[RATE_LIMIT_ENV] = "2"
        client = _client()
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 429

    def test_explicit_zero_disables(self, clean_env: None) -> None:
        """``VALIDSIM_RATE_LIMIT=0`` is the documented disable sentinel."""
        os.environ[RATE_LIMIT_ENV] = "0"
        client = _client()
        for _ in range(3):
            assert client.post("/api/v1/validations", json=_request_body()).status_code == 201

    def test_bad_limit_value_falls_back_to_protection(self, clean_env: None) -> None:
        """Unparsable config degrades to the default, never to unprotected."""
        os.environ[RATE_LIMIT_ENV] = "not-a-number"
        response = _client().get("/api/v1/health")
        assert response.status_code == 200, response.text
        assert response.json()["rate_limit"]["requests"] == DEFAULT_RATE_LIMIT

    def test_negative_limit_falls_back_to_protection(self, clean_env: None) -> None:
        os.environ[RATE_LIMIT_ENV] = "-5"
        response = _client().get("/api/v1/health")
        assert response.status_code == 200, response.text
        assert response.json()["rate_limit"]["requests"] == DEFAULT_RATE_LIMIT


class TestRateLimitEnabled:
    def test_exceeding_limit_returns_429(self, clean_env: None) -> None:
        os.environ[RATE_LIMIT_ENV] = "2"
        client = _client()
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        limited = client.post("/api/v1/validations", json=_request_body())
        assert limited.status_code == 429
        assert limited.json()["detail"] == "rate limit exceeded"

    def test_retry_after_header_is_positive_seconds(self, clean_env: None) -> None:
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        limited = client.post("/api/v1/validations", json=_request_body())
        retry_after = limited.headers.get("Retry-After")
        assert retry_after is not None
        assert int(retry_after) >= 1

    def test_reads_are_not_rate_limited(self, clean_env: None) -> None:
        """Only write routes are limited; heavy read endpoints stay wide open."""
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        # The single write credit is consumed by the create call.
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        for _ in range(10):
            assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/validations").status_code == 200

    def test_compare_route_is_rate_limited(self, clean_env: None) -> None:
        """The ad-hoc compare POST shares the same per-client write budget."""
        os.environ[RATE_LIMIT_ENV] = "2"
        client = _client()
        base = client.post("/api/v1/validations", json=_request_body("ckpt-base")).json()["run_id"]
        cur = client.post("/api/v1/validations", json=_request_body("ckpt-new")).json()["run_id"]
        limited = client.post(f"/api/v1/validations/{cur}/compare", json={"baseline_id": base})
        assert limited.status_code == 429

    def test_api_key_does_not_mint_separate_buckets(self, clean_env: None) -> None:
        """Rate limiting keys on client IP only, never on ``X-API-Key``.

        Rotating the (unauthenticated) ``X-API-Key`` header must NOT give an
        attacker a fresh bucket per request — that was the H1 bypass. All
        requests from the same client IP share one bucket regardless of header.
        """
        os.environ[RATE_LIMIT_ENV] = "1"
        client = _client()
        first = client.post(
            "/api/v1/validations",
            json=_request_body(),
            headers={API_KEY_HEADER: "key-a"},
        )
        assert first.status_code == 201
        # A different header value from the same IP shares the exhausted bucket.
        second = client.post(
            "/api/v1/validations",
            json=_request_body(),
            headers={API_KEY_HEADER: "key-b"},
        )
        assert second.status_code == 429

    def test_custom_window_config_is_honored(self, clean_env: None) -> None:
        """A valid window value is parsed and wired into the limiter."""
        os.environ[RATE_LIMIT_ENV] = "1"
        os.environ[RATE_WINDOW_ENV] = "30"
        client = _client()
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 201
        assert client.post("/api/v1/validations", json=_request_body()).status_code == 429