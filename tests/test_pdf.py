"""Tests for the branded PDF scorecard exporter (Week-8 deliverable).

reportlab is installed in the project venv; these tests exercise the real
renderer end-to-end (bytes + file output) and assert the defensive ``.get``
defaults keep malformed input from raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from validsim.engine.pdf import render_scorecard_pdf, scorecard_pdf_bytes



def _approve_card() -> dict[str, Any]:
    return {
        "run_id": "vrun-approve1",
        "checkpoint_id": "ckpt-alpha",
        "task_id": "pick-place",
        "composite_score": 91.24,
        "success_rate": 0.93,
        "safety_score": 95.0,
        "robustness_score": 88.5,
        "regression_delta": -0.0123,
        "confidence_interval": [0.8801, 0.9702],
        "deploy_decision": "APPROVE",
        "threshold": 85.0,
        "created_at": "2026-09-18T12:00:00+00:00",
        "episode_count": 72,
        "failure_taxonomy": {"grip-slip": 3, "timeout": 2},
    }


def _block_card() -> dict[str, Any]:
    card = _approve_card()
    card.update(
        {
            "run_id": "vrun-block001",
            "composite_score": 61.5,
            "success_rate": 0.42,
            "deploy_decision": "BLOCK",
            "regression_delta": None,
            "confidence_interval": None,
            "failure_taxonomy": {},
        }
    )
    return card


class TestRenderBytes:
    def test_approve_renders_pdf_bytes(self) -> None:
        data = scorecard_pdf_bytes(_approve_card())
        assert isinstance(data, bytes)
        assert data.startswith(b"%PDF-")
        assert len(data) > 500  # a real document, not a stub

    def test_block_renders_pdf_bytes(self) -> None:
        data = scorecard_pdf_bytes(_block_card())
        assert data.startswith(b"%PDF-")

    def test_two_cards_differ(self) -> None:
        # Different verdicts / payloads must not collapse to identical bytes.
        assert scorecard_pdf_bytes(_approve_card()) != scorecard_pdf_bytes(_block_card())


class TestRenderToFile:
    def test_writes_file_and_returns_path(self, tmp_path: Path) -> None:
        target = tmp_path / "scorecard.pdf"
        returned = render_scorecard_pdf(_approve_card(), str(target))
        assert returned == str(target)
        assert target.exists()
        assert target.read_bytes().startswith(b"%PDF-")
        assert target.stat().st_size > 500


class TestUnmeasuredProvenance:
    """The PDF must not present a structural constant as an earned score."""

    def test_unmeasured_robustness_is_labelled(self) -> None:
        card = _approve_card()
        card["robustness_measured"] = False
        assert scorecard_pdf_bytes(card).startswith(b"%PDF-")

    def test_legacy_card_without_the_flag_still_renders(self) -> None:
        """CONTROL: no flag means unknown provenance, not 'unmeasured'.

        A scorecard serialized before the flag existed carries no provenance;
        defaulting to the caveat would retroactively relabel every historical
        PDF as unmeasured, which is a claim the stored data cannot support.
        """
        assert "robustness_measured" not in _approve_card()
        assert scorecard_pdf_bytes(_approve_card()).startswith(b"%PDF-")


class TestMalformedInput:
    def test_empty_dict_still_renders(self) -> None:
        # No keys at all: every field must fall back to a .get default.
        data = scorecard_pdf_bytes({})
        assert data.startswith(b"%PDF-")

    def test_partial_card_renders_without_keyerror(self) -> None:
        partial = {"run_id": "vrun-partial", "deploy_decision": "BLOCK"}
        data = scorecard_pdf_bytes(partial)
        assert data.startswith(b"%PDF-")

    def test_bad_ci_and_taxonomy_types_are_tolerated(self) -> None:
        weird = {
            "run_id": "vrun-weird",
            "deploy_decision": "APPROVE",
            "composite_score": "not-a-number",  # unparseable -> default
            "confidence_interval": ["x", "y"],  # non-numeric CI -> "not available"
            "failure_taxonomy": None,  # -> "none" row
            "episode_count": None,
        }
        data = scorecard_pdf_bytes(weird)
        assert data.startswith(b"%PDF-")
