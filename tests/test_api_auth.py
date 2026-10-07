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
        assert client.get("/api/v1/validations").status_code == 401

    def test_wrong_key_401(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/validations", headers={API_KEY_HEADER: "wrong"})
        assert response.status_code == 401

    def test_health_stays_reachable_for_the_probe(self, clean_env: None) -> None:
        """The container healthcheck has no key to present, so health is exempt."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["auth_enabled"] is True

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
        missing = client.get("/api/v1/validations")
        wrong = client.get("/api/v1/validations", headers={API_KEY_HEADER: "nope"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["detail"] == wrong.json()["detail"]

    def test_writes_also_protected(self, clean_env: None) -> None:
        """POST endpoints are gated like the read-only ones."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.post("/api/v1/validations", json={}).status_code == 401


class TestNonAsciiApiKey:
    """A wrong or missing key yields a clean 401, never a 500.

    This class originally asserted that a *non-ASCII configured key* produced
    401s instead of 500s. That is no longer the right assertion: such a key is
    now refused at boot (see :class:`TestNonAsciiKeyRejectedAtBoot`), so the
    app never comes up and the transport case is unreachable through
    ``create_app``.

    What still matters, and is pinned here, is that the byte-comparison change
    in ``require_api_key`` never turns a bad credential into a server error —
    including a client that puts raw non-ASCII bytes on the wire, which is what
    the old ``str`` comparison used to crash on.
    """

    def test_wrong_key_401_not_500(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/api/v1/validations", headers={API_KEY_HEADER: "ascii"})
        assert response.status_code == 401, response.text

    def test_missing_key_401_not_500(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        assert _client().get("/api/v1/validations").status_code == 401

    def test_destructive_route_also_401_not_500(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.delete("/api/v1/validations/vrun-deadbeef")
        assert response.status_code == 401, response.text

    def test_raw_non_ascii_header_bytes_yield_401_not_500(self, clean_env: None) -> None:
        """The original crash: non-ASCII bytes must 401, not raise TypeError."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(
            "/api/v1/validations", headers={API_KEY_HEADER: b"k\xe9y"}
        )
        assert response.status_code == 401, response.text

    def test_exact_ascii_key_authenticates(self, clean_env: None) -> None:
        """The positive control: a valid ASCII key must still be accepted."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get(
            "/api/v1/health", headers={API_KEY_HEADER: "secret-key"}
        )
        assert response.status_code == 200, response.text


class TestNonAsciiKeyRejectedAtBoot:
    """A non-ASCII key must fail at startup, not lock the operator out silently.

    The byte-coercion in ``require_api_key`` made a *correctly supplied*
    non-ASCII key compare correctly instead of raising ``TypeError`` (which had
    been a loud 500). But HTTP header values cannot carry non-ASCII text
    (RFC 7230) and httpx refuses to even send one, so such a key remains
    unusable: every request 401s, indistinguishable from a wrong key, while
    ``/api/v1/health`` still reports ``status: ok`` and the Docker HEALTHCHECK
    asserts exactly that 200. A silent lockout is worse than a boot failure, so
    it must be refused up front with an actionable message.
    """

    @pytest.mark.parametrize("bad", ["k\u00e9y", "p\u00e4ss", "\u5bc6\u7801"])
    def test_non_ascii_key_refuses_to_start(self, clean_env: None, bad: str) -> None:
        # Guard the parametrisation itself: an ASCII case here would be
        # asserted to raise ValueError, which is simply false.
        assert not bad.isascii(), "this case is about the non-ASCII guard"
        os.environ[ENV_KEY] = bad
        with pytest.raises(ValueError, match="ASCII"):
            _client()


    def test_ascii_key_containing_a_space_is_usable(self, clean_env: None) -> None:
        """The boundary opposite the one above, pinned rather than skipped.

        The boot guard rejects *non-ASCII* keys because httpx cannot even send
        them, which turns a correct key into a silent lockout. A space is legal
        in an HTTP field value, so a key carrying a stray space from a password
        manager is a different case entirely: it must still authenticate. This
        used to be skipped with a "covered elsewhere" note and was not.
        """
        os.environ[ENV_KEY] = "a b"
        client = _client()
        headers = {"X-API-Key": "a b"}
        assert client.get("/api/v1/validations", headers=headers).status_code == 200
        assert client.get("/api/v1/validations").status_code == 401
        assert (
            client.get("/api/v1/validations", headers={"X-API-Key": "a  b"}).status_code
            == 401
        )
    def test_ascii_key_still_accepted(self, clean_env: None) -> None:
        """The guard must not reject ordinary keys."""
        os.environ[ENV_KEY] = "secret-key"
        assert _client().get("/api/v1/validations").status_code == 401


class TestSchemaDocsAreGated:
    """``/docs``, ``/redoc`` and ``/openapi.json`` must not leak when keyed.

    FastAPI mounts these three on the application itself, *outside* the
    ``/api/v1`` router that carries the ``X-API-Key`` dependency, so they
    answered ``200`` to an anonymous caller even with a key configured. The
    OpenAPI document discloses every route, parameter name and response schema
    — a reconnaissance gift for an otherwise authenticated API. Health stays
    public because a container probe cannot present a credential.
    """

    _SCHEMA_PATHS = ("/openapi.json", "/docs", "/redoc")

    @pytest.mark.parametrize("path", _SCHEMA_PATHS)
    def test_anonymous_is_rejected(self, clean_env: None, path: str) -> None:
        os.environ[ENV_KEY] = "secret-key"
        assert _client().get(path).status_code == 401

    @pytest.mark.parametrize("path", _SCHEMA_PATHS)
    def test_authenticated_still_works(self, clean_env: None, path: str) -> None:
        """The gate must not simply disable the docs for everyone."""
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        assert client.get(path, headers={API_KEY_HEADER: "secret-key"}).status_code == 200

    def test_docs_public_when_no_key_configured(self, clean_env: None) -> None:
        """Local development and the open test-suite keep interactive docs."""
        client = _client()
        assert client.get("/openapi.json").status_code == 200

    def test_health_stays_public(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        assert _client().get("/api/v1/health").status_code == 200

    def test_wrong_key_rejected_on_docs(self, clean_env: None) -> None:
        os.environ[ENV_KEY] = "secret-key"
        client = _client()
        response = client.get("/openapi.json", headers={API_KEY_HEADER: "wrong"})
        assert response.status_code == 401


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
