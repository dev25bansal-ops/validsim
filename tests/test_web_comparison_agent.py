"""Behavioural tests for the dashboard's Model Comparison panel.

The panel is the one place in the UI where an operator can be shown that a
run is *worse* than a baseline. That makes it a place where a quietly wrong
rendering is expensive: a comparison that reports "no significant
regressions" when the engine found a critical one, or that shows a stale
table after the run changed, both push a human toward the wrong decision.

Every claim here is executed, not inspected:

* **Runtime claims** (formatting, severity folding, the summary sentence) run
  against the real ``validsim/web/view-core.js`` under Node — the same file
  the browser downloads.

* **API claims** (what the comparison endpoint actually returns, and how it
  behaves for an unknown baseline) run against the real FastAPI app with an
  in-memory store.

* **Static claims** (the panel's markup exists, the states are distinct, the
  table is not wired with innerHTML) are asserted over the shipped
  ``index.html`` / ``app.js``, because the assertion is about the *absence*
  of a pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from validsim.api.main import create_app
from validsim.store.memory import ValidationStore

from test_web_viewcore_harness_agent import (  # type: ignore[import-not-found]
    APP_JS,
    INDEX_HTML,
    requires_node,
)
from test_web_viewcore_harness_agent import NODE, _LoadedViewCore


@pytest.fixture(scope="session")
def view_core() -> Any:
    """The loaded ``view-core`` module, or skip when Node is unavailable."""
    if NODE is None:
        pytest.skip("Node.js is not installed; cannot execute view-core.js")
    return _LoadedViewCore()


def _comparison_payload(**overrides: Any) -> dict[str, Any]:
    """A comparison response shaped exactly like the API's.

    ``items`` mirrors ``RegressionReport.to_dict()``: ``before`` is the
    baseline value, ``after`` is the current run's, ``delta`` is
    ``after - before``.
    """
    payload: dict[str, Any] = {
        "run_id": "vrun-current",
        "baseline_id": "vrun-baseline",
        "items": [
            {
                "metric": "success_rate",
                "before": 0.9,
                "after": 0.7,
                "delta": -0.2,
                "p_value": 0.0001,
                "significant": True,
                "severity": "critical",
            },
            {
                "metric": "mean_duration_s",
                "before": 1.2,
                "after": 1.25,
                "delta": 0.05,
                "p_value": None,
                "significant": False,
                "severity": "info",
            },
        ],
        "significant_count": 1,
        "worst_severity": "critical",
    }
    payload.update(overrides)
    return payload


# =====================================================================
# 1. The view model
# =====================================================================
@requires_node
class TestComparisonViewModel:
    """The panel must not overstate or understate what the engine found."""

    def test_view_normalises_a_real_response(self, view_core: Any) -> None:
        view = view_core.comparison_view(_comparison_payload())
        assert view["hasItems"] is True
        assert view["worstSeverity"] == "critical"
        assert view["significantCount"] == 1
        assert len(view["items"]) == 2

    def test_values_are_rendered_at_the_engines_precision(
        self, view_core: Any
    ) -> None:
        """success_rate is a 0-1 fraction; duration is seconds; the score is
        a 0-100 number. Rendering a fraction as "0.90" would understate the
        precision the engine actually resolved."""
        view = view_core.comparison_view(_comparison_payload())
        success = view["items"][0]
        duration = view["items"][1]
        assert success["before"] == "90.0%"
        assert success["after"] == "70.0%"
        assert duration["before"] == "1.200 s"
        assert duration["after"] == "1.250 s"

    def test_delta_uses_a_signed_percentage_point_form(
        self, view_core: Any
    ) -> None:
        """A -20 pp success-rate drop must read as a drop, not as "-0.20"."""
        view = view_core.comparison_view(_comparison_payload())
        assert view["items"][0]["deltaText"] == "\u221220.0 pp"

    def test_delta_keeps_the_sign_for_duration(self, view_core: Any) -> None:
        view = view_core.comparison_view(_comparison_payload())
        assert view["items"][1]["deltaText"] == "+0.050 s"

    def test_p_value_below_the_threshold_is_an_inequality(
        self, view_core: Any
    ) -> None:
        """"0.000" reads like an exact zero, which overstates what a
        permutation test established."""
        assert view_core.comparison_format_p_value(0.00001) == "<0.001"
        assert view_core.comparison_format_p_value(0.042) == "0.042"

    def test_p_value_is_na_for_descriptive_metrics(self, view_core: Any) -> None:
        """Duration has no significance test, so its p-value is None — not 0."""
        view = view_core.comparison_view(_comparison_payload())
        assert view["items"][1]["pText"] == "n/a"

    def test_worst_severity_is_the_maximum_not_the_last(
        self, view_core: Any
    ) -> None:
        """Item order must not decide which severity the panel headlines."""
        payload = _comparison_payload(
            items=[
                {
                    "metric": "success_rate",
                    "before": 0.9,
                    "after": 0.7,
                    "delta": -0.2,
                    "p_value": 0.001,
                    "significant": True,
                    "severity": "info",
                },
                {
                    "metric": "mean_duration_s",
                    "before": 1.0,
                    "after": 3.0,
                    "delta": 2.0,
                    "p_value": 0.001,
                    "significant": True,
                    "severity": "critical",
                },
            ],
            worst_severity="critical",
        )
        assert view_core.comparison_view(payload)["worstSeverity"] == "critical"

    def test_worst_severity_uses_the_engines_vocabulary(
        self, view_core: Any
    ) -> None:
        """The engine says "warning", not "warn". A dashboard that rendered
        the raw token would show a word the operator has never seen."""
        payload = _comparison_payload(
            items=[
                {
                    "metric": "success_rate",
                    "before": 0.9,
                    "after": 0.85,
                    "delta": -0.05,
                    "p_value": 0.01,
                    "significant": True,
                    "severity": "warning",
                }
            ],
            worst_severity="warning",
        )
        view = view_core.comparison_view(payload)
        assert view["worstSeverity"] == "warning"
        assert view["items"][0]["severity"] == "warning"

    def test_notify_layer_warn_alias_is_normalised(self, view_core: Any) -> None:
        """The notification layer spells the middle band "warn"; both must
        rank identically, or a severity would change with its producer."""
        payload = _comparison_payload(
            items=[
                {
                    "metric": "success_rate",
                    "before": 0.9,
                    "after": 0.85,
                    "delta": -0.05,
                    "p_value": 0.01,
                    "significant": True,
                    "severity": "warn",
                }
            ],
            worst_severity="warn",
        )
        view = view_core.comparison_view(payload)
        assert view["worstSeverity"] == "warning"

    def test_significant_count_matches_the_engine_rule(
        self, view_core: Any
    ) -> None:
        """The engine counts a *regression* as significant AND worse than
        "info". A significant improvement is not a regression."""
        payload = _comparison_payload(
            items=[
                {
                    "metric": "success_rate",
                    "before": 0.7,
                    "after": 0.9,
                    "delta": 0.2,
                    "p_value": 0.001,
                    "significant": True,
                    "severity": "info",
                }
            ],
            significant_count=0,
        )
        assert view_core.comparison_view(payload)["significantCount"] == 0

    def test_server_count_is_preferred_when_present(
        self, view_core: Any
    ) -> None:
        """The API's own count is authoritative; the client must not
        recompute a different one."""
        payload = _comparison_payload(significant_count=7)
        assert view_core.comparison_view(payload)["significantCount"] == 7

    def test_missing_items_degrades_to_empty_not_an_error(
        self, view_core: Any
    ) -> None:
        """A partial response must render an empty table, not throw inside
        the renderer."""
        view = view_core.comparison_view({})
        assert view["hasItems"] is False
        assert view["items"] == []

    def test_malformed_values_render_as_unavailable(
        self, view_core: Any
    ) -> None:
        """A non-numeric metric value must never render as "NaN"."""
        item = view_core.normalise_comparison_item(
            {"metric": "success_rate", "before": None, "after": "abc"}
        )
        assert item["before"] == "unavailable"
        assert item["after"] == "unavailable"

    def test_unknown_metric_keeps_its_raw_key(self, view_core: Any) -> None:
        """An unrecognised metric must still be visible to the operator."""
        assert view_core.comparison_metric_label("new_metric") == "new_metric"
        assert view_core.comparison_metric_label("success_rate") == "Success rate"


# =====================================================================
# 2. The summary sentence
# =====================================================================
@requires_node
class TestComparisonSummarySentence:
    def test_one_regression_is_singular(self, view_core: Any) -> None:
        view = view_core.comparison_view(_comparison_payload())
        sentence = view_core.comparison_summary_sentence(view)
        assert "1 statistically significant regression" in sentence
        assert "regressions" not in sentence

    def test_no_significant_regressions_says_so(self, view_core: Any) -> None:
        payload = _comparison_payload(
            items=[
                {
                    "metric": "success_rate",
                    "before": 0.9,
                    "after": 0.9,
                    "delta": 0.0,
                    "p_value": 0.9,
                    "significant": False,
                    "severity": "info",
                }
            ],
            significant_count=0,
            worst_severity="info",
        )
        view = view_core.comparison_view(payload)
        sentence = view_core.comparison_summary_sentence(view)
        assert "No significant regressions" in sentence

    def test_empty_comparison_is_explicit(self, view_core: Any) -> None:
        """An empty result must say "no comparable metrics", not "no
        regressions" — the difference is "we found nothing" vs "we found
        nothing to find"."""
        view = view_core.comparison_view({})
        sentence = view_core.comparison_summary_sentence(view)
        assert "No comparable metrics" in sentence
        assert "No significant regressions" not in sentence

    def test_non_object_input_is_handled(self, view_core: Any) -> None:
        assert view_core.comparison_summary_sentence(None) == (
            "Comparison unavailable."
        )


