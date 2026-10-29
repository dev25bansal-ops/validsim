"""Build, export, and verify the audit trail.

This module turns the store's validation history into an **auditable
evidence package**: one canonical, hash-chained record per run, exportable
as CSV, JSON, or JSON Lines.

**The record.** Each record captures the facts an auditor needs to answer
"what did this system decide, and on what evidence?":

* identity -- ``run_id``, ``checkpoint_id``, ``task_id``;
* provenance -- ``created_at`` and the ``baseline_run_id`` compared against;
* the verdict -- ``composite_score``, ``deploy_decision``, ``threshold``;
* the component scores -- success, safety, robustness, and whether
  robustness was actually *measured* (so a zero is not read as "terrible"
  when it means "not measured");
* coverage -- episode counts, adversarial coverage, failure taxonomy;
* the chain -- ``previous_hash`` and ``chain_hash``.

**The chain.** Each record's ``chain_hash`` mixes the previous record's
hash with this record's content. Editing record *N* therefore invalidates
every link after it, which is what lets a reader *detect* a gap instead of
merely suspecting one. :func:`verify_audit_chain` re-derives the chain and
reports the first broken link, so a consumer never has to take the log on
faith.

**Ordering is explicit and stable.** Records are emitted in
``created_at`` order (oldest first), with ``run_id`` as the tie-breaker.
The store's own ``history`` is already oldest-first, but a caller may pass
runs in any order, so the export sorts explicitly -- a chain whose order
depends on the caller's list would not be verifiable.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from validsim.audit.records import chain_hash, canonical_json
from validsim.store.memory import StoredRun

#: The schema version of the exported audit record.
AUDIT_SCHEMA_VERSION = "audit-record-v1"

#: Export formats supported by :func:`export_audit_log`.
AuditFormat = Literal["csv", "json", "jsonl"]

#: The column order of the CSV export. Fixed, so a downstream diff or
#: spreadsheet template stays valid as the record grows.
CSV_COLUMNS: tuple[str, ...] = (
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
    "baseline_run_id",
    "composite_score",
    "deploy_decision",
    "threshold",
    "success_rate",
    "safety_score",
    "robustness_score",
    "robustness_measured",
    "randomization_group_count",
    "regression_baseline_available",
    "regression_delta",
    "confidence_interval_low",
    "confidence_interval_high",
    "episode_count",
    "adversarial_episode_count",
    "adversarial_success_rate",
    "failure_taxonomy",
    "previous_hash",
    "chain_hash",
)


@dataclass(frozen=True)
class AuditRecord:
    """One immutable audit entry for a single validation run.

    Attributes:
        run_id: The validation run this record describes.
        checkpoint_id: The checkpoint that was validated.
        task_id: The task that was executed.
        created_at: ISO-8601 UTC timestamp of the run.
        baseline_run_id: The run compared against, if any.
        composite_score: The weighted 0-100 verdict score.
        deploy_decision: ``"APPROVE"`` or ``"BLOCK"``.
        threshold: The composite score required to approve.
        success_rate: Fraction of episodes that succeeded (0-1).
        safety_score: Weighted safety score (0-100).
        robustness_score: Cross-randomization consistency (0-100).
        robustness_measured: Whether robustness carries real information.
        randomization_group_count: Distinct randomization levels present.
        regression_baseline_available: Whether a baseline was compared.
        regression_delta: Success-rate delta vs baseline, if any.
        confidence_interval: The 95% bootstrap CI of the success rate, if any.
        episode_count: Total episodes scored.
        adversarial_episode_count: Episodes drawn from the adversarial pool.
        adversarial_success_rate: Success rate on adversarial episodes.
        failure_taxonomy: Failure-mode counts.
        previous_hash: The preceding record's ``chain_hash``, or ``None``.
        chain_hash: This record's chain link.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    created_at: str
    baseline_run_id: str | None
    composite_score: float
    deploy_decision: str
    threshold: float
    success_rate: float
    safety_score: float
    robustness_score: float
    robustness_measured: bool
    randomization_group_count: int
    regression_baseline_available: bool
    regression_delta: float | None
    confidence_interval: tuple[float, float] | None
    episode_count: int
    adversarial_episode_count: int
    adversarial_success_rate: float | None
    failure_taxonomy: dict[str, int]
    previous_hash: str | None
    chain_hash: str

    def to_dict(self) -> dict[str, Any]:
        """Return the record as a JSON-serializable dict."""
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "baseline_run_id": self.baseline_run_id,
            "composite_score": self.composite_score,
            "deploy_decision": self.deploy_decision,
            "threshold": self.threshold,
            "success_rate": self.success_rate,
            "safety_score": self.safety_score,
            "robustness_score": self.robustness_score,
            "robustness_measured": self.robustness_measured,
            "randomization_group_count": self.randomization_group_count,
            "regression_baseline_available": self.regression_baseline_available,
            "regression_delta": self.regression_delta,
            "confidence_interval": (
                list(self.confidence_interval) if self.confidence_interval else None
            ),
            "episode_count": self.episode_count,
            "adversarial_episode_count": self.adversarial_episode_count,
            "adversarial_success_rate": self.adversarial_success_rate,
            "failure_taxonomy": dict(self.failure_taxonomy),
            "previous_hash": self.previous_hash,
            "chain_hash": self.chain_hash,
        }

    def to_row(self) -> dict[str, Any]:
        """Return the record as a flat row for the CSV export.

        The tuple-valued confidence interval is split into two columns and
        the taxonomy is rendered as a compact JSON string, so every cell is
        a scalar that a spreadsheet or a ``diff`` can read.
        """
        low, high = self.confidence_interval if self.confidence_interval else (None, None)
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "baseline_run_id": self.baseline_run_id,
            "composite_score": self.composite_score,
            "deploy_decision": self.deploy_decision,
            "threshold": self.threshold,
            "success_rate": self.success_rate,
            "safety_score": self.safety_score,
            "robustness_score": self.robustness_score,
            "robustness_measured": self.robustness_measured,
            "randomization_group_count": self.randomization_group_count,
            "regression_baseline_available": self.regression_baseline_available,
            "regression_delta": self.regression_delta,
            "confidence_interval_low": low,
            "confidence_interval_high": high,
            "episode_count": self.episode_count,
            "adversarial_episode_count": self.adversarial_episode_count,
            "adversarial_success_rate": self.adversarial_success_rate,
            "failure_taxonomy": canonical_json(self.failure_taxonomy),
            "previous_hash": self.previous_hash,
            "chain_hash": self.chain_hash,
        }


