---
tags: [engineering, api, jobs, queue, redis, sse, operations]
status: prototype (v0.2.0)
---

# ⏳ Asynchronous Validation Jobs

The async path for ValidSim: instead of blocking an HTTP request until a whole
validation finishes, you **enqueue** a job, get an id back immediately, and let a
separate **worker** drain the queue and persist the scorecard. This document
covers the sync-vs-async split, the job lifecycle, the REST endpoints in
[`validsim/jobs/router.py`](../validsim/jobs/router.py), the CLI in
[`validsim/cli.py`](../validsim/cli.py), the two queue backends in
[`validsim/jobs/queue.py`](../validsim/jobs/queue.py), the worker in
[`validsim/jobs/worker.py`](../validsim/jobs/worker.py), the environment
variables, the depth-cap and SSE-deadline protections, and how to run the worker
under `docker compose`.

It is the async counterpart to the synchronous routes in
[[ValidSim REST API — Reference]]; the operational view (env vars, incident
playbook) lives in [docs/runbook.md](runbook.md), and the GPU execution contract
the worker eventually drives is in [[Isaac Sim GPU Worker — HTTP Contract]].

> [!important] Same engine, different timing
> Sync and async run the **identical** pipeline
> (`run_validation` → `evaluate` → `compute_safety` → `build_scorecard`, wrapped
> by `validsim.engine.pipeline.run_and_score`). The only difference is *when* it
> runs: synchronously inside the request, or later inside a worker process. A job
> that succeeds writes the same `StoredRun` a synchronous `POST /validations`
> would have — so the dashboard, `/models`, and `validsim gate` all see it.

---

## 1. Sync vs. async — which one to call

| | `POST /api/v1/validations` (sync) | `POST /api/v1/jobs` (async) |
|---|---|---|
| Returns | `201` with the full scorecard | `202` with `{job_id, status}` |
| Blocks until | episodes + evaluation + scorecard are done and persisted | the job is merely *queued* |
| Who runs the pipeline | the API request thread | a `JobWorker` process |
| Best for | small episode counts, CI one-shots | long runs, batches, GPU work, anything you don't want to hold a connection open for |
| Backend needed | none (in-process) | a worker + a shared queue (Redis) for multi-process |

The MVP API reference notes "no job queue exists"; that is now superseded — the
`jobs` router is mounted in `create_app()` and the async path is live. Keep the
sync route for the CI gate (`validsim gate`); reach for `/jobs` when a request
would otherwise hang for minutes.

**The shared key.** A job's `run_id` **is** its `job_id` (`vrun-<uuid8>`, from
`JobQueue.new_run_id()`). When the worker finishes, it persists the run under
that *same* id, so the queue record's `result` field points directly at the
stored scorecard. There is no id translation layer between "job" and "run" —
`GET /api/v1/jobs/{id}` tells you the lifecycle, and
`GET /api/v1/validations/{id}/scorecard` returns the artifact, both keyed by the
one id.

---

## 2. Job lifecycle

A job is a frozen `JobRecord` (see
[`validsim/jobs/models.py`](../validsim/jobs/models.py)) that moves through five
states. Transitions never mutate in place — each produces a new record via
`dataclasses.replace`.

```
enqueue()          run_once() claims        pipeline succeeds
  ┌────────┐         ┌─────────┐              ┌──────┐
  │ queued │────────▶│ running │─────────────▶│ done │  result = run_id
  └────────┘         └─────────┘              └──────┘
                          │
                          │ pipeline raises
                          ▼
                     ┌────────┐
                     │ failed │  error = str(exc)
                     └────────┘
```

| Status | Set when | Timestamps stamped | Payload fields |
|---|---|---|---|
| `queued` | `enqueue()` adds the job | `created_at` | — |
| `running` | a worker claims it (`run_once`) | `started_at` (first time only) | — |
| `done` | the pipeline returns a `StoredRun` | `finished_at` | `result` = persisted run id |
| `failed` | the backend/engine raises, and the attempt budget is spent | `finished_at` | `error` = exception message |
| `dead` | dead-lettered: the retry or reclaim budget ran out before it ever completed | `finished_at` | `error` = why it was abandoned |

`failed` and `dead` are kept apart deliberately. `failed` ran and reported an
error; `dead` never got to report anything — its worker died, or its payload
was unreadable. Folding them together would hide exactly the poison-job case an
operator needs to see, and would make the retry budget invisible in job history.

`started_at` is written the **first** time a job becomes `running` and is never
overwritten; `finished_at` is written on **any** terminal state. Because each
transition stamps exactly one timestamp, the API derives an effective
"last updated" marker as the freshest of `finished_at → started_at → created_at`
(the `updated_at` field on `/status` and SSE frames).

