---
tags:
  - product
  - personas
status: complete
created: 2026-09-18
area: "03 - Product"
---

# 👥 User Personas

Five personas, one buying committee. The **ML Engineer** is the wedge user (first to feel pain, first to try a CLI); the others determine whether a free trial becomes a [[Pricing Tiers|Pro or Enterprise contract]].

## 5.1 Persona table

| Persona | Role | Pain | Goal | Frequency |
|---|---|---|---|---|
| **ML Engineer** | Trains VLA models at a robotics lab | Spends days building test scenes; no regression detection | Submit checkpoint, get scorecard in <1 hour | **5–20×/month** |
| **Robotics Lead** | Oversees deployment at an OEM | Needs safety evidence for enterprise customers | Generate compliance-ready safety report | 2–5×/month |
| **Fleet Operator** | Runs 50–500 robots at a warehouse | Needs to validate updates before fleet-wide rollout | Approve/block deployment with confidence | 1–3×/month |
| **CTO / VP Eng** | Decision-maker at robotics company | Needs visibility into model quality over time | Dashboard with regression timeline | Weekly |
| **Compliance Officer** | At enterprise customer or insurer | Needs deployment evidence in standard format | Downloadable, auditable safety report | Per deployment |

## Persona deep-dives

### ML Engineer — the wedge 🥇
- **Day in the life:** fine-tuned pi0/GR00T-class VLA for a new bin-picking task; test-scene success 94%; remembers the night-shift lighting failure; insurance broker wants a report ([[Problem Statement]] monologue).
- **Job to be done:** "Tell me with statistical confidence whether this checkpoint is safe to deploy — in under an hour."
- **Entry surface:** `validsim run` CLI or GitHub Action push trigger ([[CLI Design]], [[GitHub Actions Integration]]).
- **Aha moment:** Slack ping — *"Validation complete. Score: 87.3. 2 regressions detected."* ([[Core User Flows]] Flow 1).

### Robotics Lead — the evidence buyer
- Sells pilots to factories; blocked deals worth **$500K–$5M** each without safety evidence ([[Pain Quantified]]).
- Surface: "Generate Compliance Report" → ISO-aligned PDF ([[Compliance]], Flow 3).

### Fleet Operator — the gatekeeper
- Manages 50–500 robots; "testing on 1 robot ≠ testing on 1,000."
- Surface: Approve/Block deployment gate with threshold, e.g. `validsim gate --run-id abc123 --threshold 85` ([[Core User Flows]] Flow 2, [[CLI Design]]).

### CTO / VP Eng — the economic buyer
- Weekly regression-timeline review; wants trend, not tickets ([[Scorecard UX]]).
- Drives [[Unit Economics]]: 5–20 runs/month/engineer × 10–50 engineers = usage expansion → NRR >120%.

### Compliance Officer — the external forcing function
- At customer or insurer (Munich Re, Swiss Re, UL, TÜV — [[Buyer Tiers]] Tier 4).
- Consumes immutable audit trail + hash-chained logs ([[Module Specs]] Module 5).

## Design implications

> [!tip]
> 1. Every feature must serve the **engineer-first** motion ([[Product Principles]] #6 "developer-native"); enterprise roles consume outputs, not dashboards.
> 2. Persona → flow mapping lives in [[Core User Flows]]; persona → tier mapping informs [[Pricing Tiers]] seat/embodiment limits.
> 3. Validate personas against reality in discovery calls — if labs have no dedicated fleet operator, Tier-3 timing shifts ([[Demand Signals]]).

Links: [[Product Vision]] · [[Core User Flows]] · [[Buyer Tiers]] · [[Go-to-Market]] · [[Home]]
