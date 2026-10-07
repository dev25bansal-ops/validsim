"""Executes validsim/web/view-core.js under Node and returns its pure functions.

The dashboard is vanilla JS with no build step and no JS test runner, so its
presentation logic is verified by loading the *same file the browser
downloads* into Node and calling the exported functions. That keeps the claims
about what an operator sees executable rather than aspirational.

Node is optional: when it is absent every runtime test skips instead of
failing, so the suite (and its coverage floor) still runs on a machine without a
JS runtime. Callers must treat an unavailable core as "cannot verify", never as
"verified".

Each call runs in a fresh child process and returns JSON, so no state leaks
between tests and a test that throws mid-expression cannot corrupt a later one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_WEB = Path(__file__).resolve().parents[1] / "validsim" / "web"

#: The real assets, resolved from the installed package (never a copy).
VIEW_CORE = _WEB / "view-core.js"
APP_JS = _WEB / "app.js"
INDEX_HTML = _WEB / "index.html"
STYLES_CSS = _WEB / "styles.css"

NODE = shutil.which("node") or shutil.which("nodejs")

requires_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


class ViewCoreError(RuntimeError):
    """The Node harness failed, or view-core.js itself threw."""


def run_view_core(script: str) -> Any:
    """Execute ``script`` with ``V`` bound to the loaded view-core; return JSON.

    A non-zero exit, a syntax error inside view-core.js, or a thrown exception
    all surface as :class:`ViewCoreError`, so a broken asset fails loudly
    instead of quietly returning ``None``.
    """
    if NODE is None:  # pragma: no cover - guarded by requires_node
        raise ViewCoreError("Node.js is not available")
    prelude = (
        'const V = require(%s);\n'
        'if (!V || typeof V.scorecardView !== "function") {'
        '  console.error("view-core did not export scorecardView"); process.exit(2); }\n'
    ) % json.dumps(str(VIEW_CORE))
    completed = subprocess.run(
        [NODE, "-e", prelude + script],
        capture_output=True,
        # bytes, not text: decoding here would run the payload through the
        # Windows console code page, which mangles the em dash / middle dot /
        # greater-than-or-equal the dashboard actually renders. The child is
        # forced to UTF-8 below and decoded here explicitly.
        timeout=60,
        env={**os.environ, "NODE_OPTIONS": "", "NODE_NO_WARNINGS": "1"},
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise ViewCoreError(
            f"node exited {completed.returncode}: {stderr.strip()[:800]}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ViewCoreError(f"non-JSON output: {stdout[:400]!r}") from exc


def call(fn: str, *args: Any) -> Any:
    """Call an exported pure function by name and return its JSON result."""
    payload = json.dumps(list(args))
    return run_view_core(f"console.log(JSON.stringify(V.{fn}.apply(null, {payload})));")


class _LoadedViewCore:
    """Each exported pure function as a Python method.

    Every call is a fresh child process, so no test can depend on state left
    behind by another.
    """

    @staticmethod
    def scorecard_view(card: Any, duration: Any = None) -> dict[str, Any]:
        return call("scorecardView", card, duration)

    @staticmethod
    def normalise_scorecard(card: Any) -> dict[str, Any]:
        return call("normaliseScorecard", card)

    @staticmethod
    def taxonomy_entries(raw: Any) -> list[list[Any]]:
        return call("taxonomyEntries", raw)

    @staticmethod
    def compute_trends(rows: Any) -> Any:
        return call("computeTrends", rows)

    @staticmethod
    def normalise_history(raw: Any) -> list[dict[str, Any]]:
        return call("normaliseHistory", raw)

    @staticmethod
    def summary_text(summary: Any) -> str:
        return call("summaryText", summary)

    @staticmethod
    def run_summary_sentence(view: Any) -> str:
        return call("runSummarySentence", view)

    @staticmethod
    def safe_decision(decision: Any) -> str:
        return call("safeDecision", decision)

    @staticmethod
    def decision_word(decision: Any) -> str:
        return call("decisionWord", decision)

    @staticmethod
    def num(value: Any) -> Any:
        return call("num", value)

    @staticmethod
    def pct(value: Any) -> str:
        return call("pct", value)

    @staticmethod
    def score2(value: Any) -> str:
        return call("score2", value)

    @staticmethod
    def count(value: Any) -> str:
        return call("count", value)

    @staticmethod
    def signed_points(value: Any) -> str:
        return call("signedPoints", value)

    @staticmethod
    def raw(script: str) -> Any:
        """Escape hatch for an assertion the named methods do not cover."""
        return run_view_core(script)


#: NOTE: this module deliberately contains no test functions. It is the Node
#: harness the presentation tests drive; pytest collects 0 tests from it. The
#: `view_core` fixture is declared in test_web_presentation_agent.py, not in
#: tests/conftest.py -- an earlier revision claimed the opposite, which sent a
#: reader looking for a fixture that does not exist. Keep the fixture next to
#: the only test module that uses it.

