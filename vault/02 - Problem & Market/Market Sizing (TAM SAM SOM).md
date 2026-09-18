---
tags:
  - market
  - sizing
status: complete
created: 2026-09-18
area: "02 - Problem & Market"
---

# 📐 Market Sizing — TAM / SAM / SOM

## 3.1 Total Addressable Market (TAM)

| Segment | Size | Growth | Source |
|---|---|---|---|
| Humanoid robot market (2033) | **$40.5B** | 38.2% CAGR | Grand View Research |
| Edge AI market (2033) | **$118.7B** | 21.7% CAGR | Grand View Research |
| Robot software & services (est. 15–20% of robot market) | **$6–8B by 2033** | — | Derived |
| Robot validation/tooling (est. 5–8% of robot software) | **$300M–$640M by 2033** | — | Derived |
| Robot FM company funding (2026 alone) | **$3.92B across 9 deals** | — | New Market Pitch |
| Total robotics VC funding (2025) | **$27.6B across 1,009 deals** | 237% YoY | PitchBook |
| Humanoid robotics funding (2026 YTD) | **$8.7B** | ~2× 2025 full year | Dealroom |

> [!note] Derivation chain to defend in interviews
> Humanoid market $40.5B × 15–20% software share = $6–8B robot software × 5–8% validation/tooling share = **$300M–$640M** direct TAM for us by 2033. Funding rows are *leading indicators*, not TAM — they size the customer-creation machine ([[Demand Signals]]).

## 3.2 Serviceable Addressable Market (SAM)

Every deployed robot fleet implies **recurring validation, observability, and compliance spend** — analogous to how cloud spend created the CI/CD industry.

| Customer Segment | Count (2026) | Count (2028) | Count (2030) | Avg. Annual Spend |
|---|---|---|---|---|
| Robot FM labs | 50–100 | 150–200 | 300–500 | $50K–$500K |
| Humanoid OEMs | 30–50 | 100–200 | 200–500 | $100K–$1M |
| Fleet operators | 50–100 | 300–500 | 1,000–2,000 | $20K–$200K |
| Integrators | 100–200 | 500–1,000 | 2,000–5,000 | $10K–$100K |
| Insurers/compliance | 10–20 | 50–100 | 100–200 | $50K–$500K |
| **SAM (annual)** | **$15M–$80M** | **$100M–$400M** | **$300M–$1B+** | |

Segment definitions and named companies: [[Buyer Tiers]].

## 3.3 Serviceable Obtainable Market (SOM)

| Year | Target Customers | ARR Target |
|---|---|---|
| Year 1 (post-YC) | 5–15 design partners → 3–8 paying | **$100K–$500K** |
| Year 2 | 30–50 paying customers | **$500K–$2M** |
| Year 3 | 100–200 paying customers | **$5M–$10M** |

Cross-check against bottom-up model in [[Financial Projections]] (Year 3: $6M–$12M ARR at 100–200 customers × $5,000 avg MRR).

## 3.4 Market analogies (for the investor narrative)

| Software Era | Robotics Era Equivalent |
|---|---|
| GitHub Actions, CircleCI, Jenkins (CI/CD) | **Us** (Sim-to-Real CI/CD) |
| Datadog, New Relic, Grafana (Monitoring) | Formant, Viam (but pre-humanoid) |
| MLflow, W&B, Neptune (Model Registry) | Us (checkpoint tracking + validation) |
| Selenium, Cypress, TestRail (Testing/QA) | **Us** (sim-based validation) |
| Vanta, Drata, OneTrust (Compliance/SOC2) | **Us** (robot safety evidence) |
| PagerDuty, OpsGenie (Incident Mgmt) | Teleoperation escalation |

> [!important] Key insight
> The CI/CD market for software was **$0 in 2010** and is now a **$10B+** category. The robot CI/CD market is at **$0 in 2026**. We are building at the inflection point.

Told as a story: [[Investor Narrative]]. Scored 7/10 on market size (large but not hyperscale-today): [[Strategic Advantages]].

Links: [[Demand Signals]] · [[Buyer Tiers]] · [[Business Model]] · [[Financial Projections]] · [[Home]]
