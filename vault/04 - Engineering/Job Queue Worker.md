---
tags:
  - engineering
  - job-queue
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# ⚙️ Job Queue Worker

The consumer side of the async pipeline described in [[Async Job Queue]]. Where the queue decouples *submission* from *execution*, `JobWorker` is the process that closes the loop: it claims a queued `JobSpec`, runs it through the **same engine path** the synchronous API and CLI use, persists a `StoredRun`, and flips the job's status to a terminal state. Component roles: [[Module Specs]] (M1 Ingestion & Orchestration); system context: [[Solution Architecture]] L1→L2.

Source: `validsim/jobs/worker.py` (class `JobWorker`).

## What it is (and is not)

`JobWorker` is a thin, dependency-light orchestrator. It owns no I/O of its own beyond three injected collaborators, which makes it trivial to drive from a test or a long-running daemon:

| Collaborator | Type | Role |
|---|---|---|
| `queue` | `JobQueue` | Source of queued jobs; sink for status transitions |
| `store` | `ValidationStore` | Where finished `StoredRun` records are persisted |
| `backend` | `SimulationBackend` | Executes episodes; defaults to `create_backend()` from `VALIDSIM_BACKEND` |

Plus three construction defaults that mirror the CLI: `_DEFAULT_ROBOT = "franka_panda"`, `_DEFAULT_ENVIRONMENT = "mock-scene"`, `_DEFAULT_THRESHOLD = 85.0`. A `threading.Event` backs graceful shutdown.

> [!note]
> The worker is the **CPU / mock-backend** consumer. The GPU Isaac Sim worker is a separate concern (see the `worker-gpu` placeholder in `docker-compose.yml` and [[Module Specs]]); nothing in `JobWorker` is GPU-specific — it simply honors whatever `create_backend()` resolves from the environment.

## The consumer loop

Two public methods, one inner primitive:

```
run_forever(poll_seconds=1.0)
  │  while not stop_event.is_set():
  └──▶ run_once() ──▶ _claim_next() ──▶ _execute(spec)
             │  None (queue empty)
             └──▶ stop_event.wait(poll_seconds)   # idle, wakes on stop()
```

### `run_once()` — claim and execute one job

Returns the final `JobRecord`, or `None` when nothing is queued (the caller may retry later). The lifecycle it drives:

```python
spec = self._claim_next()
if spec is None:
    return None
job_id = spec.run_id
self._queue.update_status(job_id, JobStatus.RUNNING)   # observers see 'running'
try:
    run = self._execute(spec)
except Exception as exc:                                # one bad job must not kill the worker
    return self._queue.update_status(job_id, JobStatus.FAILED, error=str(exc))
return self._queue.update_status(job_id, JobStatus.DONE, result=run.run_id)
```

Three properties worth calling out:

- **`running` is written before the pipeline executes**, so a poller of `GET /api/v1/jobs/{id}` ([[API Design]]) sees the intermediate state, not just queued → terminal.
- **The whole body is wrapped in `except Exception`** — a backend or engine fault is recorded on the job (`failed` + `error=str(exc)`) rather than crashing the loop.
- **`result` is set to `run.run_id`** — the queue record and the persisted run share one key (see below).

### `_claim_next()` — FIFO dequeue

Iterates `queue.list()` (which preserves insertion order for both the memory and Redis backends) and returns the `spec` of the **first `queued` record**, or `None`. There is no priority or lease in the current implementation — first in, first out.

### `run_forever(poll_seconds=1.0)` — the daemon loop

Loops `run_once()` until `stop()` is called. When a call returns `None` it idles for at most `poll_seconds` via `self._stop.wait(...)`, which **wakes immediately** if `stop()` fires during the wait — responsive shutdown without busy-spinning. `stop()` simply sets the event, so `run_forever` exits after its current iteration.

## Executing via the shared engine pipeline

`_execute()` does not reimplement validation. It builds a `TaskConfig` from the spec (injecting the worker's robot/environment names and the spec's `episodes` / `adversarial` counts) and delegates the entire `seed → scenarios → simulate → evaluate → safety → scorecard → persist` sequence to `run_and_score` in `validsim/engine/pipeline.py` — the **single implementation shared by the API, the CLI `run`, and this worker** ([[Data Flow]] Step 2):

```python
return run_and_score(
    task,
    spec.checkpoint_id,
    self._store,
    backend=self._backend,      # worker's own backend; create_backend never re-invoked
    run_id=spec.run_id,         # queue result and stored run share this key
    threshold=self._threshold,
    run_validation=run_validation,
)
```

Two wiring details that are easy to miss:

- **`backend` is passed in, not rebuilt.** The env-selected default resolved once in `__init__` is honored; `run_and_score` skips its `create_backend()` fallback entirely.
- **`run_id=spec.run_id` is the shared-key contract.** `JobSpec.run_id` *is* the job id (`JobRecord.job_id`), and the worker hands the same id to the pipeline, so `store.save(StoredRun(run_id=…))` and the queue's `result` field point at the same artifact. No lookup table needed.

Seeds derive from `stable_seed(checkpoint_id, task_id)`, so the same spec always yields the same scorecard — the worker is deterministic.

## Persisting a `StoredRun` and status transitions

`run_and_score` returns the exact `StoredRun` handed to `store.save` (every backend returns it unchanged). A finished run carries `run_id`, `checkpoint_id`, `task_id`, `created_at`, the `scorecard` (composite score + `deploy_decision`), `evaluation`, `safety`, `episodes`, and optional `baseline_run_id` / `regression` ([[Module Specs]]).

