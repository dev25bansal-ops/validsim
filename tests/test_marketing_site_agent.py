"""Structural checks for the marketing landing page.

Verifies invariants that are cheap to break and expensive to miss: the document
renders, every in-page anchor resolves, there is exactly one h1, nothing needs
a network fetch, and the two quoted scoring figures still match the engine.

The last test is the important one. The landing page quotes "42.86" and "100.0"
as evidence of a real bug we fixed; if the scoring model ever changes again,
those numbers go stale and the page starts making a false claim. This fails
loudly instead.
"""

from __future__ import annotations

import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "marketing" / "index.html"
html = PAGE.read_text(encoding="utf-8")


def test_page_exists_and_is_substantial() -> None:
    assert PAGE.exists(), f"landing page missing at {PAGE}"
    assert len(html) > 8_000, "page is too small to be the real landing page"


def test_document_skeleton_is_closed() -> None:
    for tag in ("html", "head", "body", "main", "header", "footer"):
        assert f"<{tag}" in html, f"missing opening <{tag}>"
        assert f"</{tag}>" in html, f"missing closing </{tag}>"


def test_every_anchor_resolves() -> None:
    ids = set(re.findall(r'id="([^"]+)"', html))
    targets = set(re.findall(r'href="#([^"]+)"', html))
    assert targets, "the page has no in-page navigation at all"
    broken = targets - ids
    assert not broken, f"anchors point at ids that do not exist: {sorted(broken)}"


def test_exactly_one_h1() -> None:
    """More than one h1 breaks the document outline; zero breaks the page."""
    assert html.count("<h1") == 1, f"expected exactly one <h1>, found {html.count('<h1')}"


def test_every_section_has_a_heading() -> None:
    """A section without a heading is invisible to a screen reader's rotor."""
    sections = re.findall(r"<section[^>]*>(.*?)</section>", html, re.S)
    assert len(sections) >= 5, f"expected a real page, found {len(sections)} sections"
    for body in sections:
        assert re.search(r"<h[123][ >]", body), "a section has no heading"


def test_page_has_no_images_or_external_stylesheets() -> None:
    """Icons are inline SVG; styling is inline. No <img>, no <link rel=stylesheet>.

    The page DOES fetch a webfont pair from Google Fonts (see
    test_webfont_loading_is_safe) - that is the single deliberate external
    resource, and it is a font, not a layout dependency.
    """
    assert "<img" not in html, "found <img>; icons should be inline SVG"
    assert 'rel="stylesheet"' not in html, "page pulls an external stylesheet"
    assert "<style>" in html, "page has no inline stylesheet"


def test_webfont_loading_is_safe() -> None:
    """If a webfont is used, it must not cause a flash or block first paint.

    font-display: swap is mandatory - without it a slow font request blocks
    text rendering entirely. The metric-matched @font-face fallback limits the
    reflow when the swap happens.
    """
    assert "@import" in html or "@font-face" in html, (
        "expected a webfont; distinctive typography is the main defence against "
        "an AI-generated look"
    )
    assert "display=swap" in html, (
        "webfont loaded without font-display: swap, so text is invisible until "
        "the font resolves"
    )
    assert "Space Grotesk Fallback" in html, (
        "no metric-matched fallback declared; the font swap will reflow the page"
    )
    assert "fonts.gstatic.com" in html and 'rel="preconnect"' in html, (
        "webfont host is not preconnected, adding a round-trip to first paint"
    )


def test_degrades_without_javascript() -> None:
    """No *executable* script. JSON-LD is inert data the browser never runs.

    The page must render and behave with JavaScript disabled: scroll reveals are
    native CSS, FAQ disclosure is <details>/<summary>, and there is no theming
    to wire up. JSON-LD structured data is permitted because it is metadata.
    """
    for attrs in re.findall(r"<script\b([^>]*)>", html):
        assert "application/ld+json" in attrs, (
            f"an executable <script> was added: <script{attrs}>. The page must "
            "work with JavaScript disabled"
        )


def test_honesty_caveats_are_present() -> None:
    """The two highest-risk claims are only defensible while caveated.

    Pricing is a hypothesis until customers disagree, and the company is
    pre-traction. Both statements are load-bearing for credibility, so a copy
    edit that drops one fails here rather than in a reviewer's inbox.
    """
    assert "hypothesis" in html.lower(), (
        "the pricing section must be labelled a hypothesis until customers disagree"
    )
    assert "pre-traction" in html.lower(), (
        "the page must state its traction status rather than implying customers"
    )


def test_quoted_verdict_figures_still_match_the_engine() -> None:
    """The integrity section quotes two real numbers. Keep them honest.

    "42.86 and blocks at any threshold" is the bug we found; "a flawless run
    still scores 100" is the control proving the fix did not break the happy
    path. If the scoring model changes, this test fails and the page copy must
    be corrected rather than quietly going stale.
    """
    from validsim.config import EnvironmentSpec, RobotSpec, TaskConfig
    from validsim.engine.evaluation import evaluate
    from validsim.engine.safety import compute_safety
    from validsim.engine.scorecard import build_scorecard
    from validsim.sim.runner import EpisodeResult

    task = TaskConfig(
        task_id="t",
        robot=RobotSpec(name="r"),
        environment=EnvironmentSpec(name="e"),
        episodes=100,
    )

    def score(success: bool) -> tuple[float, str]:
        episodes = [
            EpisodeResult(
                episode_id=f"e{i}",
                task_id="t",
                seed=i,
                success=success,
                randomization_level="full",
                failure_mode=None if success else "collision",
            )
            for i in range(100)
        ]
        card = build_scorecard(
            run_id="vrun-00000001",
            checkpoint_id="c",
            task=task,
            evaluation=evaluate(episodes),
            safety=compute_safety(episodes),
            episodes=episodes,
            threshold=0.0,
        )
        return round(card.composite_score, 2), card.deploy_decision

    failing_score, failing_verdict = score(False)
    perfect_score, _ = score(True)

    assert (failing_score, failing_verdict) == (42.86, "BLOCK"), (
        f"page quotes 42.86/BLOCK for an all-failure run, engine now produces "
        f"{failing_score}/{failing_verdict}"
    )
    assert perfect_score == 100.0, (
        f"page claims a flawless run still scores 100, engine now gives {perfect_score}"
    )
    assert "42.86" in html, "the integrity section no longer quotes the figure"
