"""Tests for the enriched ``GET /api/v1/health`` readiness payload.

The probe keeps its original ``status``/``version`` contract (see
``tests/test_api.py``) and additionally reports the effective runtime
configuration. Every field is derived from ``app.state`` or the injected
store/queue class names — the route performs no I/O, so these tests only need
to configure the environment and the injected objects.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim import __version__
from validsim.api.main import create_app
from validsim.jobs.queue import RedisJobQueue
from validsim.store.memory import ValidationStore
from validsim.store.postgres import PostgresValidationStore
from validsim.store.sqlite import SqliteValidationStore

#: Env vars ``create_app`` reads at build time; cleared before every test so
#: each case controls its own configuration deterministically.
_CONFIG_ENV = (
    "VALIDSIM_API_KEY",
    "VALIDSIM_CORS_ORIGINS",
    "VALIDSIM_RATE_LIMIT",
    "VALIDSIM_RATE_WINDOW_SECONDS",
    "VALIDSIM_JOB_QUEUE",
)

#: The full expected key set of the readiness payload.
_EXPECTED_KEYS = {
    "status",
    "version",
    "auth_enabled",
    "cors_wildcard",
    "rate_limit",
    "store_backend",
    "job_queue_backend",
}


@pytest.fixture(autouse=True)
def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise deployment env so defaults are reproducible per test."""
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)


def _health(client: TestClient, **headers: str) -> dict[str, Any]:
    """GET the health probe, assert 200, and return the decoded payload."""
    response = client.get("/api/v1/health", headers=headers)
    assert response.status_code == 200
    return response.json()


class TestHealthPayload:
    def test_shape_and_defaults(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        body = _health(client)

        # Original contract keys stay intact (status==ok, version present).
        assert body["status"] == "ok"
        assert body["version"] == __version__

        # New readiness fields reflect the default (open, unthrottled) config.
        assert body["auth_enabled"] is False
        assert body["cors_wildcard"] is True  # CORS allow-list defaults to "*"
        assert body["rate_limit"] is None  # rate limiting disabled by default
        assert body["store_backend"] == "memory"
        assert body["job_queue_backend"] == "memory"

        assert set(body) == _EXPECTED_KEYS


class TestAuthEnabled:
    def test_disabled_by_default(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["auth_enabled"] is False

    def test_flips_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDSIM_API_KEY", "secret-123")
        client = TestClient(create_app(ValidationStore()))

        # With auth enforced the probe is gated like every /api/v1 route.
        assert client.get("/api/v1/health").status_code == 401

        body = _health(client, **{"X-API-Key": "secret-123"})
        assert body["auth_enabled"] is True
        assert body["status"] == "ok"


class TestCorsWildcard:
    def test_true_by_default(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["cors_wildcard"] is True

    def test_false_when_origins_restricted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "VALIDSIM_CORS_ORIGINS", "http://a.example,http://b.example"
        )
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["cors_wildcard"] is False


class TestRateLimit:
    def test_none_when_disabled(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["rate_limit"] is None

    def test_reports_config_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDSIM_RATE_LIMIT", "5")
        monkeypatch.setenv("VALIDSIM_RATE_WINDOW_SECONDS", "30")
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["rate_limit"] == {"requests": 5, "window_seconds": 30.0}


class TestStoreBackend:
    def test_memory(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["store_backend"] == "memory"

    def test_sqlite(self) -> None:
        store = SqliteValidationStore(":memory:")
        try:
            client = TestClient(create_app(store))
            assert _health(client)["store_backend"] == "sqlite"
        finally:
            store.close()

    def test_postgres(self) -> None:
        # A DSN is supplied so construction stays side-effect free (no driver
        # import / connection); the label is derived purely from the class name.
        store = PostgresValidationStore(dsn="postgresql://user@localhost:5432/db")
        client = TestClient(create_app(store))
        assert _health(client)["store_backend"] == "postgres"


class TestJobQueueBackend:
    def test_memory(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert _health(client)["job_queue_backend"] == "memory"

    def test_redis(self) -> None:
        app = create_app(ValidationStore())
        # A URL is supplied so RedisJobQueue constructs without connecting.
        app.state.job_queue = RedisJobQueue(url="redis://localhost:6379/0")
        assert _health(TestClient(app))["job_queue_backend"] == "redis"
