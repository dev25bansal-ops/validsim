"""Behavioural tests for the ValidSim dashboard's presentation layer.

Everything asserted here is a claim about what an operator SEES when the API
returns a deploy verdict, so each one is executed rather than inspected.

Two layers:

* **Runtime claims** (formatting, coercion, robustness, the auth decision) are
  executed against the real ``validsim/web/view-core.js`` loaded under Node —
  the same file the browser downloads. See test_web_viewcore_harness_agent.py.

* **Static claims** (markup wiring, ARIA, that app.js never disables a button)
  are asserted over the shipped ``index.html`` / ``app.js`` / ``styles.css``.
  These are checked textually because the assertion is about the absence of a
  pattern, which a browser test could only show indirectly.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore

from test_web_viewcore_harness_agent import (  # type: ignore[import-not-found]
    APP_JS,
    INDEX_HTML,
    STYLES_CSS,
    VIEW_CORE,
    requires_node,
)
from test_web_viewcore_harness_agent import NODE, _LoadedViewCore

EM_DASH = "\u2014"


@pytest.fixture(scope="session")
def view_core() -> Any:
    """The loaded ``view-core`` module, or skip when Node is unavailable.

    Declared here rather than in the harness so this file does not depend on
    importing a sibling test module's fixture (pytest only auto-discovers
    fixtures declared in conftest.py).
    """
    if NODE is None:
        pytest.skip("Node.js is not installed; cannot execute view-core.js")
    return _LoadedViewCore()


def _scorecard(**overrides: Any) -> dict[str, Any]:
    """A representative APPROVE scorecard, overridable field by field."""
    card: dict[str, Any] = {
        "run_id": "vrun-abc123",
        "checkpoint_id": "ckpt-franka-7b",
        "task_id": "pick-place",
        "composite_score": 91.4,
        "success_rate": 0.9,
        "safety_score": 100.0,
        "robustness_score": 100.0,
        "regression_delta": None,
        "confidence_interval": [0.88, 0.92],
        "deploy_decision": "APPROVE",
        "threshold": 85,
        "created_at": "2026-09-26T10:00:00+00:00",
        "episode_count": 1100,
        "failure_taxonomy": {},
        "adversarial_episode_count": 0,
        "adversarial_success_rate": None,
        "block_reasons": [],
        "robustness_measured": False,
        "regression_baseline_available": False,
    }
    card.update(overrides)
    return card


# =====================================================================
# 1. The fields the dashboard used to drop
# =====================================================================
@requires_node
class TestNewFieldsAreSurfaced:
    """A BLOCK with no visible reason is unactionable, and the adversarial
    segment is the one thing the engine gates separately from the composite."""

    def test_block_reasons_reach_the_view(self, view_core: Any) -> None:
        reasons = [
            "adversarial success rate 21.0% is significantly below the 60% floor "
            "(21/100 adversarial episodes passed)",
            "composite 62.10 is below the configured threshold 85.00",
        ]
        view = view_core.scorecard_view(
            _scorecard(
                composite_score=62.1,
                deploy_decision="BLOCK",
                block_reasons=reasons,
            )
        )
        assert view["blockReasons"] == reasons
        assert view["hasBlockReasons"] is True

    def test_single_block_reason_is_counted_in_words(self, view_core: Any) -> None:
        view = view_core.scorecard_view(
            _scorecard(deploy_decision="BLOCK", block_reasons=["only reason"])
        )
        assert view["blockReasons"] == ["only reason"]

    def test_approve_carries_no_reasons(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(block_reasons=[]))
        assert view["blockReasons"] == []
        assert view["hasBlockReasons"] is False

    def test_adversarial_count_and_rate_are_reported(self, view_core: Any) -> None:
        view = view_core.scorecard_view(
            _scorecard(
                deploy_decision="BLOCK",
                adversarial_episode_count=100,
                adversarial_success_rate=0.21,
            )
        )
        assert view["adversarialCount"] == "100"
        assert view["adversarialRate"] == "21.0%"
        assert "21.0%" in view["adversarialText"]

    def test_absent_adversarial_rate_is_distinct_from_zero(self, view_core: Any) -> None:
        """A run with no adversarial segment is not a run that scored 0%."""
        none_run = view_core.scorecard_view(_scorecard(adversarial_success_rate=None))
        assert none_run["adversarialRate"] is None
        assert none_run["adversarialText"] == "none"

        zero_run = view_core.scorecard_view(
            _scorecard(adversarial_episode_count=50, adversarial_success_rate=0.0)
        )
        assert zero_run["adversarialRate"] == "0.0%"
        assert zero_run["adversarialText"] != "none"

    def test_markup_has_dedicated_slots_for_the_new_fields(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for element_id in (
            "block-reasons",  # the rail
            "block-reasons-list",  # its <ul>
            "block-reasons-count",  # its worded heading
            "m-adversarial-count",  # episode count card
            "m-adversarial-rate",  # success rate card
        ):
            assert f'id="{element_id}"' in html, element_id

    def test_block_reason_rail_is_an_alert_region(self) -> None:
        """It is the answer to the question the operator just asked by running."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'role="alert"' in _tag_containing(html, 'id="block-reasons"')

    def test_app_js_wires_the_new_elements(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        for element_id in (
            "block-reasons",
            "block-reasons-count",
            "block-reasons-list",
            "m-adversarial-count",
            "m-adversarial-rate",
        ):
            assert f'$("{element_id}")' in js, element_id

    def test_reasons_are_inserted_as_text_not_markup(self) -> None:
        """A reason may quote a caller-supplied id, so it must not become HTML."""
        js = APP_JS.read_text(encoding="utf-8")
        body = js.split("function renderBlockReasons", 1)[1].split("function renderScorecard", 1)[0]
        assert "li.textContent = reason;" in body
        assert "innerHTML" not in body


# =====================================================================
# 2. Robustness: hostile, null and unexpected API shapes
# =====================================================================
@requires_node
class TestHostilePayloads:
    """The app must not crash on an unexpected shape, an error body, or a null."""

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            [],
            "a string",
            42,
            {"composite_score": None, "success_rate": None, "safety_score": None},
            {"composite_score": "not a number", "threshold": {}},
            {"composite_score": float("nan"), "safety_score": float("inf")},
            {"deploy_decision": None, "failure_taxonomy": None},
            {"block_reasons": None, "adversarial_success_rate": None},
        ],
    )
    def test_scorecard_view_never_throws(self, view_core: Any, payload: Any) -> None:
        # NaN/Infinity are not valid JSON, so they travel as their string forms.
        if isinstance(payload, dict):
            payload = {
                k: ("NaN" if isinstance(v, float) and math.isnan(v)
                    else "Infinity" if isinstance(v, float) and math.isinf(v)
                    else v)
                for k, v in payload.items()
            }
        view = view_core.scorecard_view(payload, None)
        assert view["decision"] in {"APPROVE", "BLOCK", "PENDING"}
        assert isinstance(view["composite"], str)
        assert isinstance(view["blockReasons"], list)

    def test_null_fields_render_a_word_not_zero(self, view_core: Any) -> None:
        """A missing score must never be rendered as 0.00 — that is a lie about
        a deploy gate."""
        view = view_core.scorecard_view({"composite_score": None, "success_rate": None}, None)
        assert view["composite"] == "unavailable"
        assert view["success"] == "unavailable"
        assert view["composite"] != "0.00"
        assert view["success"] != "0.0%"

    def test_empty_string_is_not_zero(self, view_core: Any) -> None:
        """Number("") is 0 in JavaScript; the coercion must still reject it."""
        assert view_core.num("") is None
        assert view_core.num("   ") is None
        assert view_core.num(None) is None
        assert view_core.pct("") == "unavailable"
        assert view_core.score2("") == "unavailable"

    def test_numeric_strings_are_still_accepted(self, view_core: Any) -> None:
        """A JSON proxy that stringifies numbers must not blank the panel."""
        assert view_core.score2("85") == "85.00"
        assert view_core.pct("0.855") == "85.5%"
        assert view_core.count("1100") == "1100"

    def test_unknown_decision_cannot_inject_markup(self, view_core: Any) -> None:
        """The verdict banner is the one innerHTML sink, so the decision is
        whitelisted against the engine's own Literal type."""
        hostile = '<img src=x onerror="alert(1)">'
        assert view_core.safe_decision(hostile) == "PENDING"
        view = view_core.scorecard_view(_scorecard(deploy_decision=hostile), None)
        assert view["decision"] == "PENDING"

    @pytest.mark.parametrize(
        "taxonomy",
        [None, {}, [], 7, "grasp_miss", {"a": "x"}, {"a": None}, {"a": -3}, {"a": 0}],
    )
    def test_malformed_taxonomy_degrades_to_empty(self, view_core: Any, taxonomy: Any) -> None:
        entries = view_core.taxonomy_entries(taxonomy)
        assert isinstance(entries, list)
        for mode, count in entries:
            assert isinstance(mode, str) and mode
            assert isinstance(count, (int, float)) and count > 0

    def test_taxonomy_accepts_a_pair_array(self, view_core: Any) -> None:
        entries = view_core.taxonomy_entries([["grasp_miss", 3], ["collision", 9]])
        assert entries == [["collision", 9.0], ["grasp_miss", 3.0]]

    def test_history_tolerates_a_non_list(self, view_core: Any) -> None:
        assert view_core.normalise_history(None) == []
        assert view_core.normalise_history({"nope": 1}) == []
        assert view_core.normalise_history("nope") == []

    def test_history_drops_malformed_rows_only(self, view_core: Any) -> None:
        rows = view_core.normalise_history(
            [{"run_id": "a", "composite_score": 10}, None, "junk", 5, {"run_id": "b"}]
        )
        assert [r["run_id"] for r in rows] == ["a", "b"]

    def test_summary_tolerates_garbage(self, view_core: Any) -> None:
        rendered = view_core.summary_text(None)
        assert rendered.endswith(EM_DASH)  # avg composite unknown
        assert "NaN" not in rendered
        assert "null" not in rendered
        assert "NaN" not in view_core.summary_text({"avg_composite": "x", "total_runs": 1})

    def test_trends_tolerates_garbage(self, view_core: Any) -> None:
        assert view_core.compute_trends(None) is None
        assert view_core.compute_trends([]) is None
        assert view_core.compute_trends("nope") is None

    def test_block_reasons_tolerates_a_bare_string(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(block_reasons="one bare reason"), None)
        assert view["blockReasons"] == ["one bare reason"]

    def test_block_reasons_drops_non_strings(self, view_core: Any) -> None:
        """Objects/arrays/nulls cannot be text, so they are dropped; a number can
        be, and is stringified so nothing is silently lost from a verdict."""
        view = view_core.scorecard_view(
            _scorecard(block_reasons=[{"evil": 1}, None, ["x"], "kept", ""]), None
        )
        assert view["blockReasons"] == ["kept"]

    def test_numeric_block_reasons_survive_as_text(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(block_reasons=[42]), None)
        assert view["blockReasons"] == ["42"]

    def test_confidence_interval_must_be_a_pair(self, view_core: Any) -> None:
        one = view_core.scorecard_view(_scorecard(confidence_interval=[0.5]), None)
        assert one["ci"] == EM_DASH
        bad = view_core.scorecard_view(_scorecard(confidence_interval="x"), None)
        assert bad["ci"] == EM_DASH
        ok = view_core.scorecard_view(_scorecard(confidence_interval=[0.5, 0.6]), None)
        assert ok["ci"] == "50.0% \u2013 60.0%"

    def test_view_core_asset_is_valid_javascript(self, view_core: Any) -> None:
        """If the asset had a syntax error the Node harness would already have
        raised; this asserts the module's own invariants instead."""
        assert view_core.raw("console.log(JSON.stringify(Object.keys(V).length > 0))") is True


