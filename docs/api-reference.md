---
tags: [engineering, api, reference]
status: prototype (API v0.2.0)
---

# 📡 ValidSim REST API — Reference

The HTTP contract for the ValidSim service as implemented in
[`validsim/api/main.py`](../validsim/api/main.py),
[`validsim/api/dashboard.py`](../validsim/api/dashboard.py), and
[`validsim/jobs/router.py`](../validsim/jobs/router.py). This is the
operator- and integrator-facing counterpart to [[GitHub Actions Integration]]
and the [[Isaac Sim GPU Worker — HTTP Contract]]; the day-to-day operational
view (healthchecks, env-vars, incident playbook) lives in
[`docs/runbook.md`](runbook.md).

> [!important] Two validation paths
> The API exposes two ways to run a validation:
>
> * **Synchronous** — `POST /api/v1/validations` runs the full pipeline inside
>   the request and blocks until the run (episodes + evaluation + scorecard) is
>   complete and persisted. The simulation backend is selected by
>   `validsim.sim.create_backend`: the deterministic **mock** backend by default
>   (CPU-only/CI), or the GPU worker when `VALIDSIM_BACKEND=isaac`.
> * **Asynchronous** — `POST /api/v1/jobs` enqueues a job on a bounded queue
>   (in-memory or Redis-backed, see [§4.6 Jobs](#46-jobs)) and returns
>   immediately; status is polled or streamed over SSE. The API only *enqueues* —
>   a separate worker pool claims jobs, runs them, and reports status back.
>
> The synchronous path is the right default for the platform's interactive and
> CI episode counts; the queue exists for long-running or batched work.

The generated OpenAPI document is served at `/openapi.json`
(`create_app().openapi()`), title **ValidSim API**, version **`0.2.0`**.

---

## 1. Base URL & versioning

| Setting | Value |
|---|---|
| Base URL (local) | `http://127.0.0.1:8000` |
| API prefix | `/api/v1` |
| OpenAPI schema | `/openapi.json` |
| Interactive docs | `/docs` (Swagger UI), `/redoc` |
| API version | `0.2.0` (`validsim.__version__`) |

There is no per-request version negotiation and no deprecation header yet.
Ships pre-1.0, so breaking changes may land in a minor bump; the plan is to
freeze `/api/v1` semantics at 1.0 and introduce `/api/v2` rather than mutate
in place (see the versioning note in [`CHANGELOG.md`](../CHANGELOG.md)).

---

## 2. Authentication

Auth is **env-gated and read once at `create_app()` time**:

* `VALIDSIM_API_KEY` **unset** (default) → authentication is **disabled**.
  Every route is open — this keeps local development and the test suite free
  of credentials.
* `VALIDSIM_API_KEY` **set** → every `/api/v1` route requires the
  `X-API-Key` request header to match the configured value exactly, or it
  returns `401`.

Key properties:

* Missing, empty, and wrong keys are **indistinguishable** — all return the
  same uniform `401` so the response never reveals whether a key exists.
* Comparison uses `secrets.compare_digest` (constant-time).
* The `401` carries `WWW-Authenticate: ApiKey`.

```bash
# Disabled (default) — no header needed
curl http://127.0.0.1:8000/api/v1/health

# Enabled — send the key on every /api/v1 call
export VALIDSIM_API_KEY='super-secret'
curl -H "X-API-Key: $VALIDSIM_API_KEY" http://127.0.0.1:8000/api/v1/validations
```

```json
{ "detail": "Missing or invalid API key" }
```

> [!warning] Health probe is also gated
> The auth dependency is applied to **all** `/api/v1` routes — including
> `GET /api/v1/health`. A Docker/compose healthcheck that hits `/api/v1/health`
> without the header will go unhealthy the moment `VALIDSIM_API_KEY` is set
> (fix: send the header or exempt the route). See
> [`docs/runbook.md`](runbook.md) §4.1.
>
> Not protected (they are mounted before the dependency is installed):
> `/docs`, `/redoc`, `/openapi.json`, and the `/static` asset mount.

> [!warning] Destructive routes carry their own gate
> `DELETE /api/v1/validations/{run_id}` additionally depends on
> `require_api_key_for_destructive`, attached directly to the route rather than
> relying only on the global `/api/v1` toggle. When `VALIDSIM_API_KEY` is set the
> header is required and validated identically (constant-time compare, uniform
> `401`). When it is **not** set the gate is a deliberate no-op so local
> development and the open test-suite keep working — which means **any
> deployment that exposes `DELETE` over a network MUST set `VALIDSIM_API_KEY`**,
> otherwise stored runs are deleted unauthenticated.

---

## 3. CORS

Controlled by `VALIDSIM_CORS_ORIGINS`, read at `create_app()` time.

| Variable | Default | Behaviour |
|---|---|---|
| `VALIDSIM_CORS_ORIGINS` | `*` | Comma-separated origin allow-list. Whitespace is trimmed and empty entries are dropped, so `"*"` and trailing commas behave sensibly. `*` keeps the previous allow-all behaviour. |

Middleware settings (fixed — not env-configurable):

| Setting | Value |
|---|---|
| `allow_credentials` | `false` |
| `allow_methods` | `["*"]` |
| `allow_headers` | `["*"]` |

```bash
# Restrict to a specific dashboard origin
export VALIDSIM_CORS_ORIGINS='https://gates.example.com,https://staging.example.com'
```

---

## 4. Endpoints by resource

"Auth" = requires `X-API-Key` only when `VALIDSIM_API_KEY` is set.
"Rate limit" = per-route throttling. `*` marks the three **write** routes
covered by the opt-in IP-keyed sliding-window limiter — `POST /validations`,
`POST /validations/{run_id}/compare`, and `POST /jobs` (see
[§4.7 Rate limiting](#47-rate-limiting)). It is **disabled by default**
(`VALIDSIM_RATE_LIMIT=0`), so those entries behave like `None` until you turn it
on. Every read route is unlimited.

### 4.1 Validations

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| POST | `/api/v1/validations` | ✓ | `*` | Run a validation synchronously; returns the full scorecard (`201`). |
| GET | `/api/v1/validations` | ✓ | None | Paginated list of run summaries, newest first (optional `since`/`until` filters). |
| GET | `/api/v1/validations/{run_id}` | ✓ | None | Single run summary (no episode payloads). |
| DELETE | `/api/v1/validations/{run_id}` | ✓ | None | Permanently delete a stored run (`204`). Carries the dedicated destructive-auth gate. |
| GET | `/api/v1/validations/{run_id}/scorecard` | ✓ | None | Full scorecard JSON for a run. |
| GET | `/api/v1/validations/{run_id}/failures` | ✓ | None | Failure taxonomy + per-failure episode details. |
| POST | `/api/v1/validations/{run_id}/compare` | ✓ | `*` | Ad-hoc regression comparison of two stored runs. |
| GET | `/api/v1/validations/{run_id}/scorecard.pdf` | ✓ | None | One-page branded PDF scorecard (needs optional `reportlab`). |
| GET | `/api/v1/validations/{run_id}/scorecard.md` | ✓ | None | Markdown validation report (`text/markdown`). |
| GET | `/api/v1/validations/{run_id}/scorecard.html` | ✓ | None | Self-contained HTML scorecard (`text/html`). |

#### `POST /api/v1/validations`

Runs the full mock pipeline and persists the result. The run id is generated
server-side (`vrun-<uuid8>`); `created_at` is UTC.

**Request body — `ValidationRequest`**

```json
{
  "checkpoint_id": "checkpoint-abc123",
  "checkpoint_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "task": {
    "task_id": "pick-and-place",
    "robot": { "name": "franka_panda", "urdf_path": "robots/franka.urdf", "dof": 7 },
    "environment": { "name": "tabletop", "scene_usd": "scenes/tabletop.usd" },
    "episodes": 1000,
    "randomization": "full",
    "adversarial_count": 10
  },
  "baseline_run_id": "vrun-1a2b3c4d"
}
```

Field rules (Pydantic v2, strict — invalid input fails with `422`):

| Field | Required | Constraints |
|---|---|---|
| `checkpoint_id` | yes | non-empty string |
| `checkpoint_sha256` | no | exactly 64 chars (hex digest) |
| `task` | yes | `TaskConfig` (below) |
| `task.task_id` | yes | non-empty string |
| `task.robot` | yes | `RobotSpec` |
| `task.robot.name` | yes | non-empty string |
| `task.robot.urdf_path` | no | string or `null` |
| `task.robot.dof` | no | 1–40, default `7` |
| `task.environment` | yes | `EnvironmentSpec` |
| `task.environment.name` | yes | non-empty string |
| `task.environment.scene_usd` | no | string or `null` |
| `task.episodes` | no | 1–100000, default `1000` |
| `task.randomization` | no | `"none" \| "partial" \| "full"`, default `"full"` |
| `task.adversarial_count` | no | 0–1000, default `0` |
| `baseline_run_id` | no | string; must reference an existing run (`404` otherwise) |

**Response `201` — scorecard** (`Scorecard.to_dict()`)

```json
{
  "run_id": "vrun-1a2b3c4d",
  "checkpoint_id": "checkpoint-abc123",
  "task_id": "pick-and-place",
  "composite_score": 87.5,
  "success_rate": 0.92,
  "safety_score": 95.0,
  "robustness_score": 100.0,
  "regression_delta": null,
  "confidence_interval": [0.9021, 0.9379],
  "deploy_decision": "APPROVE",
  "threshold": 85.0,
  "created_at": "2026-03-20T12:00:00+00:00",
  "episode_count": 1000,
  "failure_taxonomy": { "grasp_failure": 12, "timeout": 8 }
}
```

* `confidence_interval` is a **list** (tuple coerced) `[low, high]`, or `null`
  when there are no episodes.
* `regression_delta` is `null` unless a baseline was supplied.
* `deploy_decision` is `"APPROVE"` when `composite_score >= threshold`,
  otherwise `"BLOCK"`.

#### `GET /api/v1/validations`

Paginated summaries, newest first (see [§5 Pagination](#5-pagination)).

**Response `200`**

```json
{
  "total": 5,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "run_id": "vrun-1a2b3c4d",
      "checkpoint_id": "checkpoint-abc123",
      "task_id": "pick-and-place",
      "created_at": "2026-03-20T12:00:00+00:00",
      "baseline_run_id": null,
      "composite_score": 87.5,
      "deploy_decision": "APPROVE",
      "episode_count": 1000
    }
  ]
}
```

`total` is the full stored-run count, independent of `limit`/`offset`.

#### `GET /api/v1/validations/{run_id}`

Single run summary — same shape as one `items[]` entry above.

```json
{
  "run_id": "vrun-1a2b3c4d",
  "checkpoint_id": "checkpoint-abc123",
  "task_id": "pick-and-place",
  "created_at": "2026-03-20T12:00:00+00:00",
  "baseline_run_id": null,
  "composite_score": 87.5,
  "deploy_decision": "APPROVE",
  "episode_count": 1000
}
```

#### `DELETE /api/v1/validations/{run_id}`

Permanently removes a stored run and returns `204` with **no body**. An unknown
`run_id` returns `404` (`{"detail": "run <id> not found"}`). The store's
`delete()` is both the existence check and the removal in a single step, so
there is no check-then-delete race.

Because the operation is destructive it is gated by
`require_api_key_for_destructive` **in addition to** the global `/api/v1` auth:
when `VALIDSIM_API_KEY` is configured a valid `X-API-Key` is mandatory, and the
`401` is returned **before** the `404` lookup. See
[§2 Authentication](#2-authentication).

```bash
curl -X DELETE \
  -H "X-API-Key: $VALIDSIM_API_KEY" \
  http://127.0.0.1:8000/api/v1/validations/vrun-1a2b3c4d
```

#### `GET /api/v1/validations/{run_id}/scorecard`

Full scorecard JSON — identical shape to the `201` response above.

#### `GET /api/v1/validations/{run_id}/failures`

```json
{
  "run_id": "vrun-1a2b3c4d",
  "failure_taxonomy": { "grasp_failure": 12, "timeout": 8 },
  "episodes": [
    {
      "episode_id": "pick-and-place-seed0000000042",
      "task_id": "pick-and-place",
      "seed": 42,
      "success": false,
      "collision_count": 1,
      "max_contact_force_n": 87.3,
      "min_human_distance_m": 1.84,
      "failure_mode": "grasp_failure",
      "duration_s": 11.243,
      "joint_states_summary": {
        "position_rms": 0.4102,
        "velocity_rms": 0.1213,
        "effort_max": 34.5,
        "dof": 7.0
      },
      "randomization_level": "full"
    }
  ]
}
```

`episodes` contains only **failed** episodes (`success == false`), each as a
full `EpisodeResult` dataclass dump.

#### `POST /api/v1/validations/{run_id}/compare`

Ad-hoc regression comparison. The permutation-test seed is derived from the
run/baseline id pair (`stable_seed`), so the same comparison is reproducible
across calls and processes.

**Request body — `CompareRequest`**

```json
{ "baseline_id": "vrun-00000000" }
```

`baseline_id` (string) is required and must reference an existing run.

**Response `200` — `RegressionReport.to_dict()`**

```json
{
  "run_id": "vrun-1a2b3c4d",
  "baseline_id": "vrun-00000000",
  "items": [
    {
      "metric": "success_rate",
      "before": 0.92,
      "after": 0.85,
      "delta": -0.07,
      "p_value": 0.012,
      "significant": true,
      "severity": "critical"
    },
    {
      "metric": "mean_duration_s",
      "before": 8.5,
      "after": 9.1,
      "delta": 0.6,
      "p_value": null,
      "significant": false,
      "severity": "info"
    }
  ],
  "significant_count": 1,
  "worst_severity": "critical"
}
```

`severity` ∈ `info | warning | critical`; `p_value` is `null` for descriptive
metrics (duration).

#### `GET /api/v1/validations/{run_id}/scorecard.pdf`

Returns `200` with `Content-Type: application/pdf` and a
`Content-Disposition: attachment; filename="<run_id>-scorecard.pdf"` header.
Returns `501` (detail `"pip install reportlab"`) when the optional `reportlab`
dependency is missing — unknown runs return `404` first.

#### `GET /api/v1/validations/{run_id}/scorecard.md`

Renders the run's scorecard as a Markdown report — the APPROVE/BLOCK verdict
against the threshold, a metrics table, a failure-taxonomy table, and the
confidence-interval line — with `Content-Type: text/markdown`. Intended for CI
job summaries. Returns `404` for an unknown run. Unlike the PDF route this needs
**no** optional dependency (the renderer is a pure function).

#### `GET /api/v1/validations/{run_id}/scorecard.html`

Renders a self-contained, branded HTML card (all CSS inlined, no external
assets, values HTML-escaped) with `Content-Type: text/html` — suitable for email
or downstream PDF distribution. Returns `404` for an unknown run; no optional
dependency.

---

### 4.2 Models

Derived model-registry views aggregated from the store's run history.

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| GET | `/api/v1/models` | ✓ | None | One row per validated checkpoint (paginated). |
| GET | `/api/v1/models/{checkpoint_id}/history` | ✓ | None | Chronological compact scorecard summaries for a checkpoint. |

#### `GET /api/v1/models`

Aggregates one row per checkpoint from the store's chronological history
(oldest first), so each checkpoint's `latest_*` fields reflect its most recent
run. Checkpoints appear in order of **first validation** (unlike
`/validations`, which is newest-first). Paginated with `limit`/`offset` over
that fixed ordering.

**Response `200`**

```json
[
  {
    "checkpoint_id": "checkpoint-abc123",
    "runs": 3,
    "latest_composite": 91.2,
    "latest_decision": "APPROVE",
    "last_validated": "2026-03-20T12:00:00+00:00"
  }
]
```

Note: this endpoint returns a **bare array**, not an envelope with `total`.

#### `GET /api/v1/models/{checkpoint_id}/history`

Chronological (**oldest-first**) compact summaries (`StoredRun.summary()` —
same shape as `/validations` items). Returns `404` for an unknown checkpoint.

**Response `200`**

```json
[
  {
    "run_id": "vrun-aaaaaaaa",
    "checkpoint_id": "checkpoint-abc123",
    "task_id": "pick-and-place",
    "created_at": "2026-03-18T09:00:00+00:00",
    "baseline_run_id": null,
    "composite_score": 86.1,
    "deploy_decision": "APPROVE",
    "episode_count": 1000
  },
  {
    "run_id": "vrun-bbbbbbbb",
    "checkpoint_id": "checkpoint-abc123",
    "task_id": "pick-and-place",
    "created_at": "2026-03-20T12:00:00+00:00",
    "baseline_run_id": "vrun-aaaaaaaa",
    "composite_score": 91.2,
    "deploy_decision": "APPROVE",
    "episode_count": 1000
  }
]
```

---

### 4.3 Regressions

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| GET | `/api/v1/regressions` | ✓ | None | Runs whose stored comparison flagged significant regressions. |

#### `GET /api/v1/regressions`

Returns a **bare array**, unbounded (no pagination). Each entry is a run
summary plus the embedded regression report.

**Response `200`**

```json
[
  {
    "run_id": "vrun-1a2b3c4d",
    "checkpoint_id": "checkpoint-abc123",
    "task_id": "pick-and-place",
    "created_at": "2026-03-20T12:00:00+00:00",
    "baseline_run_id": "vrun-00000000",
    "composite_score": 71.2,
    "deploy_decision": "BLOCK",
    "episode_count": 1000,
    "regression": {
      "items": [
        {
          "metric": "success_rate",
          "before": 0.92,
          "after": 0.85,
          "delta": -0.07,
          "p_value": 0.012,
          "significant": true,
          "severity": "critical"
        }
      ],
      "significant_count": 1,
      "worst_severity": "critical"
    }
  }
]
```

Only runs with `regression != null && regression.has_regressions` are
included (a run with no baseline never appears here).

---

### 4.4 Dashboard

Served from the same store instance as the main API; these endpoints power the
single-page dashboard.

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| GET | `/api/v1/dashboard/history` | ✓ | None | All stored runs, newest first, as compact summaries. |
| GET | `/api/v1/dashboard/summary` | ✓ | None | Gate-level counts for the KPI strip. |
| GET | `/api/v1/dashboard/` | ✓ | None | The dashboard SPA (served under the API prefix; hidden from OpenAPI). |

#### `GET /api/v1/dashboard/history`

Unbounded list (no pagination), newest first; each entry is a
`StoredRun.summary()` — same compact shape as `/validations` items.

#### `GET /api/v1/dashboard/summary`

```json
{
  "total_runs": 5,
  "approvals": 4,
  "blocks": 1,
  "avg_composite": 89.42
}
```

`avg_composite` is `null` when no runs have been recorded yet (never divides
by zero). `blocks = total_runs - approvals`.

Non-schema page routes (for completeness, not part of the data API):

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard SPA entry page. |
| GET | `/static/*` | SPA assets (`validsim/web`) via `StaticFiles`. |

---

### 4.5 Health

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| GET | `/api/v1/health` | ✓ | None | Liveness/readiness probe with the effective runtime config. |

Cheap by design: every field is read from `app.state` (populated once at
`create_app()` time) or derived from the injected store/queue class names, so
the route performs **no I/O** and is safe to back a container healthcheck.
`status` and `version` are stable contract keys; the rest describe how this
instance is configured.

**Response `200`**

```json
{
  "status": "ok",
  "version": "0.2.0",
  "auth_enabled": false,
  "cors_wildcard": true,
  "rate_limit": null,
  "store_backend": "memory",
  "job_queue_backend": "memory"
}
```

| Field | Type | Notes |
|---|---|---|
| `status` | string | Always `"ok"` when the process is up. |
| `version` | string | `validsim.__version__`. |
| `auth_enabled` | bool | `true` when `VALIDSIM_API_KEY` is set (`X-API-Key` enforced). |
| `cors_wildcard` | bool | `true` when CORS allows all origins (`"*"`). |
| `rate_limit` | object \| null | `{"requests", "window_seconds"}` when the limiter is on, else `null`. |
| `store_backend` | string | Label from the store class: `memory` \| `sqlite` \| `postgres`. |
| `job_queue_backend` | string | Label from the queue class: `memory` \| `redis`. |

---

### 4.6 Jobs

The asynchronous counterpart to `POST /validations`. A job enqueues a
validation on a bounded queue (in-memory `JobQueue` by default, or
`RedisJobQueue` when `VALIDSIM_JOB_QUEUE=redis` + `VALIDSIM_REDIS_URL` is set)
and returns immediately; the API only **enqueues** — a separate worker pool
claims jobs, runs them, and reports lifecycle transitions back through the same
queue. These routes are included under `/api/v1` after the global auth
dependency is installed, so they inherit the same `X-API-Key` gate.

| Method | Path | Auth | Rate limit | Description |
|---|---|---|---|---|
| POST | `/api/v1/jobs` | ✓ | `*` | Enqueue a validation job; returns id + initial status (`202`). |
| GET | `/api/v1/jobs` | ✓ | None | All jobs in insertion (FIFO) order. |
| GET | `/api/v1/jobs/{job_id}` | ✓ | None | Full status record for one job. |
| GET | `/api/v1/jobs/{job_id}/status` | ✓ | None | Compact lifecycle snapshot (`{job_id, status, updated_at}`). |
| GET | `/api/v1/jobs/{job_id}/events` | ✓ | None | Server-Sent-Events stream of status until terminal. |

`job_id` **is** the run id the job produces (`vrun-` + 8 lowercase hex, e.g.
`vrun-1a2b3c4d`). Every `{job_id}` route validates the shape first: a
**malformed** id returns `400` (rejected before it can be interpolated into a
backend key such as the queue's `…:jobs:index`); a **well-formed but unknown**
id falls through to `404`.

#### `POST /api/v1/jobs`

**Request body — `EnqueueJobRequest`**

```json
{
  "checkpoint_id": "checkpoint-abc123",
  "task_id": "pick-and-place",
  "episodes": 1000,
  "adversarial": 0
}
```

Field rules (Pydantic v2, strict — invalid input fails with `422`):

| Field | Required | Constraints |
|---|---|---|
| `checkpoint_id` | yes | non-empty string |
| `task_id` | yes | non-empty string |
| `episodes` | no | 1–100000, default `1000` |
| `adversarial` | no | 0–1000, default `0` |

**Response `202`**

```json
{ "job_id": "vrun-1a2b3c4d", "status": "queued" }
```

**Response `503` — queue full.** The queue holds at most `max_depth` jobs
(default `1000`, env-overridable via `VALIDSIM_JOB_QUEUE_MAX_DEPTH`). Once it is
at capacity, enqueue is refused with `503`
(`{"error": "queue_full", "max_depth": <n>}`, plus a `Retry-After: 5` header) rather
than growing without bound.

#### `GET /api/v1/jobs`

A **bare array** of full job records in FIFO insertion order (not paginated).

```json
[
  {
    "job_id": "vrun-1a2b3c4d",
    "status": "queued",
    "spec": {
      "run_id": "vrun-1a2b3c4d",
      "checkpoint_id": "checkpoint-abc123",
      "task_id": "pick-and-place",
      "episodes": 1000,
      "adversarial": 0
    },
    "created_at": "2026-03-20T12:00:00+00:00",
    "started_at": null,
    "finished_at": null,
    "error": null,
    "result": null
  }
]
```

#### `GET /api/v1/jobs/{job_id}`

A single full job record — same shape as one array element above. `status` ∈
`queued | running | done | failed`. `started_at` is stamped the first time a
worker claims the job, `finished_at` on any terminal state; `error` is populated
only when `failed`, `result` (the produced `run_id`) only when `done`.

#### `GET /api/v1/jobs/{job_id}/status`

The lightweight counterpart to `GET /jobs/{job_id}`, intended for status bars
and SSE clients:

```json
{
  "job_id": "vrun-1a2b3c4d",
  "status": "running",
  "updated_at": "2026-03-20T12:00:05+00:00"
}
```

`updated_at` is the freshest of `finished_at → started_at → created_at` (the
records carry no dedicated "updated" column; each transition stamps exactly one
of them).

#### `GET /api/v1/jobs/{job_id}/events` (SSE)

`Content-Type: text/event-stream`. The route validates the id and returns
`400`/`404` **before** opening the stream; once open it polls the queue and
emits one `data:` frame per poll carrying the compact status:

```text
data: {"job_id": "vrun-1a2b3c4d", "status": "running", "updated_at": "2026-03-20T12:00:05+00:00"}

data: {"job_id": "vrun-1a2b3c4d", "status": "done", "updated_at": "2026-03-20T12:00:42+00:00"}

event: end

```

* A terminal poll (`done`/`failed`) emits a final `data:` frame followed by
  `event: end`, so well-behaved clients close cleanly.
* The stream is **bounded**: a job that never reaches a terminal state cannot
  pin a worker thread forever. Once the wall-clock deadline (default `60s`) or a
  poll-count safety valve is reached, the stream emits `event: timeout` and
  closes — clients may reconnect.
* Defaults: poll interval `1.0s`, deadline `60.0s`; both overridable per
  deployment (or test) via `app.state.sse_poll_interval` /
  `app.state.sse_stream_deadline`.
* If the job vanishes from the queue mid-stream, the stream simply ends.

```bash
curl -N -H "X-API-Key: $VALIDSIM_API_KEY" \
  http://127.0.0.1:8000/api/v1/jobs/vrun-1a2b3c4d/events
```

---

### 4.7 Rate limiting

Opt-in, process-local, sliding-window throttling of the three **write** routes
(`POST /api/v1/validations`, `POST /api/v1/validations/{run_id}/compare`,
`POST /api/v1/jobs`). Read routes are never limited.

| Variable | Default | Behaviour |
|---|---|---|
| `VALIDSIM_RATE_LIMIT` | `0` | Requests allowed per window; `0` disables the limiter entirely (the default, preserving unlimited behaviour). |
| `VALIDSIM_RATE_WINDOW_SECONDS` | `60` | Sliding-window length in seconds. |

Key properties:

* **IP-keyed.** Buckets are keyed on the client IP **alone** — never on a
  caller-supplied header — so rotating `X-API-Key` cannot mint a fresh bucket
  and bypass the limit.
* **Bounded memory.** A max-bucket cap (10,000) evicts the least-recently-touched
  bucket, and a periodic sweep drops buckets whose newest hit has aged out of
  the window.
* **Response.** Over budget returns `429` (`{"detail": "rate limit exceeded"}`)
  with a `Retry-After` header (whole seconds until the oldest in-window hit
  expires).
* **Process-local.** It blunts abuse against a single uvicorn worker; it is not
  a distributed/global limit.

```bash
# 30 write requests per 60s, per client IP
export VALIDSIM_RATE_LIMIT=30
export VALIDSIM_RATE_WINDOW_SECONDS=60
```

---

## 5. Pagination

Pagination applies to exactly two list endpoints: `GET /api/v1/validations`
and `GET /api/v1/models`.

| Parameter | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `limit` | int | `100` | 1–500 | Page size. |
| `offset` | int | `0` | ≥ 0 | Number of items to skip. |

```bash
curl "http://127.0.0.1:8000/api/v1/validations?limit=10&offset=20"
curl "http://127.0.0.1:8000/api/v1/models?limit=50&offset=0"
```

* `/validations` returns an envelope `{total, limit, offset, items}`; `total`
  is the full count ignoring pagination. It also accepts optional inclusive
  `since`/`until` ISO-8601 bounds on `created_at` (a malformed value yields
  `422`); `total` counts the runs matching those filters.
* `/models` returns a **bare array** slice (no `total`).
* `/regressions`, `/dashboard/history`, `/dashboard/summary`,
  `/models/{checkpoint_id}/history`, and `/jobs` are **not** paginated — they
  return the full result (`/jobs` is a bare array in FIFO order).

---

## 6. Error codes

| Code | Meaning | Where / when |
|---|---|---|
| `400` | Bad Request | Malformed `{job_id}` path parameter on any `/api/v1/jobs/{job_id}*` route — the id must match `vrun-` + 8 lowercase hex. Rejected before it reaches the queue (a well-formed-but-unknown id falls through to `404`). |
| `401` | Unauthorized | `VALIDSIM_API_KEY` set and `X-API-Key` missing, empty, or wrong. Uniform `{"detail": "Missing or invalid API key"}` + `WWW-Authenticate: ApiKey`. Applies to all `/api/v1` routes and, separately, to `DELETE` via the destructive gate. |
| `404` | Not Found | Unknown `run_id` (get / delete / scorecard / failures / compare / pdf / md / html); unknown `baseline_run_id` (create & compare); unknown `checkpoint_id` in model history; unknown (well-formed) `job_id`. |
| `422` | Validation Error | Pydantic v2 rejects the `ValidationRequest`/`CompareRequest`/`EnqueueJobRequest` body, `limit`/`offset` params, or a malformed `since`/`until` filter (e.g. 64-char `checkpoint_sha256` violated, `episodes` out of 1–100000, missing required fields). |
| `429` | Too Many Requests | Opt-in rate limiter over budget on a write route (`POST /validations`, `POST /validations/{run_id}/compare`, `POST /jobs`). Body `{"detail": "rate limit exceeded"}` + `Retry-After` header. Only when `VALIDSIM_RATE_LIMIT > 0` (see [§4.7 Rate limiting](#47-rate-limiting)). |
| `501` | Not Implemented | PDF export when the optional `reportlab` dependency is absent (`{"detail": "pip install reportlab"}`). The `.md`/`.html` renderers need no optional dependency. |
| `503` | Service Unavailable | `POST /api/v1/jobs` when the queue is at `max_depth` (`{"error": "queue_full", "max_depth": <n>}` + `Retry-After: 5`). |

Error bodies for the hand-raised `HTTPException`s are `{"detail": "<message>"}`.
FastAPI validation (`422`) uses the standard envelope:

```json
{
  "detail": [
    {
      "loc": ["body", "task", "episodes"],
      "msg": "Input should be less than or equal to 100000",
      "type": "less_than_equal",
      "input": 999999
    }
  ]
}
```

> [!note] 501 vs 404 on the PDF route
> An **unknown** run returns `404`; the `501` is only ever produced after the
> run lookup has already succeeded. So `501` ⇒ "run exists, but PDF rendering
> is not installed."

---

## 7. Data model quick reference

| Concept | Source / endpoint shape | Notes |
|---|---|---|
| `ValidationRequest` | `POST /validations` body | Request model; see field table in §4.1. |
| `Scorecard` | `POST /validations`, `/scorecard` | Composite verdict: 40% success + 30% safety + 20% robustness + 10% regression. |
| `StoredRun.summary()` | list/detail items | Compact `{run_id, checkpoint_id, task_id, created_at, baseline_run_id, composite_score, deploy_decision, episode_count}`. |
| `EvaluationResult` | `failure_taxonomy` | `{total_episodes, success_count, success_rate, per_task_success, failure_taxonomy, mean_duration_s}`. |
| `SafetyResult` | (embedded in pipeline, not a standalone route) | `{collisions_per_episode, max_force_exceeded_rate, min_human_proximity_m, proximity_violation_rate, safety_score}`. |
| `RegressionReport` | compare/regressions | `{items[], significant_count, worst_severity}`. |
| `EpisodeResult` | `/failures` episodes | Full per-episode dataclass dump; failure modes sampled from `FAILURE_MODES`. |
| `EnqueueJobRequest` | `POST /jobs` body | `{checkpoint_id, task_id, episodes (1–100000, default 1000), adversarial (0–1000, default 0)}`. |
| `JobSpec` | `spec` in job records | `{run_id, checkpoint_id, task_id, episodes, adversarial}`; `run_id` == `job_id`. |
| `JobRecord.to_dict()` | `GET /jobs`, `/jobs/{job_id}` | `{job_id, status, spec, created_at, started_at, finished_at, error, result}`. |
| `JobStatus` | `status` fields | `queued \| running \| done \| failed`. |

---

## 8. Quick-start recipes

### 8.1 Run a validation and read its scorecard

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/validations \
  -H "Content-Type: application/json" \
  -d '{
    "checkpoint_id": "checkpoint-abc123",
    "task": {
      "task_id": "pick-and-place",
      "robot": { "name": "franka_panda" },
      "environment": { "name": "tabletop" },
      "episodes": 1000,
      "randomization": "full",
      "adversarial_count": 10
    }
  }'
```

### 8.2 List runs, newest first

```bash
curl "http://127.0.0.1:8000/api/v1/validations?limit=10&offset=0"
```

### 8.3 Compare two stored runs

```bash
curl -X POST http://127.0.0.1:8000/api/v1/validations/vrun-1a2b3c4d/compare \
  -H "Content-Type: application/json" \
  -d '{"baseline_id": "vrun-00000000"}'
```

### 8.4 Export a branded PDF scorecard

```bash
curl -OJ http://127.0.0.1:8000/api/v1/validations/vrun-1a2b3c4d/scorecard.pdf
```

### 8.5 Authenticated calls

```bash
curl -H "X-API-Key: $VALIDSIM_API_KEY" \
  "http://127.0.0.1:8000/api/v1/dashboard/summary"
```

### 8.6 Enqueue an async job and watch it

```bash
# Enqueue → returns {job_id, status} (202; 503 if the queue is full)
curl -s -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"checkpoint_id": "checkpoint-abc123", "task_id": "pick-and-place", "episodes": 5000}'

# Stream status until done/failed (or the bounded timeout)
curl -N http://127.0.0.1:8000/api/v1/jobs/vrun-1a2b3c4d/events
```

---

Links: [[GitHub Actions Integration]] · [[Isaac Sim GPU Worker — HTTP Contract]]
· [Operations Runbook](runbook.md)