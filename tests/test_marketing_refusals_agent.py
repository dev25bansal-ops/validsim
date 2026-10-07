"""What the page deliberately does NOT do, and why.

The "Landing Page Generator" skill prescribes a standard template: Tailwind via
CDN, a dark-mode toggle, a testimonials section, a logo bar, a system font stack.
Adopting it wholesale would have made this page worse, and in one case actively
dishonest. These tests encode the refusals so a later editor does not "helpfully"
add them back.

The template also contained genuinely good ideas - an FAQ, and structured data.
Those were taken. This file guards the line between the two.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "marketing" / "index.html"
html = PAGE.read_text(encoding="utf-8")
css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))


def _jsonld() -> dict:
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    )
    assert blocks, "no JSON-LD structured data found"
    return json.loads(blocks[0])


# ---------------------------------------------------------------- refusals


def test_no_tailwind_cdn() -> None:
    """cdn.tailwindcss.com compiles in the browser at runtime.

    It is a prototyping convenience: ~100kb of JS, a flash of unstyled content,
    and runtime cost on every page view. This page ships no JavaScript to the
    browser at all (the only <script> is inert JSON-LD), which is a real
    differentiator for a company whose product IS a CI tool.
    """
    assert "cdn.tailwindcss.com" not in html, (
        "Tailwind's runtime CDN was added; it adds ~100kb JS, a FOUC, and breaks "
        "the zero-JavaScript property this page is built on"
    )
    assert "tailwind" not in html.lower(), "Tailwind reference found in the page"


def test_no_javascript_runs_on_the_page() -> None:
    """The only permitted <script> is JSON-LD, which the browser never executes.

    Scroll animation is native CSS (animation-timeline), theming is not needed
    (dark-only), and FAQ disclosure is <details>/<summary>. Nothing here needs
    a script, so nothing should ship one.
    """
    scripts = re.findall(r"<script\b([^>]*)>", html)
    for attrs in scripts:
        assert "application/ld+json" in attrs, (
            f"an executable <script> was added: <script{attrs}>. The page must "
            "render and behave with JavaScript disabled"
        )


def test_no_fabricated_social_proof() -> None:
    """The single most important refusal.

    The Landing Page Generator template calls for testimonials and a client logo
    bar. ValidSim has zero customers, zero pilots, zero LOIs - which the vault
    records explicitly. Inventing a quote from "Sarah, Staff Engineer at a
    leading robotics lab" would contradict the entire positioning of the page
    ("we tell you what we have actually measured") and would be found out in
    due diligence.
    """
    for tell in ("testimonial", "logo bar", "trusted by", "customers include"):
        assert tell not in html.lower(), (
            f"{tell!r} appears on the page. We have no customers; any social-proof "
            "section would be fabricated"
        )
    # Named companies appear only as competitive context, never as customers.
    assert "Physical Intelligence" not in html, (
        "a target customer is named as if it were a customer"
    )


def test_no_dark_mode_toggle() -> None:
    """We are dark-only by decision, recorded in .impeccable.md.

    A half-implemented light theme (or a toggle that flashes on load) is worse
    than none, and the operator dashboard this page matches is dark-only too.
    """
    assert "prefers-color-scheme" not in html, (
        "a light theme was added; the design context commits to dark-only"
    )
    assert "theme-toggle" not in html, "a theme toggle was added; we are dark-only"


# ------------------------------------------------------------------ accepts


def test_faq_uses_native_disclosure() -> None:
    """The template's best idea, taken - but implemented without JS."""
    assert "<details>" in html and "<summary>" in html, (
        "the FAQ should use native <details>/<summary>, which needs no script"
    )
    assert "onclick" not in html.lower(), "the FAQ is wired with inline JS"


def test_faq_answers_the_hard_question_honestly() -> None:
    """"Is this in production anywhere?" must be answered No.

    The FAQ exists partly to state the weakness before a reviewer finds it. If
    this is ever softened, the page stops being consistent with the application.
    """
    faq = html[html.find('id="faq"') : html.find('id="contact"')]
    assert "pre-traction" in faq.lower() or "no customers" in faq.lower(), (
        "the FAQ no longer admits the company has no customers"
    )
    assert "not measured" in faq.lower(), (
        "the FAQ lost the 'not measured' explanation, which is the most "
        "distinctive thing the product does"
    )


def test_structured_data_is_valid_and_matches_the_page() -> None:
    """JSON-LD must parse, and must not over-claim relative to the visible copy."""
    data = _jsonld()
    graph = data["@graph"]
    types = {node["@type"] for node in graph}
    assert types == {"Organization", "SoftwareApplication", "FAQPage"}, (
        f"unexpected structured data types: {types}"
    )

    faq_node = next(n for n in graph if n["@type"] == "FAQPage")
    questions = faq_node["mainEntity"]
    assert len(questions) >= 4, f"only {len(questions)} FAQ entries"

    # Every structured question must actually appear on the page. Schema that
    # describes content the visitor cannot see is a manual-action risk.
    for entry in questions:
        assert entry["name"] in html, (
            f"FAQPage declares a question not present in the page: {entry['name']!r}"
        )

    # The offer prices must match the pricing section, not drift from it.
    offers = next(n for n in graph if n["@type"] == "SoftwareApplication")["offers"]
    assert {o["name"] for o in offers} == {"Developer", "Team", "Pro"}
    assert "$2k" in html and "$8k" in html, "pricing copy changed; update the JSON-LD offers"


def test_print_never_hides_content() -> None:
    """A scroll-driven animation never advances in a print context.

    Without an explicit override every below-the-fold section would print blank,
    because the timeline sits at its `from` keyframe (opacity 0). This is the
    concrete form of the "never leave content invisible" rule.
    """
    assert "@media print" in css, "no print stylesheet"
    print_block = css[css.find("@media print") :]
    assert "opacity: 1 !important" in print_block, (
        "print does not force .reveal visible; entrance animations would blank "
        "every below-the-fold section on paper"
    )


def test_forced_colors_mode_still_shows_content() -> None:
    """High-contrast mode ignores opacity, but an opacity:0 start state can still
    strand content. Force it visible."""
    assert "forced-colors" in css, "no forced-colors handling for the reveal animation"
