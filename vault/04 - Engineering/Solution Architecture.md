---
tags:
  - engineering
  - architecture
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🏗️ Solution Architecture

Cloud-native platform between model training and real-world deployment. Five layers, top to bottom, plus a cross-cutting observability layer. Every request surface — the synchronous API, the CLI, and the async job worker — converges on a single shared engine pipeline (`run_and_score`); the async job queue (Redis/memory) is an additive path feeding that same pipeline. Component-level specs: [[Module Specs]]; runtime sequence: [[Data Flow]]; technology choices: [[Tech Stack]].

> [!important] Blueprint status — 2026-09-21
> The diagram and five-layer table are target architecture. The shipped runtime is FastAPI/Typer + one shared engine, memory/Redis jobs, SQLite/PostgreSQL, static HTML/JS, Docker Compose, local Actions and optional HTTP Isaac worker. Kubernetes/Argo, RabbitMQ, gRPC, Next.js/React, immutable audit and fleet integration are not implemented in this repository.

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
│  • Adversarial Scenarios — 12 categories                            │
│    (shipped: deterministic rule-based generator;                    │
│     LLM generation is target state, not wired into a run)           │
│  • Physics Fidelity Layer (PhysX 5)                                 │
│  • Multi-embodiment support (GR00T, pi0, custom VLAs)               │
│  • Parallel episode runner (1,000–100,000 episodes per validation)  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L3 · EVALUATION & SCORING ENGINE                   │
│  • Success Rate Calculator (per task, per scenario)                 │
│  • Failure Taxonomy Classifier (rule-based; LLM is target)          │
│  • Regression Delta Engine (compare vs. previous checkpoint)        │
│  • Safety Score Module (collision, force limits, human proximity)   │
│  • Statistical Significance Testing (confidence intervals)          │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L4 · REPORTING & DASHBOARD LAYER                   │
│  • Web Dashboard (static SPA; Next.js is target)                    │
│  • Scorecard Generator (PDF)           • Alerting & Webhooks        │
│  • Programmatic API                    • Audit Trail (immutable)    │
│         (webhook dispatcher + SMTP notifier exist as libraries,    │
│          but nothing in the run path dispatches a scorecard)        │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L5 · INTEGRATION & DEPLOYMENT LAYER                │
│  • GitHub Actions / GitLab CI Plugin   • ROS 2 Bridge (HIL)         │
│  • NVIDIA Omniverse Connector          • Model Registry (MLflow/W&B)│
│  • Fleet Deployment Gate (approve/block deploy based on score)      │
└─────────────────────────────────────────────────────────────────────┘
```

## Request paths & the shared engine pipeline

The five layers above are a *topology*; at runtime they collapse into one reusable sequence. A single function — `run_and_score` (`validsim/engine/pipeline.py`) — owns the full `seed → scenarios → simulate → evaluate → safety → scorecard → persist` flow, and the three entry surfaces are thin adapters that only supply caller-specific inputs:

```
┌── sync API ─────────┐
│ POST /validations   │
├── CLI ──────────────┤        ┌──────────────────────────┐
│ validsim run        │──────▶ │  run_and_score (shared)  │──▶ StoredRun
├── async JobWorker ──┤        └──────────────────────────┘   (store.save)
│ POST /jobs → queue  │                     ▲
└─────────────────────┘   backend · run_id ─┘   (worker injects its own)
```

- **Why shared:** before this the sequence was duplicated verbatim in three places, so a change to one (e.g. the scorecard inputs) could silently drift from the others. `run_and_score` owns it once.
- **Async job queue as a new path:** `POST /api/v1/jobs` enqueues a `JobSpec` on a memory or Redis queue (`VALIDSIM_JOB_QUEUE`, `VALIDSIM_REDIS_URL`); a `JobWorker` claims it and calls the *same* pipeline, writing the persisted run under the job's own `run_id` (shared-key contract). Status is polled via `GET /jobs/{id}` or streamed over SSE ([[Async Job Queue]], [[Job Queue Worker]], [[Data Flow]]).
- **Domain errors, not HTTP, inside the engine:** a missing baseline raises `BaselineNotFoundError`, which only the API adapter maps to `404`.

## Observability layer (cross-cutting)

Not a sixth tier but middleware wrapped around L1, always on:

| Surface | What it emits | Where |
|---|---|---|
| Request-id middleware | Reuses inbound `X-Request-ID` or mints a `uuid4`; echoes it on every response and captures the final status (incl. 401/429) | outermost ASGI middleware |
| Structured logging | One JSON line per request — `method`, `path`, `status`, `duration_ms`, `request_id` (`VALIDSIM_LOG_LEVEL`) | `validsim/logging.py` |
| Prometheus metrics | `validsim_runs_total`, `validsim_approvals_total`, `validsim_blocks_total`, `validsim_composite_score`, `validsim_build_info`, `validsim_http_requests_total{class=…}` | `/metrics` (unauthenticated scrape) + `/api/v1/metrics` (auth-gated) |
| Health probe | `status`, `version`, auth/CORS/rate-limit config, live `store_backend` / `job_queue_backend` labels | `/api/v1/health` |

Run/approval/block gauges are derived from the injected store at scrape time (one cheap `history()` pass), so a scrape adds no extra I/O. Current repository tooling is a Prometheus text endpoint plus structured logs; Grafana and ELK are target-state integrations ([[Tech Stack]]).

## Store: pruning & date-range queries

The validation store (`ValidationStore` and its SQLite/Postgres backends) exposes two lifecycle operations the reporting layer (L4) builds on:

- **`history(since, until)`** — oldest-first, optionally bounded by inclusive ISO-8601 `created_at` bounds (lexicographic compare on the fixed UTC format). Backs `GET /api/v1/validations?since=…&until=…`; a malformed bound yields `422`.
- **`delete(run_id)`** — removes a record and returns whether it existed, under the store lock (idempotent, no check-then-delete race). Backs `DELETE /api/v1/validations/{run_id}` (→ `204`/`404`, gated by the dedicated destructive-auth dependency) and `validsim delete` ([[API Design]], [[Security Hardening]]).

## Architectural decisions (and their consequences)

| Decision | Why | Consequence |
|---|---|---|
| **Isaac Sim/Lab as the target sim substrate** | Mock backend ships now; planned GPU worker speaks HTTP. Open-source, PhysX 5, GPU-parallel; NVIDIA courting ecosystem ([[Why Now (2026)]] Force 4) | Platform risk → validate the worker before claiming NVIDIA-native throughput ([[Risk Register]] #2) |
| **Queue today; K8s/Argo later** | Current memory/Redis FIFO is adequate for the local MVP; validation is bursty, GPU-bound and embarrassingly parallel | No scheduler/autoscaling today; batch cost control remains a later decision ([[Risk Register]] #6) |
| **Deterministic 12-category scenario taxonomy today; LLM in the loop as the differentiator** | The taxonomy is generated from a seeded rule-based generator, not hand-written per run ([[Module Specs]] M3) | Reproducible today; the LLM path is a **target** — `engine/pipeline.py:131` never constructs the LLM generator, so do not present LLM scenario generation as a shipped capability ([[Moat]]) |
| **CI-native entry points (Actions/CLI/API)** | Developer-native principle ([[Product Principles]] #6) | Free/Dev tier funnel → [[Go-to-Market]] Phase 2 |
| **Deployment gate as a layer, not a feature** | The scorecard is a decision ([[Product Principles]] #4) | Sticky workflow → NRR >120% ([[Unit Economics]]) |
| **Immutable audit trail (target)** | Insurers/regulators are Tier-4 buyers ([[Buyer Tiers]]) | Current history is not hash-chained and `DELETE` is supported; compliance retention remains a product/build decision ([[Compliance]]) |
| **Single shared engine pipeline (`run_and_score`)** | API, CLI and worker must emit identical scorecards; duplication caused drift | One place to change evaluation/scoring; entry surfaces stay thin |
| **Async queue as an additive path, not a rewrite** | Long runs shouldn't hold the socket ([[Async Job Queue]]) | Sync `POST /validations` keeps working; `202` + poll/SSE for batch & GPU |
| **Observability as middleware, not a service** | A 2-founder team can't run a metrics backend ([[Strategic Advantages]]) | Request-id, structured logs and Prometheus free from day one; scrape is store-derived |
| **Store delete + date-range as first-class ops** | History grows unbounded; auditors query by window | Prune via `DELETE`, slice via `since`/`until` ([[Compliance]]) |

## Scale envelope

- Target episodes per validation: **1,000–100,000** (planned MVP benchmark: 1,000–5,000, [[MVP Scope]])
- Target GPU spread: **8–64 GPUs** per run; assumed min **10GB VRAM** per Isaac Gym instance; unmeasured today
- Adversarial scenarios injected: **50–100 per run**
- Latency budget: 5,000 episodes < 30 min; scorecard +5 min — **targets, not measurements**; requires GPU/Isaac, and the shipped engine is a serial CPU mock ([[MVP Success Metrics]])
- Async job queue depth cap: **1,000 jobs** by default (`VALIDSIM_JOB_QUEUE_MAX_DEPTH`); a full queue returns `503` back-pressure rather than growing without limit

Links: [[Module Specs]] · [[Data Flow]] · [[Async Job Queue]] · [[Job Queue Worker]] · [[Tech Stack]] · [[API Design]] · [[Home]]
