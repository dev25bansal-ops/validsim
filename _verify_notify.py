"""One-shot verification for the notify-wiring change (exit code is the signal).

Uses subprocess so the result is read from pytest's/ruff's own exit status,
never from a PowerShell pipe (which reports 0 regardless of the result).
"""

import re
import pathlib
import subprocess
import sys

NOTIFY_TESTS = [
    "tests/test_notify.py",
    "tests/test_notify_email.py",
    "tests/test_notify_email_security.py",
    "tests/test_notify_enhancements.py",
    "tests/test_notify_integration.py",
    "tests/test_notify_routing.py",
    "tests/test_notify_wiring_agent.py",
]

RX = re.compile(r'"(VALIDSIM_[A-Z0-9_]+)"')


def audit() -> int:
    """Report unclassified VALIDSIM_* literals in validsim/notify/."""
    from validsim.project_config import INFRA_KEYS

    found = set()
    for path in sorted(pathlib.Path("validsim/notify").rglob("*.py")):
        found.update(RX.findall(path.read_text(encoding="utf-8")))
    unclassified = sorted(found - INFRA_KEYS)
    print(f"    notify literals={len(found)} unclassified={unclassified}")
    return 1 if unclassified else 0


def run(label: str, argv: list[str]) -> int:
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=".")
    tail = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()][-3:]
    print(f"--- {label} ---")
    for line in tail:
        print("   ", line)
    print(f"    exit={proc.returncode}")
    return proc.returncode


results = {
    "notify env classification audit": audit(),
    "notify test suite": run(
        "notify tests",
        [sys.executable, "-m", "pytest", *NOTIFY_TESTS, "-p", "no:cacheprovider", "-q",
         "--tb=line"],
    ),
    "project_config suite": run(
        "project_config tests",
        [sys.executable, "-m", "pytest", "tests/test_project_config.py", "-p",
         "no:cacheprovider", "-q", "--tb=line"],
    ),
    "ruff (my files + project_config)": run(
        "ruff",
        [sys.executable, "-m", "ruff", "check", "validsim/notify",
         "validsim/project_config.py", "tests/test_notify_wiring_agent.py"],
    ),
}

print("\n=== SUMMARY (0 = pass) ===")
failed = False
for name, code in results.items():
    print(f"  {'PASS' if code == 0 else 'FAIL'}  {name} (exit={code})")
    failed = failed or code != 0
sys.exit(1 if failed else 0)
