"""Structural guards for the ValidSim API layer.

This file is a *characterisation* suite: it pins down behaviour of
:func:`validsim.api.main.create_app` that is currently correct but is only
implicitly protected by the rest of the suite. It exists so that the extraction
refactors proposed in the API-layer audit can be landed one at a time with a
failing test that names the specific invariant that broke.

The three areas covered:

* **Middleware ordering.** ``create_app`` registers four middlewares and its
  comments assert what the resulting order means. Starlette's
  ``add_middleware`` *prepends*, so the registration order inside
  ``create_app`` is the reverse of the run order. These tests assert the
  resolved run order, and separately record the two consequences that the
  in-code comments do not currently mention.
* **The auth gate's dependency-snapshot semantics.** ``create_app`` gates
  ``/api/v1`` by assigning ``app.router.dependencies``. FastAPI merges those
  dependencies into each route's dependant *at include time*, which makes the
  gate sensitive to statement order inside ``create_app``.
* **The route table.** The set of paths, methods and resolved per-route
  dependencies, so an extraction that drops or double-registers a route fails
  loudly instead of silently changing the public surface.

Nothing here asserts a *desired* state; everything asserts the state measured
on 2026-09-26, so these tests are expected to pass unchanged against the
current tree and to fail informatively if a refactor perturbs it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from validsim.api.main import create_app

#: Env vars ``create_app`` reads at build time. Cleared before every test so each
#: case controls its own configuration deterministically (mirrors the fixture
#: used by ``tests/test_api_health.py``).
_CONFIG_ENV = (
    "VALIDSIM_API_KEY",
    "VALIDSIM_CORS_ORIGINS",
    "VALIDSIM_RATE_LIMIT",
    "VALIDSIM_RATE_WINDOW_SECONDS",
    "VALIDSIM_JOB_QUEUE",
)

#: Middleware classes in the order ``create_app`` registers them, top to bottom
#: of the function body. The *run* order is the reverse of this (see
#: :class:`TestMiddlewareRunOrder`).
REGISTRATION_ORDER = (
    "_RateLimitMiddleware",
    "CORSMiddleware",
    "_DocsAuthMiddleware",
    "_ObservabilityMiddleware",
)

#: The resolved outermost-to-innermost run order when an API key is configured,
#: which is the only configuration that installs all four middlewares.
#: _ObservabilityMiddleware is outermost so that EVERY response -- including the
#: 401s produced by the docs gate -- is logged, correlated and counted.
EXPECTED_RUN_ORDER = (
    "_ObservabilityMiddleware",
    "_DocsAuthMiddleware",
    "CORSMiddleware",
    "_RateLimitMiddleware",
)


@pytest.fixture
def clean_env() -> Iterator[None]:
    """Clear the API configuration env vars for the duration of one test."""
    saved = {name: os.environ.get(name) for name in _CONFIG_ENV}
    for name in _CONFIG_ENV:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _middleware_names(application: FastAPI) -> list[str]:
    """Middleware class names in run order (index 0 is the outermost)."""
    return [entry.cls.__name__ for entry in application.user_middleware]


def _iter_effective_routes(application: Any) -> list[Any]:
    """Every route FastAPI will actually serve, with include prefixes resolved.

    ``app.routes`` no longer flattens ``include_router``. Each include is parked
    as a lazy ``_IncludedRouter`` node that carries no ``path``, ``methods`` or
    ``dependant``, so a naive walk drops every mounted router *silently* -- it
    filters them out rather than reporting them. That is how ``/metrics`` and the
    jobs and dashboard routes came to be missing from the assertions below while
    all of them served requests correctly.
    """
    resolved: list[Any] = []
    for route in application.routes:
        if type(route).__name__ == "_IncludedRouter":
            resolved.extend(route.effective_candidates())
        else:
            resolved.append(route)
    return resolved


def _route_dependencies(application: FastAPI) -> list[tuple[str, str, list[str]]]:
    """Every ``/api/v1`` route as ``(method, path, dependency_names)``.

    Keyed on method *and* path because two routes share
    ``/api/v1/validations/{run_id}`` (``GET`` and ``DELETE``) and only the
    ``DELETE`` dependant carries the extra destructive gate.
    """
    resolved: list[tuple[str, str, list[str]]] = []
    for route in _iter_effective_routes(application):
        path = getattr(route, "path", "")
        dependant = getattr(route, "dependant", None)
        if not path.startswith("/api/v1") or dependant is None:
            continue
        names = [getattr(dep.call, "__name__", str(dep.call)) for dep in dependant.dependencies]
        methods = getattr(route, "methods", None) or set()
        method = next(iter(methods), "")
        resolved.append((method, path, names))
    return resolved


def _dependencies_for(application: FastAPI, method: str, path: str) -> list[str]:
    """Dependencies of the single route registered for ``method``/``path``."""
    matches = [
        names
        for route_method, route_path, names in _route_dependencies(application)
        if route_method == method and route_path == path
    ]
    assert len(matches) == 1, f"expected exactly one {method} {path}, got {len(matches)}"
    return matches[0]


class TestMiddlewareRunOrder:
    """The resolved run order, which is the reverse of the registration order.

    Starlette's ``add_middleware`` inserts at index 0 and ``build_middleware_stack``
    composes the stack so that ``user_middleware[0]`` is the outermost layer. A
    middleware registered *last* therefore runs *first* — it is outermost.
    """

    def test_registration_order_is_reversed_into_run_order(self, clean_env: None) -> None:
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        application = create_app()
        assert _middleware_names(application) == list(EXPECTED_RUN_ORDER)
        assert list(reversed(_middleware_names(application))) == list(REGISTRATION_ORDER)

    def test_observability_is_outermost_of_the_ungated_middlewares(
        self, clean_env: None
    ) -> None:
        """With no key there is no docs gate, so observability is outermost.

        This is the case the in-code comment at
        ``validsim/api/main.py`` ("registered last so it is the outermost user
        middleware") actually describes.
        """
        application = create_app()
        names = _middleware_names(application)
        assert names[0] == "_ObservabilityMiddleware"
        assert "_RateLimitMiddleware" in names

    def test_docs_gate_sits_inside_observability_when_a_key_is_configured(
        self, clean_env: None
    ) -> None:
        """Observability stays outermost even when a key adds the docs gate.

        This test previously asserted the opposite -- that registering
        ``_DocsAuthMiddleware`` last made *it* outermost, which meant the 401 it
        returns escaped both the access log and the request counter. The gate is
        now registered *before* observability, so observability remains the
        outermost layer and sees every response.
        """
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        names = _middleware_names(create_app())
        assert names[0] == "_ObservabilityMiddleware"
        assert names.index("_DocsAuthMiddleware") > names.index("_ObservabilityMiddleware")

    def test_rate_limit_is_innermost_of_the_unconditional_middlewares(
        self, clean_env: None
    ) -> None:
        """Rate limiting is registered first, so it runs last (innermost).

        Consequence: CORS preflight and observability both run *before* the
        limiter, and a 429 produced by the limiter is still observed by both.
        """
        names = _middleware_names(create_app())
        assert names.index("_RateLimitMiddleware") == len(names) - 1

    def test_rate_limit_middleware_absent_when_disabled(self, clean_env: None) -> None:
        os.environ["VALIDSIM_RATE_LIMIT"] = "0"
        assert "_RateLimitMiddleware" not in _middleware_names(create_app())


class TestDocsGateIsObserved:
    """The docs gate must sit INSIDE observability, not outside it.

    This class originally asserted the opposite: that ``_DocsAuthMiddleware``
    short-circuited before ``_ObservabilityMiddleware`` was entered, leaving the
    401 it produces uncounted and uncorrelated. That was a real defect --
    anonymous probes of /docs, /redoc and /openapi.json were exactly the
    requests an operator most wants to correlate, and they were the only ones
    that vanished from both the access log and ``validsim_http_requests_total``.

    The ordering has since been corrected (the docs gate is registered *before*
    observability, making observability outermost), so these assertions are
    inverted to pin the corrected behaviour. They are deliberately phrased as
    the opposite of what they used to say, so a regression is caught rather
    than silently re-characterised.
    """

    def test_docs_401_is_counted_by_the_metrics_middleware(self, clean_env: None) -> None:
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        application = create_app()
        client = TestClient(application)

        before = application.state.metrics.snapshot_http()
        docs_status = client.get("/docs").status_code
        gate_status = client.get("/api/v1/validations").status_code
        after = application.state.metrics.snapshot_http()

        assert (docs_status, gate_status) == (401, 401)
        # Two 401s were produced, and observability is outermost, so BOTH count.
        assert after["4xx"] - before["4xx"] == 2

    def test_docs_401_carries_a_request_id(self, clean_env: None) -> None:
        """The correlation id is minted by observability, so both 401s have one."""
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        client = TestClient(create_app())

        docs_headers = {k.lower() for k in client.get("/docs").headers}
        gate_headers = {k.lower() for k in client.get("/api/v1/validations").headers}

        assert "x-request-id" in gate_headers
        assert "x-request-id" in docs_headers


class TestAuthGateDependencySnapshot:
    """``app.router.dependencies`` is snapshotted per route at include time.

    ``create_app`` sets the gate once, then registers the inline routes and
    includes the sub-routers. FastAPI copies ``router.dependencies`` into each
    route's dependant when the route is added, so a route registered *before* the
    assignment would escape the gate. ``create_app`` is currently correct because
    the assignment (line 679) precedes every route registration, but the ordering
    is invisible at the call site and is the single easiest thing an extraction
    could silently break.
    """

    def test_every_api_v1_route_carries_the_global_gate(self, clean_env: None) -> None:
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        routes = _route_dependencies(create_app())
        assert routes, "expected /api/v1 routes to be registered"
        for method, path, names in routes:
            assert "require_api_key" in names, f"{method} {path} escaped the global API-key gate"

    def test_delete_carries_the_extra_destructive_gate(self, clean_env: None) -> None:
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        application = create_app()
        delete_deps = _dependencies_for(application, "DELETE", "/api/v1/validations/{run_id}")
        get_deps = _dependencies_for(application, "GET", "/api/v1/validations/{run_id}")
        assert "require_api_key_for_destructive" in delete_deps
        # The plain GET on the same path does not.
        assert "require_api_key_for_destructive" not in get_deps

    def test_public_scrape_and_health_endpoints_are_ungated_at_runtime(
        self, clean_env: None
    ) -> None:
        """``/metrics`` and ``/api/v1/health`` carry the gate but short-circuit inside it.

        ``PUBLIC_PATHS`` exempts health inside ``require_api_key``; ``/metrics`` is
        registered with an empty ``router.dependencies`` list, so it has no gate
        at all. Both answer 200 to an anonymous caller even with a key configured.
        """
        os.environ["VALIDSIM_API_KEY"] = "secret-key"
        client = TestClient(create_app())
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/api/v1/metrics").status_code == 401

    def test_gate_escapes_routes_registered_before_the_assignment(self) -> None:
        """Demonstrates the fragility the ordering above protects against.

        A minimal app with the gate assigned *after* ``include_router`` leaves the
        included route answering 200; assigning before gates it. ``create_app``
        must keep its assignment ahead of every registration.
        """

        def build(assign_first: bool) -> FastAPI:
            application = FastAPI()

            def gate() -> None:
                raise HTTPException(status_code=401, detail="gated")

            sub = APIRouter()

            @sub.get("/late")
            def late() -> dict[str, int]:
                return {"ok": 1}

            if assign_first:
                application.router.dependencies = [Depends(gate)]
            application.include_router(sub)
            if not assign_first:
                application.router.dependencies = [Depends(gate)]
            return application

        assert TestClient(build(assign_first=False)).get("/late").status_code == 200
        assert TestClient(build(assign_first=True)).get("/late").status_code == 401


class TestRouteTableContract:
    """The public surface, pinned so an extraction cannot quietly change it."""

    def test_api_v1_paths_and_methods(self, clean_env: None) -> None:
        application = create_app()
        observed = {
            (method, path)
            for method, path, _ in _route_dependencies(application)
        }
        expected = {
            ("GET", "/api/v1/health"),
            ("POST", "/api/v1/validations"),
            ("GET", "/api/v1/validations"),
            ("GET", "/api/v1/validations/{run_id}"),
            ("DELETE", "/api/v1/validations/{run_id}"),
            ("GET", "/api/v1/validations/{run_id}/scorecard"),
            ("GET", "/api/v1/validations/{run_id}/failures"),
            ("POST", "/api/v1/validations/{run_id}/compare"),
            ("GET", "/api/v1/regressions"),
            ("GET", "/api/v1/validations/{run_id}/scorecard.pdf"),
            ("GET", "/api/v1/validations/{run_id}/scorecard.md"),
            ("GET", "/api/v1/validations/{run_id}/scorecard.html"),
            ("GET", "/api/v1/models"),
            ("GET", "/api/v1/models/{checkpoint_id}/history"),
            ("GET", "/api/v1/dashboard/history"),
            ("GET", "/api/v1/dashboard/summary"),
            ("GET", "/api/v1/dashboard/"),
            ("POST", "/api/v1/jobs"),
            ("GET", "/api/v1/jobs"),
            ("GET", "/api/v1/jobs/{job_id}"),
            ("GET", "/api/v1/jobs/{job_id}/status"),
            ("GET", "/api/v1/jobs/{job_id}/events"),
            ("GET", "/api/v1/metrics"),
        }
        assert observed == expected

    def test_no_duplicate_operation_ids_in_openapi(self, clean_env: None) -> None:
        """Route names must stay unique or the generated OpenAPI collides."""
        application = create_app()
        schema = application.openapi()
        operation_ids = [
            operation["operationId"]
            for path_item in schema["paths"].values()
            for method, operation in path_item.items()
            if isinstance(operation, dict) and "operationId" in operation
        ]
        duplicates = {oid for oid in operation_ids if operation_ids.count(oid) > 1}
        assert not duplicates, f"duplicate operationIds: {sorted(duplicates)}"

    def test_static_and_root_mounts_present(self, clean_env: None) -> None:
        """``mount_dashboard`` contributes the ``/static`` mount and the ``/`` route."""
        application = create_app()
        paths = {getattr(route, "path", "") for route in application.routes}
        assert "/static" in paths
        assert "/" in paths

    def test_health_payload_keys(self, clean_env: None) -> None:
        """The probe's key set is a contract with the container healthcheck."""
        os.environ["VALIDSIM_RATE_LIMIT"] = "5"
        os.environ["VALIDSIM_RATE_WINDOW_SECONDS"] = "30"
        payload: dict[str, Any] = TestClient(create_app()).get("/api/v1/health").json()
        assert set(payload) == {
            "status",
            "version",
            "auth_enabled",
            "cors_wildcard",
            "rate_limit",
            "store_backend",
            "job_queue_backend",
        }
        assert payload["rate_limit"] == {"requests": 5, "window_seconds": 30.0}

    def test_rate_limit_disabled_reports_none(self, clean_env: None) -> None:
        os.environ["VALIDSIM_RATE_LIMIT"] = "0"
        payload = TestClient(create_app()).get("/api/v1/health").json()
        assert payload["rate_limit"] is None
