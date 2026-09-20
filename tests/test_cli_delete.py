"""Tests for the ``validsim delete`` command (store run-deletion CLI parity).

``delete`` removes a run from the configured store, targeting it via
``--run-id`` or ``--latest`` exactly like ``status`` / ``scorecard`` / ``gate``.
The default store is process-local in-memory, so a run saved by one CLI
invocation is invisible to the next; these tests therefore patch
``validsim.cli.create_store`` to hand every invocation the *same* in-memory
store, giving cross-invocation persistence without touching the filesystem.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest
from typer.testing import CliRunner

from validsim import cli
from validsim.store.memory import ValidationStore

runner = CliRunner()
RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


@pytest.fixture
def shared_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[ValidationStore]:
    """A single in-memory store shared across all CLI invocations in a test."""
    store = ValidationStore()
    monkeypatch.setattr(cli, "create_store", lambda *args, **kwargs: store)
    yield store
    store.close()


def _invoke(*args: str) -> object:
    return runner.invoke(cli.app, list(args))


def _run_and_extract_id(checkpoint: str = "ckpt-delete") -> str:
    """Run a validation against the shared store and return its run id."""
    result = _invoke(
        "run", "--episodes", "20", "--adversarial", "4", "--checkpoint", checkpoint
    )
    assert result.exit_code == 0, result.output
    match = RUN_ID_RE.search(result.output)  # type: ignore[attr-defined]
    assert match is not None, result.output
    return match.group(0)


class TestDelete:
    def test_delete_existing_run_exits_zero_and_removes_from_store(
        self, shared_store: ValidationStore
    ) -> None:
        run_id = _run_and_extract_id()
        # Sanity: the run really is in the store before we ask to delete it.
        assert shared_store.get(run_id) is not None

        result = _invoke("delete", "--run-id", run_id)
        assert result.exit_code == 0, result.output
        assert run_id in result.output  # type: ignore[attr-defined]

        # Gone from the store afterwards.
        assert shared_store.get(run_id) is None

    def test_delete_missing_run_exits_two(self, shared_store: ValidationStore) -> None:
        result = _invoke("delete", "--run-id", "vrun-deadbeef")
        assert result.exit_code == 2

    def test_delete_requires_exactly_one_target(self) -> None:
        # Neither flag nor both flags are unresolvable -> exit 2 (BadParameter).
        assert _invoke("delete").exit_code == 2  # type: ignore[attr-defined]

    def test_delete_latest_resolves_newest_run(
        self, shared_store: ValidationStore
    ) -> None:
        first = _run_and_extract_id("ckpt-a")
        second = _run_and_extract_id("ckpt-b")
        assert first != second

        result = _invoke("delete", "--latest")
        assert result.exit_code == 0, result.output
        # --latest resolves to the newest cached run (the second one).
        assert second in result.output  # type: ignore[attr-defined]

        assert shared_store.get(second) is None
        assert shared_store.get(first) is not None