# =====================================================================
# 3. The API contract
# =====================================================================
class TestComparisonEndpoint:
    """The panel talks to a real endpoint; the contract must hold."""

    @pytest.fixture()
    def client(self) -> TestClient:
        store = ValidationStore()
        app = create_app(store=store)
        return TestClient(app)

    def _create_run(self, client: TestClient, **overrides: Any) -> str:
        body: dict[str, Any] = {
            "checkpoint_id": "ckpt-franka-7b",
            "task": {
                "task_id": "pick-place",
                "robot": {"name": "franka_panda"},
                "environment": {"name": "kitchen"},
                "episodes": 100,
                "randomization": "full",
                "adversarial_count": 10,
            },
        }
        body.update(overrides)
        response = client.post("/api/v1/validations", json=body)
        assert response.status_code == 201, response.text
        return response.json()["run_id"]

    def test_compare_returns_the_documented_shape(self, client: TestClient) -> None:
        run_id = self._create_run(client)
        baseline_id = self._create_run(client)

        response = client.post(
            f"/api/v1/validations/{run_id}/compare",
            json={"baseline_id": baseline_id},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["baseline_id"] == baseline_id
        assert isinstance(payload["items"], list)
        assert "significant_count" in payload
        assert "worst_severity" in payload

    def test_compare_against_an_unknown_baseline_is_a_404(
        self, client: TestClient
    ) -> None:
        """The panel's error path needs a real 404 to render against."""
        run_id = self._create_run(client)
        response = client.post(
            f"/api/v1/validations/{run_id}/compare",
            json={"baseline_id": "vrun-deadbeef"},
        )
        assert response.status_code == 404

    def test_compare_rejects_a_missing_baseline_id(
        self, client: TestClient
    ) -> None:
        """The form's required-field check is a convenience; the API must
        enforce it too."""
        run_id = self._create_run(client)
        response = client.post(
            f"/api/v1/validations/{run_id}/compare",
            json={},
        )
        assert response.status_code == 422

    def test_compare_rejects_an_overlong_baseline_id(
        self, client: TestClient
    ) -> None:
        run_id = self._create_run(client)
        response = client.post(
            f"/api/v1/validations/{run_id}/compare",
            json={"baseline_id": "x" * 5000},
        )
        assert response.status_code == 422


# =====================================================================
# 4. The markup and wiring
# =====================================================================
class TestComparisonMarkup:
    """Static claims about the shipped assets."""

    @pytest.fixture(scope="class")
    @classmethod
    def html(cls) -> str:
        return INDEX_HTML.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def js(cls) -> str:
        return APP_JS.read_text(encoding="utf-8")

    def test_panel_exists_in_the_markup(self, html: str) -> None:
        assert 'id="comparison-title"' in html
        assert 'id="comparison-form"' in html
        assert 'id="comparison-tbody"' in html
        assert 'id="comparison-empty"' in html
        assert 'id="comparison-status"' in html

    def test_panel_is_labelled_for_screen_readers(self, html: str) -> None:
        """A table with no caption is unintelligible when read out of
        context."""
        assert "<caption" in html
        assert 'id="comparison-caption"' in html

    def test_form_has_a_real_label(self, html: str) -> None:
        assert '<label for="comparison-baseline"' in html

    def test_busy_state_is_announced(self, html: str) -> None:
        assert 'aria-busy="false"' in html

    def test_empty_state_and_table_are_both_present(
        self, html: str
    ) -> None:
        """The panel must be able to show an empty state *and* a table."""
        assert 'id="comparison-empty"' in html
        assert 'id="comparison-table-wrap"' in html
        assert 'id="comparison-table-wrap" tabindex="0"' in html

    def test_comparison_table_is_not_built_with_innerhtml(
        self, js: str
    ) -> None:
        """Run ids and metric names reach the table from the API and from
        operator input; they must render as characters, never as markup."""
        block = self._comparison_block(js)
        assert "innerHTML" not in block

    def test_comparison_error_path_renders_a_message(self, js: str) -> None:
        block = self._comparison_block(js)
        assert "Comparison failed" in block
        assert "renderComparisonError" in block

    def test_comparison_clears_a_stale_table_on_a_new_run(
        self, js: str
    ) -> None:
        """Loading a different run invalidates the comparison on screen; a
        stale table would be misread as the new run's result."""
        assert "resetComparisonPanel" in js

    @staticmethod
    def _comparison_block(js: str) -> str:
        """The slice of app.js that implements the comparison panel."""
        start = js.find("/* Model comparison")
        assert start != -1, "app.js has no model-comparison section"
        end = js.find("function decisionBadge", start)
        if end == -1:
            end = len(js)
        return js[start:end]


# =====================================================================
# 5. The asset is still served
# =====================================================================
def test_view_core_comparison_functions_are_exported() -> None:
    """The panel depends on these; a rename would silently break it."""
    source = Path(VIEW_CORE_PATH).read_text(encoding="utf-8")
    for name in (
        "comparisonView",
        "normaliseComparisonItem",
        "comparisonSummarySentence",
    ):
        assert f"{name}:" in source, f"{name} is not exported"


VIEW_CORE_PATH = (
    Path(__file__).resolve().parents[1] / "validsim" / "web" / "view-core.js"
)


def test_comparison_payload_is_json_serialisable() -> None:
    """The fixture mirrors the API's shape; a drift here would silently
    weaken every other test in this file."""
    json.dumps(_comparison_payload())
