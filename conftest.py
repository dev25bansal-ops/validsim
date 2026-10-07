"""Ensure the repository root is importable so `validsim` resolves under pytest.

Also enables the symlink-free ``tmp_path`` shim when the host cannot traverse
symlinks (sandboxed/mounted Windows drives, containers without
``SeCreateSymbolicLink``). Without it pytest >= 8 aborts during teardown with
``WinError 448`` and the run produces **no summary line**, which makes the suite
unverifiable rather than failing. The shim is a no-op on hosts where symlinks
work normally, so CI is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - environment-dependent
    import pytest_symlink_free_tmp as _symlink_free_tmp

    _symlink_free_tmp.pytest_configure(None)  # type: ignore[arg-type]
except Exception:  # noqa: BLE001 - never block collection on this shim
    pass
