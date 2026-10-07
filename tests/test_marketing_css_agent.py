"""Verify the landing page's CSS claims are real rather than decorative.

The page advertises a specific stack - cascade layers, oklch, container
queries, color-mix, scroll-driven animation - in a comment at the top of the
stylesheet. A comment is a claim, not evidence, and the page degrades badly if
half those features are typos the browser silently drops.

These tests parse the stylesheet and assert each feature is genuinely present
and correctly scoped, so "modern stack, zero build step" stays a true statement.
"""

from __future__ import annotations

import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "marketing" / "index.html"
html = PAGE.read_text(encoding="utf-8")

style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
assert style_blocks, "no inline <style> block found"
css = "\n".join(style_blocks)


def test_stylesheet_is_balanced() -> None:
    """A missing brace makes the browser discard rules silently."""
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces: {css.count('{')} open, {css.count('}')} close"
    )
    assert css.count("(") == css.count(")"), "unbalanced parentheses in the stylesheet"


def test_cascade_layers_are_declared_and_used() -> None:
    """Layers must be declared up front AND actually used, or they do nothing."""
    declared = re.search(r"@layer\s+([a-z,\s-]+);", css)
    assert declared, "no @layer statement declaring the cascade order"
    order = [n.strip() for n in declared.group(1).split(",") if n.strip()]
    assert order == ["reset", "tokens", "base", "layout", "components", "utilities"], (
        f"unexpected cascade order: {order}"
    )
    for name in order:
        assert f"@layer {name} {{" in css, f"layer {name!r} is declared but never used"


def test_colour_is_defined_in_oklch() -> None:
    """oklch gives perceptually even lightness steps; hex ramps do not."""
    oklch_defs = re.findall(r"--[\w-]+:\s*oklch\(", css)
    assert len(oklch_defs) >= 12, (
        f"only {len(oklch_defs)} tokens defined in oklch; the palette should be "
        "perceptually uniform rather than hand-picked hex"
    )


def test_no_hex_colours_in_token_definitions() -> None:
    """Hex in :root would defeat the point of moving the palette to oklch."""
    root = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert root, "no :root block"
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", root.group(1))
    assert not hexes, f"hex colours left in :root: {hexes}"


def test_container_queries_are_used() -> None:
    """Components must size off their own container, not the viewport."""
    assert "@container" in css, "no container queries; cards still depend on the viewport"
    assert re.search(r"@container\s*\(width", css), (
        "container queries are declared but none test a width"
    )
    assert "container-type" in css, (
        "container queries are used but no element declares container-type, so "
        "they can never match"
    )


def test_scroll_animation_is_guarded_by_supports() -> None:
    """An unguarded scroll animation hides content where it is unsupported.

    animation-timeline is still not universal. Without the @supports wrapper a
    browser that cannot parse it applies the `from` keyframe unconditionally and
    the section stays at opacity 0 - invisible content, which is far worse than
    no animation at all.
    """
    assert "animation-timeline" in css, "no scroll-driven animation claimed"
    guarded = re.search(
        r"@supports\s*\(animation-timeline:\s*view\(\)\)\s*\{", css
    )
    assert guarded, (
        "animation-timeline is used outside an @supports guard; browsers that "
        "cannot parse it will leave the animated elements invisible"
    )
    # The reveal class must not set opacity:0 in the base (unguarded) rules.
    base_reveal = re.search(r"\.reveal\s*\{[^}]*\}", css)
    if base_reveal:
        assert "opacity" not in base_reveal.group(0), (
            ".reveal sets opacity outside the @supports guard, so unsupported "
            "browsers render nothing"
        )


def test_color_mix_is_used_for_derived_tints() -> None:
    """Tints should be derived, so one hue change propagates everywhere."""
    uses = len(re.findall(r"color-mix\(\s*in oklch", css))
    assert uses >= 4, (
        f"only {uses} color-mix() uses; derived tints should come from the "
        "tokens rather than being hand-written per rule"
    )


def test_layer_ordering_is_actually_respected() -> None:
    """utilities must be declared last or they lose to component rules."""
    declared = re.search(r"@layer\s+([a-z,\s-]+);", css)
    assert declared
    order = [n.strip() for n in declared.group(1).split(",") if n.strip()]
    assert order[-1] == "utilities", f"utilities is not last in the cascade: {order}"


def test_responsive_breakpoints_are_minimal() -> None:
    """A component-driven layout should need very few viewport queries."""
    queries = re.findall(r"@media\s*\(\s*width\s*<", css)
    assert len(queries) <= 2, (
        f"{len(queries)} viewport breakpoints; this layout is container-driven "
        "and should not need many"
    )


def test_page_declares_its_own_constraints() -> None:
    """The header comment documents the stack; keep it honest about no-JS."""
    header = css[: css.find("*/") + 2]
    assert "no build step" in header.lower() or "no dependencies" in header.lower(), (
        "the stylesheet's own stack note should state the zero-build constraint"
    )