def _record_payload(run: StoredRun) -> dict[str, Any]:
    """Return the un-hashed content of the audit record for *run*."""
    scorecard = run.scorecard
    ci = scorecard.confidence_interval
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "run_id": run.run_id,
        "checkpoint_id": run.checkpoint_id,
        "task_id": run.task_id,
        "created_at": run.created_at,
        "baseline_run_id": run.baseline_run_id,
        "composite_score": scorecard.composite_score,
        "deploy_decision": str(scorecard.deploy_decision),
        "threshold": scorecard.threshold,
        "success_rate": scorecard.success_rate,
        "safety_score": scorecard.safety_score,
        "robustness_score": scorecard.robustness_score,
        "robustness_measured": scorecard.robustness_measured,
        "randomization_group_count": scorecard.randomization_group_count,
        "regression_baseline_available": scorecard.regression_baseline_available,
        "regression_delta": scorecard.regression_delta,
        "confidence_interval": list(ci) if ci is not None else None,
        "episode_count": scorecard.episode_count,
        "adversarial_episode_count": scorecard.adversarial_episode_count,
        "adversarial_success_rate": scorecard.adversarial_success_rate,
        "failure_taxonomy": dict(scorecard.failure_taxonomy),
    }


def build_audit_records(runs: Iterable[StoredRun]) -> list[AuditRecord]:
    """Build the hash-chained audit records for *runs*.

    The runs are sorted by ``created_at`` (oldest first) with ``run_id`` as
    the tie-breaker, so the chain is a function of the *set* of runs rather
    than of the order the caller happened to supply. The first record's
    ``previous_hash`` is ``None``; every later record chains to its
    predecessor.

    Args:
        runs: The stored runs to audit.

    Returns:
        The audit records, oldest first.

    Raises:
        ValueError: If a run's scorecard contains a value that cannot be
            represented canonically (e.g. a non-finite float).
    """
    ordered = sorted(runs, key=lambda r: (r.created_at, r.run_id))
    records: list[AuditRecord] = []
    previous_hash: str | None = None
    for run in ordered:
        payload = _record_payload(run)
        payload["previous_hash"] = previous_hash
        digest = chain_hash(previous_hash, payload)
        records.append(
            AuditRecord(
                run_id=payload["run_id"],
                checkpoint_id=payload["checkpoint_id"],
                task_id=payload["task_id"],
                created_at=payload["created_at"],
                baseline_run_id=payload["baseline_run_id"],
                composite_score=payload["composite_score"],
                deploy_decision=payload["deploy_decision"],
                threshold=payload["threshold"],
                success_rate=payload["success_rate"],
                safety_score=payload["safety_score"],
                robustness_score=payload["robustness_score"],
                robustness_measured=payload["robustness_measured"],
                randomization_group_count=payload["randomization_group_count"],
                regression_baseline_available=payload["regression_baseline_available"],
                regression_delta=payload["regression_delta"],
                confidence_interval=(
                    tuple(payload["confidence_interval"])
                    if payload["confidence_interval"] is not None
                    else None
                ),
                episode_count=payload["episode_count"],
                adversarial_episode_count=payload["adversarial_episode_count"],
                adversarial_success_rate=payload["adversarial_success_rate"],
                failure_taxonomy=payload["failure_taxonomy"],
                previous_hash=previous_hash,
                chain_hash=digest,
            )
        )
        previous_hash = digest
    return records


