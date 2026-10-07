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

Draft application text. Apply **Week 1–2** only after Delaware incorporation and website launch ([[90-Day Roadmap]]). The repository does not verify application submission, acceptance, or a $100K DGX credit award; these are target program benefits that require confirmation ([[NVIDIA Inception]]).

> [!warning] Verify before submission
> The current workload is a deterministic mock plus an HTTP adapter. The real Isaac/Lab, A100/H100, Kubernetes/Argo, GPU-count, and physics-accuracy statements below describe intended work, not a deployed benchmark. Do not submit them as current execution.

> [!important] The one rule that gets you accepted
> Name the **GPU-accelerated workload explicitly**. Generic "AI startup" applications stall; ours states the compute shape in the program reviewers' own vocabulary: parallel Isaac Lab episodes on A100/H100.

## Eligibility checklist

| Requirement | Status |
|---|---|
| Incorporated entity | [ ] Delaware C-Corp — target Week 1 ([[Corporate Structure]]) |
| <10 years old | [verify] founding date |
| ≥1 developer on staff | [verify] current staffing |
| Working website | [ ] validsim.com — Week 1 |
| Pre-revenue stage | [verify] current financial status |

## Q: Describe your company

> ValidSim is building "GitHub Actions for robots": a CLI/API workflow that turns a checkpoint into statistical success/safety metrics, regression comparisons, and an APPROVE/BLOCK CI verdict. The current simulation is deterministic mock; real Isaac/GPU execution and compliance-grade audit retention are milestones. We plan a software-only product with no robot hardware or manufacturing.

## Q: What is your GPU-accelerated workload?

> **Target workload after porting our validated pipeline to real Isaac Sim/Lab:** thousands of parallel GPU episodes on A100/H100 with domain randomization, including 50–100 LLM-generated adversarial scenarios per run. We will validate GPU count, runtime, and physics fidelity on shadow runs before presenting a production SLO; Kubernetes/Argo and customer scale are not current capabilities.

## Q: Planned NVIDIA technology use

| NVIDIA product | Intended role after the GPU port |
|---|---|
| Isaac Sim 4.x | Simulation substrate, scene/asset pipeline (USD) |
| Isaac Lab | GPU-parallel RL/IL evaluation environments |
| PhysX 5 | Contact/force fidelity that must be benchmarked before it supports a safety claim |
| Omniverse RTX | Planned photorealistic rendering/visual-domain-randomization work |
| NVIDIA Cosmos | Planned visual randomization (post-MVP) |
| TensorRT / ONNX | Planned checkpoint-inference integration; no implemented inference path is verified |
| DGX Cloud | Target compute for the real validation engine |

## Q: What will you spend the credits on?

> If granted, DGX Cloud credits would fund porting the validated mock pipeline to real Isaac Sim/Lab on A100 80GB nodes (≥10GB VRAM per instance), followed by measured design-partner runs. Partner AWS/Nebius credits would be evaluated against the control plane (API, queues, Postgres/SQLite, static dashboard). No credit award or billable-customer run is verified.

## Milestones (what we tell NVIDIA we'll do in 6/12 months)

- **6 months, planned:** MVP live on Isaac Sim/Lab with 3–5 design partners; published GR00T/pi0 benchmark; GitHub Actions marketplace listing; GTC-adjacent demo. These are commitments to verify and execute, not achievements.
- **12 months, planned:** 10–20 paying teams; multi-embodiment support; ISO-aligned compliance evidence packages; NVIDIA co-sell motion. None is a current commitment delivered.

## Why NVIDIA should care (the strategic frame)

> We plan to make each production run consume Isaac Sim GPU time, but the current implementation is mock-only and the audit/compliance trail remains roadmap work. NVIDIA-native execution and evidence retention must be proven before either becomes an investor claim.

## Submission logistics

- Apply the same week the website goes live; attach the 1-minute demo video once recorded ([[YC Countdown]]).
- Program benefits to verify against the recipient's official offer: DGX/AWS/Nebius credits, SDK access, and GTC visibility ([[Key Figures]]).
- Source: NVIDIA Inception program terms, Thundercompute guide (July 2026), Nebius AI Lift (April 2025) — see [[Sources]] #2, #11, #12.

Links: [[NVIDIA Inception]] · [[YC Application Answers]] · [[Tech Stack]] · [[Home]]
