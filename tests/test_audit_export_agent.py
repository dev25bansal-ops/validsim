"""Tests for the audit trail export.

The audit trail is the compounding asset of this product: an insurer adopts
it *because* the evidence is trustworthy. A log that can be silently
rewritten is worse than no log, because it looks like evidence. So these
tests pin three things:

1. **Fidelity** -- the export carries the facts an auditor needs, and
   nothing is silently dropped or coerced.
2. **Chain integrity** -- each record is linked to its predecessor, and
   :func:`verify_audit_chain` *detects* an edit rather than assuming
   tampering is impossible.
3. **Determinism** -- the same set of runs always produces byte-identical
   output, because a chain whose order depends on the caller's list is not
   verifiable.

Each safety claim is paired with a **control**, so a green run means
"verified" rather than "blind".
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from validsim.audit import (
    AuditRecord,
    build_audit_records,
    canonical_json,
    chain_hash,
    export_audit_log,
    record_digest,
    record_preimage,
    verify_audit_chain,
)
from validsim.engine.scorecard import Scorecard
from validsim.store.memory import StoredRun


def _scorecard(
    run_id: str = "vrun-00000001",
    *,
    composite: float = 91.5,
    decision: str = "APPROVE",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> Scorecard:
    """Build a scorecard for the audit tests."""
    return Scorecard(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place-cube",
        composite_score=composite,
        success_rate=0.93,
        safety_score=88.0,
        robustness_score=0.0,
        robustness_measured=False,
        randomization_group_count=1,
        regression_baseline_available=True,
        regression_delta=0.04,
        confidence_interval=(0.88, 0.97),
        deploy_decision=decision,
        threshold=85.0,
        created_at=created_at,
        episode_count=120,
        adversarial_episode_count=20,
        adversarial_success_rate=0.55,
        failure_taxonomy={"timeout": 3, "collision": 1},
    )


def _run(
    run_id: str = "vrun-00000001",
    *,
    created_at: str = "2026-01-01T00:00:00+00:00",
    baseline_run_id: str | None = None,
    composite: float = 91.5,
    decision: str = "APPROVE",
) -> StoredRun:
    """Build a stored run for the audit tests."""
    return StoredRun(
        run_id=run_id,
        checkpoint_id="ckpt-1",
        task_id="pick-place-cube",
        created_at=created_at,
        scorecard=_scorecard(
            run_id, composite=composite, decision=decision, created_at=created_at
        ),
        evaluation=None,  # type: ignore[arg-type]
        safety=None,  # type: ignore[arg-type]
        baseline_run_id=baseline_run_id,
    )


# ---------------------------------------------------------------------------
# (1) CANONICAL SERIALIZATION.
# ---------------------------------------------------------------------------
class TestCanonicalJson:
    """The preimage must be byte-stable or the chain is meaningless."""

    def test_key_order_does_not_matter(self) -> None:
        """CONTROL -- sorted keys make dict order irrelevant."""
        a = {"b": 1, "a": 2, "c": 3}
        b = {"c": 3, "a": 2, "b": 1}
        assert canonical_json(a) == canonical_json(b)

    def test_control_different_content_hashes_differently(self) -> None:
        """CONTROL -- the hash is not a constant."""
        assert record_digest({"a": 1}) != record_digest({"a": 2})

    def test_non_finite_float_is_rejected(self) -> None:
        """``NaN`` has no faithful JSON form, so it must be an error."""
        with pytest.raises(ValueError, match="non-finite"):
            canonical_json({"score": float("nan")})
        with pytest.raises(ValueError, match="non-finite"):
            canonical_json({"score": float("inf")})

    def test_unsupported_type_is_rejected(self) -> None:
        """A value with no JSON form must fail loudly, not be coerced."""
        with pytest.raises(ValueError, match="unsupported"):
            canonical_json({"value": object()})

    def test_preimage_excludes_the_chain_hash(self) -> None:
        """A record's own digest must not be part of its preimage."""
        record = {"a": 1, "chain_hash": "deadbeef"}
        assert "deadbeef" not in record_preimage(record)

    def test_preimage_pins_the_schema_version(self) -> None:
        """The schema version is inside the preimage, not beside it."""
        assert "_schema" in record_preimage({"a": 1})

    def test_preimage_is_stable_across_calls(self) -> None:
        """The same record must always produce the same preimage."""
        record = {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}
        assert record_preimage(record) == record_preimage(record)


