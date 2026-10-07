"""Mutation harness part 2: the binomial gate + new-code tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"d:\SIM-TO-REAL")
PY = REPO / ".venv" / "Scripts" / "python.exe"

MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # ---- the p-value itself ---------------------------------------------
    (
        "binomial tail flipped to P(X >= k)",
        "validsim/engine/scorecard.py",
        "        for k in range(observed_ok + 1)",
        "        for k in range(observed_ok, total + 1)",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialSignificanceMatchesExactBinomial",
    ),
    (
        "binomial summation off-by-one (range(observed_ok))",
        "validsim/engine/scorecard.py",
        "        for k in range(observed_ok + 1)",
        "        for k in range(observed_ok)",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialSignificanceMatchesExactBinomial",
    ),
    (
        "binomial exponent swapped: (1-floor)**k instead of **(total-k)",
        "validsim/engine/scorecard.py",
        "math.comb(total, k) * (floor**k) * ((1.0 - floor) ** (total - k))",
        "math.comb(total, k) * (floor**k) * ((1.0 - floor) ** k)",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialSignificanceMatchesExactBinomial",
    ),
    (
        "alpha comparison inverted (p > alpha)",
        "validsim/engine/scorecard.py",
        "    return p_value < alpha",
        "    return p_value > alpha",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialSignificanceMatchesExactBinomial",
    ),
    (
        "zero-total guard removed",
        "validsim/engine/scorecard.py",
        "    if total <= 0:\n        return False",
        "    if total < 0:\n        return False",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialSignificanceMatchesExactBinomial",
    ),
    (
        "alpha constant 0.05 -> 0.5 (far too permissive)",
        "validsim/engine/scorecard.py",
        "_ADVERSARIAL_ALPHA = 0.05",
        "_ADVERSARIAL_ALPHA = 0.5",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "min-samples constant 30 -> 5",
        "validsim/engine/scorecard.py",
        "_ADVERSARIAL_MIN_SAMPLES = 30",
        "_ADVERSARIAL_MIN_SAMPLES = 5",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "min-samples guard >= -> >  (off-by-one at the boundary)",
        "validsim/engine/scorecard.py",
        "            adversarial_count >= _ADVERSARIAL_MIN_SAMPLES",
        "            adversarial_count > _ADVERSARIAL_MIN_SAMPLES",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "floor constant 0.60 -> 0.40",
        "validsim/engine/scorecard.py",
        "_ADVERSARIAL_SUCCESS_FLOOR = 0.60",
        "_ADVERSARIAL_SUCCESS_FLOOR = 0.40",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "rate guard removed (bare significance test)",
        "validsim/engine/scorecard.py",
        "            and adversarial_rate < _ADVERSARIAL_SUCCESS_FLOOR",
        "            and True",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "adversarial count includes nominal episodes",
        "validsim/engine/scorecard.py",
        "    return list(episodes[nominal_count:])",
        "    return list(episodes)",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "adversarial rate computed over all episodes",
        "validsim/engine/scorecard.py",
        "        adversarial_ok = sum(1 for e in adversarial if e.success)",
        "        adversarial_ok = sum(1 for e in episodes if e.success)",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
    (
        "empty-segment guard dropped (nominal-only runs gated)",
        "validsim/engine/scorecard.py",
        "    if adversarial_count:",
        "    if True:",
        "tests/test_binomial_gate_agent.py",
        "TestAdversarialGateBoundaries",
    ),
]


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None):
    full = {**os.environ, **(env or {})}
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=full,
                       timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    results = []
    for label, rel, old, new, test_file, node in MUTATIONS:
        with tempfile.TemporaryDirectory() as td:
            sandbox = Path(td)
            shutil.copytree(REPO / "validsim", sandbox / "validsim",
                            ignore=shutil.ignore_patterns("__pycache__"))
            for extra in ("conftest.py", "pytest_symlink_free_tmp.py", "pytest.ini"):
                shutil.copy2(REPO / extra, sandbox / extra)
            (sandbox / "tests").mkdir(exist_ok=True)
            shutil.copy2(REPO / "tests" / "conftest.py", sandbox / "tests" / "conftest.py")
            for f in ("test_binomial_gate_agent.py", "test_numeric_invariants_agent.py"):
                shutil.copy2(REPO / "tests" / f, sandbox / "tests" / f)

            target = sandbox / rel
            src = target.read_text(encoding="utf-8")
            if old not in src:
                results.append((label, "SNIPPET-NOT-FOUND", ""))
                print(f"[SKIP ] {label}")
                continue
            target.write_text(src.replace(old, new, 1), encoding="utf-8")

            rc, out = run([str(PY), "-c", "import validsim"], sandbox,
                          {"PYTHONPATH": str(sandbox)})
            if rc != 0:
                results.append((label, "IMPORT-ERROR", out[-300:]))
                print(f"[ERR  ] {label}")
                continue

            node_id = f"tests/{Path(test_file).name}::{node}"
            rc, out = run([str(PY), "-m", "pytest", node_id, "-x", "-q",
                           "--no-header", "-p", "no:cacheprovider"], sandbox,
                          {"PYTHONPATH": str(sandbox)})
            if rc == 0:
                results.append((label, "SURVIVED", ""))
                print(f"[BAD  ] {label}: test PASSED against broken code")
            else:
                results.append((label, "KILLED", ""))
                print(f"[KILL ] {label}")

    bad = [r for r in results if r[1] != "KILLED"]
    print()
    print(f"mutations: {len(results)}  killed: {len(results) - len(bad)}  "
          f"not-killed: {len(bad)}")
    for label, status, detail in bad:
        print(f"  !! {status}: {label}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
