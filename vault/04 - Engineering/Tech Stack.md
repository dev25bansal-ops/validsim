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

## 6.1 Complete stack specification

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| **Simulation** | NVIDIA Isaac Sim | 4.x | Free, open-source, GPU-parallel, PhysX 5 |
| **RL/IL Environment** | NVIDIA Isaac Lab | Latest | GPU-parallel training/evaluation |
| **Physics** | NVIDIA PhysX | 5.x | Industry standard, bundled with Isaac |
| **Rendering** | Omniverse RTX | Latest | Photorealistic, ray-traced |
| **Visual Randomization** | NVIDIA Cosmos | Latest | Photorealistic visual domain randomization |
| **ML Framework** | PyTorch | 2.x | Standard for VLA models |
| **Model Formats** | PyTorch / ONNX / TensorRT | — | Checkpoint ingestion flexibility |
| **LLM (Scenarios)** | GPT-4o / Claude 3.5 / Llama 3 | — | Adversarial scenario generation |
| **Orchestration** | Kubernetes | 1.29+ | Container orchestration, GPU scheduling |
| **Workflow Engine** | Argo Workflows | Latest | DAG-based pipeline execution |
| **Job Queue** | Redis 7.x | — | Fast job queuing, pub/sub |
| **Message Broker** | RabbitMQ | 3.13+ | Reliable message delivery |
| **API Framework** | FastAPI | 0.110+ | Async, OpenAPI, high performance |
| **Internal RPC** | gRPC + Protocol Buffers | — | High-throughput internal communication |
| **Frontend** | Next.js 14 + Tailwind CSS | — | Dashboard, scorecard viewer |
| **Charts** | Recharts / D3.js | — | Regression timeline, failure taxonomy |
| **Database** | PostgreSQL 16 + TimescaleDB | — | Validation results, time-series metrics |
| **Object Storage** | AWS S3 / GCS / Azure Blob | — | Checkpoints, episode recordings |
| **GPU Compute** | NVIDIA DGX Cloud (Inception) | — | A100/H100 for simulation |
| **Cloud Fallback** | AWS p4d/p5 / GCP A100 | — | When DGX credits exhausted |
| **CI Integration** | GitHub Actions / GitLab CI | — | Plugin for automated validation |
| **Auth** | Auth0 / Clerk | — | Multi-tenant, RBAC |
| **Billing** | Stripe | — | Subscription + usage-based |
| **Monitoring** | Grafana + Prometheus | — | Internal platform monitoring |
| **Logging** | ELK Stack | — | Centralized logging |
| **IaC** | Terraform + Pulumi | — | Infrastructure as code |
| **Container Registry** | AWS ECR / GCP Artifact Registry | — | Docker image management |
| **Secrets** | AWS Secrets Manager / HashiCorp Vault | — | API keys, credentials |

## 6.2 Infrastructure architecture

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

## Compute economics

| Fact | Implication |
|---|---|
| Min **10GB VRAM** per Isaac Gym instance | 4× A100 80GB comfortably runs 5,000-episode MVP benchmark |
| **$100K DGX credits** via Inception + **$100K AWS** + up to **$150K Nebius** | Year-1 compute nearly free ([[Financial Projections]], [[NVIDIA Inception]]) |
| Cloud fallback AWS p4d/p5 / GCP A100 | De-risks Risk #6 "compute costs exceed credits" ([[Risk Register]]) |
| Pass-through GPU pricing **$3.50/A100-hr + 20% margin** | Compute becomes revenue line, not just cost ([[Business Model]]) |

> [!note] Stack review trigger
> Any component swap must pass two tests: (1) keeps NVIDIA-native positioning for Inception/GTC visibility, (2) doesn't add a hire before [[Hiring Plan]] month 4. Log swaps in the [[Decision Log]].

Links: [[Solution Architecture]] · [[Module Specs]] · [[GitHub Actions Integration]] · [[Financial Projections]] · [[Home]]
