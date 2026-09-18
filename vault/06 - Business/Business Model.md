---
tags:
  - business
  - revenue
status: complete
created: 2026-09-18
area: "06 - Business"
---

# 💰 Business Model

Hybrid **SaaS + usage-based** (§8.1). The structural bet: *every model update = new validation run = recurring spend* — the cadence in [[Why Now (2026)]] Force 2 converts directly into revenue.

## Revenue streams

| Revenue Stream | Model | Description |
|---|---|---|
| **Platform Subscription** | Monthly SaaS | Access to dashboard, API, CLI, integrations |
| **Validation Runs** | Pay-per-run | Each validation run costs based on episode count |
| **Compute Pass-Through** | Usage-based | GPU compute cost **+ 20% margin** |
| **Enterprise Compliance** | Annual contract | Compliance evidence packages, SLA, dedicated support |
| **API/Integration Licensing** | Per-seat or per-robot | For fleet operators integrating via API |

## Why hybrid beats pure SaaS here

> [!important] Price must track physics
> A 100-episode smoke test and a 100,000-episode fleet certification consume wildly different GPU resources. Flat-rate SaaS would either over-charge researchers (killing the [[Go-to-Market]] Phase 2 developer funnel) or under-charge OEMs (leaving the $100K–$1M Tier-2 spend on the table, [[Buyer Tiers]]). Subscription = access floor; per-run + pass-through = value ceiling.

## Stream → buyer mapping

| Buyer ([[Buyer Tiers]]) | Primary streams | Entry tier |
|---|---|---|
| Tier 1 FM labs | Subscription + validation runs | Team / Pro |
| Tier 2 OEMs | Subscription + compute pass-through + API licensing | Pro |
| Tier 3 fleet operators | Per-robot API licensing + runs | Pro → Enterprise |
| Tier 4 insurers/regulators | Enterprise compliance contracts (annual) | Enterprise |

## Margin structure

- Gross margin target **70–80%** ([[Unit Economics]]) — GPU pass-through at +20% margin is the dilutive line; software lines carry 90%+
- Credits strategy keeps early COGS near zero: $100K DGX + $100K AWS + up to $150K Nebius ([[NVIDIA Inception]], [[Financial Projections]])
- Cost risk: Risk #6 compute exceeding credits → mitigated by batch scheduling + pass-through pricing ([[Risk Register]])

## Expansion mechanics (NRR >120%)

1. **Seats:** ML engineers run 5–20 validations/month each ([[User Personas]]) — usage grows with lab headcount automatically
2. **Episodes:** 1K (Free) → 5K (Team) → 10K (Pro) → 100K runs at $750 each ([[Pricing Tiers]])
3. **Embodiments:** 1 → 3 → 10 → unlimited — the multi-robot ask from design partners is upsell, not roadmap debt ([[MVP Non-Goals]])
4. **Compliance:** ISO evidence packages at $50/report à la carte or annual Enterprise contract ([[Compliance]])

## What the model is NOT

- ❌ Not a simulation marketplace or scene store — scenes are a means, the **scorecard decision** is the SKU ([[Product Principles]] #4)
- ❌ Not project-based consulting for labs — bespoke work would recreate the $200K–$500K internal-tooling problem we sell against ([[Pain Quantified]])
- ❌ Not hardware revenue — pure software margin profile ([[Strategic Advantages]])

Links: [[Pricing Tiers]] · [[Unit Economics]] · [[Financial Projections]] · [[Go-to-Market]] · [[Home]]
