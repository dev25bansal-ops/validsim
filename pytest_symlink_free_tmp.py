"""Pytest plugin: restore ``tmp_path`` on hosts where symlinks cannot be used.

pytest >= 8 creates a ``current`` symlink inside every numbered ``tmp_path``
directory and traverses it during teardown. Where the host rejects symlink
traversal (sandboxed or mounted Windows drives, containers lacking the
``SeCreateSymbolicLink`` privilege), teardown aborts with::

    OSError: [WinError 448] The path cannot be traversed because it contains
    an untrusted mount point: '...\\test_foo_current'

and the run ends with **no summary line at all** -- an absent result rather than
a failure. The tests themselves pass; only the cleanup explodes, so the suite
becomes unverifiable.

This module replaces pytest's numbering helper with one that creates a plain
directory, so no symlink is ever created. Everything else about ``tmp_path`` is
unchanged: each test still gets an isolated, writable, uniquely numbered
directory, retained when the test fails and removed when it passes.

Registered from the repository's ``conftest.py`` so it applies to every run
without extra flags.
"""

from __future__ import annotations

from typing import Any

import _pytest.pathlib as _pypath


def _noop_force_symlink(*args: Any, **kwargs: Any) -> None:
    """Do not create the ``current`` convenience symlink.

    It is purely a developer convenience pointing at the most recent numbered
    directory; nothing in the fixture contract depends on it, because
    ``tmp_path`` is resolved from the returned path rather than by following
    the link. Neutralising it is a no-op on hosts where symlinks work.
    """


def _noop_cleanup_dead_symlinks(root: Any, *args: Any, **kwargs: Any) -> None:
    """Skip the dead-symlink sweep for a temp root.

    ``cleanup_dead_symlinks`` calls ``Path.resolve()`` on every ``*current``
    entry it finds. On a host that cannot traverse symlinks that raises
    ``OSError`` (WinError 448), which propagates out of session teardown and
    destroys the run summary. The sweep only garbage-collects stale
    convenience links, so skipping it is behaviourally safe: the numbered
    directories themselves are still aged out by ``cleanup_numbered_dir``.
    """


def pytest_configure(config: Any) -> None:
    """Apply both patches before any tmp_path fixture is resolved."""
    _pypath._force_symlink = _noop_force_symlink
    _pypath.cleanup_dead_symlinks = _noop_cleanup_dead_symlinks
