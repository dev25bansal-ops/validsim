"""Adversarial / negative-path tests for the env-driven ``X-API-Key`` gate.

Companion to ``tests/test_api_auth.py``: rather than re-checking the happy
paths, this suite hammers the edges of
:func:`validsim.api.main.create_app`'s ``require_api_key`` dependency and the
CORS/auth middleware ordering to pin down behavior the positive tests leave
implicit:

1. An explicitly empty ``X-API-Key`` header is rejected (401) when a key is set.
2. A whitespace-only header is rejected; the value is compared verbatim (no
   trimming), so even a padded copy of the real key fails.
3. A wrong-length key (strict prefix / extra suffix) is rejected by the
   constant-time comparison.
4. Key comparison is case-sensitive (wrong-cased value -> 401) while the header
   *name* is matched case-insensitively (correct value via ``x-api-key`` -> 200).
5. A CORS preflight (OPTIONS) bypasses the auth dependency but still receives
   the CORS response headers.
6. ``VALIDSIM_API_KEY`` set to an empty or whitespace value is treated as *not
   configured* — auth off, reported as such by ``/api/v1/health`` — and is a
   startup failure when ``VALIDSIM_ENV=production`` asks for protection.
7. The included ``/api/v1/jobs`` router inherits the same gate (401 without a
   key, 200/202 with one); auth even runs before per-route 404/422 logic.
8. A 401 body is uniform and never reveals whether a key exists or matched.
9. ``/api/v1/health`` is the one ``/api/v1`` route exempt from the gate, because
   the container healthcheck cannot present a credential; it reports the
   effective ``auth_enabled`` instead.

Auth/CORS/job-queue env is read at :func:`create_app` time, so every case sets
the environment, builds a fresh app, and restores it via ``clean_env``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from validsim.api.main import API_KEY_HEADER, create_app
from validsim.store.memory import ValidationStore

ENV_KEY = "VALIDSIM_API_KEY"
ENV_ORIGINS = "VALIDSIM_CORS_ORIGINS"
#: Deployment mode; `production` refuses to start without a key.
ENV_ENV = "VALIDSIM_ENV"
#: Cleared so the wired jobs queue is always in-memory (never Redis).
ENV_JOB_QUEUE = "VALIDSIM_JOB_QUEUE"
ENV_REDIS_URL = "VALIDSIM_REDIS_URL"

#: Env vars this suite owns; popped before each test and restored afterwards.
_MANAGED_ENV = (ENV_KEY, ENV_ORIGINS, ENV_ENV, ENV_JOB_QUEUE, ENV_REDIS_URL)

#: A route that is always auth-gated while a key is configured.
PROTECTED = "/api/v1/jobs"
#: The one /api/v1 route deliberately left open for container healthchecks.
HEALTH = "/api/v1/health"

#: The single, uniform 401 detail every rejection must carry.
UNIFORM_DETAIL = "Missing or invalid API key"


@pytest.fixture()
def clean_env() -> None:
    """Isolate auth/CORS/job-queue env so no value leaks between tests."""
    saved = {name: os.environ.get(name) for name in _MANAGED_ENV}
    for name in _MANAGED_ENV:
        os.environ.pop(name, None)
    yield  # type: ignore[misc]
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _app() -> FastAPI:
    """Fresh app with its own in-memory store and job queue."""
    return create_app(ValidationStore())


def _client() -> TestClient:
    """TestClient over a freshly built app."""
    return TestClient(_app())


def _job_body(**overrides: Any) -> dict[str, Any]:
    """Minimal valid body for POST /api/v1/jobs, with optional overrides."""
    body: dict[str, Any] = {"checkpoint_id": "ckpt-neg", "task_id": "pick-place"}
    body.update(overrides)
    return body


class TestEmptyAndBlankHeaders:
    """Empty / whitespace header values never authenticate."""

    def test_empty_header_value_401(self, clean_env: None) -> None:
        """An explicitly empty ``X-API-Key`` is rejected when a key is set."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(PROTECTED, headers={API_KEY_HEADER: ""})
        assert response.status_code == 401

    def test_whitespace_only_header_401(self, clean_env: None) -> None:
        """A whitespace-only header is rejected (no trimming happens)."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(PROTECTED, headers={API_KEY_HEADER: "    "})
        assert response.status_code == 401

    def test_padded_correct_key_still_401(self, clean_env: None) -> None:
        """The value is compared verbatim: surrounding whitespace is NOT stripped."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        padded = client.get(PROTECTED, headers={API_KEY_HEADER: " secret-key "})
        assert padded.status_code == 401


