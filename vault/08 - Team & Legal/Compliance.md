---
tags:
  - legal
  - compliance
status: complete
created: 2026-09-18
area: "08 - Team & Legal"
---

# 📋 Compliance

§18.3 + §2.3/§2.5 context. Compliance is simultaneously our **product** (evidence packages for Tier-4 buyers), our **tailwind** (insurers demanding proof), and our **house** (we hold customers' crown-jewel checkpoints).

> [!important] Status — 2026-09-21
> The shipped product produces branded PDF/JSON/Markdown/HTML scorecards from structured run data. ISO compliance packages, deployment-decision records, and an immutable/hash-chained audit trail are target features. Current stores are neither append-only nor immutable: `DELETE /api/v1/validations/{run_id}` and `validsim delete` permanently remove evidence. Retention/compliance policy requires a company decision and a build change.

## 18.3 Compliance considerations

| Area | Action |
|---|---|
| **Data privacy** (customer model checkpoints) | **SOC 2 Type II by Year 2**; encryption at rest and in transit |
| **ISO alignment** | Design scorecard format to align with **ISO 10218 / ISO 13482** |
| **GDPR** | If serving EU customers: data processing agreement |
| **Insurance partnerships** | Work with **Munich Re / Swiss Re** on actuarial data sharing |

## The compliance context we sell into

- ISO 10218/ISO 13482 require safety documentation, but **no standard format exists** for robot policy validation → labs face regulatory delays and **insurance premiums 2–5× higher** ([[Pain Quantified]])
- **OSHA has no robot-worker playbook yet** — a standards vacuum the first incidents will fill within **12–24 months** ([[Why Now (2026)]] Force 5)
- Blocked enterprise deals worth **$500K–$5M each** stall on missing deployment evidence — the buyer monologue's "insurance broker wants a safety report" ([[Problem Statement]])

## Our two-sided compliance posture

### A. We *generate* evidence (product side)
| Deliverable | Standard | Status |
|---|---|---|
| Scorecard schema ISO-aligned from day one | ISO 10218 (industrial robots) / ISO 13482 (personal care robots, being updated for mobile manipulators) | Design-time, MVP ([[Product Principles]] #5) |
| Compliance Evidence Package (PDF + structured) | Same | **Post-MVP** — Enterprise tier ([[MVP Non-Goals]]) |
| Immutable audit trail (timestamped, hash-chained) | Regulator/insurer review | **Planned**; current validation history is deletable and not hash-chained ([[Solution Architecture]]) |
| Deployment decision records (approve/block + reasoning) | Liability defense | Stored scorecard verdict ships; reasoned JSON record/export remains planned ([[Module Specs]] M5) |

### B. We *are audited* (vendor side)
| Control | Target date | Why now |
|---|---|---|
| Encryption at rest + in transit for checkpoints/episodes | **Target** cloud controls; not implemented in this repository | Checkpoints are labs' most valuable IP ([[IP Strategy]]) |
| SOC 2 Type II | **Year 2** | Enterprise procurement gate; Tier-3/4 deals |
| GDPR DPA template | First EU customer | EU Machinery Regulation buyers exist in Tier 4 |
| Insurer data-sharing agreements | Phase 3 pilots | Munich Re/Swiss Re actuarial studies ([[Go-to-Market]]) |

> [!important] Sequence: align → pilot → certify
> ISO-format *alignment* costs design discipline (free during [[8-Week Sprint Plan]]); certification *generation* costs insurer pilots (Year 1–2); SOC 2 costs process maturity (Year 2). Doing them out of order burns founder time we don't have.

## Standards-vacuum strategy

Being early at the vacuum is the moat: whoever's scorecard insurers cite in the first robot-liability underwriting becomes the format ([[Moat]] compliance row). Concretely: publish the scorecard **schema** (not weights), donate example outputs to ISO working-group discussions, and co-brand evidence packages with UL/TÜV pilots ([[Competitive Landscape]] partner rows).

Links: [[Buyer Tiers]] · [[Pain Quantified]] · [[IP Strategy]] · [[Risk Register]] · [[Home]]