The worker marks a job `running` *before* executing the pipeline, so a poller or
SSE client observes the intermediate state rather than a single queued→done jump.

---

## 3. REST endpoints

The router carries `prefix="/jobs"` and is included by `create_app()` with
`prefix="/api/v1"`, so the public paths are `/api/v1/jobs…`. It is mounted
**after** the auth dependency, so every route inherits the same `X-API-Key` gate
as the rest of the API (401 when `VALIDSIM_API_KEY` is set and the header is
missing/wrong). `POST /api/v1/jobs` is also inside the write-route rate-limit
scope when `VALIDSIM_RATE_LIMIT` is enabled.

| Method | Path | Purpose | Errors |
|---|---|---|---|
| `POST` | `/api/v1/jobs` | Enqueue a job; returns id + initial status (`202`) | `422` bad body |
| `GET` | `/api/v1/jobs` | List all jobs in FIFO order; `limit` and/or `offset` activates a newest-first pagination envelope | `422` pagination values out of range |
| `GET` | `/api/v1/jobs/{job_id}` | Full record for one job | `400` malformed id, `404` unknown |
| `GET` | `/api/v1/jobs/{job_id}/status` | Compact `{job_id, status, updated_at}` | `400`, `404` |
| `GET` | `/api/v1/jobs/{job_id}/events` | Server-Sent Events status stream | `400`, `404` (before opening) |

### 3.1 Job-id validation (`400` vs `404`)

`{job_id}` must match `^vrun-[0-9a-f]{8}$` — the exact shape `new_run_id()`
produces. A **malformed** id is rejected with `400` *before* it reaches the
queue; a **well-formed but unknown** id falls through to the normal `404`.

> [!note] Why validate the id at all (audit L1)
> The Redis backend interpolates the id straight into a key
> (`validsim:jobs:<job_id>`). An unvalidated path parameter could collide with
> the queue's own keys — e.g. a request for `…/jobs/index` would read the
> insertion-order index list. The regex closes that hole.

### 3.2 `POST /api/v1/jobs`

**Request body — `EnqueueJobRequest`** (Pydantic v2, strict; invalid input → `422`):

```json
{
  "checkpoint_id": "checkpoint-abc123",
  "task_id": "pick-and-place",
  "episodes": 1000,
  "adversarial": 10
}
```

| Field | Required | Constraints |
|---|---|---|
| `checkpoint_id` | yes | non-empty string |
| `task_id` | yes | non-empty string |
| `episodes` | no | 1–100000, default `1000` |
| `adversarial` | no | 0–1000, default `0` |

**Response `202`:**

```json
{ "job_id": "vrun-1a2b3c4d", "status": "queued" }
```

The `run_id` is generated server-side; the caller never supplies it.

### 3.3 `GET /api/v1/jobs` and `GET /api/v1/jobs/{job_id}`

With neither `limit` nor `offset`, the list endpoint returns a bare array of
full `JobRecord.to_dict()` values in FIFO insertion order. Supplying either
pagination parameter switches it to `{total, limit, offset, items}`; `items`
holds the same record shape in **newest-first** order. `limit` is 1–500
(default `100` when only `offset` is supplied), `offset` is ≥0 (default `0` when
only `limit` is supplied), and out-of-range values return `422`. `total` counts
all queued jobs independently of the page. An offset beyond the total is valid
and returns an empty `items` list.

```json
{
  "total": 1,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "job_id": "vrun-1a2b3c4d",
      "status": "done",
      "spec": {
        "run_id": "vrun-1a2b3c4d",
        "checkpoint_id": "checkpoint-abc123",
        "task_id": "pick-and-place",
        "episodes": 1000,
        "adversarial": 10
      },
      "created_at": "2026-03-20T12:00:00+00:00",
      "started_at": "2026-03-20T12:00:05+00:00",
      "finished_at": "2026-03-20T12:04:11+00:00",
      "error": null,
      "result": "vrun-1a2b3c4d"
    }
  ]
}
```

`GET /api/v1/jobs/{job_id}` returns one full job record in the same shape as an
entry in `items[]` (or in the parameterless bare array). `status` ∈
`queued | running | done | failed | dead`; `started_at` is stamped the first time a
worker claims the job, `finished_at` on any terminal state; `error` is populated
only when `failed`, and `result` only when `done`.

### 3.4 `GET /api/v1/jobs/{job_id}/status`

The lightweight counterpart for status bars and SSE clients — only three fields:

```json
{ "job_id": "vrun-1a2b3c4d", "status": "running", "updated_at": "2026-03-20T12:00:05+00:00" }
```

### 3.5 `GET /api/v1/jobs/{job_id}/events` (SSE)

A `text/event-stream` that polls the queue and emits one `data:` frame per poll
carrying the compact status. It terminates cleanly in three ways:

* **Terminal state** — after the last `data:` frame for `done`/`failed`, an
  `event: end` frame is emitted and the stream closes.
* **Bounded lifetime** — if the job never reaches a terminal state, the stream
  stops at the deadline (§6) with an `event: timeout` frame so the client can
  reconnect.
* **Job vanished** — if the id disappears from the queue, the stream simply ends.

```
data: {"job_id": "vrun-1a2b3c4d", "status": "queued",  "updated_at": "…"}

data: {"job_id": "vrun-1a2b3c4d", "status": "running", "updated_at": "…"}

data: {"job_id": "vrun-1a2b3c4d", "status": "done",    "updated_at": "…"}

event: end

```

Consume it with any SSE client (`EventSource` in a browser, `curl -N`):

```bash
curl -N http://127.0.0.1:8000/api/v1/jobs/vrun-1a2b3c4d/events
```

The `400`/`404` checks happen **before** the stream opens, so a bad id never
becomes an endless empty stream.

---

## 4. CLI

The same queue and worker are reachable from the command line, which is handy
for local runs and for driving the worker in a container.

```bash
# Enqueue one job (VALIDSIM_JOB_QUEUE selects the backend; memory by default)
validsim job enqueue --checkpoint checkpoint-abc123 --task pick-and-place \
  --episodes 1000 --adversarial 10
# → Enqueued job vrun-1a2b3c4d (status: queued)

# List jobs in the configured queue: JOB ID · STATUS · CHECKPOINT · CREATED
validsim jobs

# Consume the queue: run ONE job then exit…
validsim worker --once
# → worker: job vrun-1a2b3c4d -> done
# …or loop until interrupted (Ctrl-C), idling --poll-seconds between empty polls
validsim worker --poll-seconds 1.0
```

* `validsim job enqueue` accepts `-c/--checkpoint` (required), `-t/--task`
  (default `pick-place`), `-e/--episodes` (default `1000`, min 1), and
  `-a/--adversarial` (default `0`, min 0). It exits `0` on success.
* `validsim jobs` always exits `0`; an empty queue prints `no jobs queued`.
* `validsim worker` wires a `JobWorker` to `create_job_queue()`,
  `create_store()`, and `create_backend()`. With `--once` it claims and runs a
  single job (printing `worker: job <id> -> <status>`, or `worker: no queued
  jobs` when empty) and exits; without it, it loops until interrupted.

> [!warning] Memory backend is per-process
> `validsim job enqueue` and `validsim worker` are separate processes. With the
> default in-memory backend the worker cannot see a job the CLI enqueued — each
> process has its own queue. Use `VALIDSIM_JOB_QUEUE=redis` (with a reachable
> `VALIDSIM_REDIS_URL`) to share one queue across processes.

---

## 5. Backends: in-memory vs. Redis

Both backends expose an identical interface (`enqueue` / `get` / `list` /
`update_status` / `__len__` / `close`), so callers are backend-agnostic.
`create_job_queue()` picks one from the environment.

One difference is worth knowing before you write a caller: on the Redis backend
an *unfenced* `update_status` can raise `QueueContentionError` if another process
out-paced every compare-and-set attempt. The memory backend holds one process's
lock for the whole transition, so it has no such failure mode. Both backends
leave the record untouched in that case — the transition is refused, never
partially applied.

### 5.1 `JobQueue` (memory, default)

Thread-safe, in-process, guarded by a `threading.Lock`. Jobs live in a dict
keyed by `run_id`; `list()` returns insertion (FIFO) order. It is the default so
tests and single-process local runs stay side-effect free — no driver, no
network. `close()` is a no-op.

### 5.2 `RedisJobQueue` (multi-process)

Subclasses `JobQueue` purely to advertise interface compatibility (and reuse
`new_run_id`); every state-touching method is overridden to read/write Redis, so
the API and one or more workers can share a single queue.

**Serialization rules:**

* Each job is a single JSON string under a plain, parameter-free key
  `validsim:jobs:<run_id>` — never a Redis hash, never a pickle, and no URL/query
  parameters baked into keys.
* An insertion-order index is kept as a Redis list `validsim:jobs:index` of run
  ids, preserving FIFO semantics for `list()`.

