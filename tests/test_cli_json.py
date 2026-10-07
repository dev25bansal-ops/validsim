"""Tests for the machine-readable CLI additions.

Covers three features layered on top of the existing command set:

* ``report --json`` — emit the cached scorecard dict as raw JSON.
* ``validate``      — a discoverability alias of ``run`` (same options,
  identical implementation).
* ``gate --json``   — print the CI decision as JSON on stdout while keeping
  the 0/1/2 exit-code contract (decided from the durable store, so these
  tests request the ``durable_store`` fixture from ``conftest.py``).

The Typer ``app``, the shared ``CliRunner`` and the autouse ``cache_file``
fixture (which points ``VALIDSIM_CACHE_FILE`` at a temp file) are reused from
``tests/test_cli.py`` so cache isolation and invocation stay consistent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Reuse the CliRunner, cache-isolating autouse fixture and helpers verbatim so
# these tests exercise the exact same harness as the base CLI suite.
from test_cli import (  # noqa: F401  (cache_file is an autouse fixture)
    RUN_ID_RE,
    _invoke,
    _run_and_extract_id,
    _run_at_threshold,
    cache_file,
    runner,
)
from validsim.cli import app


class TestReportJson:
    """``report --json`` dumps the cached scorecard instead of Markdown/HTML."""

    def test_report_json_emits_scorecard(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id, "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["run_id"] == run_id
        assert payload["checkpoint_id"] == "ckpt-cli"
        assert payload["episode_count"] == 48  # 40 nominal + 8 adversarial

    def test_report_json_matches_scorecard_command(self) -> None:
        _, run_id = _run_and_extract_id()
        report = _invoke("report", "--run-id", run_id, "--json")
        scorecard = _invoke("scorecard", "--run-id", run_id)
        assert report.exit_code == 0 and scorecard.exit_code == 0
        # Both serialise the same cached dict with indent=2, so bytes match.
        assert report.output == scorecard.output  # type: ignore[attr-defined]

    def test_report_json_overrides_format(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id, "--json", "--format", "html")
        assert result.exit_code == 0, result.output
        # --json short-circuits: no HTML doctype, valid JSON instead.
        assert "<!DOCTYPE html>" not in result.output  # type: ignore[attr-defined]
        assert json.loads(result.output)["run_id"] == run_id  # type: ignore[arg-type]

    def test_report_json_with_latest(self, cache_file: Path) -> None:  # noqa: F811
        _, run_id = _run_and_extract_id()
        result = _invoke("report", "--latest", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["run_id"] == run_id  # type: ignore[arg-type]

    def test_report_json_missing_run_exits_2(self) -> None:
        result = _invoke("report", "--run-id", "vrun-deadbeef", "--json")
        assert result.exit_code == 2

    def test_report_default_still_markdown(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("report", "--run-id", run_id)
        assert result.exit_code == 0, result.output
        assert "# ValidSim Validation Report" in result.output  # type: ignore[attr-defined]


class TestValidateAlias:
    """``validate`` is a pure alias of ``run``."""

    def test_validate_prints_summary(self) -> None:
        result = _invoke("validate", "--episodes", "40", "--adversarial", "8",
                         "--checkpoint", "ckpt-validate")
        assert result.exit_code == 0, result.output
        output = result.output  # type: ignore[attr-defined]
        assert "ValidSim validation complete" in output
        assert "Decision:" in output
        assert "Composite:" in output
        assert RUN_ID_RE.search(output) is not None

    def test_validate_caches_scorecard(self, cache_file: Path) -> None:  # noqa: F811
        result = _invoke("validate", "--episodes", "20", "--adversarial", "4",
                         "--checkpoint", "ckpt-validate-cache")
        assert result.exit_code == 0, result.output
        run_id = RUN_ID_RE.search(result.output).group(0)  # type: ignore[union-attr]
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert run_id in data
        assert data[run_id]["checkpoint_id"] == "ckpt-validate-cache"

    def test_validate_output_matches_run(self) -> None:
        # Deterministic seed (checkpoint+task) means the only difference between
        # an equivalent `run` and `validate` is the freshly minted run id.
        run_result = _invoke("run", "--episodes", "40", "--adversarial", "8",
                             "--checkpoint", "ckpt-eq")
        val_result = _invoke("validate", "--episodes", "40", "--adversarial", "8",
                             "--checkpoint", "ckpt-eq")
        assert run_result.exit_code == 0 and val_result.exit_code == 0
        mask = lambda s: RUN_ID_RE.sub("vrun-XXXXXXXX", s)  # noqa: E731
        assert mask(run_result.output) == mask(val_result.output)  # type: ignore[arg-type]

    def test_validate_accepts_run_options(self, cache_file: Path) -> None:  # noqa: F811
        # A custom --threshold must flow through to the stored scorecard,
        # proving the alias wires up the same options as `run`.
        result = _invoke("validate", "--episodes", "10", "--adversarial", "0",
                         "--checkpoint", "ckpt-thr", "--threshold", "42.0")
        assert result.exit_code == 0, result.output
        run_id = RUN_ID_RE.search(result.output).group(0)  # type: ignore[union-attr]
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data[run_id]["threshold"] == 42.0

    def test_validate_lists_all_shared_flags(self) -> None:
        help_result = runner.invoke(app, ["validate", "--help"])
        assert help_result.exit_code == 0, help_result.output
        for flag in ("--task", "--robot", "--episodes", "--adversarial",
                     "--checkpoint", "--environment", "--threshold"):
            assert flag in help_result.output

    def test_validate_appears_in_top_level_help(self) -> None:
        help_result = runner.invoke(app, ["--help"])
        assert help_result.exit_code == 0, help_result.output
        assert "validate" in help_result.output


@pytest.mark.usefixtures("durable_store")
class TestGateJson:
    """``gate --json`` prints the decision as JSON and keeps exit codes.

    Every case needs the sqlite store: ``gate`` refuses to decide from the
    per-process in-memory backend, and cross-checks the decision against
    ``scorecard`` (which reads the JSON cache) to prove the two agree.
    """

    def test_gate_json_approve(self) -> None:
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        scorecard = json.loads(
            _invoke("scorecard", "--run-id", run_id).output  # type: ignore[attr-defined]
        )
        result = _invoke("gate", "--run-id", run_id, "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert {
            "run_id", "composite_score", "threshold", "decision"
        } <= set(payload), "the documented gate JSON fields must all be present"
        assert payload["run_id"] == run_id
        assert payload["composite_score"] == scorecard["composite_score"]
        assert payload["threshold"] == 50.0
        assert payload["decision"] == "APPROVE"

    def test_gate_json_block_preserves_exit_code(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("gate", "--run-id", run_id, "--threshold", "200", "--json")
        # Exit code 1 is preserved even though we emitted JSON.
        assert result.exit_code == 1
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["decision"] == "BLOCK"
        assert payload["threshold"] == 200.0
        assert payload["run_id"] == run_id

    def test_gate_json_uses_stored_threshold(self) -> None:
        _, run_id = _run_and_extract_id()
        scorecard = json.loads(
            _invoke("scorecard", "--run-id", run_id).output  # type: ignore[attr-defined]
        )
        result = _invoke("gate", "--run-id", run_id, "--json")
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["threshold"] == scorecard["threshold"]
        assert payload["composite_score"] == scorecard["composite_score"]
        expected_code = 0 if scorecard["deploy_decision"] == "APPROVE" else 1
        assert result.exit_code == expected_code
        assert payload["decision"] == scorecard["deploy_decision"]

    def test_gate_json_is_single_json_object(self) -> None:
        run_id, _ = _run_at_threshold("50")
        result = _invoke("gate", "--run-id", run_id, "--json")
        assert result.exit_code == 0, result.output
        # The whole stdout must parse as one JSON object (no human log line).
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert {
            "run_id", "composite_score", "threshold", "decision"
        } <= set(payload)

    def test_gate_json_missing_run_exits_2(self) -> None:
        result = _invoke("gate", "--run-id", "vrun-deadbeef", "--json")
        assert result.exit_code == 2

    def test_gate_default_still_human_readable(self) -> None:
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 0, result.output
        assert result.output.startswith("gate:")  # type: ignore[attr-defined]
        assert "APPROVE" in result.output  # type: ignore[attr-defined]