# ---------------------------------------------------------------------------
# (2) THE CHAIN.
# ---------------------------------------------------------------------------
class TestChain:
    """Records must link, and a break must be detectable."""

    def test_first_record_has_no_previous_hash(self) -> None:
        """The head of a chain has nothing before it."""
        records = build_audit_records([_run()])
        assert len(records) == 1
        assert records[0].previous_hash is None

    def test_each_record_links_to_its_predecessor(self) -> None:
        """Record N's ``previous_hash`` is record N-1's ``chain_hash``."""
        runs = [
            _run("vrun-00000001", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-00000002", created_at="2026-01-02T00:00:00+00:00"),
            _run("vrun-00000003", created_at="2026-01-03T00:00:00+00:00"),
        ]
        records = build_audit_records(runs)
        assert records[0].previous_hash is None
        assert records[1].previous_hash == records[0].chain_hash
        assert records[2].previous_hash == records[1].chain_hash

    def test_chain_verifies_on_untouched_records(self) -> None:
        """A freshly built chain must verify."""
        runs = [
            _run("vrun-00000001", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-00000002", created_at="2026-01-02T00:00:00+00:00"),
        ]
        ok, reason = verify_audit_chain(build_audit_records(runs))
        assert ok is True
        assert reason is None

    def test_edited_content_is_detected(self) -> None:
        """CONTROL -- tampering with a record must break the chain."""
        runs = [
            _run("vrun-00000001", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-00000002", created_at="2026-01-02T00:00:00+00:00"),
        ]
        records = build_audit_records(runs)
        # Simulate an edit after the fact: the content changes, the
        # recorded hash does not.
        tampered = records[0]
        object.__setattr__(tampered, "composite_score", 99.9)
        ok, reason = verify_audit_chain(records)
        assert ok is False
        assert reason is not None and "vrun-00000001" in reason

    def test_removed_record_is_detected(self) -> None:
        """CONTROL -- dropping a record must break the chain."""
        runs = [
            _run("vrun-00000001", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-00000002", created_at="2026-01-02T00:00:00+00:00"),
            _run("vrun-00000003", created_at="2026-01-03T00:00:00+00:00"),
        ]
        records = build_audit_records(runs)
        ok, reason = verify_audit_chain([records[0], records[2]])
        assert ok is False
        assert reason is not None

    def test_empty_chain_verifies(self) -> None:
        """An empty log is trivially intact, not an error."""
        ok, reason = verify_audit_chain([])
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# (3) ORDERING AND DETERMINISM.
# ---------------------------------------------------------------------------
class TestOrdering:
    """The chain must be a function of the run set, not of the caller's list."""

    def test_records_are_oldest_first(self) -> None:
        """Chronological order is the audit order."""
        runs = [
            _run("vrun-c", created_at="2026-01-03T00:00:00+00:00"),
            _run("vrun-a", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-b", created_at="2026-01-02T00:00:00+00:00"),
        ]
        records = build_audit_records(runs)
        assert [r.run_id for r in records] == ["vrun-a", "vrun-b", "vrun-c"]

    def test_caller_order_does_not_change_the_output(self) -> None:
        """The same runs in a different order must export identically."""
        a = _run("vrun-a", created_at="2026-01-01T00:00:00+00:00")
        b = _run("vrun-b", created_at="2026-01-02T00:00:00+00:00")
        c = _run("vrun-c", created_at="2026-01-03T00:00:00+00:00")
        assert export_audit_log([a, b, c], "json") == export_audit_log(
            [c, a, b], "json"
        )

    def test_export_is_byte_identical_across_calls(self) -> None:
        """Repeated exports must be byte-identical, so a diff shows real change."""
        runs = [_run("vrun-a"), _run("vrun-b", created_at="2026-01-02T00:00:00+00:00")]
        first = export_audit_log(runs, "jsonl")
        second = export_audit_log(runs, "jsonl")
        assert first == second


# ---------------------------------------------------------------------------
# (4) THE EXPORT FORMATS.
# ---------------------------------------------------------------------------
class TestExportFormats:
    """Every format must carry the audit facts and nothing lossy."""

    def test_csv_has_a_stable_header(self) -> None:
        """The CSV header is fixed, so downstream templates keep working."""
        out = export_audit_log([_run()], "csv")
        header = out.splitlines()[0]
        assert header.startswith("run_id,checkpoint_id,task_id,created_at")

    def test_csv_has_one_row_per_run(self) -> None:
        """Row count must match run count."""
        runs = [
            _run("vrun-a", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-b", created_at="2026-01-02T00:00:00+00:00"),
        ]
        rows = list(csv.DictReader(io.StringIO(export_audit_log(runs, "csv"))))
        assert len(rows) == 2
        assert [r["run_id"] for r in rows] == ["vrun-a", "vrun-b"]

    def test_csv_splits_the_confidence_interval(self) -> None:
        """The CI becomes two scalar columns, not a JSON blob."""
        rows = list(csv.DictReader(io.StringIO(export_audit_log([_run()], "csv"))))
        assert rows[0]["confidence_interval_low"] == "0.88"
        assert rows[0]["confidence_interval_high"] == "0.97"

    def test_json_is_a_single_valid_document(self) -> None:
        """The JSON export must parse and declare its record count."""
        payload = json.loads(export_audit_log([_run()], "json"))
        assert payload["record_count"] == 1
        assert len(payload["records"]) == 1

    def test_jsonl_is_one_object_per_line(self) -> None:
        """The JSONL export must have one parseable object per line."""
        runs = [
            _run("vrun-a", created_at="2026-01-01T00:00:00+00:00"),
            _run("vrun-b", created_at="2026-01-02T00:00:00+00:00"),
        ]
        lines = export_audit_log(runs, "jsonl").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["run_id"] == "vrun-a"

    def test_jsonl_ends_with_a_newline(self) -> None:
        """A trailing newline keeps line-oriented consumers happy."""
        assert export_audit_log([_run()], "jsonl").endswith("\n")

    def test_unsupported_format_is_rejected(self) -> None:
        """An unknown format must fail loudly."""
        with pytest.raises(ValueError, match="unsupported audit export format"):
            export_audit_log([_run()], "xml")  # type: ignore[arg-type]

    def test_empty_run_set_exports_cleanly(self) -> None:
        """An empty log is valid, not an error."""
        assert export_audit_log([], "csv").strip() != ""
        assert json.loads(export_audit_log([], "json"))["record_count"] == 0
        assert export_audit_log([], "jsonl") == ""


# ---------------------------------------------------------------------------
# (5) FIDELITY -- the facts an auditor needs.
# ---------------------------------------------------------------------------
class TestFidelity:
    """The export must not lose or distort the decision evidence."""

    def test_verdict_is_carried(self) -> None:
        """Score, decision, and threshold must survive the export."""
        record = build_audit_records([_run(composite=91.5, decision="APPROVE")])[0]
        assert record.composite_score == 91.5
        assert record.deploy_decision == "APPROVE"
        assert record.threshold == 85.0

    def test_unmeasured_robustness_is_flagged(self) -> None:
        """A zero robustness score must not read as "measured and terrible"."""
        record = build_audit_records([_run()])[0]
        assert record.robustness_score == 0.0
        assert record.robustness_measured is False
        assert record.randomization_group_count == 1

    def test_baseline_provenance_is_carried(self) -> None:
        """Which run was compared against is part of the evidence."""
        record = build_audit_records([_run(baseline_run_id="vrun-base")])[0]
        assert record.baseline_run_id == "vrun-base"
        assert record.regression_delta == 0.04
        assert record.regression_baseline_available is True

    def test_failure_taxonomy_is_carried(self) -> None:
        """Failure-mode counts are evidence, not decoration."""
        record = build_audit_records([_run()])[0]
        assert record.failure_taxonomy == {"timeout": 3, "collision": 1}

    def test_adversarial_coverage_is_carried(self) -> None:
        """Adversarial coverage is part of what was actually tested."""
        record = build_audit_records([_run()])[0]
        assert record.adversarial_episode_count == 20
        assert record.adversarial_success_rate == 0.55

    def test_missing_optional_values_stay_none(self) -> None:
        """Absent evidence must stay absent, not become zero."""
        run = _run()
        object.__setattr__(
            run.scorecard, "confidence_interval", None
        )
        object.__setattr__(run.scorecard, "regression_delta", None)
        record = build_audit_records([run])[0]
        assert record.confidence_interval is None
        assert record.regression_delta is None


# ---------------------------------------------------------------------------
# (6) CONTROLS.
# ---------------------------------------------------------------------------
class TestControls:
    """The checks above must be able to fail."""

    def test_control_the_verifier_accepts_a_known_good_chain(self) -> None:
        """CONTROL -- the verifier is not vacuously failing."""
        records = build_audit_records([_run()])
        assert verify_audit_chain(records) == (True, None)

    def test_control_chain_hash_depends_on_the_previous_link(self) -> None:
        """CONTROL -- a link is not just a content hash."""
        payload = {"a": 1}
        assert chain_hash(None, payload) != chain_hash("0" * 64, payload)

    def test_control_records_are_immutable(self) -> None:
        """CONTROL -- an audit record cannot be edited in place."""
        record = build_audit_records([_run()])[0]
        with pytest.raises(Exception):
            record.composite_score = 1.0  # type: ignore[misc]

    def test_control_audit_record_is_exported_as_a_facade(self) -> None:
        """CONTROL -- the public facade advertises what it actually has."""
        import validsim.audit as audit

        for name in audit.__all__:
            assert hasattr(audit, name), name
        assert audit.__all__ == sorted(audit.__all__)
        # The facade must expose the *same* record class the builder returns,
        # so a caller can type-check an exported record.
        assert isinstance(build_audit_records([_run()])[0], AuditRecord)
