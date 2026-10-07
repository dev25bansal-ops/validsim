"""Temporary audit: every VALIDSIM_* literal in validsim/notify/ vs INFRA_KEYS.

Mirrors tests/test_project_config.py::test_drift_guard_every_env_key_in_the
_codebase_is_classified exactly (same regex, same rglob), but scoped to my
folder so I can see line numbers and the unclassified set.
"""

import pathlib
import re

from validsim.project_config import INFRA_KEYS, SECRET_KEYS

RX = re.compile(r'"(VALIDSIM_[A-Z0-9_]+)"')

found = {}
for path in sorted(pathlib.Path("validsim/notify").rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    for match in RX.finditer(text):
        line = text[: match.start()].count("\n") + 1
        found.setdefault(match.group(1), []).append(f"{path.as_posix()}:{line}")

print("=== VALIDSIM_* literals in validsim/notify/ ===")
for name in sorted(found):
    sites = ", ".join(found[name])
    flags = []
    if name not in INFRA_KEYS:
        flags.append("NOT-IN-INFRA_KEYS")
    if name in SECRET_KEYS:
        flags.append("secret")
    suffix = f"   [{' '.join(flags)}]" if flags else ""
    print(f"  {name}{suffix}\n      {sites}")

unclassified = sorted(set(found) - INFRA_KEYS)
print()
print(f"distinct literals: {len(found)}")
print(f"UNCLASSIFIED (drift guard fails): {unclassified}")
