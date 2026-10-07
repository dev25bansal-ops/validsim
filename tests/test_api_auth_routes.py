"""Exhaustive ``X-API-Key`` gate coverage: EVERY route, not a sample.

The module docstring of :mod:`validsim.api.main` warns that FastAPI snapshots
router-level dependencies per route at include time, so "a route defined AFTER
the dependency is attached silently escapes it". That is exactly the failure mode
a hand-written list of spot checks cannot rule out: it can only prove the routes
somebody remembered. These tests derive the route set from the built application
itself, so a route added later is covered without touching this file.

Method: build the app with a key configured, enumerate
``app.routes`` *and* ``app.router.routes`` (the metrics router is included twice
— once under ``/api/v1`` so it inherits the gate, once at the root so Prometheus
can scrape it without a credential), then assert two independent things per
route:

1. **Structural** — the resolved ``route.dependant`` actually carries the
   ``require_api_key`` dependency. This is the property that was fragile.
2. **Behavioural** — an anonymous request to the path really is answered 401.

Both must hold for every route except the deliberately public set, which is
asserted exactly (no more, no less) so that *widening* the public set fails here
too.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from validsim.api.main import API_KEY_HEADER, PUBLIC_PATHS, create_app
from validsim.store.memory import ValidationStore

ENV_KEY = "VALIDSIM_API_KEY"

#: Routes that are *deliberately* reachable without a credential, with the
#: reason each is safe. Anything not listed here must be gated.
#:
#: * ``/api/v1/health`` — the container healthcheck cannot present a credential;
#:   the payload is configuration, not data.
#: * ``/metrics`` — Prometheus scrapes by convention and has no credential;
#:   the body is aggregate counts, no run data. Its ``/api/v1/metrics`` twin
#:   *is* gated.
#: * ``/`` — the dashboard's HTML *shell* only. It is the one unauthenticated
#:   surface that can explain why a key is needed: the SPA boots, reads the 401
#:   from its first ``/api/v1`` call and renders the auth panel. Gating it would
#:   return bare 401 JSON with no page to host that panel, leaving an operator
#:   no way to authenticate. Discloses no run data — see
#:   ``validsim/api/dashboard.py::mount_dashboard``.
#:
#: Widening this set is the reason the assertion below is exact: a new public
#: route must be a deliberate, reviewed change, not a side effect.
_EXPECTED_PUBLIC = {
    "/api/v1/health",
    "/metrics",
    "/",
}

#: Gate dependency names that count as "this route is protected".
_GATE_NAMES = {"require_api_key", "require_api_key_for_destructive"}

#: ``{placeholder}`` in a path template -> a value that satisfies the route's own
#: validation but is guaranteed not to exist, so an ungated route still 404s
#: rather than accidentally succeeding.
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")
_PATH_VALUES = {
    "run_id": "vrun-deadbeef",
    "job_id": "vrun-deadbeef",
    "checkpoint_id": "ckpt-absent",
}


@pytest.fixture()
def keyed_app() -> Any:
    """A freshly built app with ``VALIDSIM_API_KEY`` configured."""
    saved = os.environ.get(ENV_KEY)
    saved_rate = os.environ.get("VALIDSIM_RATE_LIMIT")
    # A generous limit so a 401-vs-other-status probe is never a 429.
    os.environ["VALIDSIM_API_KEY"] = "route-inventory-key"
    os.environ["VALIDSIM_RATE_LIMIT"] = "100000"
    try:
        yield create_app(ValidationStore())
    finally:
        for name, value in ((ENV_KEY, saved), ("VALIDSIM_RATE_LIMIT", saved_rate)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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

def _all_routes(app: Any) -> list[Any]:
    """Every HTTP route on the app, de-duplicated by (path, method).

    Includes both :class:`APIRoute` (everything registered through a router or
    a decorator, which carries a resolved ``dependant``) and plain
    ``starlette.routing.Route`` (the routes FastAPI generates itself for
    ``/docs``, ``/redoc``, ``/openapi.json`` and ``/docs/oauth2-redirect``, which
    have no dependant at all). Enumerating only the former would silently skip
    the four documentation routes — which is precisely where the ungated
    ``/docs/oauth2-redirect`` was hiding.
    """
    seen: dict[tuple[str, str], Any] = {}
    for route in _iter_effective_routes(app):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or not methods:
            continue  # Mount (/static) and websocket routes
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            seen.setdefault((path, method), route)
    return list(seen.values())


def _api_routes(app: Any) -> list[APIRoute]:
    """Only the routes FastAPI resolved dependencies onto."""
    return [route for route in _all_routes(app) if isinstance(route, APIRoute)]


def _gate_dependencies(route: APIRoute) -> set[str]:
    """Names of the auth gates carried by ``route``'s resolved dependant."""
    found: set[str] = set()
    stack: list[Any] = [route.dependant]
    seen: set[int] = set()
    while stack:
        dependant = stack.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        call = getattr(dependant, "call", None)
        if getattr(call, "__name__", "") in _GATE_NAMES:
            found.add(getattr(call, "__name__"))
        stack.extend(getattr(dependant, "dependencies", ()) or ())
    return found


def _concrete(path: str) -> str:
    """Fill a path template with values that satisfy per-route validation."""
    return _PLACEHOLDER.sub(lambda m: _PATH_VALUES.get(m.group(1), "x"), path)


def _probe(client: TestClient, method: str, path: str) -> tuple[int, str]:
    try:
        response = client.request(method, _concrete(path))
    except Exception as exc:  # pragma: no cover - surfaced as a status string
        return -1, f"{type(exc).__name__}: {exc}"
    return response.status_code, response.text[:200]


