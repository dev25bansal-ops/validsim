---
tags:
  - engineering
  - job-queue
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🗂️ Async Job Queue

Decouples validation submission from execution. Jobs are enqueued via Redis (with an in-memory fallback for local/dev) and consumed asynchronously, so `POST /validations` stops blocking the caller and becomes fire-and-forget. Lives inside M1 Ingestion & Orchestration ([[Module Specs]]), extends the REST surface in [[API Design]], and rewrites Step 2 of [[Data Flow]].

## Why async (the problem it unblocks)

Today `POST /api/v1/validations` is **synchronous**: the request stays open through queueing, GPU scheduling, and simulation. A single run is 15–45 min ([[Data Flow]] latency budget), which means:

| Failure mode | Consequence |
|---|---|
| Client timeout | CI/webhook drops before a result returns |
| Head-of-line blocking | One slow run stalls every other submitter |
| No retry/observability surface | No handle to poll or resume a run |
| Coupled lifecycle | Status changes are invisible until the response closes |

The queue turns submission into a `202 Accepted` + `job_id`, and the lifecycle is polled via `/jobs/{id}` instead of held open on the socket.

## Architecture

```
POST /validations ─▶ Pydantic parse ─▶ JobSpec ─▶ enqueue ─▶ 202 {job_id}
                                                          │
                                   VALIDSIM_JOB_QUEUE ────┤
                                   ┌──────────┐  ┌────────┴───────┐
                                   │  memory  │  │     redis      │
                                   │ (in-proc │  │  (VALIDSIM_    │
                                   │  queue)  │  │   REDIS_URL)   │
                                   └────┬─────┘  └────────┬───────┘
                                        └────▶ worker pool ──▶ K8s/Argo
                                                              │
                                                              ▼
                                              JobRecord.status updates
                                              (queued → running → ...)
```

- **Redis backend** — durable, multi-worker, survives restarts; the production path ([[Module Specs]] M1 already names Redis 7.x).
- **In-memory fallback** — zero-dependency queue for local/dev/CI; drops jobs on process exit, which is acceptable because no GPU workload is lost mid-flight in that context.

## Dataclasses

The queue's type surface is three dataclasses, all in the ingestion module:

```python
@dataclass
class JobSpec:
    validation_id: str            # FK back to the validation run
    checkpoint: str               # s3://models/gr00t_v42.pt
    task_config: dict             # task, episodes, randomization, adversarial
    robot_spec: dict              # embodiment, format
    environment: dict             # scene, format
    priority: int = 0             # higher = dequeued first
    created_at: datetime = field(default_factory=utcnow)

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    EVALUATING = "evaluating"
    REPORTED = "reported"
    FAILED = "failed"

@dataclass
class JobRecord:
    job_id: str                   # opaque handle returned to the caller
    spec: JobSpec                 # immutable once submitted
    status: JobStatus             # updated by the worker as it progresses
    result: dict | None = None    # populated at REPORTED (scorecard ref, gate decision)
    error: str | None = None      # populated at FAILED (traceback summary)
    updated_at: datetime = field(default_factory=utcnow)
```

`JobStatus` mirrors the response lifecycle in [[API Design]] (`queued → running → evaluating → reported → gate:{APPROVE|BLOCK}`), splitting `reported` from the gate so the queue owns execution and the fleet gate stays a first-class verb.

## REST router — `/jobs`

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Enqueue a `JobSpec`; returns `202 {job_id}` immediately |
| `GET` | `/api/v1/jobs` | List jobs, filterable by `?status=queued|running|...` |
| `GET` | `/api/v1/jobs/{id}` | Poll one `JobRecord` (status + result/error) |

Request shape — `POST /api/v1/jobs` (same body as `POST /validations`, re-keyed as a job):

```json
{
  "validation_id": "val_9f3a",
  "checkpoint": "s3://models/gr00t_v42.pt",
  "task_config": { "task": "bin_picking", "episodes": 5000,
                   "randomization": "full", "adversarial": 100 },
  "robot_spec": { "embodiment": "franka_panda", "format": "URDF" },
  "environment": { "scene": "warehouse_a", "format": "USD" }
}
```

## Environment variables

| Variable | Values | Default | Purpose |
|---|---|---|---|
| `VALIDSIM_JOB_QUEUE` | `memory` \| `redis` | `memory` | Select the queue backend |
| `VALIDSIM_REDIS_URL` | `redis://host:port[/db]` | — (required when `redis`) | Redis connection for the enqueue/dequeue path |

> [!tip]
> Keep `memory` the default so `validsim run` works out of the box on a laptop with no infrastructure. Flip to `redis` only when `VALIDSIM_REDIS_URL` is set — mirroring the MVP vs post-MVP split in [[API Design]] Auth & tenancy.

## Non-blocking validation (before/after)

| | Synchronous (today) | Async (this design) |
|---|---|---|
| Submission | Blocks until reported | `202 Accepted` + `job_id` |
| Caller contract | Socket held open | Poll `GET /jobs/{id}` or webhook |
| Scaling | Head-of-line blocking | Worker pool drains independently |
| Failure surface | Lost on timeout | `JobRecord.error` + `FAILED` status |

The change is additive: `POST /validations` keeps working, but now stubs a `JobSpec` onto the queue and returns `202` instead of holding the connection — the `validation_id` still keys history and comparability per [[API Design]] principle #1 ("runs are resources, jobs are side effects").

Links: [[Solution Architecture]] · [[Module Specs]] · [[API Design]] · [[Data Flow]] · [[Home]]