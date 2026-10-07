---
tags:
  - engineering
  - tech-stack
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🛠️ Tech Stack

Complete stack specification (§6 of the founding doc). Rule of thumb: NVIDIA-native wherever possible (Inception fit, [[NVIDIA Inception]]), boring-and-open everywhere else — the "pure software, no hardware" feasibility advantage for a 2-founder team ([[Strategic Advantages]]).

> [!important] Blueprint status — 2026-09-21
> This is a target stack, not a description of the repository. Shipped today: Python/FastAPI/Typer, static HTML/JavaScript + Chart.js, Redis/memory jobs, SQLite/PostgreSQL stores, Docker, API-key auth, local composite Actions, and an optional HTTP Isaac worker client. Not implemented here: Kubernetes, Argo, RabbitMQ, gRPC/Protobuf, Next.js/React, TimescaleDB, S3/GCS recording storage, Terraform/Pulumi, Auth0/Clerk, or Stripe. Evidence: `requirements.txt`, `pyproject.toml`, `docker-compose.yml`, `Dockerfile`, and `validsim/`.

## 6.1 Target stack specification (status: planned unless marked shipped)

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| **Simulation** | Deterministic mock (shipped); NVIDIA Isaac Sim 4.x (target HTTP worker) | 4.x target | Free, open-source, GPU-parallel, PhysX 5 |
| **RL/IL Environment** | NVIDIA Isaac Lab (target) | Latest target | GPU-parallel training/evaluation |
| **Physics** | NVIDIA PhysX | 5.x | Industry standard, bundled with Isaac |
| **Rendering** | Omniverse RTX | Latest | Photorealistic, ray-traced |
| **Visual Randomization** | NVIDIA Cosmos | Latest | Photorealistic visual domain randomization |
| **ML Framework** | PyTorch | 2.x | Standard for VLA models |
| **Model Formats** | PyTorch / ONNX / TensorRT | — | Checkpoint ingestion flexibility |
| **LLM (Scenarios)** | Rule/template scenario generation (shipped); hosted GPT/Claude/Llama generation (target) | — | Adversarial scenario generation |
| **Orchestration** | Docker Compose (shipped); Kubernetes | 1.29+ target | Container orchestration, GPU scheduling |
| **Workflow Engine** | Argo Workflows (target) | Latest target | DAG-based pipeline execution |
| **Job Queue** | Memory / Redis 7.x | — | Fast job queuing, pub/sub |
| **Message Broker** | RabbitMQ (target) | 3.13+ target | Reliable message delivery |
| **API Framework** | FastAPI (shipped) | 0.110+ | Async, OpenAPI, high performance |
| **Internal RPC** | HTTP (shipped); gRPC + Protocol Buffers (target) | — | High-throughput internal communication |
| **Frontend** | Static HTML/JS + Chart.js (shipped); Next.js 14 + Tailwind CSS (target) | — | Dashboard, scorecard viewer |
| **Charts** | Chart.js (shipped); Recharts / D3.js (target) | — | Regression timeline, failure taxonomy |
| **Database** | SQLite / PostgreSQL 16 (shipped); TimescaleDB (target) | — | Validation results, time-series metrics |
| **Object Storage** | PostgreSQL JSON episode detail (shipped); AWS S3 / GCS / Azure Blob (target) | — | Checkpoints, episode recordings |
| **GPU Compute** | NVIDIA DGX Cloud (Inception) | — | A100/H100 for simulation |
| **Cloud Fallback** | AWS p4d/p5 / GCP A100 | — | Target if DGX credits are not awarded/exhausted |
| **CI Integration** | GitHub Actions local composite actions (shipped); GitLab CI target | — | Plugin for automated validation |
| **Auth** | Shared `VALIDSIM_API_KEY` (shipped); Auth0 / Clerk target | — | Multi-tenant, RBAC |
| **Billing** | Stripe (target) | — | Subscription + usage-based |
| **Monitoring** | Prometheus metrics endpoint (shipped); Grafana target | — | Internal platform monitoring |
| **Logging** | Structured Python logging (shipped); ELK target | — | Centralized logging |
| **IaC** | Docker Compose (shipped); Terraform + Pulumi target | — | Infrastructure as code |
| **Container Registry** | Local Docker image / Compose (shipped); ECR / Artifact Registry target | — | Docker image management |
| **Secrets** | AWS Secrets Manager / HashiCorp Vault | — | API keys, credentials |

## 6.2 Target infrastructure architecture

```
┌─────────────────────────────────────────────────────────┐
│                    NVIDIA DGX CLOUD                      │
│              (Inception Credits: $100K)                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐      │
│  │ A100    │ │ A100    │ │ A100    │ │ A100    │      │
│  │ 80GB    │ │ 80GB    │ │ 80GB    │ │ 80GB    │      │
│  │ Isaac   │ │ Isaac   │ │ Isaac   │ │ Isaac   │      │
│  │ Sim     │ │ Sim     │ │ Sim     │ │ Sim     │      │
│  │ Pod 1   │ │ Pod 2   │ │ Pod 3   │ │ Pod 4   │      │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘      │
│  Kubernetes Cluster (GPU Node Pool) · Argo Workflows    │
│  Redis + RabbitMQ                                       │
└──────────────────────────┬──────────────────────────────┘
                           │ API / Data
┌──────────────────────────▼──────────────────────────────┐
│                    CLOUD SERVICES                        │
│  FastAPI API Server (ECS/GKE) · Next.js Dashboard       │
│  (Vercel) · PostgreSQL + Timescale (RDS)                │
│  S3 (Checkpoints + Episodes) · Auth0 · Stripe           │
└─────────────────────────────────────────────────────────┘
```

> [!warning] Compute and credit assumptions
> The 4×/8× A100 performance, DGX/AWS/Nebius credit and pass-through-pricing lines below are planning assumptions, not measured ValidSim results. NVIDIA Inception acceptance and credits were not verified in the repository and must be confirmed before external use.

## Compute economics (target assumptions)

| Fact | Implication |
|---|---|
| Min **10GB VRAM** per Isaac Gym instance | 4× A100 80GB comfortably runs 5,000-episode MVP benchmark |
| **Potential** $100K DGX + $100K AWS + up to $150K Nebius via Inception | No credits are verified as granted; base plan must be cash-funded ([[Financial Projections]], [[NVIDIA Inception]]) |
| Cloud fallback AWS p4d/p5 / GCP A100 | De-risks Risk #6 "compute costs exceed credits" ([[Risk Register]]) |
| Pass-through GPU pricing **$3.50/A100-hr + 20% margin** | Compute becomes revenue line, not just cost ([[Business Model]]) |

> [!note] Stack review trigger
> Any component swap must pass two tests: (1) keeps NVIDIA-native positioning for Inception/GTC visibility, (2) doesn't add a hire before [[Hiring Plan]] month 4. Log swaps in the [[Decision Log]].

Links: [[Solution Architecture]] · [[Module Specs]] · [[GitHub Actions Integration]] · [[Financial Projections]] · [[Home]]
