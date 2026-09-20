"""Branded PDF scorecard export.

Renders a frozen :class:`~validsim.engine.scorecard.Scorecard` (as its
``to_dict`` mapping) into a one-page, print-ready PDF via reportlab's
``platypus`` layout engine. The visual language mirrors the HTML/Markdown
renderers in :mod:`validsim.engine.export`: a branded header, an APPROVE /
BLOCK verdict banner, a metrics table, a failure-taxonomy table and a
CONFIDENTIAL footer.

Accessibility note (design-system/validsim/MASTER.md): the verdict is encoded
in *both* colour (``#1E40AF`` approve / ``#B91C1C`` block) and explicit text,
never colour alone.

reportlab is imported **lazily inside the render functions** so the rest of the
platform never hard-depends on PDF rendering: importing this module is always
safe, and only an actual render call raises when the optional dependency is
missing (surfaced as a friendly ``RuntimeError("pip install reportlab")``).
"""

from __future__ import annotations

import io
from typing import Any, Callable

from validsim import __version__
from validsim.engine._coerce import as_float

__all__ = ["render_scorecard_pdf", "scorecard_pdf_bytes"]

#: Verdict banner backgrounds (from the ValidSim design system).
_APPROVE_BG = "#1E40AF"  # primary blue
_BLOCK_BG = "#B91C1C"  # danger red
_TEXT_MUTED = "#667085"
_TABLE_HEADER_BG = "#1E3A8A"  # text-navy header band
_ROW_ALT_BG = "#F1F5F9"


def _num(value: Any, spec: str = "{:.2f}", dash: str = "\u2014") -> str:
    """Format an optional number with ``spec``; ``dash`` when absent/unparseable."""
    parsed = as_float(value, default=None)
    return dash if parsed is None else spec.format(parsed)


def _ci_text(ci: Any) -> str:
    """Render a 95% CI ``[low, high]`` pair, or ``not available`` when absent."""
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return "not available"
    low, high = as_float(ci[0], default=None), as_float(ci[1], default=None)
    if low is None or high is None:
        return "not available"
    return f"[{low:.4f}, {high:.4f}]"


