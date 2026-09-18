---
tags:
  - engineering
  - api
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🔌 API Design

REST API surface (v1). FastAPI 0.110+, async, OpenAPI spec auto-generated, rate-limited ([[Module Specs]] M1, [[Tech Stack]]). Docs ship at `docs.validsim.com` (sprint W6 deliverable).

## 4.5 Key endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/validations` | Submit a new validation run |
| `GET` | `/api/v1/validations/{id}` | Get validation status/results |
| `GET` | `/api/v1/validations/{id}/scorecard` | Download scorecard (PDF/JSON) |
| `GET` | `/api/v1/validations/{id}/episodes` | List episode recordings |
| `GET` | `/api/v1/validations/{id}/failures` | Get failure taxonomy |
| `POST` | `/api/v1/validations/{id}/compare` | Compare with another validation |
| `GET` | `/api/v1/models` | List registered model checkpoints |
| `GET` | `/api/v1/models/{id}/history` | Validation history for a model |
| `GET` | `/api/v1/regressions` | Regression timeline |
| `POST` | `/api/v1/webhooks` | Configure notification webhooks |
| `GET` | `/api/v1/audit-log` | Immutable audit trail |
| `POST` | `/api/v1/deployment-gate` | Deployment approval/rejection |

## Request shape — `POST /api/v1/validations`

```json
{
  "checkpoint": "s3://models/gr00t_v42.pt",
  "task_config": { "task": "bin_picking", "episodes": 5000,
                   "randomization": "full", "adversarial": 100 },
  "robot_spec": { "embodiment": "franka_panda", "format": "URDF" },
  "environment": { "scene": "warehouse_a", "format": "USD" }
}
```

## Response lifecycle

`queued → running → evaluating → reported → gate:{APPROVE|BLOCK}` — status polled via `GET /validations/{id}` or pushed via webhook (`Slack, email, GitHub PR comment`, [[Data Flow]] step 5).

## Endpoint → module → flow mapping

| Endpoint group | Served by | User flow ([[Core User Flows]]) |
|---|---|---|
| `/validations*` | M1 Ingestion + M2 Simulation | Flow 1 Submit & Validate |
| `/compare`, `/regressions` | M4 Evaluation (regression delta) | Flow 1 step 7, CTO weekly review |
| `/scorecard`, `/episodes`, `/failures` | M5 Reporting | Flows 1–3 |
| `/deployment-gate` | M5 + L5 Fleet Gate | Flow 2 Deployment Gate |
| `/audit-log` | M5 immutable log | Flow 3 Compliance |

## API design principles

> [!tip]
> 1. **Runs are resources, jobs are side effects** — everything keys off `validation_id`; comparability and history fall out naturally.
> 2. **Gate is a first-class verb** — `POST /deployment-gate` returns `{approve/block + reasoning}` as machine-readable JSON so CI can fail the build ([[Product Principles]] #4).
> 3. **gRPC internally, REST externally** — high-throughput inter-service calls use Protocol Buffers; the public surface stays OpenAPI-friendly for CLI + Actions plugin ([[CLI Design]], [[GitHub Actions Integration]]).
> 4. **Versioned from day one** (`/api/v1`) — fleet operators pin integrations per-robot; breaking changes are a billing event ([[Business Model]] API/integration licensing).

## Auth & tenancy (MVP vs later)

- MVP: single project, API key auth (`secrets.VALIDSIM_API_KEY`)
- Post-MVP: Auth0/Clerk multi-tenant + RBAC; enterprise SSO ([[MVP Non-Goals]], [[Pricing Tiers]])

Links: [[CLI Design]] · [[GitHub Actions Integration]] · [[Module Specs]] · [[Data Flow]] · [[Home]]
