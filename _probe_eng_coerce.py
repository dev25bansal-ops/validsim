"""REPRO: OverflowError escapes the tolerant coercion helpers."""
from __future__ import annotations

import math
import traceback

from validsim.engine._coerce import as_float, as_int
from validsim.engine.anomaly import detect_anomalies

print("=" * 78)
print("A. as_int / as_float: which bad inputs are actually tolerated?")
print("=" * 78)
print("  as_int docstring: 'None and non-numeric strings never raise'")
print("  as_float docstring: 'bad types never raise'")
print()
for label, val in [
    ("None", None),
    ("'abc'", "abc"),
    ("'3.5'", "3.5"),
    ("object()", object()),
    ("float('nan')", float("nan")),
    ("float('inf')", float("inf")),
    ("float('-inf')", float("-inf")),
    ("10**400 (huge int)", 10 ** 400),
    ("Decimal('1e400')", __import__("decimal").Decimal("1e400")),
]:
    for name, fn in (("as_int", as_int), ("as_float", as_float)):
        try:
            r = fn(val)
            print(f"  {name:9s}({label:22s}) = {r!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:9s}({label:22s}) !! {type(exc).__name__}: {exc}")

print()
print("=" * 78)
print("B. Root cause: OverflowError is not in the except clause")
print("=" * 78)
import inspect
from validsim.engine import _coerce
print(inspect.getsource(_coerce.as_int).split('"""')[-1].strip())
print(inspect.getsource(_coerce.as_float).split('"""')[-1].strip())

print()
print("=" * 78)
print("C. The crash reaches the public detector")
print("=" * 78)
hist = [
    {"run_id": f"b{i}", "total_episodes": float("inf"), "failure_taxonomy": {"collision": 5}}
    for i in range(3)
]
hist.append({"run_id": "cur", "total_episodes": 100, "failure_taxonomy": {"collision": 50}})
try:
    detect_anomalies(hist)
except Exception:  # noqa: BLE001
    traceback.print_exc()

print()
print("  Control A: same history, total_episodes as a plain int")
ok = [
    {"run_id": f"b{i}", "total_episodes": 100, "failure_taxonomy": {"collision": 5}}
    for i in range(3)
]
ok.append({"run_id": "cur", "total_episodes": 100, "failure_taxonomy": {"collision": 50}})
print("   ->", detect_anomalies(ok))

print()
print("  Control B: total_episodes = NaN (a sibling of inf)")
nan = [
    {"run_id": f"b{i}", "total_episodes": float("nan"), "failure_taxonomy": {"collision": 5}}
    for i in range(3)
]
nan.append({"run_id": "cur", "total_episodes": 100, "failure_taxonomy": {"collision": 50}})
print("   ->", detect_anomalies(nan), " (NaN is caught by ValueError, inf is not)")

print()
print("=" * 78)
print("D. Is Infinity reachable from persisted/serialized data?")
print("=" * 78)
import json
print("  json.dumps(float('inf')) ->", json.dumps(float("inf")))
print("  json.loads('1e400')     ->", json.loads("1e400"), "(parses to inf!)")
print("  json.loads of a 400-digit int ->", type(json.loads("1" + "0" * 400)).__name__)
print("  => a store row / API payload carrying Infinity or 1e400 reaches as_int")
