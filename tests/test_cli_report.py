"""Tests for the `validsim report` command and `run --environment` option."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim.cli import app

runner = CliRunner()
RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _run_and_extract_id(*extra: str) -> str:
    result = _invoke("run", "--episodes", "20", "--adversarial", "4",
                     "--checkpoint", "ckpt-report", *extra)
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
    match = RUN_ID_RE.search(result.output)  # type: ignore[attr-defined]
    assert match is not None, result.output  # type: ignore[attr-defined]
    return match.group(0)


class TestReport:
    def test_markdown_report_by_run_id(self) -> None:
        run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id)
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        output = result.output  # type: ignore[attr-defined]
        assert "# ValidSim Validation Report" in output
        assert f"`{run_id}`" in output
        assert "## Metrics" in output
        assert "## Failure Taxonomy" in output
        assert "## Confidence Interval" in output

    def test_html_report_by_run_id(self) -> None:
        run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id, "--format", "html")
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        output = result.output  # type: ignore[attr-defined]
        assert output.lstrip().startswith("<!DOCTYPE html>")
        assert "Composite score" in output

    def test_report_latest(self) -> None:
        _run_and_extract_id()
        result = _invoke("report", "--latest")
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        assert "# ValidSim Validation Report" in result.output  # type: ignore[attr-defined]

    def test_missing_run_exits_2(self) -> None:
        result = _invoke("report", "--run-id", "vrun-deadbeef")
        assert result.exit_code == 2

    def test_latest_with_empty_cache_exits_2(self) -> None:
        result = _invoke("report", "--latest")
        assert result.exit_code == 2

    def test_neither_flag_exits_2(self) -> None:
        result = _invoke("report")
        assert result.exit_code == 2

    def test_both_flags_exits_2(self) -> None:
        run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id, "--latest")
        assert result.exit_code == 2

    def test_invalid_format_exits_2(self) -> None:
        run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id, "--format", "pdf")
        assert result.exit_code == 2

    def test_markdown_default_format(self) -> None:
        run_id = _run_and_extract_id()
        default = _invoke("report", "--run-id", run_id)
        explicit = _invoke("report", "--run-id", run_id, "--format", "markdown")
        assert default.output == explicit.output  # type: ignore[attr-defined]


class TestRunEnvironment:
    def test_default_environment(self) -> None:
        run_id = _run_and_extract_id()
        payload = _invoke("scorecard", "--run-id", run_id)  # type: ignore[attr-defined]
        # Scorecard JSON does not include the environment; instead assert the
        # default run succeeds without --environment.
        assert payload.exit_code == 0

    def test_environment_option_accepted(self, tmp_path: Path) -> None:
        run_id = _run_and_extract_id("--environment", "warehouse-bay")
        assert run_id.startswith("vrun-")

    def test_environment_short_flag(self, tmp_path: Path) -> None:
        run_id = _run_and_extract_id("-E", "lab-room")
        assert run_id.startswith("vrun-")
