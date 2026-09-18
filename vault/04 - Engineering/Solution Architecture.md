---
tags:
  - engineering
  - architecture
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🏗️ Solution Architecture

Cloud-native platform between model training and real-world deployment. Five layers, top to bottom. Component-level specs: [[Module Specs]]; runtime sequence: [[Data Flow]]; technology choices: [[Tech Stack]].

## 4.2 High-level system architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER / CI PIPELINE                           │
│  (GitHub Actions, GitLab CI, Jenkins, or CLI/API submission)        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  Submit: model checkpoint + task config
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L1 · INGESTION & ORCHESTRATION LAYER               │
│  • API Gateway (FastAPI / gRPC)        • Job Queue (Redis/RabbitMQ) │
│  • Orchestrator (Kubernetes / Argo)    • Config Parser (Pydantic)   │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L2 · SIMULATION EXECUTION ENGINE                   │
│  • NVIDIA Isaac Sim / Isaac Lab (GPU-parallel episodes)             │
│  • Domain Randomization Module                                      │
│  • LLM-Generated Adversarial Scenarios (GPT-4 / Cosmos)             │
│  • Physics Fidelity Layer (PhysX 5)                                 │
│  • Multi-embodiment support (GR00T, pi0, custom VLAs)               │
│  • Parallel episode runner (1,000–100,000 episodes per validation)  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L3 · EVALUATION & SCORING ENGINE                   │
│  • Success Rate Calculator (per task, per scenario)                 │
│  • Failure Taxonomy Classifier (LLM + rule-based)                   │
│  • Regression Delta Engine (compare vs. previous checkpoint)        │
│  • Safety Score Module (collision, force limits, human proximity)   │
│  • Statistical Significance Testing (confidence intervals)          │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L4 · REPORTING & DASHBOARD LAYER                   │
│  • Web Dashboard (React / Next.js)     • Scorecard Generator (PDF)  │
│  • Regression Timeline                 • Alerting & Webhooks        │
│  • Programmatic API                    • Audit Trail (immutable)    │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L5 · INTEGRATION & DEPLOYMENT LAYER                │
│  • GitHub Actions / GitLab CI Plugin   • ROS 2 Bridge (HIL)         │
│  • NVIDIA Omniverse Connector          • Model Registry (MLflow/W&B)│
│  • Fleet Deployment Gate (approve/block deploy based on score)      │
└─────────────────────────────────────────────────────────────────────┘
```

## Architectural decisions (and their consequences)

| Decision | Why | Consequence |
|---|---|---|
| **Isaac Sim/Lab as the sim substrate** | Open-source, PhysX 5, GPU-parallel; NVIDIA courting ecosystem ([[Why Now (2026)]] Force 4) | Platform risk → move fast, own workflow ([[Risk Register]] #2) |
| **Queue + K8s + Argo DAGs** | Validation runs are bursty, GPU-bound, embarrassingly parallel | Cost control via batch scheduling ([[Risk Register]] #6) |
| **LLM in the loop for scenarios** | 12-category adversarial taxonomy generated, not hand-written ([[Module Specs]] M3) | Novelty + data flywheel ([[Moat]]) |
| **CI-native entry points (Actions/CLI/API)** | Developer-native principle ([[Product Principles]] #6) | Free/Dev tier funnel → [[Go-to-Market]] Phase 2 |
| **Deployment gate as a layer, not a feature** | The scorecard is a decision ([[Product Principles]] #4) | Sticky workflow → NRR >120% ([[Unit Economics]]) |
| **Immutable audit trail** | Insurers/regulators are Tier-4 buyers ([[Buyer Tiers]]) | Compliance moat ([[Compliance]]) |

## Scale envelope

- Episodes per validation: **1,000–100,000** (MVP: 1,000–5,000, [[MVP Scope]])
- GPU spread: **8–64 GPUs** per run; min **10GB VRAM** per Isaac Gym instance
- Adversarial scenarios injected: **50–100 per run**
- Latency budget: 5,000 episodes < 30 min; scorecard +5 min ([[MVP Success Metrics]])

Links: [[Module Specs]] · [[Data Flow]] · [[Tech Stack]] · [[API Design]] · [[Home]]
