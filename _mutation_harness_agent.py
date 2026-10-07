"""Mutation harness: verify each new test actually FAILS against broken code.

Method: copy the repo's validsim package into a temp sandbox, apply a textual
mutation to one file, run the named test against the mutated package, and assert
the test FAILS. A test that passes against broken code is worthless, so this
harness is how each new test earns its place.

Usage:  python _mutation_harness_agent.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"d:\SIM-TO-REAL")
PY = REPO / ".venv" / "Scripts" / "python.exe"

# (label, relative file, old snippet, new snippet, test file, test node substring)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # ---- weight sum invariants -------------------------------------------
    (
        "scorecard weight _W_SAFETY 0.3 -> 0.4",
        "validsim/engine/scorecard.py",
        "_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1",
        "_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.4, 0.2, 0.1",
        "tests/test_numeric_invariants_agent.py",
        "TestWeightsSumToOne",
    ),
    (
        "safety weight _PROXIMITY_WEIGHT 0.2 -> 0.3",
        "validsim/engine/safety.py",
        "_COLLISION_WEIGHT = 0.5\n_FORCE_WEIGHT = 0.3\n_PROXIMITY_WEIGHT = 0.2",
        "_COLLISION_WEIGHT = 0.5\n_FORCE_WEIGHT = 0.3\n_PROXIMITY_WEIGHT = 0.3",
        "tests/test_numeric_invariants_agent.py",
        "TestWeightsSumToOne",
    ),
    (
        "safety weight zeroed (channel disabled)",
        "validsim/engine/safety.py",
        "_PROXIMITY_WEIGHT = 0.2",
        "_PROXIMITY_WEIGHT = 0.0",
        "tests/test_numeric_invariants_agent.py",
        "TestWeightsSumToOne",
    ),
    (
        "scorecard weight negated (inverted sign)",
        "validsim/engine/scorecard.py",
        "_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1",
        "_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, -0.3, 0.2, 0.1",
        "tests/test_numeric_invariants_agent.py",
        "TestWeightsSumToOne",
    ),
    # ---- bounds ----------------------------------------------------------
    (
        "safety per-channel cap removed (collision)",
        "validsim/engine/safety.py",
        "min(collisions_per_episode, 1.0) * _COLLISION_WEIGHT",
        "collisions_per_episode * _COLLISION_WEIGHT",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "safety force rate loses its division (rate -> raw count)",
        "validsim/engine/safety.py",
        "    force_rate = force_exceeded / total",
        "    force_rate = force_exceeded",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "safety force limit comparison flipped (> -> <)",
        "validsim/engine/safety.py",
        "or e.max_contact_force_n > force_limit_n",
        "or e.max_contact_force_n < force_limit_n",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "safety force channel dropped entirely",
        "validsim/engine/safety.py",
        "        + min(force_rate, 1.0) * _FORCE_WEIGHT",
        "        + 0.0 * _FORCE_WEIGHT",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "safety force weight swapped with collision weight",
        "validsim/engine/safety.py",
        "        + min(force_rate, 1.0) * _FORCE_WEIGHT",
        "        + min(force_rate, 1.0) * _COLLISION_WEIGHT",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "composite clamp upper bound 100 -> 1",
        "validsim/engine/scorecard.py",
        "composite = _clamp(composite, 0.0, 100.0)",
        "composite = _clamp(composite, 0.0, 1.0)",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "robustness scale 200 -> 300 (dispersion overshoots)",
        "validsim/engine/scorecard.py",
        "_ROBUSTNESS_SCALE = 200.0",
        "_ROBUSTNESS_SCALE = 300.0",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "robustness uses sample stdev instead of pstdev",
        "validsim/engine/scorecard.py",
        "spread = statistics.pstdev(rates)",
        "spread = statistics.stdev(rates)",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    (
        "success-count floor dropped (0-success run becomes certifiable)",
        "validsim/engine/scorecard.py",
        "and evaluation.success_count > 0",
        "and True",
        "tests/test_numeric_invariants_agent.py",
        "TestScorecardStaysInRange",
    ),
    # ---- monotonicity ----------------------------------------------------
    (
        "regression penalty sign inverted",
        "validsim/engine/scorecard.py",
        "return max(0.0, 100.0 - _REGRESSION_PENALTY * count)",
        "return max(0.0, 100.0 + _REGRESSION_PENALTY * count)",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "regression penalty 25 -> 10",
        "validsim/engine/scorecard.py",
        "_REGRESSION_PENALTY = 25.0",
        "_REGRESSION_PENALTY = 10.0",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "CI resamples double the draws (CI widens with more evidence)",
        "validsim/engine/stats.py",
        "float(statistic(rng.choices(values, k=n))) for _ in range(n_resamples)",
        "float(statistic(rng.choices(values, k=2 * n))) for _ in range(n_resamples)",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "CI percentile index scaled by sample size",
        "validsim/engine/stats.py",
        "low = _percentile(estimates, alpha)",
        "low = _percentile(estimates, alpha * n)",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "adversarial split sliced from the front",
        "validsim/engine/scorecard.py",
        "return list(episodes[nominal_count:])",
        "return list(episodes[:nominal_count])",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "adversarial floor applied to empty segment",
        "validsim/engine/scorecard.py",
        "    if adversarial_count:",
        "    if True:",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "success_rate inverted (failures score higher)",
        "validsim/engine/evaluation.py",
        "success_rate=success_count / total,",
        "success_rate=1.0 - success_count / total,",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
    (
        "CI bracket widening removed (interval may exclude the point)",
        "validsim/engine/stats.py",
        "    low = min(low, point)\n    high = max(high, point)",
        "    pass",
        "tests/test_numeric_invariants_agent.py",
        "TestMonotonicity",
    ),
]


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, env=full_env, timeout=600
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    results = []
    for label, rel, old, new, test_file, node in MUTATIONS:
        with tempfile.TemporaryDirectory() as td:
            sandbox = Path(td)
            shutil.copytree(
                REPO / "validsim", sandbox / "validsim",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for extra in ("conftest.py", "pytest_symlink_free_tmp.py", "pytest.ini"):
                shutil.copy2(REPO / extra, sandbox / extra)
            (sandbox / "tests").mkdir(exist_ok=True)
            shutil.copy2(REPO / test_file, sandbox / "tests" / Path(test_file).name)
            shutil.copy2(REPO / "tests" / "conftest.py", sandbox / "tests" / "conftest.py")

            target = sandbox / rel
            src = target.read_text(encoding="utf-8")
            if old not in src:
                results.append((label, "SNIPPET-NOT-FOUND", ""))
                print(f"[SKIP ] {label}: snippet not found in {rel}")
                continue
            target.write_text(src.replace(old, new, 1), encoding="utf-8")

            # Sanity: the mutated package must still import.
            rc, out = run(
                [str(PY), "-c", "import validsim"], sandbox,
                {"PYTHONPATH": str(sandbox)},
            )
            if rc != 0:
                results.append((label, "IMPORT-ERROR", out[-400:]))
                print(f"[ERR  ] {label}: mutated package does not import")
                continue

            node_id = f"tests/{Path(test_file).name}::{node}"
            rc, out = run(
                [str(PY), "-m", "pytest", node_id, "-x", "-q", "--no-header", "-p",
                 "no:cacheprovider"],
                sandbox,
                {"PYTHONPATH": str(sandbox)},
            )
            if rc == 0:
                results.append((label, "SURVIVED", ""))
                print(f"[BAD  ] {label}: test PASSED against broken code")
            else:
                first = next(
                    (ln for ln in out.splitlines() if ln.startswith("E ")), ""
                )
                results.append((label, "KILLED", first[:150]))
                print(f"[KILL ] {label}")

    print()
    survived = [r for r in results if r[1] != "KILLED"]
    print(f"mutations: {len(results)}  killed: {len(results) - len(survived)}  "
          f"not-killed: {len(survived)}")
    for label, status, detail in survived:
        print(f"  !! {status}: {label}  {detail}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
