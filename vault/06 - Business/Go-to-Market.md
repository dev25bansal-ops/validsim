---
tags:
  - business
  - gtm
status: complete
created: 2026-09-18
area: "06 - Business"
---

# 🚀 Go-to-Market Strategy

Three phases (§9), sequenced exactly like [[Buyer Tiers]]: Tier 1 labs → developer community → Tier 3/4 enterprise & insurers.

## Phase 1 — Design Partners (Months 1–4)

| Action | Detail |
|---|---|
| **Target** | 5–10 robot FM labs (Physical Intelligence, Skild AI, 1X, Figure AI, Apptronik) |
| **Offer** | Free validation runs (up to 50/month) in exchange for feedback + **LOI** |
| **Channel** | Direct outreach, NVIDIA Inception network, robotics conferences |
| **Cadence** | **10 discovery calls/week (Weeks 1–4), 5/week (Weeks 5–12)** |
| **Discovery question** | *"How do you validate a model update before deploying it to your fleet?"* |
| **Goal** | **2–3 signed LOIs or paid pilots before YC application** |

> [!important] LOI quality bar
> Written commitments **with company names and success criteria** — the [[90-Day Roadmap]] Phase-3 failure mode is literally "free pilots with no committed success criteria." Use the `Discovery Call` and `Decision Log` templates for every interaction.

## Phase 2 — Developer-Led Growth (Months 4–9)

| Action |
|---|
| Open-source **CLI tool + GitHub Actions plugin** (MIT license) |
| Publish **benchmark results on public models** (GR00T, pi0, OpenVLA) |
| Technical blog: *"How we found 12 regression bugs in a VLA model"* |
| Present at **NVIDIA GTC, ICRA, RSS, CoRL** |
| **Discord/Slack community** for robotics developers |
| **Goal: 500+ GitHub stars, 50+ signups, 10 paying teams** |

The Actions PR comment is the loop that compounds: every validated push advertises ValidSim inside the customer's own repo ([[GitHub Actions Integration]]).

## Phase 3 — Enterprise & Compliance (Months 9–18)

| Action |
|---|
| Target **fleet operators, 3PLs, enterprise manufacturers** (Amazon Robotics, GXO, DHL, BMW, Mercedes, Toyota, Foxconn, Samsung) |
| Sell **compliance evidence packages, safety scorecards, deployment gating** |
| Partner with **insurers (Munich Re, Swiss Re)** and **certification bodies (UL, TÜV)** |
| **Enterprise sales, NVIDIA co-sell**, integrator partnerships |
| **Goal: $500K–$2M ARR** |

## Sales motion by tier (§9.4)

| Tier | Motion | Cycle Length |
|---|---|---|
| Free / Developer | Self-serve, product-led | Instant |
| Team / Pro | Product-led + light sales touch | 1–2 weeks |
| Enterprise | Sales-led, pilot → contract | 1–3 months |

Tier ↔ price mapping: [[Pricing Tiers]].

## Why this order (and not enterprise-first)

1. **Tier-1 labs are the only buyers with pain today** — weekly shipping cadence ([[Why Now (2026)]] Force 2); fleet operators' pain arrives with 2027–2029 volume
2. **Developer artifacts are also fundraising artifacts** — stars, benchmarks, and the blog post double as YC "public artifact" evidence ([[YC Countdown]])
3. **Bottom-up adoption de-risks slow enterprise sales** — Risk #5 mitigation: "enterprise follows bottom-up" ([[Risk Register]])
4. **Insurers are partners, not prospects, in Phase 3** — co-design the evidence standard rather than sell them software ([[Compliance]], [[Moat]])

## Phase → metric dashboard

| Phase | Leading indicators | Lagging target |
|---|---|---|
| 1 | Calls/week, quoted pain, named targets | 2–3 LOIs; 3 design partners by M6 ([[KPIs]]) |
| 2 | GitHub stars, signups, free-tier runs | 10 paying teams; 500 runs by M6 |
| 3 | Enterprise pilots, insurer meetings | $500K–$2M ARR; 15 paying by M12 |

Links: [[Buyer Tiers]] · [[Pricing Tiers]] · [[Business Model]] · [[90-Day Roadmap]] · [[Home]]
