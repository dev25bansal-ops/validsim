"""Guard the aesthetic direction recorded in .impeccable.md.

Design decisions rot silently. A future edit that swaps Space Grotesk back to
system-ui, reintroduces an ambient glow, or turns the pipeline back into a card
grid would look fine in a diff and would quietly return the page to the generic
dark-SaaS look that every AI-generated landing page ships with.

These tests make the intent executable. They are deliberately opinionated: the
point is that "taste" is enforced, not left to whoever edits next.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "marketing" / "index.html"
CONTEXT = ROOT / ".impeccable.md"

html = PAGE.read_text(encoding="utf-8")
css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))


def test_design_context_is_recorded() -> None:
    """The Impeccable skill forbids inferring design context from the code.

    Context must be written down, or the next session guesses - and a guess is
    how a page ends up looking generic.
    """
    assert CONTEXT.exists(), (
        ".impeccable.md is missing; design context must be recorded, not inferred"
    )
    context = CONTEXT.read_text(encoding="utf-8")
    for required in ("Purpose", "Audience", "Brand personality", "Aesthetic direction"):
        assert required in context, f"design context does not cover {required!r}"


def test_typography_is_not_the_invisible_default() -> None:
    """Inter, Roboto and friends are the signature of AI-generated work.

    The page must name a deliberate typeface. system-ui alone is a fail: on most
    machines it resolves to exactly the font we are avoiding.
    """
    banned = ("Inter", "Roboto", "Open Sans", "Lato", "Montserrat", "-apple-system")
    font_stack = re.search(r"--font:\s*([^;]+);", css)
    assert font_stack, "no --font token defined"
    stack = font_stack.group(1)
    for name in banned:
        assert name not in stack, (
            f"--font uses {name!r}, the invisible default. Distinctive typography "
            "is the highest-leverage defence against a generic look"
        )
    assert "system-ui" not in stack.split(",")[0], (
        "--font leads with system-ui, so the display face is whatever the OS ships"
    )


def test_mono_is_reserved_for_data() -> None:
    """Monospace as decoration is lazy; monospace for data is instrument-grade.

    Allowed: figure numbers, run ids, scores, verdicts, the transcript.
    Not allowed: the site wordmark or navigation, which would be costume.
    """
    logo = re.search(r'<a class="logo".*?</a>', html, re.S)
    assert logo, "no logo element found"
    assert "--mono" not in logo.group(0), "the wordmark is set in monospace as costume"
    nav = re.search(r'<nav class="nav".*?</nav>', html, re.S)
    assert nav and "--mono" not in nav.group(0), "navigation is set in monospace as costume"
    # ...but the transcript and the figure labels must use it.
    assert "artifact" in html and "--mono" in css, "the data surfaces lost their monospace"


def test_no_ambient_glow() -> None:
    """A blue/purple radial wash behind dark content is the AI-slop signature.

    The instrument direction is explicit: light comes from the readout, not from
    a lamp behind the chassis. The only background treatment permitted is the
    engineering grid.
    """
    washes = re.findall(r"radial-gradient\([^)]*\)", css)
    assert not washes, (
        f"ambient radial glow reintroduced: {washes}. The chassis is lit by its "
        "own readout, not by a decorative wash"
    )
    # The grid must still be there - it is the intended texture.
    assert "linear-gradient(to right" in css, "the engineering-drawing grid was removed"


def test_accent_is_the_verdict_palette() -> None:
    """Green=APPROVE, red=BLOCK, amber=unmeasured: the palette is the semantics."""
    for token in ("--approve", "--block", "--unmeasured"):
        assert f"{token}:" in css, f"verdict token {token} is missing"
    # Amber carries the accent, not blue.
    assert re.search(r"\.btn-primary\s*\{[^}]*var\(--unmeasured\)", css), (
        "the primary action is not amber; the accent should be the instrument "
        "warning colour, not the default brand blue"
    )


def test_verdicts_never_rely_on_colour_alone() -> None:
    """WCAG 1.4.1: the verdict must be a word, not just a hue."""
    assert "readout-verdict" in html and ">BLOCK<" in html, (
        "the hero verdict must render the word BLOCK, not only a red colour"
    )


def test_there_is_exactly_one_focal_point() -> None:
    """Bold means one dramatic moment. A page where everything shouts, says nothing.

    The readout is the focal point: its type size must be far larger than any
    other text on the page.
    """
    readout = re.search(r"--t-readout:\s*clamp\(([^,]+),", css)
    hero = re.search(r"--t-hero:\s*clamp\(([^,]+),", css)
    assert readout and hero, "readout or hero type scale is missing"
    def floor(value: str) -> float:
        return float(re.sub(r"[^0-9.]", "", value))
    assert floor(readout.group(1)) >= floor(hero.group(1)) * 2, (
        "the readout must dwarf the headline; it is the single focal point"
    )


def test_layout_breaks_the_centred_column() -> None:
    """bolder.md: replace centred-everything with asymmetry.

    At least one multi-column layout must be asymmetric, and the closing
    section must not be centred.
    """
    assert re.search(r"grid-template-columns:\s*2fr 1fr", css), (
        "no asymmetric column split; the hero is a centred single column"
    )
    assert not re.search(r"\.close\s*\{[^}]*text-align:\s*center", css), (
        ".close is centre-aligned, which is the centred-everything default"
    )


def test_pipeline_is_not_a_card_grid() -> None:
    """Cards for everything is on the Impeccable DON'T list.

    The pipeline and pricing read as ruled rows, the way a spec sheet does.
    """
    assert "border-block-start: 1px solid var(--rule)" in css, (
        "ruled rows are gone; the pipeline/pricing sections fell back to boxes"
    )
    assert ".card" not in css, "a .card component reappeared; use ruled rows instead"


def test_no_glassmorphism_or_neon() -> None:
    """Frosted panels and neon accents are named AI-slop tells.

    A light backdrop-filter on the sticky header is a deliberate legibility
    measure over scrolling content, so the check is on the *heavy* variants
    that signal decorative glassmorphism, not on backdrop-filter itself.
    """
    lowered = css.lower()
    for tell in ("blur(16px)", "blur(24px)", "blur(32px)", "backdrop-filter: blur(40px)"):
        assert tell not in lowered, f"decorative glassmorphism tell: {tell!r}"
    for tell in ("neon", "text-shadow: 0 0", "0 0 20px", "0 0 40px"):
        assert tell not in lowered, f"neon/glow tell: {tell!r}"
    # The header's own 12px blur is load-bearing and must stay.
    assert "backdrop-filter: blur(12px)" in css, (
        "the sticky header lost its light backdrop-filter, which is load-bearing "
        "for legibility over scrolling content"
    )


def test_no_emoji_as_iconography() -> None:
    """Emoji in place of real icons reads as unconsidered."""
    emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", html)
    assert not emoji, f"emoji used as iconography: {set(emoji)}"


def test_readout_shows_a_real_run() -> None:
    """The focal point must be the measured number, matching the transcript.

    If the engine's output changes, this fails rather than letting the hero
    quietly become fiction.
    """
    assert ">72.7<" in html, "the hero readout no longer shows the real composite"
    assert "vrun-ed0c150a" in html, "the transcript run id changed"
    assert html.count("72.7") >= 2, (
        "the readout and the transcript disagree about the composite score"
    )


def test_verdict_is_not_colour_only_in_the_transcript() -> None:
    """BLOCK must appear as text in the transcript too."""
    assert "Decision:" in html and "BLOCK" in html, "the transcript lost its verdict"