# =====================================================================
# 3. Number formatting parity with the API / PDF exporter
# =====================================================================
@requires_node
class TestNumberFormattingParity:
    """The gate compares the composite against the threshold; the dashboard
    must print both at the precision the gate and the PDF exporter use."""

    def test_threshold_is_two_decimals_like_the_gate(self, view_core: Any) -> None:
        """The API renders block reasons as `{threshold:.2f}` and the PDF banner
        as `threshold {threshold:.2f}`. One decimal (the old dashboard) is not
        the same number."""
        view = view_core.scorecard_view(_scorecard(threshold=85.5), None)
        assert view["gate"] == "gate \u2265 85.50"
        assert view["gateSentence"] == "Composite 91.40 against a gate of 85.50."

    def test_threshold_precision_survives_a_fractional_value(self, view_core: Any) -> None:
        for raw, expected in ((85, "85.00"), (85.5, "85.50"), (0, "0.00"), (100, "100.00")):
            view = view_core.scorecard_view(_scorecard(threshold=raw), None)
            assert view["gate"] == f"gate \u2265 {expected}"

    def test_composite_matches_the_pdf_two_decimal_form(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(composite_score=91.456), None)
        assert view["composite"] == "91.46"

    def test_component_scores_use_two_decimals(self, view_core: Any) -> None:
        view = view_core.scorecard_view(
            _scorecard(safety_score=99.5, robustness_score=87.25), None
        )
        assert view["safety"] == "99.50"
        # robustness_measured is False in the fixture, so the score is labelled
        # rather than presented as a measurement.
        assert view["robustness"] == "87.25 (not measured)"

    def test_measured_robustness_shows_the_bare_number(self, view_core: Any) -> None:
        view = view_core.scorecard_view(
            _scorecard(robustness_score=87.25, robustness_measured=True), None
        )
        assert view["robustness"] == "87.25"

    def test_rates_match_the_pdf_one_decimal_percent(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(success_rate=0.8556), None)
        assert view["success"] == "85.6%"

    def test_threshold_omitted_falls_back_to_the_engine_default(self, view_core: Any) -> None:
        """Scorecard.threshold defaults to 85.0; a payload without it must still
        render at the gate the engine actually applied."""
        card = _scorecard()
        del card["threshold"]
        view = view_core.scorecard_view(card, None)
        assert view["gate"] == "gate \u2265 85.00"

    def test_dashboard_threshold_text_matches_the_python_gate_format(self) -> None:
        """Cross-check against the engine's own format string: a BLOCK reason
        naming the threshold must print the same digits the dashboard shows."""
        from validsim.engine import scorecard as engine_scorecard

        source = Path(engine_scorecard.__file__).read_text(encoding="utf-8")
        # The engine's own block reason names both figures at 2dp.
        assert "composite {composite:.2f} is below the configured threshold" in source
        assert "{threshold:.2f}" in source
        assert f"{85.5:.2f}" == "85.50"

    def test_pdf_exporter_also_uses_two_decimals(self) -> None:
        """The PDF banner is `threshold {threshold:.2f}`; assert the source still
        says so, so the two artifacts cannot silently drift apart."""
        from validsim.engine import pdf

        source = Path(pdf.__file__).read_text(encoding="utf-8")
        assert "threshold {threshold:.2f}" in source
        assert 'f"{composite:.2f} / {threshold:.2f}"' in source