**Laziness** (mirrors the optional PostgreSQL store):

* The `redis` driver is imported lazily — never at module level — so importing
  `validsim.jobs.queue` always succeeds. A missing driver surfaces as an
  actionable `RuntimeError` (`… Install it with: pip install redis`) at *first
  use*, not at construction.
* The client connection is opened lazily on first use under a lock (redis-py
  clients are not thread-safe), so `create_job_queue()` returns a configured
  queue without touching the network.
* If **no URL is configured** and the driver **is** installed, construction fails
  fast with `ValueError` ("No Redis URL configured…"). With the driver missing,
  construction still succeeds and the more actionable missing-driver error
  surfaces at first use instead.

---

## 6. Protections: depth cap & SSE deadline

Two audit-driven guards keep the async path from growing without bound.

### 6.1 Queue depth cap (audit H3)

The queue holds at most `max_depth` jobs; beyond that `enqueue()` raises
`QueueFullError` instead of growing forever.

* **Default:** `1000`.
* **Resolution precedence:** an explicit `max_depth=` argument wins → otherwise
  `VALIDSIM_JOB_QUEUE_MAX_DEPTH` → otherwise the default. A non-positive or
  non-numeric value raises `ValueError`.
* **Enforcement:** the in-memory backend checks `len(jobs) >= max_depth` under
  its lock; the Redis backend checks `LLEN validsim:jobs:index >= max_depth`
  under the same lock that performs the `SET`/`RPUSH` writes.

> [!warning] Two caveats, stated honestly
> 1. **Cross-process best-effort (Redis).** The depth check is guarded by a
>    *per-process* lock, so it reliably bounds a single process. Several API
>    processes sharing one Redis can each pass the check concurrently and
>    slightly overshoot the cap — treat `max_depth` as a soft ceiling under
>    multi-process load.
> 2. **HTTP mapping is wired.** `QueueFullError` is a distinct exception type,
>    and `POST /api/v1/jobs` catches it and returns **`503 Service Unavailable`**
>    with a `{"error": "queue_full", "max_depth": N}` body and a `Retry-After: 5`
>    header, so callers can back off and retry instead of seeing an opaque `500`.

### 6.2 SSE stream deadline (audit M1)

A single `/events` stream is **bounded** so a stuck or never-finishing job
cannot pin a worker thread for the life of the request:

* **Deadline:** default `60.0` seconds of wall-clock since the stream opened
  (`_DEFAULT_STREAM_TIMEOUT`).
* **Poll interval:** default `1.0` second between status frames
  (`_DEFAULT_POLL_INTERVAL`).
* When the deadline (or a `max_polls` safety valve) is reached, the stream emits
  a final `event: timeout` frame and closes, letting the client reconnect.

Both are overridable per deployment (or test) via `app.state.sse_stream_deadline`
and `app.state.sse_poll_interval`.

---

## 7. Environment variables

All configuration is env-driven and read at use time (or at `create_app()` /
`create_job_queue()` time), never at import time. These join the store/backend
vars documented in [docs/runbook.md](runbook.md) §2.

| Variable | Default | Description |
|---|---|---|
| `VALIDSIM_JOB_QUEUE` | `memory` | Queue backend: `memory` \| `redis` (case-insensitive; anything else falls back to memory). |
| `VALIDSIM_REDIS_URL` | *(none)* | Redis URL for the `redis` backend (e.g. `redis://localhost:6379/0`). Required when `VALIDSIM_JOB_QUEUE=redis`; missing URL + installed driver fails fast with `ValueError`. |
| `VALIDSIM_JOB_QUEUE_MAX_DEPTH` | `1000` | Maximum jobs the queue holds before `enqueue` raises `QueueFullError`. Must be a positive integer; malformed value raises `ValueError`. |
| `VALIDSIM_JOB_LEASE_SECONDS` | `3600` | How long a worker's claim stays valid before `reap_expired()` may hand the job to another worker. This is the dead-worker timeout; a slow-but-alive worker renews at one third of it. Must be a positive integer. |
| `VALIDSIM_JOB_MAX_ATTEMPTS` | `3` | Attempts allowed before a raising job becomes `failed` instead of being requeued. Must be a positive integer. |
| `VALIDSIM_JOB_RETRY_BACKOFF_SECONDS` | `5.0` | First retry delay, doubling per attempt and capped at 300s. Without it a persistently failing backend hot-spins and burns the whole budget in milliseconds. Accepts a float. |
| `VALIDSIM_JOB_MAX_RECLAIMS` | `3` | Abandoned claims tolerated before a job is dead-lettered as `dead`. Counts reclaims, not failures. Must be a positive integer. |

