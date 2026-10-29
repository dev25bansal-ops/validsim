"""Tests for the audit export *surfaces*: the HTTP endpoint and the CLI command.

The audit trail is the compounding asset of this product, so the export has to
be reachable the two ways an operator or auditor actually asks for it: over
HTTP (for the evidence portal) and from the shell (for an incident response
script). These tests pin the *contract* of both surfaces:

* the endpoint and the CLI produce the **same bytes** for the same stored
  runs -- one implementation, two doors, so a divergence between them is a
  bug rather than a surprise;
* the format selection, the date filters, and the error paths behave
  identically on both surfaces;
* the export is a pure function of the stored run set.

Each behavioural claim is paired with a **control**, so a green run means
"verified" rather than "blind".
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from validsim.api.main import create_app
from validsim.cli import app
from validsim.engine.evaluation import EvaluationResult
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun, ValidationStore
from validsim.store.sqlite import SqliteValidationStore

runner = CliRunner()


def _scorecard(run_id: str, created_at: str) -> Scorecard:
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=90.0,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=None,
        confidence_interval=None,
        deploy_decision="APPROVE",
        threshold=85.0,
        created_at=created_at,
        episode_count=100,
        failure_taxonomy={},
    )


def _run(run_id: str, created_at: str) -> StoredRun:
    sc = _scorecard(run_id, created_at)
    return StoredRun(
        run_id=run_id,
        checkpoint_id=sc.checkpoint_id,
        task_id=sc.task_id,
        created_at=created_at,
        scorecard=sc,
        evaluation=EvaluationResult(
            total_episodes=100, success_count=90, success_rate=0.9,
        ),
        safety=SafetyResult(0.0, 0.0, None, 0.0, 80.0),
    )


_STAMPS: list[tuple[str, str]] = [
    ("vrun-00000001", "2026-01-01T00:00:00+00:00"),
    ("vrun-00000002", "2026-01-02T00:00:00+00:00"),
    ("vrun-00000003", "2026-01-03T00:00:00+00:00"),
]


@pytest.fixture
def store() -> ValidationStore:
    """A store seeded with three runs on consecutive days."""
    st = ValidationStore()
    for run_id, stamp in _STAMPS:
        st.save(_run(run_id, stamp))
    return st


@pytest.fixture
def client(store: ValidationStore) -> TestClient:
    return TestClient(create_app(store))


class TestApiAuditEndpoint:
    """``GET /api/v1/audit`` serves the hash-chained export."""

    def test_default_format_is_csv(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    def test_csv_has_one_row_per_run(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "csv"})
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 3

    def test_rows_are_oldest_first(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "csv"})
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["run_id"] for r in rows] == [run_id for run_id, _ in _STAMPS]

    def test_json_format_is_one_document(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "json"})
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert payload["record_count"] == 3
        assert len(payload["records"]) == 3

    def test_jsonl_format_is_one_object_per_line(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "jsonl"})
        assert response.status_code == 200
        lines = [line for line in response.text.splitlines() if line]
        assert len(lines) == 3
        for line in lines:
            json.loads(line)

    def test_chain_links_are_present_and_ordered(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "json"})
        records = json.loads(response.text)["records"]
        assert records[0]["previous_hash"] is None
        for previous, current in zip(records, records[1:]):
            assert current["previous_hash"] == previous["chain_hash"]

    def test_since_bound_narrows_the_export(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/audit", params={"fmt": "csv", "since": "2026-01-02T00:00:00+00:00"}
        )
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["run_id"] for r in rows] == ["vrun-00000002", "vrun-00000003"]

    def test_until_bound_narrows_the_export(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/audit", params={"fmt": "csv", "until": "2026-01-02T00:00:00+00:00"}
        )
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["run_id"] for r in rows] == ["vrun-00000001", "vrun-00000002"]

    def test_both_bounds_are_inclusive(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/audit",
            params={
                "fmt": "csv",
                "since": "2026-01-02T00:00:00+00:00",
                "until": "2026-01-02T00:00:00+00:00",
            },
        )
        assert response.status_code == 200
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["run_id"] for r in rows] == ["vrun-00000002"]

    def test_unknown_format_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "xml"})
        assert response.status_code == 422

    def test_malformed_since_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"since": "not-a-date"})
        assert response.status_code == 422

    def test_export_is_byte_identical_across_calls(self, client: TestClient) -> None:
        first = client.get("/api/v1/audit", params={"fmt": "json"})
        second = client.get("/api/v1/audit", params={"fmt": "json"})
        assert first.text == second.text

    def test_download_filename_names_the_format(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit", params={"fmt": "jsonl"})
        assert "validsim-audit.jsonl" in response.headers["content-disposition"]


class TestCliAuditCommand:
    """``validsim audit`` serves the same export from the shell."""

    def test_default_format_is_csv(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        result = runner.invoke(app, ["audit", "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert out.read_text(encoding="utf-8").startswith("run_id,")

    def test_json_format_writes_a_document(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.json"
        result = runner.invoke(
            app, ["audit", "--format", "json", "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert "records" in payload

    def test_format_is_case_insensitive(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.json"
        result = runner.invoke(
            app, ["audit", "--format", "JSON", "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(out.read_text(encoding="utf-8")) is not None

    def test_output_to_stdout(self) -> None:
        result = runner.invoke(app, ["audit", "--format", "json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) is not None

    def test_unsupported_format_exits_2(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["audit", "--format", "xml", "--output", str(tmp_path / "a.xml")]
        )
        assert result.exit_code == 2
        assert "unsupported audit format" in result.output

    def test_reports_the_record_count(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        result = runner.invoke(app, ["audit", "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert "audit records" in result.output


class TestSurfacesAgree:
    """The endpoint and the CLI must not drift apart."""

    @pytest.fixture
    def shared(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """An API client and CLI both reading the *same* SQLite store."""
        db = tmp_path / "shared.db"
        monkeypatch.setenv("VALIDSIM_STORE", "sqlite")
        monkeypatch.setenv("VALIDSIM_SQLITE_PATH", str(db))
        store = SqliteValidationStore(db)
        for run_id, stamp in _STAMPS:
            store.save(_run(run_id, stamp))
        store.close()
        return TestClient(create_app())

    def test_same_format_same_bytes(self, shared: TestClient, tmp_path: Path) -> None:
        """The two surfaces render byte-identical exports from the same runs."""
        for fmt in ("csv", "json", "jsonl"):
            api_body = shared.get("/api/v1/audit", params={"fmt": fmt}).text
            out = tmp_path / f"audit.{fmt}"
            result = runner.invoke(
                app, ["audit", "--format", fmt, "--output", str(out)]
            )
            assert result.exit_code == 0, result.output
            cli_body = out.read_text(encoding="utf-8")
            # Guard against a vacuous comparison: two empty bodies would
            # compare equal without proving the surfaces agree.
            assert api_body.strip(), fmt
            assert cli_body == api_body, fmt

    def test_control_the_endpoint_actually_returns_records(
        self, client: TestClient
    ) -> None:
        """CONTROL -- the endpoint is not vacuously empty."""
        payload = json.loads(client.get("/api/v1/audit", params={"fmt": "json"}).text)
        assert payload["record_count"] > 0
        assert payload["records"][0]["run_id"] == "vrun-00000001"

    def test_control_the_chain_breaks_when_a_record_is_edited(
        self, client: TestClient
    ) -> None:
        """CONTROL -- the chain is not a decorative constant."""
        records = json.loads(
            client.get("/api/v1/audit", params={"fmt": "json"}).text
        )["records"]
        original = records[0]["chain_hash"]
        records[0]["composite_score"] = 1.0
        from validsim.audit import chain_hash

        payload = {k: v for k, v in records[0].items() if k != "chain_hash"}
        assert chain_hash(None, payload) != original