The status surface the worker actually drives is a four-state lifecycle:

```
queued ──▶ running ──▶ done      (result = run_id)
                  └──▶ failed     (error  = str(exc))
```

| Status | Set when | Side fields |
|---|---|---|
| `queued` | at `enqueue` (producer) | `created_at` |
| `running` | worker claims it, before the pipeline | `started_at` (first time only) |
| `done` | pipeline returns a `StoredRun` | `result=run_id`, `finished_at` |
| `failed` | backend/engine raised | `error`, `finished_at` |

> [!warning]
> The design note [[Async Job Queue]] sketches a richer `queued → running → evaluating → reported → gate:*` lifecycle. The **shipped** `JobStatus` enum collapses that to `queued / running / done / failed` — the gate decision lives on the persisted `Scorecard.deploy_decision`, not on the job record. Document consumers against the four-state set.

## Running it: docker `worker` service and `validsim worker --once`

### CLI — `validsim worker`

The Typer command wires the three collaborators from the environment and either drains one job or loops:

```python
queue = create_job_queue()          # VALIDSIM_JOB_QUEUE: memory | redis
store = create_store()              # VALIDSIM_STORE: memory | sqlite | postgres
job_worker = JobWorker(queue, store, create_backend())
if once:
    record = job_worker.run_once()
    # None → "worker: no queued jobs"; else → "worker: job <id> -> <status>"
else:
    job_worker.run_forever(poll_seconds=poll_seconds)
# finally: queue.close(); store.close()
```

| Flag | Default | Purpose |
|---|---|---|
| `--once` | `False` | Claim and execute a **single** queued job, print its resulting status, exit `0` |
| `--poll-seconds` | `1.0` (min `0.0`) | Idle interval when looping (no `--once`) |

`--once` is the primitive CI and smoke tests lean on: enqueue with `validsim job enqueue`, run `validsim worker --once`, then read the persisted run. Full surface: [[CLI Design]].

### Compose — the `worker` service

`docker-compose.yml` runs the daemon form of the same command against the shared Redis queue and Postgres store:

```yaml
worker:
  image: validsim:local            # reuses the api image (same build context/tag)
  command: ["python", "-m", "validsim.cli", "worker"]
  restart: unless-stopped
  environment:
    VALIDSIM_ENV: development
    VALIDSIM_STORE: "${VALIDSIM_STORE:-postgres}"
    VALIDSIM_PG_URL: "postgresql://validsim:${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}@postgres:5432/validsim"
    VALIDSIM_JOB_QUEUE: redis
    VALIDSIM_REDIS_URL: "redis://redis:6379/0"
  depends_on:
    redis:    { condition: service_healthy }
    postgres: { condition: service_healthy }
  healthcheck:
    test: ["CMD", "python", "-c", "<Redis PING/PONG liveness proxy>"]
```

The point of the shared env is the **handoff**: jobs enqueued through the API's `POST /api/v1/jobs` land in the same Redis queue the worker drains, and their results land in the same Postgres system-of-record the API reads back. The worker has no HTTP server, so Compose uses a Redis reachability probe rather than the base image's `:8000` check.

## Hardening: depth-cap and SSE-deadline

Two protections added during the security audit ([[Security Hardening]]) keep the async path from turning a stuck or flooded queue into a resource-exhaustion incident.

### Queue depth-cap (audit H3)

The queue is **bounded**, not unbounded. `JobQueue` / `RedisJobQueue` hold at most `max_depth` jobs (default `_DEFAULT_MAX_DEPTH = 1000`) before rejecting new work:

| Knob | Value |
|---|---|
| `VALIDSIM_JOB_QUEUE_MAX_DEPTH` | env override; must be a positive integer |
| `max_depth=` argument | per-instance override, wins over env |
| At capacity | `enqueue` raises `QueueFullError` |

The Redis backend reads `LLEN(validsim:jobs:index)` **under the same lock** that performs the `SET`/`RPUSH` writes, so a single process cannot slip past the cap. `QueueFullError` is a distinct type (not the `ValueError` used for duplicate `run_id`) precisely so the HTTP layer can surface a full queue as **`503 Service Unavailable`** rather than a generic error — back-pressure instead of silent growth.

### SSE stream deadline (audit M1)

`GET /api/v1/jobs/{job_id}/events` streams status over Server-Sent Events by polling the queue until the job reaches `done`/`failed`. Left open, a job that never finishes would pin a worker thread for the life of the request. The stream is therefore **bounded on two axes**:

| Bound | Default | Override |
|---|---|---|
| Wall-clock deadline | `_DEFAULT_STREAM_TIMEOUT = 60.0` s | `app.state.sse_stream_deadline` |
| Poll count (safety valve) | — | `max_polls` |
| Poll interval | `_DEFAULT_POLL_INTERVAL = 1.0` s | `app.state.sse_poll_interval` |

```
data: {job_id, status, updated_at}   # one frame per poll
event: end                           # job reached done/failed  → client closes
event: timeout                       # deadline/max_polls hit  → client may reconnect
```

A well-behaved client distinguishes the two terminators: `end` means "the verdict is in", `timeout` means "reconnect and keep watching". This pairs with the `job_id` path-parameter validation (audit L1, `^vrun-[0-9a-f]{8}$`) that stops a malformed id from being interpolated into a Redis key — together they close the DoS surface on the async status endpoints in [[API Design]].

Links: [[Async Job Queue]] · [[Solution Architecture]] · [[API Design]] · [[Module Specs]] · [[Home]]
