"""Tests for the Typer CLI: run/status/scorecard/gate and CI exit codes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from validsim.cli import app
from validsim.store.sqlite import SqliteValidationStore

runner = CliRunner()
RUN_ID_RE = re.compile(r"vrun-[0-9a-f]{8}")


@pytest.fixture(autouse=True)
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the local scorecard cache at a temp file for every test."""
    path = tmp_path / "scorecards.json"
    monkeypatch.setenv("VALIDSIM_CACHE_FILE", str(path))
    return path


def _tamper_stored_verdict(db: Path, run_id: str, verdict: object) -> None:
    """Rewrite a stored run's recorded decision, as a direct DB edit would.

    Every validation store is **append-only**: ``save`` is
    ``INSERT ... ON CONFLICT (run_id) DO NOTHING``, so re-saving a run id
    silently keeps the first record (that is the fix which stopped a
    crash-and-retry from rewriting a recorded APPROVE into a BLOCK). The
    sanctioned way to change what is on the record for an id is therefore
    delete-then-save, so the tamper has to do that -- otherwise it asserts
    nothing, which is exactly how this helper went stale when the store
    contract changed.
    """
    store = SqliteValidationStore(db)
    try:
        run = store.get(run_id)
        assert run is not None, run_id
        assert store.delete(run_id) is True, "could not free the run id for the tamper"
        store.save(replace(run, scorecard=replace(run.scorecard, deploy_decision=verdict)))
        # Prove the tamper actually landed; a silently-ignored write would make
        # every caller of this helper vacuous.
        assert store.get(run_id).scorecard.deploy_decision == verdict  # type: ignore[union-attr]
    finally:
        store.close()


def _invoke(*args: str) -> object:
    return runner.invoke(app, list(args))


def _run_and_extract_id() -> tuple[object, str]:
    result = _invoke("run", "--episodes", "40", "--adversarial", "8",
                     "--checkpoint", "ckpt-cli")
    assert result.exit_code == 0, result.output
    match = RUN_ID_RE.search(result.output)
    assert match is not None, result.output
    return result, match.group(0)


def _run_at_threshold(threshold: str) -> tuple[str, str]:
    """Run a real validation at ``threshold``; return its run id and verdict.

    The default 40-episode run scores composite 81.4, so the caller picks the
    threshold the *engine* should record rather than overriding it at gate time.
    """
    result = _invoke("run", "--episodes", "40", "--adversarial", "8",
                     "--checkpoint", "ckpt-cli", "--threshold", threshold)
    assert result.exit_code == 0, result.output
    run_id = RUN_ID_RE.search(result.output)
    decision = re.search(r"Decision:\s+(\S+)", result.output)
    assert run_id is not None and decision is not None, result.output
    return run_id.group(0), decision.group(1)


class TestRun:
    def test_run_prints_summary_and_caches(self) -> None:
        result, run_id = _run_and_extract_id()
        output = result.output  # type: ignore[attr-defined]
        assert f"Run ID:        {run_id}" in output
        assert "Decision:" in output
        assert "Composite:" in output
        assert "Success rate:" in output

    def test_cache_written(self, cache_file: Path) -> None:
        _, run_id = _run_and_extract_id()
        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert run_id in data
        assert data[run_id]["checkpoint_id"] == "ckpt-cli"


