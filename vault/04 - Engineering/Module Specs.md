---
tags:
  - engineering
  - modules
  - specs
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🧩 Module Specs

Five core modules of [[Solution Architecture]], each with its sprint week from [[8-Week Sprint Plan]].

## Module 1 · Ingestion & Orchestration *(Sprint W1–W2, W6)*

| Component | Technology | Specification |
|---|---|---|
| API Gateway | FastAPI (Python 3.11+) | Async, OpenAPI spec, rate limiting |
| gRPC Service | Protocol Buffers | High-throughput internal communication |
| Job Queue | Redis 7.x + RabbitMQ | Priority queuing, dead letter handling |
| Orchestration | Kubernetes 1.29+ | Auto-scaling GPU node pools |
| Workflow Engine | Argo Workflows | DAG-based pipeline execution |
| Config Parser | Pydantic v2 | Task library, robot spec, environment validation |
| Model Format Support | PyTorch, ONNX, TensorRT | Checkpoint ingestion |

> [!note] ONNX/PyTorch/TensorRT flexibility is the hedge against Risk #4 ("VLA architectures change rapidly", [[Risk Register]]) — evaluation is abstracted from model architecture.

## Module 2 · Simulation Execution Engine *(Sprint W1–W2)*

| Component | Technology | Specification |
|---|---|---|
| Simulation Platform | NVIDIA Isaac Sim 4.x | Open-source, PhysX 5 physics |
| RL/IL Environment | NVIDIA Isaac Lab | GPU-parallel environments |
| Rendering | Omniverse RTX | Photorealistic, ray-traced |
| Visual Domain Randomization | NVIDIA Cosmos | Photorealistic visual variants *(post-MVP, [[MVP Non-Goals]])* |
| Physics Engine | NVIDIA PhysX 5 | Rigid body, soft body, contact dynamics |
| Scene Format | USD (Universal Scene Description) | Environment specification |
| Robot Format | URDF / USD | Robot embodiment specification |
| Parallel Execution | CUDA + Isaac Gym | 1,000–100,000 episodes per run |
| GPU Requirement | NVIDIA A100 80GB / H100 | Minimum 10GB VRAM per Isaac Gym instance |
| Episode Recording | Custom (HDF5 / MP4) | Video, joint states, forces, contacts |

## Module 3 · LLM Adversarial Scenario Generator *(Sprint W4)*

Prompt pipeline: task description → GPT-4o → scenario JSON → automated Isaac Sim scene edits. **12 adversarial categories:**

| # | Category | Example |
|---|---|---|
| 1 | Lighting changes | Intensity, color temp, direction, shadows |
| 2 | Object property changes | Mass, friction, deformability, size |
| 3 | Human proximity | Worker enters workspace, reaches near robot |
| 4 | Unexpected obstacles | New objects in workspace |
| 5 | Sensor degradation | Camera noise, occlusion, lens flare |
| 6 | Mechanical variation | Joint backlash, gripper wear |
| 7 | Environmental disturbance | Vibration, wind, temperature |
| 8 | Task ambiguity | Wrong object presented, partial occlusion |
| 9 | Multi-robot interference | Another robot in shared workspace |
| 10 | Emergency scenarios | Power loss, e-stop, network dropout |
| 11 | Adversarial inputs | Out-of-distribution objects, unusual poses |
| 12 | Temporal pressure | Speed increase, deadline constraints |

MVP target: **100 scenarios for bin picking** ([[8-Week Sprint Plan]] W4 exit criteria). Category #1 (lighting) and #2 (deformability) are pulled straight from the buyer's trauma in [[Problem Statement]] — night-shift lighting failure, broken gripper on deformable object.

## Module 4 · Evaluation & Scoring Engine *(Sprint W3, W5)*

| Metric | Calculation | Output |
|---|---|---|
| Success Rate | Completed episodes / Total episodes × 100% | Per-task, per-scenario, overall |
| Failure Taxonomy | Rule-based + LLM classification | Categorized failure list |
| Safety Score | Weighted composite of collision, force, proximity metrics | 0–100 score |
| Robustness Score | Std deviation of success rate across randomization variants | 0–100 score |
| Regression Delta | Success(N) − Success(N−1) with CI | Per-task delta |
| Statistical Significance | Bootstrap resampling, 95% CI | p-value, confidence interval |
| **Composite Score** | **40% success + 30% safety + 20% robustness + 10% regression** | **0–100** |

Composite drives [[Scorecard UX]] and the gate threshold (e.g. `--threshold 85`, [[CLI Design]]).

## Module 5 · Reporting & Compliance *(Sprint W7–W8)*

| Output | Format | Purpose |
|---|---|---|
| Scorecard | PDF + JSON | Developer review, management reporting |
| Compliance Evidence Package | PDF + structured data | ISO 10218 / ISO 13482 evidence *(post-MVP)* |
| Audit Trail | Immutable log (timestamped, hash-chained) | Regulatory/insurer review |
| Regression Timeline | Interactive chart | Version-over-version tracking |
| Failure Analysis Report | PDF + video clips | Root cause analysis |
| Deployment Decision | JSON (approve/block + reasoning) | Fleet deployment gate |

## Flywheel coupling

Every run feeds back: episode recordings → failure taxonomy training data → better LLM scenario prompts → more discriminating benchmarks. This compounding loop is moat #3 in [[Moat]] and advantage #7 in [[Strategic Advantages]].

Links: [[Solution Architecture]] · [[Data Flow]] · [[Tech Stack]] · [[API Design]] · [[Home]]
