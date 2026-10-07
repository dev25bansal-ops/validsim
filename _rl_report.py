"""Temporary: re-run the suites and write results to disk, since the shell's
stdout is dead (host ran out of disk). Deleted after use.
"""

import io
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_rl_report.txt")

lines = []
usage = shutil.disk_usage("C:/")
lines.append("C: free %.2f GB / total %.2f GB" % (usage[2] / 2**30, usage[0] / 2**30))


def run(label, argv):
    lines.append("")
    lines.append("=" * 70)
    lines.append("$ " + " ".join(argv))
    proc = subprocess.run(
        argv, cwd=HERE, capture_output=True, text=True, errors="replace"
    )
    lines.append("EXIT=%d" % proc.returncode)
    tail = proc.stdout.splitlines()
    # drop the per-request log noise the app emits
    keep = [ln for ln in tail if '"timestamp"' not in ln]
    lines.extend(keep[-60:] if len(keep) > 60 else keep)
    return proc.returncode


run("rate-limit", [sys.executable, "-m", "pytest",
                  "tests/test_api_rate_limit.py",
                  "tests/test_api_rate_limit_scope.py",
                  "-p", "no:cacheprovider", "-q", "--tb=short"])
run("full", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=no"])
run("ruff", [sys.executable, "-m", "ruff", "check", "validsim", "tests"])

with io.open(OUT, "w", encoding="utf-8", errors="replace") as fh:
    fh.write("\n".join(lines))