class TestStatusAndScorecard:
    def test_status(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("status", "--run-id", run_id)
        assert result.exit_code == 0
        assert run_id in result.output  # type: ignore[attr-defined]

    def test_scorecard_json(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("scorecard", "--run-id", run_id)
        assert result.exit_code == 0
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["run_id"] == run_id
        assert payload["episode_count"] == 48  # 40 nominal + 8 adversarial

    def test_missing_run_errors(self) -> None:
        result = _invoke("status", "--run-id", "vrun-deadbeef")
        assert result.exit_code == 2


def _write_cache(path: Path, entries: dict[str, dict[str, object]]) -> None:
    """Seed the local scorecard cache by hand, so a test owns the verdict."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _cached_scorecard(**overrides: object) -> dict[str, object]:
    """A plausible cached scorecard, with the fields `gate` consumes present."""
    entry: dict[str, object] = {
        "run_id": "vrun-cafe1234",
        "checkpoint_id": "ckpt-forged",
        "task_id": "pick-place",
        "composite_score": 96.0,
        "success_rate": 0.96,
        "safety_score": 95.0,
        "robustness_score": 94.0,
        "regression_delta": None,
        "confidence_interval": None,
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": "2026-09-21T00:00:00Z",
        "episode_count": 48,
        "failure_taxonomy": {"collision": 2},
    }
    entry.update(overrides)
    return entry


class TestGate:
    """`gate` must read the durable store, never the forgeable local cache.

    The cache is a JSON file any process with write access can create (item 06);
    the store is the system of record. Every test here states which one it seeded.
    """

    def test_ephemeral_store_refuses_to_gate(
        self, cache_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """memory (the default) is per-process: a gate run cannot see any verdict."""
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
        monkeypatch.delenv("VALIDSIM_SQLITE_PATH", raising=False)
        _write_cache(cache_file, {"vrun-cafe1234": _cached_scorecard()})
        result = _invoke("gate", "--run-id", "vrun-cafe1234")
        assert result.exit_code == 2
        assert "VALIDSIM_STORE" in result.output  # type: ignore[attr-defined]

    def test_forged_cache_cannot_overrule_the_store(
        self, durable_store: Path
    ) -> None:
        """Store says BLOCK, a hand-written cache file says APPROVE: it blocks.

        This is item 06's acceptance test. Before the fix `gate` read only the
        cache, so the forged entry won.
        """
        run_id, decision = _run_at_threshold("85")
        assert decision == "BLOCK"
        _write_cache(
            Path(os.environ["VALIDSIM_CACHE_FILE"]),
            {run_id: _cached_scorecard(run_id=run_id, composite_score=99.0)},
        )
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 1
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_malformed_run_id_is_rejected(self, durable_store: Path) -> None:
        """`abc123` cannot be a run id, so it must never reach the store."""
        result = _invoke("gate", "--run-id", "abc123")
        assert result.exit_code == 2
        assert "vrun-" in result.output  # type: ignore[attr-defined]

    def test_absent_run_exits_two(self, durable_store: Path) -> None:
        result = _invoke("gate", "--run-id", "vrun-deadbeef")
        assert result.exit_code == 2

    @pytest.mark.parametrize("verdict", ["DEFINITELY_APPROVE", "approve ", "APPROVE-ish"])
    def test_only_the_exact_stored_verdict_approves(
        self, durable_store: Path, verdict: str
    ) -> None:
        """A stored verdict that is not literally APPROVE blocks (fail closed).

        Covers a tampered or corrupted `deploy_decision`: the gate must not be
        talked into an approval by a near-miss value. The column is NOT NULL, so
        a missing verdict cannot be written at all.
        """
        run_id, _ = _run_at_threshold("50")
        _tamper_stored_verdict(durable_store, run_id, verdict)
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 1
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_json_reports_the_blocked_verdict(self, durable_store: Path) -> None:
        run_id, decision = _run_at_threshold("85")
        assert decision == "BLOCK"
        result = _invoke("gate", "--run-id", run_id, "--json")
        assert result.exit_code == 1
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["decision"] == "BLOCK"
        assert payload["run_id"] == run_id


class TestGateOnRealRuns:
    """The same contract with no hand-written records at all.

    The default 40-episode run scores composite 81.4 against a stored threshold
    of 85, so the engine blocks it and the gate must agree with the engine rather
    than re-deriving a verdict from the score.
    """

    def test_engine_approved_run_passes_the_gate(self, durable_store: Path) -> None:
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 0
        assert "APPROVE" in result.output  # type: ignore[attr-defined]

    def test_gate_blocks_the_run_the_engine_blocked(self, durable_store: Path) -> None:
        run_id, decision = _run_at_threshold("85")
        assert decision == "BLOCK"
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 1
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_threshold_cannot_rescue_a_blocked_run(self, durable_store: Path) -> None:
        run_id, decision = _run_at_threshold("85")
        assert decision == "BLOCK"
        result = _invoke("gate", "--run-id", run_id, "--threshold", "0")
        assert result.exit_code == 1
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_threshold_still_tightens(self, durable_store: Path) -> None:
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        result = _invoke("gate", "--run-id", run_id, "--threshold", "95")
        assert result.exit_code == 1
        assert "BLOCK" in result.output  # type: ignore[attr-defined]

    def test_latest_resolves_from_the_store(self, durable_store: Path) -> None:
        """`--latest` works with no cache file present: the store is the source."""
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        Path(os.environ["VALIDSIM_CACHE_FILE"]).unlink(missing_ok=True)
        result = _invoke("gate", "--latest")
        assert result.exit_code == 0, result.output
        assert run_id in result.output  # type: ignore[attr-defined]



class TestStorePersistence:
    """`run` must also persist the full StoredRun to the configured store."""

    def test_run_persists_to_sqlite_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "cli-store.db"
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
        result = _invoke("run", "--episodes", "20", "--adversarial", "4",
                         "--checkpoint", "ckpt-store")
        assert result.exit_code == 0, result.output
        match = RUN_ID_RE.search(result.output)
        assert match is not None, result.output

        store = SqliteValidationStore(db)
        try:
            run = store.get(match.group(0))
            assert run is not None
            assert run.checkpoint_id == "ckpt-store"
            assert run.task_id == "pick-place"
            assert run.scorecard.episode_count == 24  # 20 nominal + 4 adversarial
            assert len(run.episodes) == 24            # raw detail persisted too
        finally:
            store.close()

    def test_default_memory_store_leaves_no_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without VALIDSIM_STORE the run only touches the JSON cache."""
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
        monkeypatch.delenv("VALIDSIM_SQLITE_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        result = _invoke("run", "--episodes", "10", "--adversarial", "0",
                         "--checkpoint", "ckpt-mem")
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "validsim.db").exists()


class TestLatest:
    """``--latest`` resolves the newest cached run for status/scorecard/gate.

    Cache entries are written directly (via the autouse ``cache_file``
    fixture, which points VALIDSIM_CACHE_FILE at a tmp_path file) so the
    created_at ordering can be controlled precisely.
    """

    @staticmethod
    def _entry(run_id: str, created_at: str, composite: float = 90.0) -> dict:
        """Build a minimal scorecard dict matching cli.py's cache schema."""
        return {
            "run_id": run_id,
            "checkpoint_id": "ckpt-latest",
            "task_id": "pick-place",
            "composite_score": composite,
            "success_rate": 0.9,
            "safety_score": 95.0,
            "robustness_score": 90.0,
            "regression_delta": None,
            "confidence_interval": [0.85, 0.95],
            "deploy_decision": "APPROVE" if composite >= 85.0 else "BLOCK",
            "threshold": 85.0,
            "created_at": created_at,
            "episode_count": 48,
            "failure_taxonomy": {},
        }

    @staticmethod
    def _seed(cache_file: Path, entries: list[dict]) -> None:
        """Write the given scorecard entries into the cache, keyed by run id."""
        cache_file.write_text(
            json.dumps({e["run_id"]: e for e in entries}, indent=2),
            encoding="utf-8",
        )

    def test_latest_returns_newest_by_created_at(self, cache_file: Path) -> None:
        self._seed(cache_file, [
            self._entry("vrun-aaaaaaaa", "2024-01-01T00:00:00+00:00"),
            self._entry("vrun-bbbbbbbb", "2024-06-01T12:30:00+00:00"),
        ])
        result = _invoke("status", "--latest")
        assert result.exit_code == 0, result.output
        assert "vrun-bbbbbbbb" in result.output  # type: ignore[attr-defined]

    def test_latest_tie_break_prefers_last_inserted(self, cache_file: Path) -> None:
        ts = "2024-03-03T00:00:00+00:00"
        self._seed(cache_file, [
            self._entry("vrun-11111111", ts),
            self._entry("vrun-22222222", ts),
        ])
        result = _invoke("status", "--latest")
        assert result.exit_code == 0, result.output
        assert "vrun-22222222" in result.output  # type: ignore[attr-defined]

    def test_scorecard_latest_matches_newest(self, cache_file: Path) -> None:
        self._seed(cache_file, [
            self._entry("vrun-aaaaaaaa", "2024-01-01T00:00:00+00:00"),
            self._entry("vrun-bbbbbbbb", "2024-06-01T12:30:00+00:00", composite=70.0),
        ])
        result = _invoke("scorecard", "--latest")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["run_id"] == "vrun-bbbbbbbb"

    def test_neither_flag_errors(self) -> None:
        result = _invoke("status")
        assert result.exit_code == 2

    def test_both_flags_errors(self, cache_file: Path) -> None:
        self._seed(cache_file, [
            self._entry("vrun-aaaaaaaa", "2024-01-01T00:00:00+00:00"),
        ])
        result = _invoke("status", "--run-id", "vrun-aaaaaaaa", "--latest")
        assert result.exit_code == 2

    def test_latest_empty_store_errors(self, durable_store: Path) -> None:
        result = _invoke("gate", "--latest")
        assert result.exit_code == 2

    def test_gate_latest_respects_threshold_contract(self, durable_store: Path) -> None:
        # `--latest` picks the newest stored run, and gate honours the 0/1/2
        # contract the actions depend on for whichever run it picks. The approve
        # path through --latest is covered by
        # TestGateOnRealRuns.test_latest_resolves_from_the_store, where exactly
        # one run exists so ordering cannot be ambiguous.
        run_id, decision = _run_at_threshold("85")
        assert decision == "BLOCK"
        block = _invoke("gate", "--latest")
        assert block.exit_code == 1, block.output
        assert run_id in block.output  # type: ignore[attr-defined]
        assert "BLOCK" in block.output  # type: ignore[attr-defined]

        loosened = _invoke("gate", "--latest", "--threshold", "50")
        assert loosened.exit_code == 1, loosened.output
        assert "BLOCK" in loosened.output  # type: ignore[attr-defined]


class TestCompareRefusesSelfComparison:
    """``compare`` must never report a clean bill of health for a run vs itself.

    ``--baseline-latest`` and ``--candidate-latest`` both read the same cache, so
    asking for both resolves to one run. That is not a harmless no-op: every
    delta becomes exactly 0.0, the statistical test is degenerate, and the
    command printed ``Significant regressions: 0`` with p-value 1.000 -- a
    confident all-clear for a comparison that never happened. Silently passing
    such a check is worse than failing loudly, so this must exit 2.
    """

    def test_both_latest_flags_rejected(
        self, cache_file: Path, durable_store: Path
    ) -> None:
        TestLatest._seed(cache_file, [
            TestLatest._entry("vrun-aaaaaaaa", "2024-01-01T00:00:00+00:00"),
        ])
        result = _invoke("compare", "--baseline-latest", "--candidate-latest")
        assert result.exit_code == 2, result.output
        assert "same run" in result.output  # type: ignore[attr-defined]
        # The critical assertion: no fabricated "0 regressions" report.
        assert "Significant regressions" not in result.output  # type: ignore[attr-defined]

    def test_explicit_identical_ids_rejected(
        self, cache_file: Path, durable_store: Path
    ) -> None:
        TestLatest._seed(cache_file, [
            TestLatest._entry("vrun-aaaaaaaa", "2024-01-01T00:00:00+00:00"),
        ])
        result = _invoke(
            "compare", "--baseline", "vrun-aaaaaaaa", "--candidate", "vrun-aaaaaaaa"
        )
        assert result.exit_code == 2, result.output
        assert "same run" in result.output  # type: ignore[attr-defined]

    def test_two_distinct_runs_still_compare(
        self, cache_file: Path, durable_store: Path
    ) -> None:
        """The guard must not break the legitimate two-run comparison."""
        _, first = _run_and_extract_id()
        _, second = _run_and_extract_id()
        result = _invoke("compare", "--baseline", first, "--candidate", second)
        assert result.exit_code == 0, result.output
        assert "Significant regressions" in result.output  # type: ignore[attr-defined]