class TestEveryRouteIsGated:
    def test_public_set_is_exactly_the_agreed_one(self, keyed_app: Any) -> None:
        """A *newly* exempted route must be a deliberate, reviewed change."""
        public_routes = {
            route.path
            for route in _all_routes(keyed_app)
            if route.path in _EXPECTED_PUBLIC or route.path in PUBLIC_PATHS
        }
        assert public_routes == _EXPECTED_PUBLIC, (
            "the set of publicly reachable routes changed: "
            f"{sorted(public_routes)} != {sorted(_EXPECTED_PUBLIC)}"
        )

    def test_every_non_public_route_carries_the_auth_dependency(
        self, keyed_app: Any
    ) -> None:
        """Structural proof: the gate is on the route, not merely in a middleware.

        This is the regression that matters. A route registered after
        ``application.router.dependencies`` was set, or one mounted directly on
        the app, can answer 401 today by accident (a middleware, a dependency on
        the router) while being one refactor away from answering 200.

        Scoped to :class:`APIRoute`, which is where FastAPI resolves
        dependencies. The four FastAPI-generated documentation routes carry no
        dependant by construction and are covered behaviourally by
        :meth:`TestEveryRouteIsGated.test_generated_docs_routes_are_covered`.
        """
        ungated = [
            (sorted(route.methods), route.path)
            for route in _api_routes(keyed_app)
            if route.path not in _EXPECTED_PUBLIC and not _gate_dependencies(route)
        ]
        assert ungated == [], (
            "routes with no auth dependency resolved onto them (they may be "
            f"protected only by accident): {ungated}"
        )

    def test_every_non_public_route_answers_401_anonymously(
        self, keyed_app: Any
    ) -> None:
        """Behavioural proof, per method, for the *whole* route surface.

        This is the assertion that has no blind spot: it covers plain Starlette
        routes (``/docs`` and friends) as well as FastAPI's, and it derives its
        own inventory, so it cannot be weakened by forgetting to add a route.
        """
        client = TestClient(keyed_app, raise_server_exceptions=False)
        observed: list[str] = []
        for route in _all_routes(keyed_app):
            if route.path in _EXPECTED_PUBLIC or route.path.startswith("/static"):
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                status, text = _probe(client, method, route.path)
                if status != 401:
                    observed.append(f"{method} {route.path} -> {status} {text!r}")
        assert observed == [], "routes reachable without a key:\n" + "\n".join(observed)

    def test_correct_key_reaches_the_whole_surface(self, keyed_app: Any) -> None:
        """The positive control: the gate is not simply refusing everyone.

        A blanket-401 app would pass the two tests above. Every non-public route
        must get *past* auth when the key is right — anything other than 401
        proves the request reached route logic (a 404 for an unknown id, a 422
        for a missing body, 200 for a list, ...).
        """
        client = TestClient(keyed_app, raise_server_exceptions=False)
        still_rejected: list[str] = []
        for route in _all_routes(keyed_app):
            if route.path in _EXPECTED_PUBLIC or route.path.startswith("/static"):
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                response = client.request(
                    method,
                    _concrete(route.path),
                    headers={API_KEY_HEADER: "route-inventory-key"},
                )
                if response.status_code == 401:
                    still_rejected.append(f"{method} {route.path}")
        assert still_rejected == [], f"correct key still rejected on: {still_rejected}"

    def test_generated_docs_routes_are_covered(self, keyed_app: Any) -> None:
        """``/docs``' own siblings must be gated, not just ``/docs`` itself.

        RED before the fix: ``/docs/oauth2-redirect`` answered **200** to a fully
        anonymous caller while ``/docs`` and ``/redoc`` were correctly gated. It
        is a Swagger UI helper page, so the disclosure is minor, but the gate was
        demonstrably an incomplete list — and an incomplete list is exactly the
        pattern that lets the next FastAPI-generated route slip through.
        """
        client = TestClient(keyed_app)
        for path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            assert client.get(path).status_code == 401, (
                f"{path} is not gated"
            )
            assert (
                client.get(path, headers={API_KEY_HEADER: "route-inventory-key"}).status_code
                == 200
            ), f"{path} rejects the correct key"

    def test_static_mount_is_not_a_data_surface(self, keyed_app: Any) -> None:
        """``/static`` serves SPA assets, and is intentionally left open.

        Pinned explicitly so the assertion above does not silently grow: the
        dashboard HTML/JS bundle is a build artifact containing no run data, and
        the *data* endpoints it calls are all under ``/api/v1`` and gated.
        """
        client = TestClient(keyed_app)
        assert client.get("/static/app.js").status_code == 200


class TestGateIsPerMethod:
    def test_write_methods_are_gated_like_reads(self, keyed_app: Any) -> None:
        """A sample of every method present, to catch a method-scoped hole."""
        client = TestClient(keyed_app, raise_server_exceptions=False)
        cases: Iterator[tuple[str, str]] = iter(
            [
                ("POST", "/api/v1/validations"),
                ("GET", "/api/v1/validations"),
                ("DELETE", "/api/v1/validations/vrun-deadbeef"),
                ("POST", "/api/v1/validations/vrun-deadbeef/compare"),
                ("POST", "/api/v1/jobs"),
                ("GET", "/api/v1/jobs"),
                ("GET", "/api/v1/metrics"),
            ]
        )
        for method, path in cases:
            assert client.request(method, path).status_code == 401, f"{method} {path}"