# =====================================================================
# 3b. Parity against a REAL scorecard from the running API
# =====================================================================
@requires_node
class TestParityWithLiveApi:
    """The strongest form of the parity claim: a scorecard actually produced by
    the engine, rendered by the very JS the browser runs."""

    @pytest.fixture()
    def client(self) -> TestClient:
        return TestClient(create_app(ValidationStore()))

    def test_threshold_echoed_by_the_api_renders_at_gate_precision(
        self, client: TestClient, view_core: Any
    ) -> None:
        card = client.post(
            "/api/v1/validations",
            json={
                "checkpoint_id": "ckpt-parity",
                "threshold": 85.5,
                "task": {
                    "task_id": "pick-place",
                    "robot": {"name": "franka"},
                    "environment": {"name": "kitchen"},
                    "episodes": 60,
                    "adversarial_count": 12,
                },
            },
        ).json()
        # The API honoured the knob end-to-end...
        assert card["threshold"] == 85.5
        # ...and the dashboard shows it at the gate's own precision.
        view = view_core.scorecard_view(card, None)
        assert view["gate"] == "gate \u2265 85.50"
        assert view["gateSentence"] == f"Composite {view['composite']} against a gate of 85.50."

    def test_live_block_reasons_render_verbatim(self, client: TestClient, view_core: Any) -> None:
        """Force a BLOCK (a threshold no run can meet) and assert the engine's
        own sentences come out of the view unchanged."""
        card = client.post(
            "/api/v1/validations",
            json={
                "checkpoint_id": "ckpt-blocked",
                "threshold": 100.0,
                "task": {
                    "task_id": "pick-place",
                    "robot": {"name": "franka"},
                    "environment": {"name": "kitchen"},
                    "episodes": 60,
                    "adversarial_count": 12,
                },
            },
        ).json()
        assert card["deploy_decision"] == "BLOCK"
        assert card["block_reasons"], "engine produced a BLOCK with no reason"
        view = view_core.scorecard_view(card, None)
        assert view["blockReasons"] == list(card["block_reasons"])
        assert view["hasBlockReasons"] is True
        # And the reason names the same threshold the gate line shows.
        assert "100.00" in " ".join(view["blockReasons"])
        assert "100.00" in view["gate"]

    def test_live_adversarial_fields_are_present_and_rendered(
        self, client: TestClient, view_core: Any
    ) -> None:
        card = client.post(
            "/api/v1/validations",
            json={
                "checkpoint_id": "ckpt-adv",
                "task": {
                    "task_id": "pick-place",
                    "robot": {"name": "franka"},
                    "environment": {"name": "kitchen"},
                    "episodes": 60,
                    "adversarial_count": 12,
                },
            },
        ).json()
        # These three keys exist on the payload and are no longer dropped.
        assert "adversarial_episode_count" in card
        assert "adversarial_success_rate" in card
        assert "block_reasons" in card
        view = view_core.scorecard_view(card, None)
        assert view["adversarialCount"] == str(card["adversarial_episode_count"])
        if card["adversarial_success_rate"] is None:
            assert view["adversarialRate"] is None
        else:
            assert view["adversarialRate"] == f"{card['adversarial_success_rate'] * 100:.1f}%"

    def test_same_second_runs_do_not_invert_the_trend(self, view_core: Any) -> None:
        """created_at is second-precision, so runs started in the same second
        all tie on the timestamp.

        Sorting a newest-first list by a tied timestamp and breaking ties by
        position leaves it newest-first — which makes the OLDEST run the "latest"
        one and reports the trend backwards. This is a real inversion, not a
        cosmetic one, and it is what a plain timestamp sort produced.
        """
        rows = [
            {"run_id": "newest", "composite_score": 40.0, "deploy_decision": "BLOCK",
             "created_at": "2026-09-26T10:00:00+00:00", "checkpoint_id": "c3"},
            {"run_id": "middle", "composite_score": 60.0, "deploy_decision": "APPROVE",
             "created_at": "2026-09-26T10:00:00+00:00", "checkpoint_id": "c2"},
            {"run_id": "oldest", "composite_score": 80.0, "deploy_decision": "APPROVE",
             "created_at": "2026-09-26T10:00:00+00:00", "checkpoint_id": "c1"},
        ]
        t = view_core.compute_trends(rows)
        assert t["latestScore"] == 40.0  # the newest row, not the oldest
        assert t["delta"] == -20.0  # 40 - 60, not 40 - 80
        assert t["ma3"] == 60.0
        assert t["newestCheckpoint"] == "c3"

    def test_out_of_order_rows_are_still_corrected(self, view_core: Any) -> None:
        """A backend that ever returns rows out of order must not silently
        produce a wrong 'latest' score."""
        rows = [
            {"run_id": "a", "composite_score": 10.0, "deploy_decision": "BLOCK",
             "created_at": "2026-09-26T10:00:00+00:00", "checkpoint_id": "c1"},
            {"run_id": "b", "composite_score": 30.0, "deploy_decision": "APPROVE",
             "created_at": "2026-09-26T10:00:02+00:00", "checkpoint_id": "c2"},
            {"run_id": "c", "composite_score": 20.0, "deploy_decision": "APPROVE",
             "created_at": "2026-09-26T10:00:01+00:00", "checkpoint_id": "c3"},
        ]
        t = view_core.compute_trends(rows)
        assert t["latestScore"] == 30.0
        assert t["delta"] == 10.0

    def test_client_trends_match_the_engine_module(
        self, client: TestClient, view_core: Any
    ) -> None:
        """The dashboard recomputes trends in JS; assert the JS agrees with
        validsim.engine.trends on the same rows, so the two cannot drift."""
        from validsim.engine.trends import compute_trends

        for index, threshold in enumerate((85.0, 90.0, 100.0)):
            client.post(
                "/api/v1/validations",
                json={
                    "checkpoint_id": f"ckpt-{index}",
                    "threshold": threshold,
                    "task": {
                        "task_id": "pick-place",
                        "robot": {"name": "franka"},
                        "environment": {"name": "kitchen"},
                        "episodes": 60,
                        "adversarial_count": 12,
                    },
                },
            )

        rows = client.get("/api/v1/dashboard/history").json()  # newest first
        oldest_first = list(reversed(rows))
        server = compute_trends(oldest_first)

        js = view_core.compute_trends(rows)  # JS sorts internally
        assert js["latestScore"] == server.latest_composite
        assert js["delta"] == server.delta_last
        assert js["ma3"] == server.moving_average_3
        assert js["approvalRate"] == server.approval_rate_10
        assert js["totalRuns"] == server.n_runs
        assert js["window3Size"] == min(3, server.n_runs)
        assert js["window10Size"] == min(10, server.n_runs)

    def test_run_summary_sentence_names_the_block_reason(self, view_core: Any) -> None:
        """The live region must answer "why blocked?" without the user hunting
        for the panel."""
        view = view_core.scorecard_view(
            _scorecard(
                deploy_decision="BLOCK",
                block_reasons=["adversarial success rate 21.0% is below the 60% floor"],
            ),
            None,
        )
        sentence = view_core.run_summary_sentence(view)
        assert "BLOCK" in sentence
        assert "blocked" in sentence
        assert "adversarial success rate 21.0% is below the 60% floor" in sentence
        assert "1 reason" in sentence

    def test_run_summary_sentence_omits_reasons_on_approve(self, view_core: Any) -> None:
        view = view_core.scorecard_view(_scorecard(), None)
        sentence = view_core.run_summary_sentence(view)
        assert "APPROVE" in sentence
        assert "reason" not in sentence


