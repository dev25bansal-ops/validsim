"""Tests for Markdown and HTML scorecard exporters."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from validsim.engine.export import _md, scorecard_to_html, scorecard_to_markdown
from validsim.engine.scorecard import Scorecard


def _scorecard(decision: str = "APPROVE") -> Scorecard:
    return Scorecard(
        run_id="vrun-cafe1234",
        checkpoint_id="ckpt-1",
        task_id="pick-place",
        composite_score=91.5,
        success_rate=0.9,
        safety_score=80.0,
        robustness_score=100.0,
        regression_delta=-0.1,
        confidence_interval=(0.82, 0.95),
        deploy_decision=decision,  # type: ignore[arg-type]
        threshold=85.0,
        created_at="2026-01-01T00:00:00+00:00",
        episode_count=100,
        failure_taxonomy={"collision": 7, "grasp_failure": 3},
    )


class _RecordingParser(HTMLParser):
    """Minimal HTMLParser that records tags/text; raises on malformed input."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


class TestMarkdown:
    @pytest.mark.parametrize("decision", ["APPROVE", "BLOCK"])
    def test_contains_score_decision_and_taxonomy(self, decision: str) -> None:
        sc = _scorecard(decision)
        md = scorecard_to_markdown(sc)
        assert str(sc.composite_score) in md
        assert decision in md
        assert str(sc.threshold) in md
        # Taxonomy names are escaped for the table cell, so '_' appears as
        # '\_' in the source. Compare against the escaped form the renderer
        # emits; the rendered output still reads as a literal underscore.
        for key in sc.failure_taxonomy:
            assert _md(key) in md

    def test_reports_confidence_interval(self) -> None:
        md = scorecard_to_markdown(_scorecard())
        assert "Confidence Interval" in md
        assert "0.8200" in md and "0.9500" in md

    def test_empty_taxonomy_is_handled(self) -> None:
        sc = _scorecard()
        empty = Scorecard(**{**sc.to_dict(), "failure_taxonomy": {}})
        md = scorecard_to_markdown(empty)
        assert "No failures" in md


class TestMarkdownEscaping:
    """User-controlled strings must not be able to inject Markdown.

    Regression cover for the renderer being reachable with caller-supplied
    ``checkpoint_id`` / ``task_id`` / failure-mode names via
    ``GET /api/v1/validations/{id}/scorecard.md`` and ``validsim report``.
    """

    def _card(self, **overrides: object) -> Scorecard:
        sc = _scorecard()
        return Scorecard(**{**sc.to_dict(), **overrides})

    def test_table_pipe_in_checkpoint_id_cannot_open_a_new_row(self) -> None:
        payload = "| injected | row |\n| --- | --- |"
        md = scorecard_to_markdown(self._card(checkpoint_id=payload))
        # The payload must stay inside its single code span: the newline is
        # flattened and the row separator cannot terminate the line.
        body = [ln for ln in md.splitlines() if "Checkpoint" in ln]
        assert len(body) == 1, md
        # The span is still well-formed: exactly one delimiter pair on the line.
        assert body[0].count("`") == 2, body[0]

    def test_angle_brackets_in_taxonomy_are_neutralised(self) -> None:
        md = scorecard_to_markdown(
            self._card(failure_taxonomy={"<script>alert(1)</script>": 2})
        )
        assert "<script>" not in md
        assert "\\<script\\>" in md

    def test_ids_render_without_stray_backslashes(self) -> None:
        # Backslash escapes are NOT processed inside a code span, so escaping
        # ordinary identifier characters would leak backslashes to the reader.
        md = scorecard_to_markdown(self._card(checkpoint_id="llama-3.1-70b:ckpt-42"))
        assert "llama-3.1-70b:ckpt-42" in md
        assert "\\-" not in md

    def test_backtick_in_id_is_contained_by_a_wider_fence(self) -> None:
        md = scorecard_to_markdown(self._card(task_id="a`b"))
        line = [ln for ln in md.splitlines() if "Task" in ln][0]
        # The fence widens to two backticks so the embedded one is literal.
        # No space padding is added: the value does not start/end with one.
        assert "``a`b``" in line, line
        # The opening and closing fences are the SAME width, which is what
        # makes the span well-formed. A plain backtick count is meaningless
        # here: a doubled fence legitimately contains an odd number of them.
        assert line.endswith("``") and "``a`b``" in line, line

    def test_plain_id_keeps_the_compact_single_backtick_form(self) -> None:
        # The compact form is part of the CLI report contract asserted by
        # tests/test_cli_report.py, so ordinary ids must not gain padding.
        md = scorecard_to_markdown(self._card(checkpoint_id="ckpt-report"))
        assert "`ckpt-report`" in md

    def test_ordinary_taxonomy_names_stay_readable(self) -> None:
        md = scorecard_to_markdown(_scorecard())
        # '_' is escaped for the table cell, but renders as a literal
        # underscore, so the human-readable name is preserved.
        assert "grasp\\_failure" in md


class TestUnmeasuredProvenance:
    """A structural constant must not be rendered as an earned score.

    ``robustness_score`` is 100.0 for every ordinary run (``run_validation``
    applies one randomization level to every episode), and the regression
    component is 100.0 whenever no baseline was supplied. Both are
    "no evidence" states that render identically to a perfect result unless the
    report says so. The scorecard now carries the provenance; these tests pin
    the human-readable renderings that consume it.
    """

    @staticmethod
    def _card(**overrides: object) -> Scorecard:
        return Scorecard(**{**_scorecard().to_dict(), **overrides})

    def test_markdown_marks_unmeasured_robustness(self) -> None:
        md = scorecard_to_markdown(self._card(robustness_measured=False))
        assert "not measured" in md
        # The score itself is still reported, just not as a bare number.
        assert "100.0" in md

    def test_markdown_keeps_measured_robustness_bare(self) -> None:
        """CONTROL: a genuinely measured run must not carry the caveat."""
        md = scorecard_to_markdown(self._card(robustness_measured=True))
        assert "not measured" not in md

    def test_html_marks_unmeasured_robustness(self) -> None:
        html = scorecard_to_html(self._card(robustness_measured=False))
        assert "not measured" in html
        parser = _RecordingParser()
        parser.feed(html)
        parser.close()  # must remain well-formed

    def test_html_keeps_measured_robustness_bare(self) -> None:
        """CONTROL: the measured path is unchanged."""
        assert "not measured" not in scorecard_to_html(self._card(robustness_measured=True))


class TestHtml:
    def test_contains_composite_and_parses_cleanly(self) -> None:
        sc = _scorecard()
        html = scorecard_to_html(sc)
        assert str(sc.composite_score) in html
        parser = _RecordingParser()
        parser.feed(html)  # must not raise
        parser.close()
        assert "html" in parser.tags and "style" in parser.tags

    def test_is_self_contained(self) -> None:
        html = scorecard_to_html(_scorecard("BLOCK"))
        assert "<style>" in html
        assert "http://" not in html and "https://" not in html  # no external assets

    def test_empty_taxonomy_is_handled(self) -> None:
        sc = _scorecard()
        empty = Scorecard(**{**sc.to_dict(), "failure_taxonomy": {}})
        assert "No failures" in scorecard_to_html(empty)
