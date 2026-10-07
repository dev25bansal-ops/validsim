"""``validsim gate`` must be diagnosable by a chained, machine consumer.

Motivation
----------
Two documented failure *meanings* both surfaced as an undifferentiated
``decision: "BLOCK"``:

1. **The run did not meet the bar.** Composite below threshold, or the engine
   recorded a non-score block reason. This is the expected, correct answer.
2. **The gate could not find out.** Ephemeral store, malformed run id, run
   absent from the store, empty store. This is a *broken check*, and a deploy
   pipeline that treats it as #1 has learned that ``BLOCK`` is noise.

Worse, the usage/lookup errors all raised :class:`typer.Exit` or
:class:`typer.BadParameter` **before any JSON was printed**. A consumer running
``validsim gate --json`` and parsing stdout got an empty string on exit 2 --
indistinguishable from a crash, and forcing it to scrape human-readable stderr
text to tell "the model is bad" from "I could not reach the store".

The contract
------------
Exit codes are unchanged (0/1/2) because the shipped actions depend on them.
What changes is the *payload*: ``--json`` always emits exactly one JSON object
on stdout, for **every** outcome, and the object says which of the two things
happened:

* ``decision``   — ``APPROVE`` / ``BLOCK`` (unchanged, so existing consumers
  keep working)
* ``outcome``    — the diagnostic: ``approved`` | ``blocked`` | ``usage_error``
* ``reason``     — a stable machine token: ``below_threshold``,
  ``engine_blocked``, ``not_found``, ``invalid_run_id``, ``no_runs``,
  ``ephemeral_store``, ``missing_run_id``, ``ambiguous_run_selector``
* ``detail``     — the human sentence, for a log line
* ``message``    — the human sentence, for back-compat with a consumer that
  greps stdout

A single non-zero code cannot carry three meanings, so ``exit_code`` is
reported **in the payload** as well, and the semantic outcome is what a
consumer should branch on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Reuse the CLI harness (CliRunner, cache-isolating autouse fixture, helpers).
from test_cli import (  # noqa: F401  (cache_file is an autouse fixture)
    RUN_ID_RE,
    _invoke,
    _run_and_extract_id,
    _run_at_threshold,
    cache_file,
)
from test_cli import _tamper_stored_verdict  # noqa: F401
# Store fixture builders shared by the store suites.
from conftest import make_scorecard, make_stored_run
from validsim.cli import app
from validsim.store.sqlite import SqliteValidationStore

#: The two *meanings* previously collapsed into one.
_BLOCKED = "blocked"
_USAGE = "usage_error"

#: A block reason that is *not* about the score. The engine appends this when a
#: run did not deliver the requested episodes, i.e. the run proves nothing at
#: all — which is exactly the case a score-only gate would misreport.
_NON_SCORE_BLOCK_REASON = (
    "insufficient evidence: run did not deliver the requested episodes, "
    "or recorded no successful episode at all"
)


def _seed_engine_block(db: Path) -> tuple[str, dict]:
    """Seed a run the **engine** blocked, on a non-score ground.

    Written straight into the durable store rather than produced by ``validsim
    run`` on purpose. The composite is data-dependent, so a real run can only be
    made to engine-BLOCK by luck (an under-delivered run turns out to deliver
    everything, or the adversarial significance test does not fire), which makes
    such a fixture pass or fail depending on the run. Here the numbers are
    pinned: a **high** composite and a **low** stored threshold, so the score
    is not the reason for the block and ``--threshold 0`` cannot introduce one.
    The only thing that can block the gate is the engine's own verdict — which
    is precisely the distinction under test.

    Returns ``(run_id, scorecard_dict)``.
    """
    card = make_scorecard(
        # Must satisfy the gate's own shape check (vrun-<8 hex>), or the
        # command rejects it as a malformed id before it ever reads the record.
        run_id="vrun-e1a2c3d4",
        checkpoint_id="ckpt-engine-block",
        composite_score=99.0,
        threshold=1.0,
        deploy_decision="BLOCK",
        block_reasons=(_NON_SCORE_BLOCK_REASON,),
    )
    store = SqliteValidationStore(db)
    try:
        store.save(make_stored_run(card))
        persisted = store.get(card.run_id)
        assert persisted is not None, "fixture run did not persist"
        assert persisted.scorecard.deploy_decision == "BLOCK", (
            "fixture must be an engine BLOCK"
        )
    finally:
        store.close()
    return card.run_id, card.to_dict()


def _gate_json(*args: str) -> tuple[dict, int]:
    """Run ``gate --json`` and return (parsed payload, exit code)."""
    result = _invoke("gate", *args, "--json")
    assert result.output.strip(), (
        f"gate --json printed nothing to stdout (exit {result.exit_code}); a "
        "machine consumer cannot diagnose an empty payload"
    )
    payload = json.loads(result.output)
    assert isinstance(payload, dict), payload
    return payload, result.exit_code


@pytest.mark.usefixtures("durable_store")
class TestApprovedIsUnambiguous:
    def test_approve_reports_the_approved_outcome(self) -> None:
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        payload, code = _gate_json("--run-id", run_id)
        assert code == 0
        assert payload["decision"] == "APPROVE"
        assert payload["outcome"] == "approved"
        assert payload["reason"] is None
        assert payload["exit_code"] == 0
        assert payload["run_id"] == run_id

    def test_pre_existing_field_names_are_all_retained(self) -> None:
        """A consumer written against the old 4-key payload must not break.

        The diagnostic fields are *additive*: ``{run_id, composite_score,
        threshold, decision}`` is a subset of the payload, never a
        replacement. (A closed-schema assertion here is what a pre-existing
        suite pinned with ``set(payload) == {...}``; it is stated as a subset
        precisely so the payload can grow without breaking anyone.)
        """
        run_id, _ = _run_at_threshold("50")
        payload, _ = _gate_json("--run-id", run_id)
        for field in ("run_id", "composite_score", "threshold", "decision"):
            assert field in payload, f"{field} was removed from the gate payload"


@pytest.mark.usefixtures("durable_store")
class TestBlockedIsDiagnosable:
    """Two *different* blocks must carry two *different* reasons.

    Collapsing these is the whole complaint: a pipeline cannot tell "the model
    is bad" from "we re-derived the verdict and it is bad for a different
    reason" — nor from "we could not check".
    """

    def test_below_threshold_names_the_score_as_the_cause(self) -> None:
        # The engine must have APPROVED here, so the *only* thing that can
        # block is the operator raising the bar above the composite.
        run_id, decision = _run_at_threshold("50")
        assert decision == "APPROVE"
        payload, code = _gate_json("--run-id", run_id, "--threshold", "200")
        assert code == 1
        assert payload["decision"] == "BLOCK"
        assert payload["outcome"] == _BLOCKED
        assert payload["reason"] == "below_threshold"
        assert payload["engine_decision"] == "APPROVE"
        assert payload["exit_code"] == 1
        # The numbers that produced the verdict must be present, not just the word.
        assert payload["composite_score"] < payload["threshold"]
        assert payload["threshold"] == 200.0

    def test_engine_recorded_block_is_distinguished_from_a_score_miss(
        self, durable_store: Path
    ) -> None:
        """The engine's own block reasons are not a composite shortfall.

        Both block, but a pipeline that cannot tell them apart cannot decide
        whether to retune a threshold or fix an under-delivering run.
        """
        run_id, _ = _seed_engine_block(durable_store)
        payload, code = _gate_json("--run-id", run_id, "--threshold", "0")
        assert code == 1
        assert payload["outcome"] == _BLOCKED
        assert payload["reason"] == "engine_blocked"
        assert payload["engine_decision"] == "BLOCK"
        # The score cleared any plausible bar, which is exactly why the
        # reason matters: reporting "below_threshold" here would send an
        # operator to retune a threshold that was never the problem.
        assert payload["composite_score"] > payload["threshold"]

    def test_engine_block_reason_is_surfaced_verbatim(self, durable_store: Path) -> None:
        """A BLOCK with no stated reason is unactionable."""
        run_id, stored = _seed_engine_block(durable_store)
        payload, _ = _gate_json("--run-id", run_id, "--threshold", "0")
        assert payload["reason"] == "engine_blocked"
        for reason in stored["block_reasons"]:
            assert reason in payload["message"], f"dropped engine reason: {reason!r}"

    def test_tampered_verdict_is_reported_as_engine_blocked(
        self, durable_store: Path
    ) -> None:
        """A stored verdict that is not literally APPROVE blocks, and says so.

        The pre-existing suite (``test_cli.py``) pins the exit code; this pins
        the *diagnosis*, so a tampered or corrupted ``deploy_decision`` is
        reported as an engine block rather than silently reclassified.
        """
        run_id, _ = _run_at_threshold("50")
        _tamper_stored_verdict(durable_store, run_id, "APPROVE-ish")
        payload, code = _gate_json("--run-id", run_id)
        assert code == 1
        assert payload["outcome"] == _BLOCKED
        assert payload["reason"] == "engine_blocked"
        assert payload["engine_decision"] == "APPROVE-ish"

    def test_ambiguous_flag_pair_is_reported_as_usage(self) -> None:
        """``--run-id X --latest`` must not silently gate a *different* run.

        Regression: ``_resolve_stored_run_id`` short-circuited to
        ``--latest`` **before** running the mutual-exclusion check, so
        ``--run-id <stale> --latest`` passed the stale id, reported success,
        and gated the newest run instead. A deploy gate that approves a run
        the caller never named is a safety control that can be pointed
        anywhere without saying so.
        """
        run_id, _ = _run_at_threshold("50")
        result = _invoke("gate", "--run-id", run_id, "--latest", "--json")
        assert result.exit_code == 2, result.output
        output = result.output  # type: ignore[attr-defined]
        assert "exactly one" in output or "not both" in output
        # Crucially, no verdict was produced for either run.
        assert "APPROVE" not in output
        assert "BLOCK" not in output


@pytest.mark.usefixtures("durable_store")
class TestUsageErrorsAreNotBlocks:
    """Exit 2 means "no verdict obtained", and the JSON must say so.

    Before this, every one of these printed nothing to stdout, so a consumer
    saw an empty payload on exit 2 and had no way to tell a usage mistake from
    a missing run from an unreachable store.
    """

    def test_unknown_run_is_a_lookup_error_not_a_block(self) -> None:
        payload, code = _gate_json("--run-id", "vrun-deadbeef")
        assert code == 2
        assert payload["outcome"] == _USAGE
        assert payload["reason"] == "not_found"
        assert payload["exit_code"] == 2
        # Crucially: not a BLOCK verdict.
        assert payload["decision"] is None

    def test_malformed_run_id_is_reported_as_such(self) -> None:
        payload, code = _gate_json("--run-id", "abc123")
        assert code == 2
        assert payload["reason"] == "invalid_run_id"
        assert payload["decision"] is None
        # The actionability hint survives into the payload.
        assert "vrun-" in payload["message"]

    def test_empty_store_says_no_runs(self) -> None:
        payload, code = _gate_json("--latest")
        assert code == 2
        assert payload["reason"] == "no_runs"
        assert payload["decision"] is None

    def test_missing_flag_pair_is_reported_as_usage(self) -> None:
        result = _invoke("gate", "--json")
        assert result.exit_code == 2
        # A flag error is raised by Click before the command body runs, so
        # there is no payload -- but the human message must still explain it.
        assert "--run-id" in result.output or "--latest" in result.output

    def test_usage_errors_are_never_reported_as_blocks(self) -> None:
        """The core anti-collision property, stated once and directly."""
        for args, code in (
            (("--run-id", "vrun-deadbeef"), 2),
            (("--run-id", "abc123"), 2),
        ):
            payload, actual = _gate_json(*args)
            assert actual == code
            assert payload["outcome"] != _BLOCKED
            assert payload["decision"] != "BLOCK"

    def test_ephemeral_store_is_reported_as_a_config_error(self, monkeypatch):
        """The documented "no durable store" failure must be diagnosable too.

        It is raised by the store guard *before* any verdict exists, so it is
        a usage/config error, never a BLOCK.
        """
        monkeypatch.delenv("VALIDSIM_STORE", raising=False)
        monkeypatch.delenv("VALIDSIM_SQLITE_PATH", raising=False)
        result = _invoke("gate", "--run-id", "vrun-cafe1234", "--json")
        assert result.exit_code == 2
        payload = json.loads(result.output)  # type: ignore[arg-type]
        assert payload["outcome"] == _USAGE
        assert payload["reason"] == "ephemeral_store"
        assert payload["decision"] is None
        assert "VALIDSIM_STORE" in payload["message"]


@pytest.mark.usefixtures("durable_store")
class TestHumanOutputAndDocs:
    @pytest.mark.usefixtures("durable_store")
    def test_human_output_still_leads_with_gate(self) -> None:
        run_id, _ = _run_at_threshold("50")
        result = _invoke("gate", "--run-id", run_id)
        assert result.exit_code == 0, result.output
        assert result.output.startswith("gate:")  # type: ignore[attr-defined]

    def test_human_block_output_states_the_cause(self) -> None:
        _, run_id = _run_and_extract_id()
        result = _invoke("gate", "--run-id", run_id, "--threshold", "200")
        assert result.exit_code == 1
        output = result.output  # type: ignore[attr-defined]
        assert "BLOCK" in output
        # "below" is the reason, not merely the verdict.
        assert "threshold" in output

    def test_help_documents_the_two_failure_meanings(self) -> None:
        """Precise documentation is the minimum the brief allows: a consumer
        must be able to learn the contract from --help."""
        from typer.testing import CliRunner

        result = CliRunner().invoke(app, ["gate", "--help"])
        assert result.exit_code == 0, result.output
        text = result.output.lower()
        assert "0" in text and "1" in text and "2" in text
        assert "approve" in text
        assert "block" in text
        # The usage-error/bad-verdict distinction must be spelled out.
        assert "usage" in text or "no verdict" in text or "cannot" in text

    def test_run_id_key_is_absent_only_when_unknown(self) -> None:
        """A lookup error knows no run id; a block always does."""
        payload, _ = _gate_json("--run-id", "vrun-deadbeef")
        assert payload.get("run_id") in (None, "vrun-deadbeef")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
