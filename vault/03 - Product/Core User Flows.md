---
tags:
  - product
  - flows
  - ux
status: complete
created: 2026-09-18
area: "03 - Product"
---

# 🔀 Core User Flows

Three flows map the three buying roles ([[User Personas]]) onto the platform. Each flow is an acceptance test for the [[8-Week Sprint Plan]] and a scene in the YC demo video ([[YC Countdown]]).

## Flow 1 — Submit & Validate *(ML Engineer)* 🥇

1. Push model checkpoint to registry
2. GitHub Action triggers automatically (or manual CLI — `validsim run`)
3. Validation runs in cloud (**15–45 min for 5,000 episodes**)
4. Slack notification: *"Validation complete. Score: 87.3. 2 regressions detected."*
5. Click link → dashboard with full scorecard ([[Scorecard UX]])
6. Review failure taxonomy, watch episode replays
7. Fix regressions, retrain, resubmit

> [!important] Flow 1 is the product
> Target: checkpoint → scorecard in **< 1 hour** with **zero config** ([[Product Principles]] #1). Built by sprint weeks 2, 6, 8. The GitHub Action YAML that starts this flow: [[GitHub Actions Integration]].

## Flow 2 — Deployment Gate *(Fleet Operator)* 🚦

1. Receive notification: *"New policy v42 validated. Score: 87.3"*
2. Review scorecard: success rate, safety score, robustness
3. Check regression delta: *"No critical regressions vs v41"*
4. Click **"Approve Deployment"** → fleet update begins
5. Or click **"Block"** → developer notified with failure details

Mechanics: `POST /api/v1/deployment-gate` returns JSON `{approve/block + reasoning}`; CLI equivalent `validsim gate --run-id abc123 --threshold 85` ([[API Design]], [[CLI Design]]). Every decision lands in the immutable audit trail ([[Data Flow]] step 6).

## Flow 3 — Compliance Report *(Compliance Officer / Insurer)* 📋

1. Navigate to validation history
2. Select specific validation run
3. Click **"Generate Compliance Report"**
4. Download PDF: **ISO 10218 format**, safety evidence, test scenarios, results
5. Forward to enterprise customer or insurer

> [!note] MVP status
> ISO-format compliance generation is explicitly **out of MVP scope** ([[MVP Non-Goals]]); the MVP ships a branded professional PDF scorecard instead (Week 8 deliverable). Flow 3 is the Year-1 Enterprise upsell ([[Pricing Tiers]], [[Compliance]]).

## Flow → build mapping

| Flow | Sprint weeks | Key endpoints | Persona |
|---|---|---|---|
| 1 Submit & Validate | W2, W3, W6, W8 | `POST /validations`, `GET /validations/{id}` | ML Engineer |
| 2 Deployment Gate | W5, W6, W7 | `POST /deployment-gate`, `POST /validations/{id}/compare` | Fleet Operator |
| 3 Compliance Report | Post-MVP (W8 PDF only) | `GET /validations/{id}/scorecard` | Compliance Officer |

## Failure-path flows (design for these too)

- **Blocked deploy:** developer receives failure analysis + regression details with video clips — "don't just say failed; show the video, classify the failure, suggest the fix" ([[Product Principles]] #3)
- **Timeout/partial run:** webhook still fires with status; queue is retry-safe ([[Module Specs]] Module 1)
- **Statistically insignificant delta:** regression flagged with p-value, e.g. *"Low-light condition: 91% → 87% (p=0.08)"* — surfaced, not hidden ([[Module Specs]] Module 4)

Links: [[Scorecard UX]] · [[Product Vision]] · [[Data Flow]] · [[8-Week Sprint Plan]] · [[Home]]
