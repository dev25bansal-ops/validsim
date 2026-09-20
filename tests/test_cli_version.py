"""Tests for the ``validsim version`` command.

The command is a pure, side-effect-free introspection helper: it prints the
installed package version plus a one-line feature summary and always exits 0,
without touching the cache or any validation store. These tests assert that
contract against the live package ``__version__`` so the CLI output and the
distribution metadata can never silently drift apart.
"""

from __future__ import annotations

from typer.testing import CliRunner

from validsim import __version__
from validsim.cli import app

runner = CliRunner()


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


class TestVersionCommand:
    def test_version_exits_zero(self) -> None:
        result = _invoke("version")
        assert result.exit_code == 0, result.output

    def test_version_prints_package_version(self) -> None:
        result = _invoke("version")
        output = result.output  # type: ignore[attr-defined]
        # The literal package version (e.g. "0.2.0") must appear in the output.
        assert __version__ in output
        assert "0.2.0" in output

    def test_version_prints_feature_summary(self) -> None:
        result = _invoke("version")
        output = result.output  # type: ignore[attr-defined]
        # A one-line human-readable feature summary accompanies the version.
        assert "ValidSim" in output
        assert "validation" in output.lower()

    def test_version_does_not_require_cache(self, tmp_path, monkeypatch) -> None:
        # Point the cache at a non-existent path: version must still succeed
        # because it never reads or writes the cache/store.
        monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(tmp_path / "missing.json"))
        result = _invoke("version")
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "missing.json").exists()


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
