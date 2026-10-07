"""HTTP-layer response-shape tests for the adversarial scorecard fields.

Gap this file closes
--------------------
``Scorecard`` grew three fields -- ``adversarial_episode_count``,
``adversarial_success_rate`` and ``block_reasons`` -- and they are now
well covered at the *store* and *engine* layers (``tests/test_store_sqlite.py``,
``tests/test_store_filters.py``, ``tests/test_store_parity.py``,
``tests/test_scorecard.py``, ``tests/conftest.py::make_adversarial_scorecard``).
A grep across every ``tests/test_api*.py`` returned **zero** hits for all three
names, so nothing verified that the fields actually survive the HTTP boundary
on the endpoints a client reads them from. These are exactly the assertions a
schema change would break silently, because the endpoints hand out plain
``dict`` payloads with no response model, so a renamed or dropped key is only
visible to a client at runtime.

Contract asserted here
----------------------
The three fields appear on the two endpoints that return a **full scorecard**:

* ``POST /api/v1/validations`` (201)
* ``GET  /api/v1/validations/{run_id}/scorecard`` (200)

and are **absent** from the compact summary rows, which is the current and
documented behaviour (``StoredRun.summary`` is described as a "compact
summary"; ``tests/test_api.py::test_model_history_chronological`` already
asserts the compact shape). The summary assertions are marked SUSPECTED-WRONG
below: a list view that omits ``block_reasons`` cannot tell a dashboard which
of several runs were blocked *and why*, which is the single most useful
column, and the CLI (``validsim/cli.py``) prints exactly those reasons. Until
someone decides otherwise, these tests pin the current shape so that adding
the fields later is a visible, deliberate diff rather than an accident.

Every test here is a **characterization** test: it asserts what the code does
today, including behaviour believed to be wrong, so that whoever changes the
semantics gets a clear, specific failure to update.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore

#: The three adversarial-segment fields under test.
ADVERSARIAL_FIELDS = frozenset(
    {
        "adversarial_episode_count",
        "adversarial_success_rate",
        "block_reasons",
    }
)

#: The full scorecard key set as it stands today, derived from
#: ``dataclasses.fields(Scorecard)``. Asserting the *whole* set (rather than a
#: subset) is the point: a new field that never reaches the API would be
#: caught here, and a field that silently disappears is caught too.
#:
#: The last three are the measurement-provenance flags
#: (``robustness_measured`` / ``randomization_group_count`` /
#: ``regression_baseline_available``) another agent added in parallel with this
#: file; they are included so this test reflects the real schema rather than
#: failing for an unrelated addition. They are not asserted semantically here.
EXPECTED_SCORECARD_KEYS = {
    "run_id",
    "checkpoint_id",
    "task_id",
    "composite_score",
    "success_rate",
    "safety_score",
    "robustness_score",
    "regression_delta",
    "confidence_interval",
    "deploy_decision",
    "threshold",
    "created_at",
    "episode_count",
    "failure_taxonomy",
    "robustness_measured",
    "randomization_group_count",
    "regression_baseline_available",
    *ADVERSARIAL_FIELDS,
}

#: The compact summary key set, per ``StoredRun.summary()``.
EXPECTED_SUMMARY_KEYS = {
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
    "baseline_run_id",
    "composite_score",
    "deploy_decision",
    "episode_count",
}


def _body(
    checkpoint: str = "ckpt-alpha",
    episodes: int = 60,
    adversarial: int = 40,
    **extra: Any,
) -> dict[str, Any]:
    """A valid ``POST /api/v1/validations`` body.

    Defaults request 40 adversarial episodes against a 60-episode nominal
    block, which clears the engine's ``_ADVERSARIAL_MIN_SAMPLES`` (30) so the
    adversarial floor is actually *evaluated* rather than merely reported.
    """
    payload: dict[str, Any] = {
        "checkpoint_id": checkpoint,
        "task": {
            "task_id": "pick-place",
            "robot": {"name": "franka"},
            "environment": {"name": "kitchen"},
            "episodes": episodes,
            "adversarial_count": adversarial,
        },
    }
    payload.update(extra)
    return payload


@pytest.fixture()
def client() -> TestClient:
    """TestClient over an app with a fresh in-memory store."""
    return TestClient(create_app(ValidationStore()))


def _create(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """POST one validation, assert 201, return the scorecard payload."""
    response = client.post("/api/v1/validations", json=_body(**kwargs))
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestScorecardResponseShape:
    """The full-scorecard endpoints must carry every documented field."""

    def test_create_returns_every_scorecard_field(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: the 201 body is the scorecard, so it must
        # expose the whole documented field set and nothing else.
        card = _create(client)
        assert set(card) == EXPECTED_SCORECARD_KEYS

    def test_get_scorecard_returns_every_scorecard_field(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: same contract as the 201 body.
        run_id = _create(client)["run_id"]
        response = client.get(f"/api/v1/validations/{run_id}/scorecard")
        assert response.status_code == 200
        assert set(response.json()) == EXPECTED_SCORECARD_KEYS

    def test_get_scorecard_is_byte_identical_to_the_create_body(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: both read the same persisted scorecard, so a
        # client that POSTs and later re-fetches must see an identical object.
        created = _create(client)
        fetched = client.get(
            f"/api/v1/validations/{created['run_id']}/scorecard"
        ).json()
        assert fetched == created

    def test_adversarial_fields_are_typed_not_absent(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: a JSON ``null`` for the rate (not a missing
        # key) is what lets a client distinguish "no adversarial episodes were
        # run" from "the field is not implemented".
        card = _create(client)
        assert isinstance(card["adversarial_episode_count"], int)
        assert isinstance(card["block_reasons"], list)
        assert card["adversarial_success_rate"] is None or isinstance(
            card["adversarial_success_rate"], float
        )

    def test_block_reasons_is_empty_list_not_null_when_approved(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: ``Scorecard.block_reasons`` defaults to the
        # empty tuple and ``to_dict`` renders it as ``[]``, never ``None`` --
        # a client iterating the reasons must not need a null check.
        card = _create(client, threshold=0.0)
        if card["deploy_decision"] == "APPROVE":
            assert card["block_reasons"] == []
        else:  # pragma: no cover - mock seed dependent
            assert all(isinstance(r, str) for r in card["block_reasons"])

    def test_adversarial_count_matches_requested_scenarios(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: ``_adversarial_segment`` is positional --
        # everything past ``task.episodes`` is adversarial -- so the reported
        # count must equal the requested ``adversarial_count``.
        card = _create(client, episodes=60, adversarial=40)
        assert card["adversarial_episode_count"] == 40

    def test_zero_adversarial_run_reports_null_rate(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: a nominal-only run has no adversarial segment,
        # so the rate is ``None`` (not 0.0, which would assert a measured 0%
        # success rate for episodes that never ran) and the floor is not applied.
        card = _create(client, episodes=60, adversarial=0)
        assert card["adversarial_episode_count"] == 0
        assert card["adversarial_success_rate"] is None
        assert not any(
            "adversarial" in reason for reason in card["block_reasons"]
        )

    def test_block_reasons_are_plain_strings(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: the reasons are free-form English sentences
        # (the CLI prints them verbatim), so the API must not mangle them.
        card = _create(client, threshold=100.0)
        assert card["deploy_decision"] == "BLOCK"
        assert card["block_reasons"]
        assert all(isinstance(r, str) and r.strip() for r in card["block_reasons"])


class TestSummaryOmitsAdversarialFields:
    """Compact summary rows currently omit the adversarial fields.

    Every test in this class is marked SUSPECTED-WRONG: these assertions pin
    behaviour the team probably wants to change. They pass today, so they are a
    safe characterization baseline, and they will fail loudly the moment the
    fields are added to ``StoredRun.summary`` -- which is the point.
    """

    def test_get_run_summary_omits_them(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: a client fetching a single run cannot see why it was
        # blocked without a second request to /scorecard.
        run_id = _create(client, threshold=100.0)["run_id"]
        summary = client.get(f"/api/v1/validations/{run_id}").json()
        assert set(summary) == EXPECTED_SUMMARY_KEYS
        assert not (ADVERSARIAL_FIELDS & set(summary))

    def test_list_items_omit_them(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: same omission in the paginated list, so the
        # dashboard's main table cannot render a block reason either.
        _create(client, threshold=100.0)
        payload = client.get("/api/v1/validations").json()
        assert payload["items"]
        for item in payload["items"]:
            assert set(item) == EXPECTED_SUMMARY_KEYS
            assert not (ADVERSARIAL_FIELDS & set(item))

    def test_dashboard_history_rows_omit_them(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: the dashboard history is the primary operator view.
        _create(client, threshold=100.0)
        rows = client.get("/api/v1/dashboard/history").json()
        assert rows
        for row in rows:
            assert set(row) == EXPECTED_SUMMARY_KEYS
            assert not (ADVERSARIAL_FIELDS & set(row))

    def test_model_history_rows_omit_them(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: the per-checkpoint history has the same gap.
        _create(client, checkpoint="ckpt-alpha", threshold=100.0)
        rows = client.get("/api/v1/models/ckpt-alpha/history").json()
        assert rows
        for row in rows:
            assert set(row) == EXPECTED_SUMMARY_KEYS
            assert not (ADVERSARIAL_FIELDS & set(row))


class TestRenderersOmitAdversarialFields:
    """The Markdown/HTML scorecard renderers omit the adversarial fields.

    Every test in this class is marked SUSPECTED-WRONG. These are the artifacts
    a human actually reads in CI (``scorecard.md``) and receives by email
    (``scorecard.html``), so a run blocked *because* of the adversarial suite
    renders with no indication of that. ``scorecard_to_markdown`` /
    ``scorecard_to_html`` in ``validsim/engine/export.py`` build their metric
    tables from a hard-coded row list that simply does not mention the three
    fields, which is why they are absent. The verdict itself ("BLOCK") is
    still correct, so this is a reporting gap, not a correctness bug.
    """

    def test_markdown_omits_adversarial_segment(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: no adversarial row in the metrics table.
        run_id = _create(client, threshold=100.0)["run_id"]
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.md").text
        assert "adversarial" not in body.lower()

    def test_markdown_omits_block_reasons(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: a BLOCK verdict with no stated reason is the single
        # most confusing thing this report can do. Asserted precisely: none of
        # the run's actual reason strings appear in the rendered document.
        # (The word "BLOCK" itself legitimately appears in the verdict line.)
        run_id = _create(client, threshold=100.0)["run_id"]
        card = client.get(f"/api/v1/validations/{run_id}/scorecard").json()
        assert card["block_reasons"], "precondition: this run has reasons"
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.md").text
        for reason in card["block_reasons"]:
            assert reason not in body

    def test_html_omits_adversarial_segment(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: same gap in the HTML card's metric table.
        run_id = _create(client, threshold=100.0)["run_id"]
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.html").text
        assert "adversarial" not in body.lower()

    def test_html_omits_block_reasons(self, client: TestClient) -> None:
        # SUSPECTED-WRONG: same gap in the HTML card. Asserted precisely, since
        # the literal string "BLOCK" legitimately appears in the verdict badge.
        run_id = _create(client, threshold=100.0)["run_id"]
        card = client.get(f"/api/v1/validations/{run_id}/scorecard").json()
        assert card["block_reasons"], "precondition: this run has reasons"
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.html").text
        for reason in card["block_reasons"]:
            assert reason not in body

    def test_markdown_still_reports_the_verdict(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: whatever else is missing, the verdict and the
        # composite-vs-threshold comparison must survive, because those are
        # what the existing suite already guarantees.
        run_id = _create(client, threshold=100.0)["run_id"]
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.md").text
        assert "## Verdict: BLOCK" in body
        assert "below the deploy" in body

    def test_html_still_reports_the_verdict(self, client: TestClient) -> None:
        # CORRECT-AS-DOCUMENTED: the HTML card shows the verdict prominently.
        run_id = _create(client, threshold=100.0)["run_id"]
        body = client.get(f"/api/v1/validations/{run_id}/scorecard.html").text
        assert "BLOCK" in body
        assert "threshold" in body


class TestResponseShapeIsStableAcrossEndpoints:
    """Cross-endpoint invariants a client can rely on."""

    def test_every_scorecard_endpoint_agrees_on_the_key_set(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: no endpoint silently adds or drops a field,
        # so a client can parse one and reuse the parser everywhere.
        created = _create(client)
        run_id = created["run_id"]
        fetched = client.get(f"/api/v1/validations/{run_id}/scorecard").json()
        assert set(created) == set(fetched) == EXPECTED_SCORECARD_KEYS

    def test_confidence_interval_is_a_list_not_a_tuple(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: ``to_dict`` converts the CI tuple to a list so
        # the payload is JSON-native; a tuple would break strict JSON decoders.
        card = _create(client)
        assert card["confidence_interval"] is None or isinstance(
            card["confidence_interval"], list
        )

    def test_failures_endpoint_does_not_leak_scorecard_fields(
        self, client: TestClient
    ) -> None:
        # CORRECT-AS-DOCUMENTED: the failures view is a different contract --
        # {run_id, failure_taxonomy, episodes} -- and must not grow scorecard
        # keys just because the scorecard gained some.
        run_id = _create(client)["run_id"]
        payload = client.get(f"/api/v1/validations/{run_id}/failures").json()
        assert set(payload) == {"run_id", "failure_taxonomy", "episodes"}
        assert not (ADVERSARIAL_FIELDS & set(payload))
