---
tags:
  - product
  - mvp
  - scope
status: complete
created: 2026-09-18
area: "03 - Product"
---

# 🚫 MVP Non-Goals

What the MVP does **NOT** do (yet). This list is a scope-defense document: when a design partner, advisor, or founder wants to "just quickly add" one of these during the sprint, cite this note and log the request in the [[Decision Log]].

## 7.2 The nine exclusions

| ❌ Excluded from MVP | Why excluded | When it returns |
|---|---|---|
| **Multi-robot / multi-embodiment support** | One stack (GR00T/pi0-class VLA on Franka Panda) proves the pipeline; embodiment matrix explodes test surface | Post-MVP, Pro tier ("10 embodiments") [[Pricing Tiers]] |
| **Hardware-in-the-loop (HIL) testing** | Requires robot fleet access + ROS 2 bridge; pure software is the feasibility advantage [[Strategic Advantages]] | Enterprise tier [[Business Model]]; ROS 2 Bridge layer 5 [[Solution Architecture]] |
| **Compliance certification generation (ISO format)** | ISO 10218/13482 alignment needs legal review + insurer input; ship generic PDF scorecard first | Year 1–2, with Munich Re/Swiss Re pilots [[Compliance]], [[Core User Flows]] Flow 3 |
| **Fleet-scale deployment gating** | Needs multi-tenant auth, RBAC, fleet inventory integration | Year 2, Tier-3 buyers [[Buyer Tiers]] |
| **Custom physics environments** | Scene authoring support = infinite edge cases; USD task library defaults only | Self-serve after task library matures [[Module Specs]] |
| **Billing / multi-tenant SaaS** | Stripe integration distracts from demo; design partners are contracted manually | Pre-launch for Team tier [[Go-to-Market]] Phase 2 |
| **ROS 2 bridge** | Same rationale as HIL — deferred with it | Year 2 roadmap [[90-Day Roadmap]] |
| **NVIDIA Cosmos integration** | Photorealistic visual randomization is a v2; classical domain randomization (6+ axes) suffices [[8-Week Sprint Plan]] W2 | Year 2, once Inception early-SDK access lands [[NVIDIA Inception]] |
| **Enterprise SSO / RBAC** | Auth0 multi-tenant is on the stack but out of MVP scope; demo uses single project | Enterprise sales motion [[Go-to-Market]] Phase 3 |

## The meta-rule

> [!important] One wedge task, one wedge flow
> The MVP optimizes for **Flow 1: Submit & Validate** in [[Core User Flows]] — the ML Engineer's 5–20 runs/month. Flows 2 and 3 exist in the architecture but not the product. The 90-day roadmap names the exact failure mode: *"Scope creep beyond the single wedge task"* ([[90-Day Roadmap]]).

## What non-goals do NOT mean

- **Not** "never." Every row has a return path tied to a revenue tier.
- **Not** architecture blindness. The 5-layer design already reserves slots for these modules ([[Solution Architecture]], [[Module Specs]]) — deferral is a build-order choice, not a redesign risk.
- **Not** competitive weakness. Formant/Viam live in the *post-deployment* quadrant we're also excluding ([[Competitive Landscape]]).

## Review cadence

- Weekly demo ([[Product Principles]] operating rule via [[YC Countdown]]) re-checks: did anything on this list creep in?
- Design-partner requests logged with frequency — the count of LOI-holders demanding HIL is the trigger to re-prioritize this table ([[Go-to-Market]] Phase 1).

Links: [[MVP Scope]] · [[8-Week Sprint Plan]] · [[Product Vision]] · [[Home]]