def _build_story(scorecard: dict[str, Any]) -> tuple[list[Any], float]:
    """Construct the platypus flowables and the content width (in points).

    Every field is read defensively via ``.get`` with sensible defaults so a
    malformed / partial scorecard still renders rather than raising ``KeyError``.
    """
    # Imported lazily so the module has no hard reportlab dependency.
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - exercised only w/o reportlab
        raise RuntimeError("pip install reportlab") from exc

    run_id = str(scorecard.get("run_id", "\u2014"))
    checkpoint_id = str(scorecard.get("checkpoint_id", "\u2014"))
    task_id = str(scorecard.get("task_id", "\u2014"))
    created_at = str(scorecard.get("created_at", "\u2014"))
    decision = str(scorecard.get("deploy_decision", "BLOCK")).upper()
    composite = as_float(scorecard.get("composite_score"), 0.0)
    threshold = as_float(scorecard.get("threshold"), 85.0)
    success_rate = as_float(scorecard.get("success_rate"), 0.0)
    episodes = scorecard.get("episode_count", 0)
    taxonomy = scorecard.get("failure_taxonomy") or {}

    left = right = 18 * mm
    content_width = A4[0] - left - right

    # --- styles (Helvetica: reportlab built-in, no external font files) -------
    title_style = ParagraphStyle(
        "vsTitle", fontName="Helvetica-Bold", fontSize=16, leading=20,
        textColor=colors.HexColor(_APPROVE_BG), alignment=TA_LEFT,
    )
    sub_style = ParagraphStyle(
        "vsSub", fontName="Helvetica", fontSize=9, leading=12,
        textColor=colors.HexColor(_TEXT_MUTED),
    )
    banner_style = ParagraphStyle(
        "vsBanner", fontName="Helvetica-Bold", fontSize=13, leading=17,
        textColor=colors.white,
    )
    section_style = ParagraphStyle(
        "vsSection", fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=colors.HexColor(_APPROVE_BG), spaceBefore=6,
    )
    cell_style = ParagraphStyle("vsCell", fontName="Helvetica", fontSize=9, leading=12)

    story: list[Any] = [
        Paragraph("ValidSim \u2014 Validation Scorecard", title_style),
        Spacer(1, 2 * mm),
        Paragraph(
            f"Run <b>{run_id}</b> &nbsp;|&nbsp; Checkpoint {checkpoint_id} "
            f"&nbsp;|&nbsp; Task {task_id}",
            sub_style,
        ),
        Spacer(1, 5 * mm),
    ]

    # --- verdict banner (colour + text, never colour-only) --------------------
    is_approve = decision == "APPROVE"
    bg = colors.HexColor(_APPROVE_BG if is_approve else _BLOCK_BG)
    relation = "meets" if is_approve else "is below"
    banner_text = (
        f"VERDICT: {decision} \u2014 composite {composite:.2f} {relation} "
        f"threshold {threshold:.2f}"
    )
    banner = Table(
        [[Paragraph(banner_text, banner_style)]],
        colWidths=[content_width],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )
    story += [banner, Spacer(1, 6 * mm), Paragraph("Metrics", section_style)]

    # --- metrics table --------------------------------------------------------
    pct = success_rate * 100.0
    ci = scorecard.get("confidence_interval")
    regression_delta = scorecard.get("regression_delta")
    delta_text = "\u2014" if regression_delta is None else _num(regression_delta, "{:+.4f}")
    metric_rows: list[list[Any]] = [
        ["Metric", "Value"],
        ["Composite / threshold", f"{composite:.2f} / {threshold:.2f}"],
        ["Success rate", f"{pct:.1f}%  \u00b7  95% CI {_ci_text(ci)}"],
        ["Safety score", _num(scorecard.get("safety_score"))],
        ["Robustness score", _num(scorecard.get("robustness_score"))],
        ["Regression delta", delta_text],
        ["Episodes", str(episodes)],
        ["Created (UTC)", created_at],
    ]
    metrics = Table(
        metric_rows,
        colWidths=[content_width * 0.42, content_width * 0.58],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_TABLE_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_ROW_ALT_BG)]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )
    story += [metrics, Spacer(1, 6 * mm), Paragraph("Failure Taxonomy", section_style)]

    # --- failure taxonomy table ----------------------------------------------
    if taxonomy:
        tax_rows: list[list[Any]] = [["Failure mode", "Count"]]
        tax_rows += [
            [Paragraph(str(mode), cell_style), Paragraph(str(count), cell_style)]
            for mode, count in taxonomy.items()
        ]
    else:
        tax_rows = [["Failure mode", "Count"], ["none", "\u2014"]]
    taxonomy_table = Table(
        tax_rows,
        colWidths=[content_width * 0.7, content_width * 0.3],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_TABLE_HEADER_BG)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(_ROW_ALT_BG)]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )
    story.append(taxonomy_table)
    return story, content_width


def _make_footer() -> Callable[[Any, Any], None]:
    """Return an ``onPage`` callback drawing the branded CONFIDENTIAL footer."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    def _footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(_TEXT_MUTED))
        width = doc.pagesize[0]
        text = f"Generated by ValidSim v{__version__} \u00b7 CONFIDENTIAL"
        canvas.drawCentredString(width / 2.0, 10 * mm, text)
        canvas.restoreState()

    return _footer


def scorecard_pdf_bytes(scorecard: dict[str, Any]) -> bytes:
    """Render ``scorecard`` (a :class:`Scorecard` ``to_dict`` mapping) to PDF bytes.

    Args:
        scorecard: The scorecard mapping to render. Missing keys fall back to
            safe defaults so partial/malformed input still produces a document.

    Returns:
        The complete PDF document as a ``bytes`` blob (starts with ``%PDF``).

    Raises:
        RuntimeError: If reportlab is not installed (``pip install reportlab``).
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate
    except ImportError as exc:  # pragma: no cover - exercised only w/o reportlab
        raise RuntimeError("pip install reportlab") from exc

    story, _ = _build_story(scorecard)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=22 * mm,
        title=f"ValidSim Scorecard {scorecard.get('run_id', '')}".strip(),
        author="ValidSim",
    )
    footer = _make_footer()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def render_scorecard_pdf(scorecard: dict[str, Any], path: str) -> str:
    """Render ``scorecard`` to a branded one-page PDF written to ``path``.

    Args:
        scorecard: The scorecard mapping (see :class:`Scorecard.to_dict`).
        path: Destination filesystem path for the ``.pdf`` file.

    Returns:
        The ``path`` the document was written to.

    Raises:
        RuntimeError: If reportlab is not installed (``pip install reportlab``).
    """
    data = scorecard_pdf_bytes(scorecard)
    with open(path, "wb") as handle:
        handle.write(data)
    return path
