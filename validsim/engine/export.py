"""Human-readable scorecard renderers: Markdown report and HTML card.

These exporters turn a frozen :class:`~validsim.engine.scorecard.Scorecard`
into artifacts suitable for CI job summaries (Markdown) and email/PDF
distribution (a self-contained HTML card with inline CSS and no external
assets). They are pure functions — no I/O, no network — and never mutate the
input.
"""

from __future__ import annotations

from html import escape

from validsim.engine.scorecard import Scorecard

__all__ = ["scorecard_to_markdown", "scorecard_to_html"]


def _fmt(value: float | None, spec: str = "{:.1f}", dash: str = "—") -> str:
    """Format an optional number, returning ``dash`` when the value is None."""
    return dash if value is None else spec.format(value)


def _ci_text(sc: Scorecard) -> str:
    """Render the 95% bootstrap confidence interval (or an em-dash)."""
    if sc.confidence_interval is None:
        return "not available"
    low, high = sc.confidence_interval
    return f"[{low:.4f}, {high:.4f}]"


def scorecard_to_markdown(sc: Scorecard) -> str:
    """Render ``sc`` as a professional Markdown validation report.

    Includes a header, the APPROVE/BLOCK verdict against the threshold, a
    metrics table, the failure-taxonomy table, and confidence-interval info.

    Args:
        sc: The scorecard to render.

    Returns:
        A Markdown document as a single string.
    """
    verdict = sc.deploy_decision
    relation = "meets or exceeds" if sc.deploy_decision == "APPROVE" else "is below"
    lines: list[str] = [
        "# ValidSim Validation Report",
        "",
        f"- **Run:** `{sc.run_id}`",
        f"- **Checkpoint:** `{sc.checkpoint_id}`",
        f"- **Task:** `{sc.task_id}`",
        f"- **Created:** {sc.created_at}",
        "",
        f"## Verdict: {verdict}",
        "",
        f"Composite score **{sc.composite_score}** {relation} the deploy "
        f"threshold of **{sc.threshold}**.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Composite score | {sc.composite_score} |",
        f"| Success rate | {sc.success_rate * 100:.1f}% |",
        f"| Safety score | {_fmt(sc.safety_score)} |",
        f"| Robustness score | {_fmt(sc.robustness_score)} |",
        f"| Regression delta | {_fmt(sc.regression_delta, '{:+.4f}')} |",
        f"| Episodes | {sc.episode_count} |",
        "",
        "## Failure Taxonomy",
        "",
    ]
    if sc.failure_taxonomy:
        lines += ["| Failure mode | Count |", "| --- | --- |"]
        lines += [f"| {mode} | {count} |" for mode, count in sc.failure_taxonomy.items()]
    else:
        lines.append("_No failures recorded._")
    lines += [
        "",
        "## Confidence Interval",
        "",
        f"95% bootstrap CI of success rate: {_ci_text(sc)}",
        "",
    ]
    return "\n".join(lines)


_CSS = (
    "body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    "margin:24px;color:#1a1a1a;background:#f6f7f9;}"
    ".card{max-width:640px;margin:0 auto;background:#fff;border-radius:12px;"
    "box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden;}"
    ".head{padding:20px 24px;color:#fff;}"
    ".approve{background:#1a7f37;}.block{background:#b42318;}"
    ".head h1{margin:0;font-size:20px;}.head p{margin:4px 0 0;opacity:.9;font-size:13px;}"
    ".body{padding:20px 24px;}table{width:100%;border-collapse:collapse;margin:8px 0 16px;}"
    "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eee;font-size:14px;}"
    "th{color:#667085;font-weight:600;}.score{font-size:34px;font-weight:700;margin:0;}"
    ".muted{color:#667085;font-size:13px;}"
)


def scorecard_to_html(sc: Scorecard) -> str:
    """Render ``sc`` as a self-contained, styled HTML card.

    All CSS is inlined (no external assets) so the output can be dropped into
    an email or rendered as a branded PDF by :mod:`validsim.engine.pdf`.
    Values are HTML-escaped.

    Args:
        sc: The scorecard to render.

    Returns:
        A complete ``<!DOCTYPE html>`` document as a single string.
    """
    tone = "approve" if sc.deploy_decision == "APPROVE" else "block"
    metric_rows = [
        ("Success rate", f"{sc.success_rate * 100:.1f}%"),
        ("Safety score", _fmt(sc.safety_score)),
        ("Robustness score", _fmt(sc.robustness_score)),
        ("Regression delta", _fmt(sc.regression_delta, "{:+.4f}")),
        ("Episodes", str(sc.episode_count)),
        ("95% CI", _ci_text(sc)),
    ]
    metric_html = "".join(
        f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>"
        for name, value in metric_rows
    )
    if sc.failure_taxonomy:
        tax_html = (
            "<h3>Failure Taxonomy</h3><table><tr><th>Mode</th><th>Count</th></tr>"
            + "".join(
                f"<tr><td>{escape(mode)}</td><td>{count}</td></tr>"
                for mode, count in sc.failure_taxonomy.items()
            )
            + "</table>"
        )
    else:
        tax_html = "<h3>Failure Taxonomy</h3><p class='muted'>No failures recorded.</p>"

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>ValidSim {escape(sc.run_id)}</title><style>{_CSS}</style></head>"
        "<body><div class='card'>"
        f"<div class='head {tone}'><h1>{escape(sc.deploy_decision)}</h1>"
        f"<p>Checkpoint {escape(sc.checkpoint_id)} &middot; Task {escape(sc.task_id)}</p></div>"
        "<div class='body'>"
        f"<p class='score'>{sc.composite_score}</p>"
        f"<p class='muted'>Composite score &middot; threshold {sc.threshold}"
        f" &middot; created {escape(sc.created_at)}</p>"
        f"<table><tr><th>Metric</th><th>Value</th></tr>{metric_html}</table>"
        f"{tax_html}"
        "</div></div></body></html>"
    )