def export_audit_log(
    runs: Iterable[StoredRun],
    fmt: AuditFormat = "csv",
) -> str:
    """Render the audit trail for *runs* in the requested format.

    Args:
        runs: The stored runs to audit.
        fmt: ``"csv"`` for a spreadsheet-friendly table, ``"json"`` for a
            single JSON document, or ``"jsonl"`` for one JSON object per
            line (the format most log pipelines ingest).

    Returns:
        The rendered audit log.

    Raises:
        ValueError: If *fmt* is not a supported format, or if a run cannot
            be represented canonically.
    """
    records = build_audit_records(runs)
    if fmt == "csv":
        return _export_csv(records)
    if fmt == "json":
        return canonical_json(
            {
                "schema_version": AUDIT_SCHEMA_VERSION,
                "record_count": len(records),
                "records": [r.to_dict() for r in records],
            }
        )
    if fmt == "jsonl":
        return "".join(canonical_json(r.to_dict()) + "\n" for r in records)
    raise ValueError(f"unsupported audit export format: {fmt!r}")


def _export_csv(records: Sequence[AuditRecord]) -> str:
    """Render *records* as CSV with a fixed column order."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n", extrasaction="ignore"
    )
    writer.writeheader()
    for record in records:
        writer.writerow(record.to_row())
    return buffer.getvalue()


def verify_audit_chain(records: Sequence[AuditRecord]) -> tuple[bool, str | None]:
    """Re-derive the chain of *records* and report whether it holds.

    The chain is checked by recomputing each record's link from its own
    content and the previous record's hash, then comparing against the
    recorded link. A record whose content was edited after the fact
    produces a different link, so the break is *detected* rather than
    assumed impossible.

    Args:
        records: The records to verify, in chain order.

    Returns:
        ``(True, None)`` when every link holds, or ``(False, reason)``
        describing the first problem found. The reason names the offending
        ``run_id`` and the kind of break, so an operator knows where to
        look.
    """
    previous_hash: str | None = None
    for index, record in enumerate(records):
        if record.previous_hash != previous_hash:
            return (
                False,
                f"record {index} ({record.run_id}) has previous_hash "
                f"{record.previous_hash!r}, expected {previous_hash!r}",
            )
        payload = record.to_dict()
        payload.pop("chain_hash", None)
        expected = chain_hash(previous_hash, payload)
        if expected != record.chain_hash:
            return (
                False,
                f"record {index} ({record.run_id}) chain hash mismatch: "
                f"expected {expected}, found {record.chain_hash}",
            )
        previous_hash = record.chain_hash
    return True, None
