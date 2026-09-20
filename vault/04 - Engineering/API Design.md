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

## Async job endpoints — `/api/v1/jobs`

Long-running or batched validations run on an asynchronous queue ([[Async Job Queue]], [[Job Queue Worker]]) instead of blocking a request. The router is mounted under `/api/v1` and inherits the same `X-API-Key` gate as the rest of the API. Job ids share the run-id shape `vrun-` + 8 hex chars; a malformed id is rejected with `400` before it ever reaches the queue.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Enqueue a validation job → `202 {job_id, status}` |
| `GET` | `/api/v1/jobs` | List jobs (bare array, or `{total,limit,offset,items}` when paginated) |
| `GET` | `/api/v1/jobs/{id}` | Full job record (spec + lifecycle timestamps) |
| `GET` | `/api/v1/jobs/{id}/status` | Compact `{job_id, status, updated_at}` snapshot |
| `GET` | `/api/v1/jobs/{id}/events` | Server-Sent-Events stream until the job is terminal |

## Request shape — `POST /api/v1/jobs`

```json
{
  "checkpoint_id": "gr00t_v42",
  "task_id": "bin_picking",
  "episodes": 5000,
  "adversarial": 100
}
```

`episodes` defaults to `1000` (1–100000), `adversarial` to `0` (0–1000). Success returns `202 {"job_id": "vrun-1a2b3c4d", "status": "queued"}`. When the queue is already at its `max_depth` cap (default 1000, env `VALIDSIM_JOB_QUEUE_MAX_DEPTH`), enqueue returns `503 {"error":"queue_full","max_depth":N}` with a `Retry-After: 5` header so well-behaved clients back off instead of hammering — a full queue is an expected overload condition, not a server error.

`GET /api/v1/jobs` is backwards-compatible: with neither `limit` nor `offset` it returns the historical bare JSON array (FIFO order); supplying either switches to the same newest-first `{total, limit, offset, items}` envelope used by `GET /validations` (`limit` 1–500 defaulting to 100, `offset ≥ 0`, out-of-range → `422`).

## Job lifecycle & SSE

`queued → running → done` (success) or `queued → running → failed` (backend/engine error) — see [[Data Flow]]. The queue and the validation store share the `run_id` key, so a finished job's `result` points straight at the persisted run.

`GET /jobs/{id}/events` streams `text/event-stream`: one `data: {job_id, status, updated_at}` frame per poll (default 1s), terminated by `event: end` once the job reaches a terminal state. A job that never finishes cannot pin a worker thread — the stream is bounded (default 60s) and closes with `event: timeout` so clients can reconnect. An unknown id returns `404` before the stream opens.

## Run deletion — `DELETE /api/v1/validations/{id}`

Permanently removes a stored run. Returns `204` (no body) on success and `404` when the id is unknown; the store's single-step delete doubles as the existence check, so there is no check-then-delete race. Because it is destructive it carries its own auth gate (`require_api_key_for_destructive`) on top of the global `/api/v1` toggle — a deployment that exposes `DELETE` over a network MUST set `VALIDSIM_API_KEY` ([[Security Hardening]]).

## Scorecard renderers — `.md` / `.html` / `.pdf`

Beyond the raw JSON at `/validations/{id}/scorecard`, the reporting module ([[Module Specs]] M5) renders the same scorecard into distributable formats ([[Scorecard UX]]):

| Endpoint | Media type | Use |
|---|---|---|
| `GET /validations/{id}/scorecard.md` | `text/markdown` | CI job summaries (verdict, metrics + failure-taxonomy tables, 95% CI) |
| `GET /validations/{id}/scorecard.html` | `text/html` | Email / in-browser card (self-contained, inline CSS, HTML-escaped) |
| `GET /validations/{id}/scorecard.pdf` | `application/pdf` | One-page branded attachment (`Content-Disposition: attachment; filename="{id}-scorecard.pdf"`) |

All three `404` on an unknown run; the PDF additionally returns `501` when the optional `reportlab` dependency is absent (the rest of the platform runs fine without it — only PDF export is gated).

## Operations — `/health` & `/metrics`

`GET /api/v1/health` is a no-I/O liveness/readiness probe (safe to back a container healthcheck). Alongside the stable `status` / `version` keys it reports the effective runtime config: `auth_enabled`, `cors_wildcard`, `rate_limit` (`{requests, window_seconds}` or `null`), and `store_backend` / `job_queue_backend` labels (`memory` / `sqlite` / `postgres` / `redis`).

`GET /metrics` serves Prometheus text exposition (`text/plain; version=0.0.4`) derived from the injected store in a single pass: `validsim_runs_total`, `validsim_approvals_total`, `validsim_blocks_total`, `validsim_composite_score` (latest run, `0` when empty), `validsim_build_info{version="…"}`, and `validsim_http_requests_total{class="2xx|3xx|4xx|5xx"}` counted in-process by the observability middleware. It is mounted twice — `/api/v1/metrics` (auth-gated) and bare `/metrics` (unauthenticated, the path Prometheus scrapes by convention).

## Auth & rate limiting (implemented)

Deployment hardening is env-driven and read once at app-build time ([[Security Hardening]]):

- **`X-API-Key` auth** — when `VALIDSIM_API_KEY` is set, every `/api/v1` route requires a matching `X-API-Key` header (constant-time compare; missing / empty / wrong all yield a uniform `401` that never reveals whether a key exists). Unset ⇒ auth off, keeping local dev and the test suite open. `DELETE` keeps its own gate regardless.
- **IP-keyed rate limiting** — `VALIDSIM_RATE_LIMIT` (requests per `VALIDSIM_RATE_WINDOW_SECONDS`, default 60; `0` disables, which is also the default) applies a process-local sliding window to the write/sensitive routes only: `POST /validations`, `POST /jobs`, and `POST /validations/{id}/compare`. Buckets are keyed on the **client IP alone** — never a caller-supplied header, so rotating `X-API-Key` cannot mint a fresh bucket — and over-limit requests get `429` with `Retry-After`. Memory is bounded by a max-bucket cap plus a periodic stale-bucket sweep.
- **Request correlation** — every response echoes an `X-Request-ID` (an inbound value is reused, otherwise a fresh `uuid4`); the outermost middleware logs one structured line per request and feeds the `/metrics` counters.
- **CORS** — `VALIDSIM_CORS_ORIGINS` (comma-separated allow-list, default `*`).

## API design principles

> [!tip]
> 1. **Runs are resources, jobs are side effects** — everything keys off `validation_id`; comparability and history fall out naturally.
> 2. **Gate is a first-class verb** — `POST /deployment-gate` returns `{approve/block + reasoning}` as machine-readable JSON so CI can fail the build ([[Product Principles]] #4).
> 3. **gRPC internally, REST externally** — high-throughput inter-service calls use Protocol Buffers; the public surface stays OpenAPI-friendly for CLI + Actions plugin ([[CLI Design]], [[GitHub Actions Integration]]).
> 4. **Versioned from day one** (`/api/v1`) — fleet operators pin integrations per-robot; breaking changes are a billing event ([[Business Model]] API/integration licensing).

## Auth & tenancy (MVP vs later)

- MVP: single project, API key auth (`secrets.VALIDSIM_API_KEY`)
- Post-MVP: Auth0/Clerk multi-tenant + RBAC; enterprise SSO ([[MVP Non-Goals]], [[Pricing Tiers]])

Links: [[CLI Design]] · [[GitHub Actions Integration]] · [[Module Specs]] · [[Data Flow]] · [[Async Job Queue]] · [[Job Queue Worker]] · [[Security Hardening]] · [[Home]]