class TestWrongLengthAndCasing:
    """Length and case mismatches are rejected by the constant-time compare."""

    def test_shorter_prefix_key_401(self, clean_env: None) -> None:
        """A strict prefix of the real key (same chars, wrong length) fails."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(PROTECTED, headers={API_KEY_HEADER: "secret-ke"})
        assert response.status_code == 401

    def test_longer_key_401(self, clean_env: None) -> None:
        """The real key with extra characters appended fails."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(PROTECTED, headers={API_KEY_HEADER: "secret-key-EXTRA"})
        assert response.status_code == 401

    def test_wrong_cased_value_401(self, clean_env: None) -> None:
        """Key comparison is case-sensitive; only the exact casing passes."""
        os.environ[ENV_KEY] = "Secret-Key"
        client = _client()
        assert client.get(PROTECTED, headers={API_KEY_HEADER: "secret-key"}).status_code == 401
        assert client.get(PROTECTED, headers={API_KEY_HEADER: "SECRET-KEY"}).status_code == 401
        assert client.get(PROTECTED, headers={API_KEY_HEADER: "Secret-Key"}).status_code == 200

    def test_header_name_is_case_insensitive(self, clean_env: None) -> None:
        """HTTP header names are case-insensitive: ``x-api-key`` still authenticates."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get(PROTECTED, headers={"x-api-key": "secret-key"}).status_code == 200
        assert client.get(PROTECTED, headers={"X-API-KEY": "secret-key"}).status_code == 200


class TestPreflightBypassesAuth:
    """CORS preflight (OPTIONS) skips the auth dependency but keeps CORS."""

    def test_preflight_without_key_is_not_401(self, clean_env: None) -> None:
        """A correctly-keyed app still answers an unauthenticated preflight."""
        os.environ[ENV_KEY] = "secret-key"
        os.environ[ENV_ORIGINS] = "https://app.example.com"
        client = _client()
        response = client.options(
            "/api/v1/jobs",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": API_KEY_HEADER,
            },
        )
        assert response.status_code == 200
        assert response.status_code != 401
        # CORS still applies to the preflight response.
        assert response.headers.get("access-control-allow-origin") == "https://app.example.com"
        allow_methods = (response.headers.get("access-control-allow-methods") or "").upper()
        assert "*" in allow_methods or "POST" in allow_methods
        allow_headers = (response.headers.get("access-control-allow-headers") or "").lower()
        assert "*" in allow_headers or API_KEY_HEADER.lower() in allow_headers

    def test_real_request_to_same_path_still_needs_key(self, clean_env: None) -> None:
        """The bypass is specific to OPTIONS: a GET to the same route is 401."""
        os.environ[ENV_KEY] = "secret-key"
        os.environ[ENV_ORIGINS] = "https://app.example.com"
        client = _client()
        assert client.get("/api/v1/jobs").status_code == 401


class TestBlankKeyMeansAuthDisabled:
    """``VALIDSIM_API_KEY=""`` is auth *off*, and says so.

    It used to be auth "on" with an empty key, which made the missing header
    compare equal to the configured value: every protected route opened, while
    ``/api/v1/health`` reported ``auth_enabled: true``.
    """

    def test_empty_string_key_marks_auth_disabled(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = ""
        app = _app()
        assert app.state.api_key_enabled is False

    def test_whitespace_key_marks_auth_disabled(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "   "
        app = _app()
        assert app.state.api_key_enabled is False

    def test_health_agrees_with_the_resolved_policy(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = ""
        client = _client()
        assert client.get(HEALTH).json()["auth_enabled"] is False

    def test_unset_key_marks_auth_disabled(self, clean_env: None) -> None:
        # clean_env popped the var, so it is genuinely unset here.
        app = _app()
        assert app.state.api_key_enabled is False

    def test_blank_key_grants_nothing_that_unset_does_not(self, clean_env: None) -> None:
        """Empty and unset behave identically: no key configured, routes open."""
        os.environ[ENV_KEY] = ""
        client = _client()
        assert client.get(PROTECTED).status_code == 200
        assert client.get(HEALTH).status_code == 200


class TestProductionRefusesToStartUnauthenticated:
    """With ``VALIDSIM_ENV=production`` a missing key is a startup failure."""

    def test_empty_key_in_production_raises(self, clean_env: None) -> None:
        os.environ[ENV_ENV] = "production"
        os.environ[ENV_KEY] = ""
        with pytest.raises(RuntimeError, match="VALIDSIM_API_KEY"):
            _app()

    def test_unset_key_in_production_raises(self, clean_env: None) -> None:
        os.environ[ENV_ENV] = "production"
        with pytest.raises(RuntimeError, match="VALIDSIM_API_KEY"):
            _app()

    def test_configured_key_in_production_starts(self, clean_env: None) -> None:
        os.environ[ENV_ENV] = "production"
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get(PROTECTED).status_code == 401
        assert client.get(PROTECTED, headers={API_KEY_HEADER: "secret-key"}).status_code == 200

    def test_development_stays_open_without_a_key(self, clean_env: None) -> None:
        os.environ[ENV_ENV] = "development"
        assert _app().state.api_key_enabled is False


class TestHealthRouteStaysReachable:
    """``/api/v1/health`` backs the container healthcheck, which has no key."""

    def test_health_open_when_auth_enabled(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(HEALTH)
        assert response.status_code == 200
        assert response.json()["auth_enabled"] is True

    def test_protected_sibling_still_401_on_the_same_client(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get(HEALTH).status_code == 200
        assert client.get(PROTECTED).status_code == 401

    def test_health_ignores_a_supplied_wrong_key(self, clean_env: None) -> None:
        """Exempt means exempt: a wrong header must not fail the probe."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get(HEALTH, headers={API_KEY_HEADER: "wrong"}).status_code == 200


