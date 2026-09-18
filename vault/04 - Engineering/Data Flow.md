---
tags:
  - engineering
  - data-flow
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🔄 Data Flow (End-to-End)

The six-step lifecycle of one validation run through [[Solution Architecture]]. This is the script for the demo video ([[YC Countdown]]) and the sequence diagram for the blog post ([[8-Week Sprint Plan]] W8).

## Step 1: SUBMIT
```
Developer pushes model checkpoint to registry
→ Triggers GitHub Action / CLI command
→ POST /api/v1/validations {checkpoint, task_config, robot_spec, environment}
```
- Entry surfaces: `validsim run --checkpoint ./models/gr00t_v42.pt --task bin_picking ...` ([[CLI Design]]) or the Actions YAML ([[GitHub Actions Integration]])
- Payload validated by Pydantic v2 config parser ([[Module Specs]] M1)

## Step 2: QUEUE
```
Job enters Redis queue
→ Orchestrator assigns GPU resources (Kubernetes + Argo Workflows)
→ Isaac Sim container spins up with specified scene
```
- Priority queuing + dead-letter handling; GPU node pool auto-scales on DGX Cloud ([[Tech Stack]])

## Step 3: SIMULATE
```
Parallel episode execution begins
→ 1,000–100,000 episodes across 8–64 GPUs
→ Domain randomization applied per episode
→ LLM-generated adversarial scenarios injected (50–100 per run)
→ Episode data recorded: video, joint states, forces, contacts, timestamps
```
- MVP envelope: 1,000–5,000 episodes, <30 min on 4× A100 ([[MVP Success Metrics]])
- Recordings land in S3/GCS object storage as HDF5 + MP4

## Step 4: EVALUATE
```
All episodes complete
→ Success rate calculated per task, per scenario
→ Failure taxonomy classified (rule-based + LLM)
→ Safety metrics computed (collisions, forces, proximity)
→ Regression delta computed vs. previous checkpoint
→ Statistical significance tested (bootstrap, 95% CI)
```
- Writes to PostgreSQL 16 + TimescaleDB (results + time-series metrics)
- Formulas and weights: [[Module Specs]] M4; example output: "Deformable grasp 89% → 84% (p=0.03)" ([[Scorecard UX]])

## Step 5: REPORT
```
Scorecard generated
→ Web dashboard updated
→ PDF/JSON report generated
→ Webhook fired (Slack, email, GitHub PR comment)
→ Deployment gate decision: APPROVE ✅ or BLOCK ❌
```
- Scorecard generation < 5 min post-simulation; dashboard loads < 2 s

## Step 6: DEPLOY (or don't)
```
If approved → checkpoint deployed to fleet
If blocked → developer receives failure analysis + regression details
→ Audit log updated (immutable, timestamped)
```

> [!important] The audit trail is the compounding asset
> Every run — approved or blocked — appends a hash-chained record. That log is: (a) the insurer-facing product for Tier-4 buyers ([[Buyer Tiers]], [[Compliance]]), (b) the training corpus for the failure-taxonomy flywheel ([[Moat]]), and (c) the liability shield when a field failure is questioned ([[Pain Quantified]]).

## Latency budget (single run)

| Stage | Budget |
|---|---|
| Submit → queue | seconds |
| Simulate (5,000 episodes) | 15–45 min (target <30 min, 4× A100) |
| Evaluate + report | < 5 min |
| Engineer's total wait | **< 1 hour** (persona goal, [[User Personas]]) |

Links: [[Module Specs]] · [[API Design]] · [[Scorecard UX]] · [[Product Principles]] · [[Home]]
