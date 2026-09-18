---
tags:
  - business
  - economics
status: complete
created: 2026-09-18
area: "06 - Business"
---

# 🧮 Unit Economics (Target Year 2)

§8.4. The numbers that make ValidSim fundable rather than merely interesting.

## Target metrics

| Metric | Value |
|---|---|
| Average revenue per customer (blended) | **$3,000/mo** |
| CAC (developer-led, low-touch) | **$5,000–$15,000** |
| LTV (24-month retention) | **$72,000** |
| LTV/CAC | **5–14×** |
| Gross margin | **70–80%** |
| Payback period | **2–5 months** |
| Net revenue retention (target) | **>120%** |

## The math behind each line

> [!note] Derivations — sanity-check these in every [[Financial Projections]] update
> - **LTV $72,000** = $3,000/mo × 24 months retention
> - **LTV/CAC 5–14×** = $72,000 ÷ $15,000 (worst) … ÷ $5,000 (best)
> - **Payback 2–5 months** = CAC ÷ monthly gross profit ($3,000 × 70–80% GM ≈ $2,100–$2,400/mo)
> - **CAC $5K–$15K** holds only because the motion is developer-led: free tier → GitHub Actions installs → self-serve upgrade ([[Go-to-Market]] Phase 2, [[Product Principles]] #6)

## Why CAC stays low (structural, not aspirational)

1. **Zero-config entry** — the $0 trial is a `pip install` + one push, not a pilot project ([[CLI Design]])
2. **PR comments advertise the product** inside customer repos ([[GitHub Actions Integration]])
3. **NVIDIA Inception network + conferences** (GTC, ICRA, RSS, CoRL) supply warm Tier-1 intros ([[NVIDIA Inception]], [[Go-to-Market]])
4. **Public benchmarks on GR00T/pi0/OpenVLA** convert researcher traffic into lab signups ([[Key Figures]] — those labs raised $3.92B in 2026; they have budget)

## Why NRR >120% is credible

Four automatic expansion axes in [[Business Model]]: seats (5–20 runs/mo/engineer), episodes (1K→100K), embodiments (1→10→unlimited), compliance reports ($50 each). Robot fleets grow linearly; validation spend grows with them ("scaling risk grows linearly with fleet size" — [[Pain Quantified]]).

## Sensitivity analysis — what breaks the model

| Assumption stress | Impact | Mitigation |
|---|---|---|
| Labs build in-house (Risk #1) | CAC spikes; some segments unbuyable | Price under the $200K–$500K internal build; target labs without sim expertise ([[Risk Register]]) |
| GPU prices rise / credits exhaust (Risk #6) | GM < 70% | Pass-through +20% keeps margin % intact; multi-cloud fallback A100/H100 ([[Tech Stack]]) |
| Enterprise cycle stretches past 3 months (Risk #5) | Payback > 5 months | Developer-led bottom-up pull; Team tier revenue while enterprise pilots bake ([[Go-to-Market]]) |
| Retention < 24 months | LTV < $72K, LTV/CAC < 5× | Deployment gating makes ValidSim load-bearing; audit trail creates lock-in ([[Moat]], [[Product Principles]] #4) |

## Investor framing

> [!quote] One-liner for the [[Investor Narrative]]
> "A $3K/mo customer who costs <$15K to acquire, pays back in under 5 months, and expands >20%/year because their robot fleet — and their model-update cadence — never slow down."

Links: [[Pricing Tiers]] · [[Business Model]] · [[Financial Projections]] · [[KPIs]] · [[Home]]
