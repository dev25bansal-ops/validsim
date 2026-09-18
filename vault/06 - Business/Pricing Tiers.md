---
tags:
  - business
  - pricing
status: complete
created: 2026-09-18
area: "06 - Business"
---

# 🏷️ Pricing Tiers

§8.2–8.3. Tiers ladder from free researcher to $50K/mo OEM; usage pricing above the ladder. All prices USD, monthly.

## Tier structure

| Tier | Price | Includes | Target |
|---|---|---|---|
| **Free / Developer** | **$0/mo** | 10 runs/mo, 1 embodiment, 1K episodes, community support | Researchers, OSS |
| **Team** | **$2,000/mo** | 100 runs, 3 embodiments, 5K episodes, dashboard, GitHub Actions, Slack support | Small labs (2–10 eng) |
| **Pro** | **$8,000/mo** | 500 runs, 10 embodiments, 10K episodes, adversarial, regression timeline, priority support | Mid-size companies |
| **Enterprise** | **Custom ($20K–$50K/mo)** | Unlimited, HIL, compliance reports, dedicated support, SLA, SSO | Large OEMs, fleet operators |

## Usage-based pricing (beyond tier limits)

| Item | Price |
|---|---|
| Additional validation run (5,000 episodes) | **$75** |
| Additional validation run (10,000 episodes) | **$150** |
| Additional validation run (100,000 episodes) | **$750** |
| Additional adversarial scenario batch (100) | **$25** |
| Additional GPU hour (A100) | **$3.50** |
| PDF compliance report generation | **$50** |

## Tier logic

> [!tip] Anchors that make $2,000/mo feel cheap
> - Team tier ≈ **1/100th** of the $200K–$500K a lab spends building bespoke validation ([[Pain Quantified]])
> - Pro tier ≈ **half of one hour** of $10K–$100K/hr production downtime it exists to prevent
> - Enterprise $20K–$50K/mo ($240K–$600K/yr) sits inside the Tier-2 OEM budget band of $100K–$1M/yr ([[Buyer Tiers]])

**Design rules:**
1. **Free tier is the funnel** — researchers publishing ValidSim benchmarks are Phase-2 marketing ([[Go-to-Market]]: public results on GR00T, pi0, OpenVLA)
2. **Episodes scale with trust, not just compute** — 1K → 5K → 10K → unlimited mirrors the statistical-confidence story ([[Product Principles]] #2)
3. **Adversarial + regression timeline gated at Pro** — these are the "caught a bug" features that justify the $8K step
4. **HIL, compliance, SSO, SLA at Enterprise** — the [[MVP Non-Goals]] list is literally the Enterprise feature roadmap

## Sales motion per tier

| Tier | Motion | Cycle |
|---|---|---|
| Free / Developer | Self-serve, product-led | Instant |
| Team / Pro | Product-led + light sales touch | 1–2 weeks |
| Enterprise | Sales-led, pilot → contract | 1–3 months |

(§9.4, [[Go-to-Market]])

## Blended economics check

- Blended ARPU target: **$3,000/mo** → Team/Pro mix with usage overage ≈ $36K/yr/customer → supports Year-2 avg MRR $4,000 and Year-3 $5,000 ([[Financial Projections]])
- LTV at 24-month retention: **$72,000**; LTV/CAC **5–14×** ([[Unit Economics]])
- GPU pass-through at $3.50/A100-hr carries the +20% margin line ([[Business Model]])

> [!warning] Pricing is a hypothesis until 3 LOIs disagree with it
> The [[90-Day Roadmap]] Phase-3 LOIs are the experiment: if Tier-1 labs balk at $2K/mo (they can "afford $500K internal tooling"), consider compute-credited pricing; log any change in the [[Decision Log]].

Links: [[Business Model]] · [[Unit Economics]] · [[Go-to-Market]] · [[KPIs]] · [[Home]]
