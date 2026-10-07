"""Mutation harness part 3: the new-code behaviour tests."""

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
    # ---- checkpoint_id propagation ---------------------------------------
    (
        "pipeline drops the checkpoint binding on the task",
        "validsim/engine/pipeline.py",
        'task = task.model_copy(update={"checkpoint_id": checkpoint_id})',
        "task = task",
        "tests/test_newcode_behaviour_agent.py",
        "TestCheckpointIdReachesTheBackend",
    ),
    (
        "pipeline binds an empty checkpoint id",
        "validsim/engine/pipeline.py",
        'task = task.model_copy(update={"checkpoint_id": checkpoint_id})',
        'task = task.model_copy(update={"checkpoint_id": ""})',
        "tests/test_newcode_behaviour_agent.py",
        "TestCheckpointIdReachesTheBackend",
    ),
    (
        "pipeline binds a hardcoded checkpoint id",
        "validsim/engine/pipeline.py",
        'task = task.model_copy(update={"checkpoint_id": checkpoint_id})',
        'task = task.model_copy(update={"checkpoint_id": "ckpt-frozen"})',
        "tests/test_newcode_behaviour_agent.py",
        "TestCheckpointIdReachesTheBackend",
    ),
    (
        "scorecard records a different checkpoint than the run",
        "validsim/engine/pipeline.py",
        "        checkpoint_id=checkpoint_id,\n        task_id=task.task_id,",
        '        checkpoint_id="ckpt-frozen",\n        task_id=task.task_id,',
        "tests/test_newcode_behaviour_agent.py",
        "TestCheckpointIdReachesTheBackend",
    ),
    (
        "run_id supplied by the caller is ignored",
        "validsim/engine/pipeline.py",
        "    if run_id is None:\n        run_id = ValidationStore.new_run_id()",
        "    run_id = ValidationStore.new_run_id()",
        "tests/test_newcode_behaviour_agent.py",
        "TestCheckpointIdReachesTheBackend",
    ),
    # ---- the rng.choices swap --------------------------------------------
    (
        "resample size decoupled from the sample size (k=1)",
        "validsim/engine/stats.py",
        "float(statistic(rng.choices(values, k=n))) for _ in range(n_resamples)",
        "float(statistic(rng.choices(values, k=1))) for _ in range(n_resamples)",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    (
        "resample size decoupled (k=n_resamples)",
        "validsim/engine/stats.py",
        "float(statistic(rng.choices(values, k=n))) for _ in range(n_resamples)",
        "float(statistic(rng.choices(values, k=n_resamples))) for _ in range(n_resamples)",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    (
        "bootstrap uses an unseeded global RNG",
        "validsim/engine/stats.py",
        "    rng = random.Random(seed)",
        "    rng = random.Random(0)",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    (
        "bootstrap hardcodes the seed (ignores the argument)",
        "validsim/engine/stats.py",
        "    rng = random.Random(seed)",
        "    rng = random.Random(42)",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    (
        "point estimate taken from a resample instead of the sample",
        "validsim/engine/stats.py",
        "    point = float(statistic(values))",
        "    point = float(statistic(rng.choices(values, k=n)))",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    (
        "resampling without replacement (each resample == the sample)",
        "validsim/engine/stats.py",
        "float(statistic(rng.choices(values, k=n))) for _ in range(n_resamples)",
        "float(statistic(list(values))) for _ in range(n_resamples)",
        "tests/test_newcode_behaviour_agent.py",
        "TestBootstrapChoicesSwap",
    ),
    # ---- the symlink shim ------------------------------------------------
    (
        "shim no longer patches _force_symlink",
        "pytest_symlink_free_tmp.py",
        "    _pypath._force_symlink = _noop_force_symlink",
        "    pass",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
    ),
    (
        "shim no longer patches cleanup_dead_symlinks",
        "pytest_symlink_free_tmp.py",
        "    _pypath.cleanup_dead_symlinks = _noop_cleanup_dead_symlinks",
        "    pass",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
    ),
    (
        "shim patches a misspelled attribute (dead assignment)",
        "pytest_symlink_free_tmp.py",
        "    _pypath._force_symlink = _noop_force_symlink",
        "    _pypath._force_symlink_ = _noop_force_symlink",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
    ),
    (
        "shim noop delegates to the original helper",
        "pytest_symlink_free_tmp.py",
        "def _noop_force_symlink(*args: Any, **kwargs: Any) -> None:",
        "def _noop_force_symlink(*args: Any, **kwargs: Any) -> None:\n"
        "    _ORIGINAL(*args, **kwargs)\n",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
    ),
    (
        "shim module renamed out from under conftest",
        "pytest_symlink_free_tmp.py",
        "def pytest_configure(config: Any) -> None:",
        "def pytest_configure_disabled(config: Any) -> None:",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
    ),
    (
        "shim swaps the two patched helpers",
        "pytest_symlink_free_tmp.py",
        "    _pypath.cleanup_dead_symlinks = _noop_cleanup_dead_symlinks",
        "    _pypath._force_symlink = _noop_cleanup_dead_symlinks",
        "tests/test_newcode_behaviour_agent.py",
        "TestSymlinkFreeTmpShim",
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
            for f in ("test_binomial_gate_agent.py", "test_numeric_invariants_agent.py",
                      "test_newcode_behaviour_agent.py"):
                shutil.copy2(REPO / "tests" / f, sandbox / "tests" / f)

            target = sandbox / rel
            if not target.exists():
                results.append((label, "NO-FILE", ""))
                print(f"[SKIP ] {label}: {rel} not found")
                continue
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