class TestJobsRouterInheritsAuth:
    """The included ``/api/v1/jobs`` router is gated like every other route."""

    def test_list_without_key_401(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get("/api/v1/jobs").status_code == 401

    def test_enqueue_without_key_401(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.post("/api/v1/jobs", json=_job_body()).status_code == 401

    def test_unknown_job_without_key_is_401_not_404(self, clean_env: None) -> None:
        """Auth runs before route logic, so a bad id is still 401 (not 404)."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get("/api/v1/jobs/vrun-00000000/status").status_code == 401

    def test_valid_key_reaches_jobs_list(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/jobs", headers={API_KEY_HEADER: "secret-key"})
        assert response.status_code == 200
        assert response.json() == []


class TestNoKeyStateLeakage:
    """The 401 response is uniform and never reveals whether a key exists."""

    def test_missing_and_wrong_share_identical_body(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        missing = client.get(PROTECTED)
        wrong = client.get(PROTECTED, headers={API_KEY_HEADER: "nope"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.json() == wrong.json()
        assert missing.json()["detail"] == UNIFORM_DETAIL

    def test_body_omits_configured_key(self, clean_env: None) -> None:
        """The payload never echoes the configured key or its length."""
        secret = "super-secret-token-123"
        os.environ[ENV_KEY] = secret
        client = _client()
        response = client.get(PROTECTED, headers={API_KEY_HEADER: "wrong-value"})
        assert secret.lower() not in response.text.lower()
        assert str(len(secret)) not in response.text
        assert response.json()["detail"] == UNIFORM_DETAIL

    def test_same_detail_across_all_protected_surfaces(self, clean_env: None) -> None:
        """Dashboard, validations and jobs all return the identical 401."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        details: set[str] = set()
        for method, path in (
            ("GET", "/api/v1/dashboard/summary"),
            ("POST", "/api/v1/validations"),
            ("GET", "/api/v1/jobs"),
        ):
            response = client.request(method, path)
            assert response.status_code == 401
            details.add(response.json()["detail"])
        assert details == {UNIFORM_DETAIL}

    def test_www_authenticate_challenge_present(self, clean_env: None) -> None:
        """Rejections carry the ``WWW-Authenticate: ApiKey`` hint (no key state)."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(PROTECTED)
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "ApiKey"
