"""Tests for Markdown and HTML scorecard exporters."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest

from validsim.engine.export import scorecard_to_html, scorecard_to_markdown
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
        for key in sc.failure_taxonomy:
            assert key in md

    def test_reports_confidence_interval(self) -> None:
        md = scorecard_to_markdown(_scorecard())
        assert "Confidence Interval" in md
        assert "0.8200" in md and "0.9500" in md

    def test_empty_taxonomy_is_handled(self) -> None:
        sc = _scorecard()
        empty = Scorecard(**{**sc.to_dict(), "failure_taxonomy": {}})
        md = scorecard_to_markdown(empty)
        assert "No failures" in md


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
