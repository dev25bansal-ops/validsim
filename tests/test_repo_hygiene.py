"""Guard repository-root test artifacts against accidental commits."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GIT_IGNORE = _REPOSITORY_ROOT / ".gitignore"
_ROOT_ARTIFACT_SAMPLES = (
    "junit.xml",
    "pytest-junit.xml",
    "_sts_junit2.xml",
    "validsim-nightly-junit.xml",
    "coverage.xml",
    ".coverage.xml",
    "pytest-output.txt",
    "collect.txt",
    "pytest.log",
    "run.log",
    "local.sqlite",
    "local.sqlite3",
)


def _is_ignored(relative_path: str) -> bool:
    """Return whether Git ignores a repository-root path that need not exist."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", relative_path],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def test_root_test_and_coverage_artifacts_are_ignored() -> None:
    """Known test-run outputs stay ignored even when they do not exist yet."""
    assert _GIT_IGNORE.is_file()
    for path in _ROOT_ARTIFACT_SAMPLES:
        assert _is_ignored(path), f"{path} is not protected by .gitignore"
