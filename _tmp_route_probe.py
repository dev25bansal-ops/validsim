"""MEASUREMENT (throwaway probe, deleted after use).

Enumerate EVERY route on the app and report, per route:
  * structural: does the route's dependant carry the require_api_key gate?
  * behavioural: does an unauthenticated request actually get 401?

Run:  python _tmp_route_probe.py
"""

from __future__ import annotations

import os
import re

from fastapi.testclient import TestClient

os.environ["VALIDSIM_API_KEY"] = "secret-key"
os.environ["VALIDSIM_RATE_LIMIT"] = "100000"

from validsim.api.main import API_KEY_HEADER, create_app  # noqa: E402
from validsim.store.memory import ValidationStore  # noqa: E402

app = create_app(ValidationStore())
client = TestClient(app, raise_server_exceptions=False)

PLACEHOLDER = re.compile(r"\{[^}]+\}")


def gate_names(route) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def walk(dep) -> None:
        call = getattr(dep, "call", None)
        name = getattr(call, "__name__", "")
        if name in ("require_api_key", "require_api_key_for_destructive"):
            names.append(name)
        for sub in getattr(dep, "dependencies", ()) or ():
            if id(sub) not in seen:
                seen.add(id(sub))
                walk(sub)

    # APIRoute exposes the resolved dependant; Mount/WebSocketRoute do not.
    dep = getattr(route, "dependant", None)
    if dep is None:
        dep = getattr(route, "app", None)
        dep = getattr(getattr(dep, "dependency_cache", None), "dependant", None)
    if dep is not None:
        walk(dep)
    return sorted(set(names))


print("=== route inventory (key configured) ===")
print(f"{'METHOD':7} {'PATH':52} {'GATE(dep)':40} {'anon status'}")
gaps = []
for route in app.routes:
    path = getattr(route, "path", None)
    if path is None:
        print(f"{'-':7} {type(route).__name__ + ' ' + str(getattr(route, 'path', '')):52}")
        continue
    methods = sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"})
    if not methods:
        continue
    gates = gate_names(route)
    probe_path = PLACEHOLDER.sub("vrun-deadbeef", path)
    for method in methods:
        try:
            response = client.request(method, probe_path)
            status = response.status_code
        except Exception as exc:  # pragma: no cover
            status = f"ERR {type(exc).__name__}"
        is_public_path = path in ("/api/v1/health", "/metrics")
        flag = ""
        if not is_public_path and not path.startswith("/static"):
            if not gates:
                flag = "  <== NO DEPENDENT GATE"
                gaps.append((method, path, "no-dependant-gate", status))
            elif status != 401:
                flag = "  <== ESCAPES GATE"
                gaps.append((method, path, ",".join(gates), status))
        print(f"{method:7} {path:52} {','.join(gates) or '-':40} {status}{flag}")

print()
print("=== GAPS ===")
for item in gaps:
    print(item)
if not gaps:
    print("none")
