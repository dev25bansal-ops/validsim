---
tags:
  - fundraising
  - nvidia
  - accelerator
status: active
created: 2026-09-18
area: "07 - Fundraising"
---

# 🟩 NVIDIA Inception Program

§14.1. The accelerator track that runs **parallel** to YC (apply Week 1–2, not either/or) and scores **10/10 fit** in [[Strategic Advantages]] — native Isaac/Omniverse use case.

## Program facts

| Field | Detail |
|---|---|
| **Eligibility** | Incorporated, <10 years old, ≥1 developer, working website ✅ |
| **Cost** | Free |
| **Benefits** | **$100K DGX Cloud credits**, **$100K AWS credits**, up to **$150K Nebius credits**, early SDK access, GTC visibility |
| **Application tip** | Name the GPU-accelerated workload explicitly (below) |
| **Timeline** | Apply immediately after incorporation; approval in days to weeks |

## The application line that gets you in

> [!quote] Explicit GPU workload statement
> *"Large-scale Isaac Lab simulation for robot policy validation running thousands of parallel GPU episodes."*

Why it works: it maps 1:1 to Inception's eligibility requirement of a GPU-accelerated workload — 1,000–100,000 parallel episodes on A100/H100 ([[Module Specs]] M2, [[Tech Stack]]).

## Sequencing (from Week 1)

| Step | When | Dependency |
|---|---|---|
| Incorporate Delaware C-Corp | Sprint W1, day 5 | [[Corporate Structure]] |
| Website live (validsim.com) | W1–W2 | Eligibility requires working website |
| **Submit Inception application** | Immediately after incorporation (roadmap weeks 1–2) | [[90-Day Roadmap]] Phase 1 exit criterion |
| Approval | Days–weeks after submission | — |
| DGX credits provisioned | Before/at sprint W2 benchmark | 4× A100 <30 min test ([[MVP Success Metrics]]) |
| Early SDK access (Cosmos etc.) | Year 1–2 | Unlocks [[MVP Non-Goals]] Cosmos row |
| GTC visibility + co-sell | Months 6–18 | [[Go-to-Market]] Phase 2–3 |

## What Inception is worth in dollars

```
$100K DGX Cloud  +  $100K AWS  +  up to $150K Nebius  ≈  up to $350K compute
Year 1 total cash burn: $200K–$350K  →  Year 1 is effectively compute-funded
```
([[Financial Projections]] credits-arbitrage callout.)

## Beyond credits — three strategic uses

1. **Distribution:** NVIDIA co-sell + GTC talks put us in front of every Isaac-using lab ([[Buyer Tiers]] Tier 1 = Isaac Sim users)
2. **Signal to YC:** "NVIDIA Inception member" is a third-party technical validation line in the application ([[YC Application]] traction checklist)
3. **Advisor channel:** ex-NVIDIA Isaac team member is a target advisor seat ([[Hiring Plan]] advisory table)

## Failure modes & fallbacks

| Risk | Fallback |
|---|---|
| Rejection / slow approval | AWS/GCP A100 pay-as-you-go + Nebius AI Lift direct ([[Tech Stack]] cloud fallback) |
| Credits exhausted mid-year | Pass-through pricing absorbs cost; batch scheduling ([[Risk Register]] #6, [[Business Model]]) |
| NVIDIA productizes the CI layer | Inception relationship is the hedge — position as ecosystem partner, not competitor ([[Risk Register]] #2, [[Moat]]) |

Links: [[Strategic Advantages]] · [[Tech Stack]] · [[YC Countdown]] · [[Funding Plan]] · [[Home]]
