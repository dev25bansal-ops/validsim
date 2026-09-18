---
tags:
  - fundraising
  - nvidia
  - copy
status: draft
created: 2026-09-18
area: "07 - Fundraising"
---

# 🟢 NVIDIA Inception Application (Drafted)

Paste-ready application text. Apply **Week 1–2** — immediately after Delaware incorporation and website launch ([[90-Day Roadmap]]). Free to apply, approval in days-to-weeks, and the $100K DGX credits are the direct unlock for the real Isaac Sim backend (see honest limitation in [[YC Application Answers]] Q3).

> [!important] The one rule that gets you accepted
> Name the **GPU-accelerated workload explicitly**. Generic "AI startup" applications stall; ours states the compute shape in the program reviewers' own vocabulary: parallel Isaac Lab episodes on A100/H100.

## Eligibility checklist

| Requirement | Status |
|---|---|
| Incorporated entity | [ ] Delaware C-Corp — target Week 1 ([[Corporate Structure]]) |
| <10 years old | ✅ founded 2026 |
| ≥1 developer on staff | ✅ 2 AI/ML founders ([[Founding Team]]) |
| Working website | [ ] validsim.com — Week 1 |
| Pre-revenue stage OK | ✅ by design |

## Q: Describe your company

> ValidSim is the CI/CD layer for robot fleets — "GitHub Actions for robots." Robot foundation model companies (Physical Intelligence, Skild AI, Figure, 1X) ship policy updates weekly but validate them with hand-built test scenes and judgment calls. A developer submits a model checkpoint via CLI or GitHub Action; ValidSim returns a defensible safety-and-success scorecard — success rate with 95% confidence intervals, a 0–100 safety score, regression diffs against the previous checkpoint, and an APPROVE/BLOCK deployment gate. We are pure software: no robots, no hardware, no manufacturing.

## Q: What is your GPU-accelerated workload?

> **Large-scale Isaac Lab simulation for robot policy validation, running thousands of parallel GPU episodes on A100/H100 with domain randomization and Omniverse RTX rendering.** Each validation run executes 1,000–100,000 physics-accurate episodes (PhysX 5 rigid/soft-body contact dynamics) across 8–64 GPUs orchestrated by Kubernetes + Argo Workflows, including 50–100 LLM-generated adversarial scenarios per run. The workload is embarrassingly parallel, bursty (triggered by model pushes in CI), and grows with every customer fleet — exactly the profile DGX Cloud is built for.

## Q: How do you use NVIDIA technology?

| NVIDIA product | Role in ValidSim |
|---|---|
| Isaac Sim 4.x (open-source) | Simulation substrate, scene/asset pipeline (USD) |
| Isaac Lab | GPU-parallel RL/IL evaluation environments |
| PhysX 5 | Contact/force fidelity — the physics that makes a safety score defensible |
| Omniverse RTX | Photorealistic rendering for visual-domain-randomization validity |
| NVIDIA Cosmos | Photorealistic visual domain randomization (post-MVP) |
| TensorRT / ONNX | Checkpoint ingestion for accelerated policy inference in sim |
| DGX Cloud | Compute for the validation engine itself |

## Q: What will you spend the credits on?

> $100K DGX Cloud credits → the Week-1–2 milestone of porting our validated pipeline from the mock backend to real Isaac Sim/Lab on A100 80GB nodes (≥10GB VRAM per instance), then every subsequent design-partner validation run. This is COGS for our first 5–10 customers, not R&D theater: each run is a billable event in the usage-based pricing model ([[Pricing Tiers]]). Complementary credits: up to $100K AWS + up to $150K Nebius via Inception partner offers for the control plane (API, queues, Postgres/Timescale, dashboard).

## Milestones (what we tell NVIDIA we'll do in 6/12 months)

- **6 months:** MVP live on Isaac Sim/Lab with 3–5 design partners; published public benchmark of GR00T/pi0 regression results; GitHub Actions marketplace listing; demo at a GTC-adjacent event.
- **12 months:** 10–20 paying teams; multi-embodiment support; compliance evidence packages aligned to ISO 10218/13482; NVIDIA co-sell motion into robot FM labs ([[Go-to-Market]]).

## Why NVIDIA should care (the strategic frame)

> Every ValidSim run is Isaac Sim consumption at industrial scale. We are demand-creation for the NVIDIA simulation stack: robot teams that validate continuously burn GPU hours weekly, and we make the NVIDIA-native path the default. If a deployment incident forces the compliance question ([[Why Now (2026)]] Force 5), audit-ready NVIDIA-physics evidence is the answer — which makes the Inception relationship itself part of our [[Moat]] against the "NVIDIA will build this" objection ([[Risk Register]] #2).

## Submission logistics

- Apply the same week the website goes live; attach the 1-minute demo video once recorded ([[YC Countdown]]).
- Program benefits tracked: $100K DGX credits · up to $100K AWS · up to $150K Nebius · early Isaac/Omniverse SDK access · GTC visibility ([[Key Figures]]).
- Source: NVIDIA Inception program terms, Thundercompute guide (July 2026), Nebius AI Lift (April 2025) — see [[Sources]] #2, #11, #12.

Links: [[NVIDIA Inception]] · [[YC Application Answers]] · [[Tech Stack]] · [[Home]]
