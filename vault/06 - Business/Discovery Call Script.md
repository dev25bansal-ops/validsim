---
tags:
  - gtm
  - sales
  - discovery
status: draft
created: 2026-09-18
area: "06 - Business"
---

# 📞 Discovery Call Script — The First 25 Conversations

> [!warning] This is the highest-priority artifact in the vault
> *"The single most common cause of accelerator rejection is an idea chosen from a report like this one and never confronted with a real customer conversation."* The next 25 phone calls matter more than the next 25 pages of planning. Every [[YC Application Answers]] traction claim is built from these calls.

**Goal:** 25 conversations → 2–3 signed LOIs with named success criteria before the YC submission week (target: week of Oct 26, 2026 — see [[YC Countdown]]).

## Target list (Tier 1 — immediate buyers, [[Buyer Tiers]])

| Priority | Company | Why them | Warm path |
|---|---|---|---|
| 1 | Physical Intelligence | pi0 updates weekly; $5.6B war chest | NVIDIA Inception network |
| 1 | Skild AI | general-purpose FM, $14–15B, multi-embodiment | founder referrals |
| 1 | Figure AI | Figure 02 fleet pilots at BMW | conference (ICRA/RSS) |
| 1 | 1X Technologies | NEO humanoid deployments | conference |
| 1 | Apptronik | Apollo + automotive pilots | NVIDIA ecosystem |
| 2 | Agility Robotics | Digit fleet at GXO | direct outreach |
| 2 | FieldAI | autonomous robot intelligence | direct outreach |
| 2 | Boston Dynamics (Hyundai) | Spot/Atlas enterprise fleets | advisor intro |
| 3 | NVIDIA GR00T team | the model we benchmark against | Inception |
| 3 | Amazon Robotics / GXO / DHL | fleet operators (Tier 3 preview) | Phase 2 |

Log every attempt (including rejections — reviewers probe honesty) with the `Discovery Call` template in `98 - Templates`.

## Cadence & quota math

| Weeks | Calls/week | Cumulative |
|---|---|---|
| 1–4 (Sep 21 – Oct 17) | 10 | 40 attempts → ≥25 completed |
| 5–6 (Oct 19 – Oct 30) | 5 | 25 completed ✅ → submit YC |
| 7+ | 5 | LOI conversion + design partners |

> [!warning] Calendar tension
> 10 calls/week for 4 weeks + MVP build + YC submission in the same window is aggressive for two founders. Mitigation: calls are 15–20 min, batched 3/day in one founder's afternoon (Founder 1 owns calls, Founder 2 owns build during Weeks 3–6, swap after MVP demo). The [[90-Day Roadmap]] failure mode to avoid is "polishing decks instead of booking calls."

## The 15-minute structure

| Min | Phase | What happens |
|---|---|---|
| 0–2 | Rapport | Who we are in one sentence; why we asked for 15 minutes; permission to be blunt |
| 2–10 | Problem discovery | The core question + probes below. **We talk <30% of this phase.** Capture verbatim quotes |
| 10–13 | Solution tease | 90-second demo clip or live scorecard; watch face reaction; ask "does this map to your pain?" |
| 13–15 | Close | Ask for exactly one: (a) design-partner LOI, (b) second call with their fleet/ops lead, (c) intro to another lab |

### The core question (verbatim)

> **"How do you validate a model update before deploying it to your fleet?"**

### Follow-up probes (the money is in the answers)

1. "Walk me through the last time you shipped a policy to real robots — who decided it was ready?"
2. "How long did that take, and how many engineer-hours went into the test scenes?" *(→ maps to [[Pain Quantified]]: 2–4 engs × 6 months = $200K–$500K)*
3. "What happened the time a 'good' model failed after deploy? Lighting? Deformable objects? Night shift?" *(→ [[Module Specs]] adversarial categories)*
4. "How do you catch silent regressions — update N breaks a behavior from update N−1?"
5. "Do your enterprise customers or insurers ask for deployment evidence today? What do you hand them?" *(→ compliance wedge, ISO 10218/13482)*
6. "How many teleop hours per month at $30–80/hr just for validation?" *(→ $500K–$2M/yr per lab)*
7. "If you had 1,000 robots across 5 customer sites, what would scare you most about the next update?" *(→ fleet-scale pain)*

## Objection handling

| Objection | Response |
|---|---|
| "We build validation in-house." | "Great — you're the right person to talk to. What did it cost, and what does it *not* catch? Our bet is cross-lab benchmarks are structurally impossible for an internal tool. Could we show you the scorecard format?" ([[Risk Register]] #1) |
| "Isn't this just Isaac Sim?" | "Isaac is the substrate — free and excellent. We build the scorecard, the gate, and the audit trail on top: job orchestration, adversarial scenario generation, regression statistics, compliance export. You'd otherwise build that in the 2–4 engineer × 6 month bucket." |
| "Send me a deck." | "Will do — but the deck is the least useful artifact. Can I show you a live run on your task in 20 minutes instead? One number from your checkpoint beats ten slides." |
| "Humanoid deployments are years out." | "The validation need exists today on AMRs and arms — the platform is robot-type-agnostic. That's exactly why we'd want you as a design partner now." ([[Risk Register]] #3) |
| "We're not buying tooling." | "Not asking for money today. Asking for 50 free validation runs in exchange for brutal feedback and, if it earns it, a one-page LOI." |

## The LOI ask (design-partner close)

One page, four lines: (1) company + named executive sponsor, (2) success criteria that trigger purchase (e.g., "catches ≥1 real regression in our bin-picking task within 3 runs"), (3) data/feedback commitment (anonymized failure logs), (4) intent to move to the $2,000/mo Team tier at pilot end ([[Pricing Tiers]]). A free pilot with no success criteria is **not** a commitment — do not count it ([[90-Day Roadmap]] failure mode).

## CRM tracking table (copy per call into a `Discovery Call` note)

| Company | Contact / role | Date | Pain quote (verbatim) | Current method | Teleop hrs/mo | Evidence demands? | Next step | LOI status |
|---|---|---|---|---|---|---|---|---|
| [ ] | | | | | | | | 🔴 not started / 🟡 demo / 🟠 verbal / 🟢 signed |

**Weekly review:** count completed calls, verbatim quotes collected (target: ≥10 usable), LOIs. Feed totals into [[YC Application Answers]] Q3/Q4 and [[KPIs]] (50 conversations by Month 6).

Links: [[Go-to-Market]] · [[Buyer Tiers]] · [[Pain Quantified]] · [[YC Countdown]] · [[Home]]