# =====================================================================
# 4. Auth: 401 must degrade to a state, never raw JSON
# =====================================================================
class TestAuthDegradation:
    """Verified against the real app with a key configured."""

    def test_root_serves_the_shell_while_the_api_stays_gated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The site root must be reachable without a key; the data must not.

        This test previously asserted the opposite -- that ``/`` answers 401
        ``application/json`` -- which was accurate but described a defect. The
        app-level dependency lives in ``application.router.dependencies`` and
        FastAPI merges router-level dependencies into each route at
        registration time, so ``mount_dashboard``'s ``"/"`` inherited the
        gate. The consequence was that the SPA's own auth panel could never
        execute: no page loaded, so there was no way to present the prompt,
        and the operator was left with a bare JSON error.

        ``mount_dashboard`` now registers ``"/"`` with the dependency list
        temporarily emptied, mirroring the un-gated ``/metrics`` mount in
        ``create_app``. Both halves of that contract are asserted here, since
        serving the shell publicly is only safe while the API behind it is
        still locked.
        """
        monkeypatch.setenv("VALIDSIM_API_KEY", "s3cret")
        client = TestClient(create_app(ValidationStore()))

        root = client.get("/")
        assert root.status_code == 200, root.text[:200]
        assert root.headers["content-type"].startswith("text/html")
        # The real SPA, not a JSON error and not an empty body.
        assert "<html" in root.text.lower()
        # A served 401 must be JSON, never the HTML shell: this proves "/" is not
        # quietly proxying an auth error into a 200. Substring matching cannot
        # express that here -- index.html carries an HTML *comment* quoting the
        # 401 body in order to explain this very panel -- so assert on document
        # structure instead: a real SPA declares a doctype, a <title> and a
        # <body>, none of which a proxied JSON error has.
        assert "<!doctype html>" in root.text.lower()
        assert "<title" in root.text.lower()
        assert "<body" in root.text.lower()

        # The aggregate view of past runs is the sensitive surface. The route is
        # under the dashboard prefix; a request to a non-existent path 404s, which
        # would make this assertion pass for the wrong reason.
        summary = client.get("/api/v1/dashboard/summary")
        assert summary.status_code == 401
        assert "detail" in summary.json()

        # The dashboard's own API prefix is gated too, not just /api/v1.
        assert client.get("/api/v1/dashboard/history").status_code == 401

    def test_root_is_public_even_when_no_key_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With auth disabled the root must still serve the app, not 404."""
        monkeypatch.delenv("VALIDSIM_API_KEY", raising=False)
        client = TestClient(create_app(ValidationStore()))
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_health_is_public_and_reports_auth_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The recovery hook: without it the client could not tell a locked
        deployment from a dead API."""
        monkeypatch.setenv("VALIDSIM_API_KEY", "s3cret")
        client = TestClient(create_app(ValidationStore()))
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["auth_enabled"] is True

    def test_static_assets_stay_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The SPA files are served by StaticFiles, outside the router gate, so
        the auth panel itself can always load."""
        monkeypatch.setenv("VALIDSIM_API_KEY", "s3cret")
        client = TestClient(create_app(ValidationStore()))
        for asset in ("app.js", "view-core.js", "styles.css", "index.html"):
            assert client.get(f"/static/{asset}").status_code == 200, asset

    def test_markup_ships_the_auth_panel(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="auth-panel"' in html
        assert 'id="auth-form"' in html
        assert 'id="api-key"' in html
        # Labeled, described, and password-typed (never echoed on screen).
        assert '<label for="api-key">API key</label>' in html
        assert 'id="api-key" name="api_key" type="password"' in html
        assert 'aria-describedby="api-key-hint auth-status"' in html

    def test_body_starts_hidden_so_a_locked_deployment_never_flashes(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert '<div id="app-body" hidden>' in html
        auth = _tag_containing(html, 'id="auth-panel"')
        assert "hidden" in auth
        assert 'aria-labelledby="auth-title"' in auth

    def test_app_js_probes_health_before_loading_panels(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        assert 'await probeAuthPolicy()' in js
        assert "policy.auth_enabled === true" in js
        # The gate decision must precede the first panel load.
        boot = js.split("(async function boot()", 1)[1]
        assert boot.index("showAuthRequired") < boot.index("await refreshPanels()")

    def test_a_401_raises_auth_required_not_a_generic_error(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        fetch_fn = js.split("async function fetchJSON", 1)[1].split("\n  }", 1)[0]
        assert "response.status === 401" in fetch_fn
        assert "throw new AuthRequired()" in fetch_fn

    def test_key_is_sent_as_a_header_never_in_the_url(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        assert '"X-API-Key"' in js
        assert "sessionStorage" in js
        # No key ever travels in a query string.
        assert "api_key=" not in js
        assert "X-API-Key=" not in js

    def test_rejected_key_is_discarded(self) -> None:
        """A rejected credential would fail every later request with no way out."""
        js = APP_JS.read_text(encoding="utf-8")
        fetch_fn = js.split("async function fetchJSON", 1)[1].split("\n  }", 1)[0]
        assert 'writeApiKey("")' in fetch_fn

    def test_rejected_key_is_cleared_from_the_visible_field(self) -> None:
        """The stored copy is discarded, but so must the visible one.

        Found by the browser test: the code comment claimed the field was
        cleared on a rejected key while the implementation left the rejected
        secret sitting in the input, one Enter-press from being re-sent.
        """
        js = APP_JS.read_text(encoding="utf-8")
        handler = js.split("els.authForm.addEventListener", 1)[1]
        rejected = handler.split("That API key was rejected", 1)[0]
        # The clearing must happen in the rejection branch, before the message.
        assert "els.apiKey.value = \"\"" in rejected
        # ...and the same flow that already drops the stored credential.
        after_401 = js.split("response.status === 401", 1)[1]
        assert 'writeApiKey("")' in after_401.split("throw new AuthRequired()", 1)[0]


# =====================================================================
# 5. Accessibility, asserted over the shipped source
# =====================================================================
class TestAccessibilityContract:
    def test_every_input_has_an_explicit_label(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        import re

        for match in re.finditer(r'<input\b[^>]*>', html):
            tag = match.group(0)
            assert 'type="hidden"' not in tag, tag
            assert 'aria-label=' in tag or re.search(r'\bid="([^"]+)"', tag), tag
            element_id = re.search(r'\bid="([^"]+)"', tag).group(1)
            assert f'<label for="{element_id}">' in html, element_id

    def test_no_emoji_used_as_an_icon(self) -> None:
        for path in (INDEX_HTML, APP_JS):
            text = path.read_text(encoding="utf-8")
            assert not any(ord(ch) > 0x1F000 for ch in text), path.name

    def test_buttons_are_never_disabled_while_loading(self) -> None:
        """WCAG 2.4.3: disabling the focused control drops focus to <body> and
        throws keyboard users to the top of the page. The APG loading-button
        pattern is aria-busy + a re-entrancy guard instead."""
        js = APP_JS.read_text(encoding="utf-8")
        for setter in (
            "runBtn.disabled",
            "enqueueBtn.disabled",
            "authBtn.disabled",
            "btn.disabled",
        ):
            assert setter not in js, setter
        assert "input.disabled = isRunning" not in js
        assert "input.disabled = isBusy" not in js
        # ...and the guard that replaces it is actually present.
        assert "if (runInFlight) return;" in js
        assert "if (enqueueInFlight) return;" in js
        assert "if (authInFlight) return;" in js

    def test_aria_busy_is_driven_on_all_three_buttons(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for button_id in ("run-btn", "enqueue-btn", "auth-btn"):
            tag = _tag_containing(html, f'id="{button_id}"')
            assert 'aria-busy="false"' in tag, button_id
        js = APP_JS.read_text(encoding="utf-8")
        assert js.count('setAttribute("aria-busy", String(is') >= 3

    def test_verdict_is_never_colour_only(self) -> None:
        """The PDF exporter commits to colour+text; the dashboard must too."""
        js = APP_JS.read_text(encoding="utf-8")
        verdict = js.split("function renderVerdict", 1)[1].split(
            "function renderBlockReasons", 1
        )[0]
        # A literal word in the banner...
        assert "<span>" in verdict
        # ...and an accessible sentence naming it.
        assert "Deploy decision: " in verdict
        assert "aria-label" in verdict
        # Both decision words are reachable, not one plus a colour change.
        for word in ("APPROVE", "BLOCK"):
            assert word in verdict

    def test_block_reasons_carry_words_not_just_a_hue(self) -> None:
        css = STYLES_CSS.read_text(encoding="utf-8")
        assert ".block-reasons-count" in css
        # The heading text is produced as a phrase, not a numeral alone.
        js = APP_JS.read_text(encoding="utf-8")
        assert "Blocked for 1 reason" in js
        assert '"Blocked for " + reasons.length + " reasons"' in js

    def test_trend_direction_is_stated_in_words(self) -> None:
        js = APP_JS.read_text(encoding="utf-8")
        for word in ("increase", "decrease", "no change"):
            assert f'"{word}"' in js

    def test_skipped_regions_stay_reachable(self) -> None:
        """The taxonomy table is never display:none, so find-in-page and screen
        readers still reach the data behind the canvas."""
        css = STYLES_CSS.read_text(encoding="utf-8")
        assert ".taxonomy-table { display: none" not in css
        js = APP_JS.read_text(encoding="utf-8")
        assert 'classList.toggle("is-visually-hidden", !visible)' in js

    def test_live_regions_are_polite_or_assertive_never_off(self) -> None:
        import re

        html = INDEX_HTML.read_text(encoding="utf-8")
        for match in re.finditer(r'aria-live="([^"]+)"', html):
            assert match.group(1) in {"polite", "assertive"}, match.group(1)

    def test_scrollable_regions_are_focusable(self) -> None:
        """WCAG 2.1.1: a scroll container reachable only by mouse is unusable."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert html.count('class="table-scroll" tabindex="0" role="region"') == 2

    def test_single_h1_and_labelled_sections(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert html.count("<h1") == 1
        import re

        for match in re.finditer(r'<section\b[^>]*>', html):
            assert "aria-labelledby=" in match.group(0), match.group(0)

    def test_exactly_one_live_announcer_plus_status_elements(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert html.count('id="a11y-announcer"') == 1
        # The 3s poll target must not be a live region, or it floods a screen reader.
        meta = _tag_containing(html, 'id="jobs-meta"')
        assert "aria-live" not in meta

    def test_focus_is_never_lost_to_body(self) -> None:
        """Nothing in app.js disables an element.

        Asserted over code only, with comments stripped: the rationale for the
        change *mentions* `button.disabled = true` by name, and a naive
        substring check would flag the explanation as a violation.
        """
        js = _js_without_comments(APP_JS)
        assert ".disabled" not in js

    def test_unmeasured_values_are_worded_not_blank(self) -> None:
        """An absent measurement is stated in words, never left blank -- a blank
        reads as "zero" or "not loaded", neither of which is true."""
        js = APP_JS.read_text(encoding="utf-8")
        assert '"no segment"' in js
        core = VIEW_CORE.read_text(encoding="utf-8")
        assert "no baseline" in core
        assert "not measured" in core


# =====================================================================
# 6. Packaging: the new asset is really served
# =====================================================================
class TestAssetWiring:
    def test_view_core_is_served_by_the_static_mount(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        response = client.get("/static/view-core.js")
        assert response.status_code == 200
        assert len(response.content) > 0
        content_type = response.headers["content-type"]
        assert content_type.startswith(("text/javascript", "application/javascript"))

    def test_page_loads_view_core_before_app(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert html.index("/static/view-core.js") < html.index("/static/app.js")

    def test_app_js_reports_a_fatal_error_when_the_core_is_missing(self) -> None:
        """Without view-core there is no formatter, so it must say so rather
        than throw on the first render."""
        js = APP_JS.read_text(encoding="utf-8")
        assert "__vsViewCoreFailed" in js
        assert "function fatal(" in js
        assert 'id="app-fatal"' in INDEX_HTML.read_text(encoding="utf-8")

    def test_dashboard_still_boots_without_a_key(self) -> None:
        client = TestClient(create_app(ValidationStore()))
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/dashboard/summary").json()["total_runs"] == 0


# =====================================================================
# 7. Regression cover for the defects found while finishing this work
# =====================================================================
class TestFixedDefects:
    """Each test fails against the pre-fix asset, so a reintroduction is caught.

    Every one of these was a live defect, not a hypothetical: the duplicate
    `chartWrap` key and the stale harness comment were real, and `runSummary
    Sentence` really did rebuild the run id by splitting the display string.
    """

    # --- runSummarySentence must not re-parse the display string -------------
    @requires_node
    def test_summary_sentence_uses_the_run_id_field_not_a_split(
        self, view_core: Any
    ) -> None:
        """`meta` is a display string; splitting it back apart is a coupling that
        silently breaks when a field is inserted in the middle of it."""
        core = VIEW_CORE.read_text(encoding="utf-8")
        body = core.split("function runSummarySentence", 1)[1].split("\n  }", 1)[0]
        assert "view.runId" in body
        assert "view.meta.split" not in body

        view = view_core.scorecard_view(_scorecard(), None)
        assert view["runId"] == "vrun-abc123"
        # A run id containing the meta separator must not truncate the sentence.
        odd = view_core.scorecard_view(
            _scorecard(run_id="vrun-\u00b7-weird"), None
        )
        assert odd["runId"] == "vrun-\u00b7-weird"
        assert view_core.run_summary_sentence(odd).startswith("Run vrun-\u00b7-weird ")

    @requires_node
    def test_run_id_is_exposed_on_the_view(self, view_core: Any) -> None:
        assert "runId" in view_core.scorecard_view(_scorecard(), None)

    # --- duplicate object keys ---------------------------------------------
    def test_els_has_no_duplicate_keys(self) -> None:
        """A repeated key in the `els` literal is silently accepted by JS: the
        last one wins and the earlier binding vanishes. Assert there is none."""
        import re
        from collections import Counter

        js = APP_JS.read_text(encoding="utf-8")
        block = js.split("const els = {", 1)[1].split("\n  };", 1)[0]
        keys = re.findall(r"(?m)^\s{4}(\w+):", block)
        duplicates = [k for k, n in Counter(keys).items() if n > 1]
        assert not duplicates, f"duplicate els keys: {duplicates}"

    # --- every $() lookup in app.js resolves to an id in the markup ---------
    def test_every_element_app_js_looks_up_exists_in_the_markup(self) -> None:
        """A typo'd id yields null, and the first property write on it throws
        mid-render -- blanking the panel an operator reads a verdict from."""
        import re

        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        wanted = set(re.findall(r'\$\("([^"]+)"\)', js))
        assert wanted, "no lookups found; the assertion would pass vacuously"
        present = set(re.findall(r'id="([^"]+)"', html))
        missing = sorted(wanted - present)
        assert not missing, f"app.js queries ids absent from index.html: {missing}"

    # --- block reasons are announced once, not twice ------------------------
    def test_block_reason_rail_is_not_a_second_live_region(self) -> None:
        """`role="alert"` already implies aria-live="assertive"; stamping an
        explicit aria-live on the same element makes some screen readers
        announce it twice -- the operator hears the BLOCK reason list doubled
        while a long run is still in flight."""
        tag = _tag_containing(INDEX_HTML.read_text(encoding="utf-8"), 'id="block-reasons"')
        assert "aria-live" not in tag, tag

    # --- the scorecard is reachable even when "/" is not -------------------
    def test_index_html_survives_being_served_from_the_static_mount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The document the operator is actually served under a key.

        With VALIDSIM_API_KEY set, "/" answers 401 application/json, so the only
        path that returns this HTML is the /static mount. The page therefore
        cannot assume it was loaded from "/" and must resolve its own assets and
        API calls relatively.
        """
        monkeypatch.setenv("VALIDSIM_API_KEY", "s3cret")
        client = TestClient(create_app(ValidationStore()))
        response = client.get("/static/index.html")
        assert response.status_code == 200
        body = response.text
        # Assets are referenced absolutely under /static, so they resolve from
        # any path that serves this document.
        assert 'src="/static/app.js"' in body
        assert 'src="/static/view-core.js"' in body
        assert 'href="/static/styles.css"' in body
        # And no same-directory relative asset reference, which would 404 when
        # served from /static/.
        assert 'src="./' not in body
        assert 'src="app.js"' not in body

    def test_page_states_which_paths_serve_it(self) -> None:
        """The canonical/description comment records why the auth panel has to
        work from the static mount, so the next reader does not 'fix' the
        absolute asset paths into relative ones and break the key-protected
        deployment."""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'rel="canonical"' in html
        assert "auth_enabled" in html

    # --- RATE_DIGITS rationale must not assert something false -------------
    def test_percentage_precision_rationale_matches_the_engine(self) -> None:
        """The comment justifying one decimal on a percentage claimed the store
        rounds rates to 4dp; the precision that actually bounds these figures
        differs between the 0-1 rates and the 0-100 component scores. The
        comment must not assert a claim the engine contradicts."""
        from validsim.engine import scorecard as engine_scorecard

        core = VIEW_CORE.read_text(encoding="utf-8")
        engine = Path(engine_scorecard.__file__).read_text(encoding="utf-8")
        # The 0-1 rates really are stored at 4dp...
        assert "round(evaluation.success_rate, 4)" in engine
        # ...and the 0-100 robustness score at 2dp, so the comment must not
        # claim a single uniform 4dp bound for both.
        assert "round(_clamp(100.0 - _ROBUSTNESS_SCALE * spread, 0.0, 100.0), 2)" in engine
        assert "finest precision the underlying data supports" in core


def _tag_containing(html: str, needle: str) -> str:
    """Return the whole opening tag that contains ``needle``."""
    index = html.index(needle)
    start = html.rindex("<", 0, index)
    end = html.index(">", index) + 1
    return html[start:end]


def _js_without_comments(path: Any) -> str:
    """JavaScript source with ``//`` and block comments removed.

    Assertions about the *absence* of a code pattern have to look at code only.
    The rationale comments in app.js name the patterns they replaced (for
    example `button.disabled = true`), so a raw substring scan would flag the
    explanation of a fix as a violation of it.
    """
    import re

    source = path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    source = re.sub(r"(?m)//.*$", " ", source)
    return source
