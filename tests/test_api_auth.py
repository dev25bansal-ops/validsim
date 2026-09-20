"""Tests for env-driven optional API-key auth and configurable CORS origins.

``VALIDSIM_API_KEY`` gates every ``/api/v1`` route behind an ``X-API-Key``
header; when the variable is unset, auth is disabled entirely (the behavior
the rest of the test-suite relies on). CORS origins come from
``VALIDSIM_CORS_ORIGINS`` (comma-separated, default ``"*"``).

Both variables are read at :func:`create_app` time, so each test sets env,
builds a fresh app, and restores the environment in a finally block.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import API_KEY_HEADER, create_app
from validsim.store.memory import ValidationStore

ENV_KEY = "VALIDSIM_API_KEY"
ENV_ORIGINS = "VALIDSIM_CORS_ORIGINS"


@pytest.fixture()
def clean_env() -> None:
    """Ensure neither auth nor CORS variables leak between tests."""
    saved = {name: os.environ.get(name) for name in (ENV_KEY, ENV_ORIGINS)}
    for name in (ENV_KEY, ENV_ORIGINS):
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


class TestAuthDisabled:
    def test_no_key_env_routes_open(self, clean_env: None) -> None:
        """Without ``VALIDSIM_API_KEY`` the API is open (current behavior)."""
        client = _client()
        assert client.get("/api/v1/health").status_code == 200

    def test_header_ignored_when_disabled(self, clean_env: None) -> None:
        """A stray ``X-API-Key`` header is harmless when auth is disabled."""
        client = _client()
        response = client.get("/api/v1/health", headers={API_KEY_HEADER: "whatever"})
        assert response.status_code == 200


class TestAuthEnabled:
    def test_missing_header_401(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get("/api/v1/health").status_code == 401

    def test_wrong_key_401(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/health", headers={API_KEY_HEADER: "wrong"})
        assert response.status_code == 401

    def test_correct_key_200(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/health", headers={API_KEY_HEADER: "secret-key"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_protects_dashboard_routes_too(self, clean_env: None) -> None:
        """Auth covers the dashboard router mounted under /api/v1 as well."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get("/api/v1/dashboard/summary").status_code == 401
        ok = client.get(
            "/api/v1/dashboard/summary", headers={API_KEY_HEADER: "secret-key"}
        )
        assert ok.status_code == 200

    def test_401_does_not_reveal_key_state(self, clean_env: None) -> None:
        """Wrong and missing keys return the same uniform 401 detail."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        missing = client.get("/api/v1/health")
        wrong = client.get("/api/v1/health", headers={API_KEY_HEADER: "nope"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["detail"] == wrong.json()["detail"]

    def test_writes_also_protected(self, clean_env: None) -> None:
        """POST endpoints are gated like the read-only ones."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.post("/api/v1/validations", json={}).status_code == 401


class TestCorsOrigins:
    def test_default_allows_all(self, clean_env: None) -> None:
        """Default CORS behavior (``"*"``) is preserved for existing tests."""
        client = _client()
        response = client.get("/api/v1/health", headers={"Origin": "http://a.test"})
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_explicit_origin_allow_list(self, clean_env: None) -> None:
        """Only configured origins are echoed back in ``Access-Control-Allow-Origin``."""
        os.environ[ENV_ORIGINS] = "https://app.example.com, https://ci.example.com"
        client = _client()
        allowed = client.get(
            "/api/v1/health", headers={"Origin": "https://app.example.com"}
        )
        assert allowed.headers.get("access-control-allow-origin") == "https://app.example.com"
        other = client.get(
            "/api/v1/health", headers={"Origin": "https://evil.example.com"}
        )
        assert "access-control-allow-origin" not in other.headers

    def test_blank_entries_tolerated(self, clean_env: None) -> None:
        """Empty entries from sloppy comma lists are dropped, not 500s."""
        os.environ[ENV_ORIGINS] = " https://app.example.com ,,"
        client = _client()
        response = client.get(
            "/api/v1/health", headers={"Origin": "https://app.example.com"}
        )
        assert response.headers.get("access-control-allow-origin") == "https://app.example.com"


class TestAuthAndCorsCombined:
    def test_preflight_not_blocked_by_auth(self, clean_env: None) -> None:
        """CORS preflight (OPTIONS) passes the auth gate when keyed."""
        os.environ[ENV_KEY] = "secret-key"
        os.environ[ENV_ORIGINS] = "https://app.example.com"
        client = _client()
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://app.example.com"
