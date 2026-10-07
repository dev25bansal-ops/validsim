# ValidSim Design System

> **Scope:** this document covers the browser dashboard, not scorecard exports.
> The dashboard is implemented by `validsim/web/styles.css` and
> `validsim/web/index.html`. The self-contained HTML and print-PDF scorecards are
> independent renderer-owned output profiles described below.

## Source-of-truth map

| Surface | Implementation | Source of truth |
|---------|----------------|-----------------|
| FastAPI browser dashboard | `validsim/web/` | `validsim/web/styles.css` |
| HTML scorecard (`report --format html`) | `validsim.engine.export.scorecard_to_html` | the renderer's `_CSS` in `validsim/engine/export.py` |
| PDF scorecard | `validsim.engine.pdf.scorecard_pdf_bytes` / `render_scorecard_pdf` | constants and layout in `validsim/engine/pdf.py` |

There is currently no shared machine-readable token file and no CSS-framework or
component-library scaffold. Do not assume that a value in the dashboard is an
alias of a similarly named export value. The two export profiles also use
different colour palettes and typography, as detailed below.

## Dashboard profile

`validsim/web/styles.css` is the executable dashboard contract. Its core values
are:

- **Primary:** `#1E40AF`
- **Secondary:** `#3B82F6`
- **CTA/accent:** `#F59E0B`
- **Body typography:** Fira Sans
- **Heading and numeric typography:** Fira Code
- **Spacing:** 4, 8, 16, 24, 32, and 48 px
- **Theme:** dark OLED by default, with an explicit light-theme override
- **Motion:** 200 ms ease transitions, with reduced-motion handling in CSS

Colour is never the only status signal. Keyboard focus, contrast, icons, and
text labels follow the accessibility rules in `validsim/web/styles.css`.

### Page guidance

No page-specific override exists. This directory currently contains only this
master document; the `pages/` directory is empty. There is no
`design-system/pages/` override convention to apply.

## HTML scorecard profile

`validsim.engine.export.scorecard_to_html` emits a self-contained email/PDF
distribution card. It deliberately does **not** load the dashboard CSS, Google
fonts, or any framework:

- **Body font:** system stack (`-apple-system`, `Segoe UI`, `Roboto`, `Helvetica`,
  `Arial`, sans-serif)
- **APPROVE header:** `#1a7f37`
- **BLOCK header:** `#b42318`
- **Body text/background:** `#1a1a1a` on `#f6f7f9`
- **Card surface:** `#fff`
- **Muted text:** `#667085`

These values currently differ from the dashboard palette and typography. They
are legacy export values, not aliases of dashboard design tokens. Changing them
would change archived or emailed scorecards and therefore requires an explicit
visual-compatibility decision.

## PDF scorecard profile

`validsim.engine.pdf.scorecard_pdf_bytes` and `render_scorecard_pdf` render a
one-page A4 document directly with ReportLab. They do not consume the HTML
export or dashboard CSS:

- **Fonts:** built-in `Helvetica` and `Helvetica-Bold`; no external font files
- **APPROVE banner:** `#1E40AF`
- **BLOCK banner:** `#B91C1C`
- **Table header:** `#1E3A8A`
- **Muted text:** `#667085`
- **Alternating row:** `#F1F5F9`
- **Table grid:** `#CBD5E1`

The PDF's APPROVE colour matches the dashboard primary; its font choice,
BLOCK colour, and surfaces are export-specific. All verdicts are encoded in
both text and colour.

## Verification and change policy

To verify a profile, compare it directly with the source named in the map:

1. dashboard tokens and component rules: `validsim/web/styles.css`
2. HTML colours/font stack: `validsim.engine.export._CSS`
3. PDF colours/fonts/layout: `validsim.engine.pdf`

An automated cross-profile token-equality test would be misleading while these
are separate profiles: Markdown and CSS are not a shared token schema, and CSS
pixels and ReportLab points are different units. Add parity checks only after
unification is approved and the output is represented by structured tokens.
Any future unification must include before/after visual and byte-level export
checks; no parity between HTML and PDF is assumed.
