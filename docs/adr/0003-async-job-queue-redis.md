# ADR 0003: Async job queue — Redis with an in-memory fallback (not Celery)

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

A single validation run takes **15–45 minutes** end-to-end (queueing, GPU
scheduling, 1,000–100,000 simulated episodes, scoring)
([[Data Flow]] latency budget, [[Solution Architecture]] scale envelope). The
original `POST /api/v1/validations` held the HTTP connection open for that whole
time, which produces four failure modes ([[Async Job Queue]]):

| Failure mode | Consequence |
|---|---|
| Client timeout | CI/webhook drops before a result returns |
| Head-of-line blocking | One slow run stalls every other submitter |
| No retry/observability surface | No handle to poll or resume a run |
| Coupled lifecycle | Status changes invisible until the response closes |

The fix is to decouple *submit* from *run*: submission returns `202 Accepted` +
a `job_id`, and the lifecycle (`queued → running → done|failed`) is polled or
streamed over SSE instead of held open on the socket. That requires a queue that
two independent processes — the API and one or more workers — can share.

Constraints shaping the choice:

- The MVP must run **zero-config on a laptop with no infrastructure**
  ([[Product Principles]] #1, #6); a queue that demands a broker to even start
  violates this.
- The platform is already deploying **Redis 7.x** for the production path
  ([[Tech Stack]]), and the same lazy/optional pattern is used for PostgreSQL
  (see [[ADR 0004]]).
- The team is two founders; operational surface area is expensive
  ([[Strategic Advantages]]).

## Decision

Implement a small **pluggable job queue** in `validsim/jobs/queue.py` with two
backends behind one interface (`enqueue / get / list / update_status /
__len__ / close`), selected by `VALIDSIM_JOB_QUEUE`:

- **`memory` (default)** — `JobQueue`, a thread-safe in-process dict + FIFO
  insertion order. Zero dependencies, side-effect free, used by tests, the CLI,
  and local dev. It intentionally drops jobs on process exit, which is acceptable
  in that context because no GPU workload is lost mid-flight.
- **`redis`** — `RedisJobQueue`, which subclasses `JobQueue` purely to advertise
  interface compatibility and overrides every state-touching method to read/write
  Redis. Each job is one JSON string under a plain key (`validsim:jobs:<run_id>`)
  plus an insertion-order index list (`validsim:jobs:index`) preserving FIFO.
  Durable, multi-worker, survives restarts — the production path.

`RedisJobQueue` treats Redis exactly like the optional PostgreSQL store: the
`redis` driver is imported **lazily** (never at module level), the connection is
opened **lazily** on first use, so `create_job_queue()` returns a configured queue
without touching the network, and a missing driver surfaces as an actionable
`RuntimeError` ("pip install redis") only at first use. Queue depth is bounded by
`max_depth` (default 1000, env-overridable via `VALIDSIM_JOB_QUEUE_MAX_DEPTH`) so
an unbounded backlog cannot grow without limit (audit H3); a full queue raises
`QueueFullError`, which the HTTP router maps to `503`.

A `JobWorker` (`validsim/jobs/worker.py`) claims the oldest `queued` job, runs the
same engine path the synchronous API uses, and persists the finished run to the
shared store under the job's own `run_id` (the queue and store share that key).

### Why Redis, not Celery

We deliberately did **not** adopt Celery (or Dramatiq/ARQ):

- **Celery is the wrong shape for this workload.** Celery's value is distributed
  *task* execution with a broker + result backend, retries, routing, and a large
  dependency tree (Kombu, billiard, etc.). ValidSim's "task" is a long, GPU-bound,
  embarrassingly-parallel simulation that the real orchestrator (Kubernetes / Argo,
  [[Solution Architecture]] L1) schedules — not a Python function Celery should
  fan out. We need a *queue and status store*, not a task framework.
- **Celery needs its own broker anyway.** Running Celery well means operating
  Redis/RabbitMQ *plus* Celery workers, beat, and result backends — more moving
  parts and more failure modes for a two-founder team, for orchestration we've
  already decided to do in K8s/Argo.
- **The interface is tiny and we own it.** The queue contract is six methods;
  reimplementing it over Redis is far less code — and less operational risk — than
  integrating, configuring, and version-tracking Celery.
- **Zero-config default.** Celery has no meaningful "no broker" mode; our
  in-memory backend does, which is what keeps `validsim run` working out of the
  box on a laptop.
- **Redis is already in the stack** ([[Tech Stack]]), so this adds no new
  infrastructure dependency to the production deployment.

## Consequences

**Positive**

- `POST /validations` and `POST /jobs` stop blocking; CI/webhooks get a durable
  `job_id` to poll or stream.
- One interface, two interchangeable backends: dev/tests stay dependency-free,
  production scales to multiple workers sharing one Redis queue.
- Redis is optional and lazy — selecting `redis` without the driver or a URL fails
  fast with a clear message rather than at import time.
- Bounded depth turns an overload into a clean `503` instead of memory exhaustion.

**Negative / trade-offs**

- We own queue semantics (FIFO index, status transitions, the `max_depth` check)
  that Celery would have provided — more code to test and maintain.
- The in-memory backend is single-process and loses jobs on exit; it is explicitly
  *not* a production queue, and the `memory`↔`redis` behaviors differ (e.g. the
  depth check is per-process under a lock, not atomic across processes).
- Redis serialization is JSON-by-convention (no hashes, no pickles, parameter-free
  keys); a future need for atomic multi-process claim or priority dequeue would
  require reworking this layer rather than configuring Celery.
- No built-in retries/dead-lettering; a failed job is recorded `failed` and left
  for the caller to resubmit (retry policy is a candidate for a future ADR).

## References

- `validsim/jobs/queue.py` · `validsim/jobs/worker.py` · `validsim/jobs/models.py`
  · `validsim/jobs/router.py`.
- [[Async Job Queue]] · [[Job Queue Worker]] · [[API Design]] · [[Tech Stack]].
- [[ADR 0004]] (the same lazy/optional pattern for the store).
