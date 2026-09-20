"""Tests for validsim.config_loader (optional config-file overrides)."""

from __future__ import annotations

from pathlib import Path

import pytest

from validsim.config_loader import (
    ConfigFileError,
    discover_config_file,
    load_config_file,
    load_default_config,
)


class TestLoadConfigFile:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert load_config_file(tmp_path / "does-not-exist.yaml") == {}

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported config format"):
            load_config_file(path)


class TestYamlFlatFile:
    def test_valid_flat_file(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text(
            "# leading comment\n"
            "robot: franka_panda\n"
            "episodes: 1000\n"
            "threshold: 89.5\n"
            "randomization_enabled: true\n"
            "verbose: false\n"
            "note: 'quoted value'\n"
            "data_dir: /tmp/data\n"
            "\n",
            encoding="utf-8",
        )
        assert load_config_file(path) == {
            "robot": "franka_panda",
            "episodes": 1000,
            "threshold": 89.5,
            "randomization_enabled": True,
            "verbose": False,
            "note": "quoted value",
            "data_dir": "/tmp/data",
        }

    def test_scalar_coercion_types(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text(
            "a_int: 42\n"
            "b_neg_int: -7\n"
            "c_float: 3.14\n"
            "d_neg_float: -0.5\n"
            "e_true: true\n"
            "f_false: false\n"
            "g_yes: yes\n"
            "h_no: no\n"
            "i_on: on\n"
            "j_off: off\n"
            "k_null: null\n"
            "l_quoted_num: '42'\n"
            "m_empty:\n",
            encoding="utf-8",
        )
        overrides = load_config_file(path)

        assert overrides["a_int"] == 42
        assert isinstance(overrides["a_int"], int)
        assert overrides["b_neg_int"] == -7
        assert overrides["c_float"] == 3.14
        assert isinstance(overrides["c_float"], float)
        assert overrides["d_neg_float"] == -0.5
        assert overrides["e_true"] is True
        assert overrides["f_false"] is False
        assert overrides["g_yes"] is True
        assert overrides["h_no"] is False
        assert overrides["i_on"] is True
        assert overrides["j_off"] is False
        assert overrides["k_null"] is None
        assert overrides["l_quoted_num"] == "42"
        assert overrides["m_empty"] is None

    def test_inline_comment_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text(
            "episodes: 500  # nominal episode count\n"
            "hash_like: 'text # not a comment'\n",
            encoding="utf-8",
        )
        assert load_config_file(path) == {
            "episodes": 500,
            "hash_like": "text # not a comment",
        }

    def test_malformed_line_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text("this line has no colon\n", encoding="utf-8")
        with pytest.raises(ValueError, match="key: value"):
            load_config_file(path)

    def test_nested_mapping_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text("parent:\n  child: 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="nested mappings"):
            load_config_file(path)

    def test_flow_collection_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text("items: [a, b]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="flow collections"):
            load_config_file(path)

    def test_duplicate_key_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text("a: 1\na: 2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate key"):
            load_config_file(path)

    def test_config_error_is_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.yaml"
        path.write_text("bad line\n", encoding="utf-8")
        with pytest.raises(ConfigFileError):
            load_config_file(path)


class TestToml:
    def test_toml_path(self, tmp_path: Path) -> None:
        pytest.importorskip("tomllib")
        path = tmp_path / "validsim.config.toml"
        path.write_text(
            'robot = "franka_panda"\n'
            "episodes = 1000\n"
            "threshold = 89.5\n"
            "enabled = true\n",
            encoding="utf-8",
        )
        assert load_config_file(path) == {
            "robot": "franka_panda",
            "episodes": 1000,
            "threshold": 89.5,
            "enabled": True,
        }

    def test_malformed_toml_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "validsim.config.toml"
        path.write_text("= not valid toml\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_config_file(path)


class TestDiscovery:
    def test_discover_prefers_toml_over_yaml(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("VALIDSIM_CONFIG", raising=False)
        (tmp_path / "validsim.config.yaml").write_text("from_yaml: 1\n", encoding="utf-8")
        (tmp_path / "validsim.config.toml").write_text("from_toml = 1\n", encoding="utf-8")
        assert discover_config_file(search_dir=tmp_path) == tmp_path / "validsim.config.toml"
        assert load_default_config(search_dir=tmp_path) == {"from_toml": 1}

    def test_discover_env_override(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "validsim.config.yaml").write_text("default: true\n", encoding="utf-8")
        explicit = tmp_path / "explicit.toml"
        explicit.write_text("explicit = 42\n", encoding="utf-8")
        monkeypatch.setenv("VALIDSIM_CONFIG", str(explicit))
        assert discover_config_file(search_dir=tmp_path) == explicit

    def test_discover_returns_none_when_absent(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("VALIDSIM_CONFIG", raising=False)
        assert discover_config_file(search_dir=tmp_path) is None
        assert load_default_config(search_dir=tmp_path) == {}