```bash
# Single-process / local: default memory backend, nothing to set
validsim job enqueue -c checkpoint-abc123

# Multi-process: share one Redis queue
export VALIDSIM_JOB_QUEUE=redis
export VALIDSIM_REDIS_URL='redis://localhost:6379/0'
export VALIDSIM_JOB_QUEUE_MAX_DEPTH=2000   # optional override
```

---

## 8. Running the worker (docker compose)

The `worker` service in [`docker-compose.yml`](../docker-compose.yml) is the CPU
(mock-backend) job worker that drains the Redis queue and persists finished runs
to the shared store. It **reuses the api image** (same build context + tag, so
Compose builds it once) and is *not* the GPU Isaac worker.

```yaml
worker:
  image: validsim:local
  command: ["python", "-m", "validsim.cli", "worker"]   # run_forever loop
  restart: unless-stopped
  environment:
    VALIDSIM_STORE: "${VALIDSIM_STORE:-postgres}"
    VALIDSIM_PG_URL: "postgresql://validsim:${POSTGRES_PASSWORD}@postgres:5432/validsim"
    VALIDSIM_JOB_QUEUE: redis
    VALIDSIM_REDIS_URL: "redis://redis:6379/0"
  depends_on:
    redis:    { condition: service_healthy }
    postgres: { condition: service_healthy }
  healthcheck:
    disable: true            # the base image probes :8000; this process never serves it
```

Key points:

* **Same wiring as the API.** The `api` service sets `VALIDSIM_JOB_QUEUE=redis`
  and `VALIDSIM_REDIS_URL=redis://redis:6379/0`, so a job enqueued through
  `POST /api/v1/jobs` is claimed by this worker and its result lands in the same
  system-of-record (Postgres in this Compose stack, where Compose supplies
  `postgres` when `VALIDSIM_STORE` is unset or blank; outside Compose, it
  defaults to `memory`).
* **`healthcheck.disable`.** The base image's `HEALTHCHECK` probes the HTTP API
  on `:8000`, which the worker never serves — disabling it stops Compose from
  reporting the worker unhealthy.
* **Bring it up** with the rest of the stack (`make docker-up`, or
  `docker compose up -d worker`). Each worker is an independent FIFO consumer, so
  you can run several — but the service pins `container_name: validsim-worker`,
  which blocks `--scale`; drop that line first, then
  `docker compose up -d --scale worker=N`.

> [!note] Not the GPU worker
> This `worker` runs the deterministic **mock** backend unless
> `VALIDSIM_BACKEND=isaac` selects the GPU path. The ~15 GB Isaac Sim worker is a
> separate image and is intentionally out of this stack for now — see the
> commented `worker-gpu` placeholder at the bottom of
> [`docker-compose.yml`](../docker-compose.yml) and
> [[Isaac Sim GPU Worker — HTTP Contract]].

---

## 9. Worker internals

`JobWorker` is deliberately dependency-light and deterministic. Given a queue, a
store, and a backend:

* `run_once()` calls `queue.claim_next()`, which hands back one claimable job
  and stamps it with a lease deadline **and a fresh `lease_epoch`** — a fencing
  token. Work still inside its retry backoff is skipped, not blocked on, and a
  job whose attempt or reclaim budget is spent is dead-lettered rather than
  handed out again. The job is marked `running`, the pipeline executes, then the
  outcome is written **fenced on that epoch** (`_finish(..., lease_epoch=...)`),
  so a worker that stalled past its lease cannot overwrite the run of whichever
  worker now owns the job. Returns `None` when nothing is claimable. A failure
  with attempts left requeues the job with a backoff instead of failing it.
  A single bad job **does not** kill the
  worker — the exception is caught and recorded on that job only.
* `run_forever(poll_seconds=1.0)` loops `run_once()`, idling up to `poll_seconds`
  when the queue is empty but waking immediately if `stop()` is called — so
  shutdown is responsive without busy-spinning.
* `_execute()` builds a `TaskConfig` from the spec (robot defaults to
  `franka_panda`, environment to `mock-scene`, approve threshold to `85.0`) and
  calls `run_and_score(...)` with the worker's own backend and the job's
  `run_id`, so the stored run reuses `spec.run_id` — the shared-key contract from
  §1. Seeds derive from `stable_seed(checkpoint_id, task_id)`, so the same spec
  always yields the same scorecard.

---

Links: [[ValidSim REST API — Reference]] · [[Isaac Sim GPU Worker — HTTP Contract]]
· [Operations Runbook](runbook.md)
