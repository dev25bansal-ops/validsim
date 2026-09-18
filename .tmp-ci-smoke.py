"""Throwaway CI-smoke test: validate action.yml parsing + renderer against real CLI output."""
import json
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, VALIDSIM_CACHE_FILE=os.path.join(ROOT, ".tmp-ci-smoke", "scorecards.json"))

# 1. Simulate the validate action's grep-based parsing on the real run log.
log = open(os.path.join(ROOT, ".tmp-ci-smoke-run.log"), encoding="utf-8").read()
run_id = re.findall(r"vrun-[0-9a-f]{8}", log)[-1]
score_line = [ln for ln in log.splitlines() if "Composite:" in ln][-1]
score = re.search(r"Composite:\s+([0-9]+(?:\.[0-9]+)?)", score_line).group(1)
print(f"parsed run-id={run_id} score={score}")

# 2. gate exit code must be 1 (82.2 < 85 -> BLOCK).
p = subprocess.run(
    [sys.executable, "-m", "validsim.cli", "gate", "--run-id", run_id, "--threshold", "85"],
    env=ENV, capture_output=True, text=True, cwd=ROOT,
)
print(f"gate exit={p.returncode} out={p.stdout.strip()}")
assert p.returncode == 1, "expected BLOCK -> exit 1"

# 3. Extract the heredoc renderer from actions/scorecard/action.yml and run it.
doc = yaml.safe_load(open(os.path.join(ROOT, "actions", "scorecard", "action.yml"), encoding="utf-8"))
step = next(s for s in doc["runs"]["steps"] if s.get("id") == "render")
run_block = step["run"]  # YAML already stripped the 8-space base indent
m = re.search(r"<<'PYX'\n(.*?)\nPYX\n", run_block, re.S)
assert m, "could not extract PYX heredoc"
renderer = m.group(1)
open(os.path.join(ROOT, ".tmp-renderer.py"), "w", encoding="utf-8").write(renderer)

p = subprocess.run(
    [sys.executable, "-m", "validsim.cli", "scorecard", "--run-id", run_id],
    env=ENV, capture_output=True, text=True, cwd=ROOT,
)
assert p.returncode == 0, p.stderr
json.loads(p.stdout)  # CLI really prints JSON
open(os.path.join(ROOT, ".tmp-scorecard.json"), "w", encoding="utf-8").write(p.stdout)

p = subprocess.run(
    [sys.executable, os.path.join(ROOT, ".tmp-renderer.py"),
     os.path.join(ROOT, ".tmp-scorecard.json"), os.path.join(ROOT, ".tmp-scorecard.md")],
    capture_output=True, text=True, cwd=ROOT,
)
print(f"renderer exit={p.returncode} err={p.stderr.strip()}")
assert p.returncode == 0
print("---- generated markdown ----")
print(open(os.path.join(ROOT, ".tmp-scorecard.md"), encoding="utf-8").read())
print("SMOKE OK")
