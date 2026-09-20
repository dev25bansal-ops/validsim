"""Tests for filesystem path hardening in :mod:`validsim.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from validsim.config import EnvironmentSpec, RobotSpec, resolve_asset_path


class TestPathFieldValidators:
    @pytest.mark.parametrize("path", ["robots/franka.urdf", "scenes/kitchen.usda", "a/b/c"])
    def test_accepts_relative_paths(self, path: str) -> None:
        assert RobotSpec(name="r", urdf_path=path).urdf_path == path
        assert EnvironmentSpec(name="e", scene_usd=path).scene_usd == path

    def test_accepts_none(self) -> None:
        assert RobotSpec(name="r").urdf_path is None
        assert EnvironmentSpec(name="e").scene_usd is None

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "C:/Windows/system32",
            "C:\\Windows\\system32",
            "\\\\server\\share",
        ],
    )
    def test_rejects_absolute_paths(self, path: str) -> None:
        with pytest.raises(ValidationError, match="absolute"):
            RobotSpec(name="r", urdf_path=path)
        with pytest.raises(ValidationError, match="absolute"):
            EnvironmentSpec(name="e", scene_usd=path)

    @pytest.mark.parametrize(
        "path",
        [
            "../secret",
            "robots/../franka.urdf",
            "..\\secret",
            "robots\\..\\franka.urdf",
        ],
    )
    def test_rejects_traversal_segments(self, path: str) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            RobotSpec(name="r", urdf_path=path)
        with pytest.raises(ValidationError, match="traversal"):
            EnvironmentSpec(name="e", scene_usd=path)

    @pytest.mark.parametrize("path", ["robots/franka.urdf\x00", "\x00scenes/x"])
    def test_rejects_nul_bytes(self, path: str) -> None:
        with pytest.raises(ValidationError, match="NUL"):
            RobotSpec(name="r", urdf_path=path)
        with pytest.raises(ValidationError, match="NUL"):
            EnvironmentSpec(name="e", scene_usd=path)


class TestResolveAssetPath:
    def test_joins_beneath_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("VALIDSIM_ASSET_ROOT", str(tmp_path))
        assert resolve_asset_path("robots/franka.urdf") == (
            tmp_path / "robots" / "franka.urdf"
        ).resolve()

    def test_rejects_escape_above_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("VALIDSIM_ASSET_ROOT", str(tmp_path))
        with pytest.raises(ValueError, match="escapes"):
            resolve_asset_path("../secret.txt")

    def test_respects_asset_root_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = tmp_path / "assets"
        root.mkdir()
        monkeypatch.setenv("VALIDSIM_ASSET_ROOT", str(root))
        assert resolve_asset_path("x.urdf") == (root / "x.urdf").resolve()

    def test_default_root_is_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDSIM_ASSET_ROOT", raising=False)
        assert resolve_asset_path("sub/file.txt") == (
            Path.cwd() / "sub" / "file.txt"
        ).resolve()