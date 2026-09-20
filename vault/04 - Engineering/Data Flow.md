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
- **Shared engine pipeline.** Every surface — the synchronous API (`POST /api/v1/validations`), the CLI (`validsim run`/`validate`), and the async job worker — executes the *same* `run_and_score` sequence (Steps 3–5 below), so the scorecard inputs can never drift between paths

## Step 2: QUEUE
```
Job enters Redis queue
→ Orchestrator assigns GPU resources (Kubernetes + Argo Workflows)
→ Isaac Sim container spins up with specified scene
```
- Priority queuing + dead-letter handling; GPU node pool auto-scales on DGX Cloud ([[Tech Stack]])
- **Async job queue (shipped path).** `POST /api/v1/jobs` enqueues a `JobSpec` on a memory or Redis queue (`VALIDSIM_JOB_QUEUE`) and returns `202 {job_id}` immediately; a `JobWorker` later claims it and runs the *identical* `run_and_score` pipeline, persisting the result under the job's own `run_id` ([[Async Job Queue]], [[Job Queue Worker]])

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
- History is queryable by date range (`GET /api/v1/validations?since=…&until=…`, inclusive ISO-8601 bounds on `created_at`) and prunable (`DELETE /api/v1/validations/{run_id}` → `204`, or `validsim delete`), both backed by the store's `history(since, until)` / `delete()` ([[API Design]])

## Step 6: DEPLOY (or don't)
```
If approved → checkpoint deployed to fleet
If blocked → developer receives failure analysis + regression details
→ Audit log updated (immutable, timestamped)
```

> [!important] The audit trail is the compounding asset
> Every run — approved or blocked — appends a hash-chained record. That log is: (a) the insurer-facing product for Tier-4 buyers ([[Buyer Tiers]], [[Compliance]]), (b) the training corpus for the failure-taxonomy flywheel ([[Moat]]), and (c) the liability shield when a field failure is questioned ([[Pain Quantified]]).

## Async submission flow

The six steps above describe the *synchronous* run (API/CLI hold the connection through Steps 3–5). The async path decouples submission from execution while reusing the exact same engine pipeline:

```
POST /api/v1/jobs ─▶ JobSpec ─▶ enqueue (memory | Redis) ─▶ 202 {job_id}
                                                              │
                                                JobWorker.run_once()
                                                              ▼
                                  run_and_score  (Steps 3–5, identical)
                                                              ▼
                                  StoredRun persisted ─▶ JobRecord: queued→running→done|failed
                                                              │
                                  caller polls  GET /jobs/{id}[/status]  or streams /events (SSE)
```

- **Same pipeline, different transport.** The worker injects its own backend and the job's `run_id` into `run_and_score`, so the queue's `result` and the stored run share one key — no lookup table ([[Job Queue Worker]]).
- **Fire-and-forget.** Submission returns in milliseconds; the 15–45 min simulation no longer blocks the socket, removing the CI/webhook timeout that motivated [[Async Job Queue]].
- **Bounded overload.** The queue caps at `max_depth` (default 1,000); a full queue returns `503` + `Retry-After` instead of growing without limit.

> [!note] Observability spans every step
> Each request is stamped with an `X-Request-ID` (reused if supplied, else a fresh `uuid4`) and echoed on the response; one structured JSON log line (method, path, status, duration_ms, request_id) is emitted per request, and a Prometheus scrape at `/metrics` exposes run/approval/block gauges plus HTTP-request counters by status class. The `/api/v1/health` probe reports the live store and queue backends ([[Tech Stack]], [[Module Specs]]).

## Latency budget (single run)

| Stage | Budget |
|---|---|
| Submit → queue | seconds |
| Simulate (5,000 episodes) | 15–45 min (target <30 min, 4× A100) |
| Evaluate + report | < 5 min |
| Engineer's total wait | **< 1 hour** (persona goal, [[User Personas]]) |

Links: [[Module Specs]] · [[API Design]] · [[Async Job Queue]] · [[Job Queue Worker]] · [[Scorecard UX]] · [[Product Principles]] · [[Home]]
