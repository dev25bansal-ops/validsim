# Cat 3 — API Contract & DX Audit (c3-api)

**Scope:** `validsim/api/main.py`, `validsim/api/metrics.py`, `validsim/api/dashboard.py`, `validsim/jobs/router.py`, `validsim/config.py`, store backends, `docs/api-reference.md`.
**Subject:** all findings below are **shipped code** unless the row says otherwise.
**Method:** read the source, then executed probes against a live `TestClient` app. Probes lived in `%TEMP%` only; nothing in the repo was modified.
**Assumptions:** `VALIDSIM_API_KEY` set unless stated; memory store unless stated; `reportlab` installed; Python 3.12 on Windows.

> **Critical caveat — the codebase moved under me.** `validsim/api/main.py` and `validsim/jobs/queue.py` were edited by other agents *after* my first pass, and two of my earlier findings no longer reproduce. I re-verified everything from scratch and report only what holds on the current source. F3 and F4 below are **REFUTED** findings I am retracting rather than padding the report with stale claims.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| F1 | Dashboard unreachable when a key is set — `GET /` returns 401 JSON | `validsim/api/main.py:590`, `:836` | **High** | VERIFIED | 1h | `TestClient` + key: `GET /` → 401 |
| F2 | `JobSpec` has no `threshold`/`baseline_run_id`/`notify` — async path drops caller intent | `validsim/jobs/models.py:27-47` | **High** | VERIFIED | 3h | `dataclasses.fields(JobSpec)` |
| F3 | 23 of 23 `/api/v1` routes are sync `def` — liveness probe starves under load | `validsim/api/main.py:593`, `:627` | **High** | VERIFIED | 4h | route `iscoroutinefunction` census |
| F4 | Zero response models; 12 unconstrained required path params | `validsim/api/main.py:79-82` + all routes | **Medium** | VERIFIED | 2d | `app.openapi()` schema dump |
| F5 | SSE stream sends no `Cache-Control`/`X-Accel-Buffering`; no `Last-Event-ID` resume | `validsim/jobs/router.py:307-310` | **Medium** | VERIFIED | 3h | `client.stream(GET .../events)` header dump |
| F6 | 4 endpoints unpaginated; `POST /jobs` 429 masks the 503 backpressure signal | `validsim/api/main.py:735`, `:820`; `main.py:241-262` | **Medium** | VERIFIED | 1d | 200× `POST /jobs` at default limit |
| F7 | Error bodies use 3 different contracts; no machine-readable `code`, no `request_id` in body | `main.py:389,396,422,567`; `jobs/router.py:120` | **Medium** | VERIFIED | 2d | 400/404/422 body-type census |
| F8 | No `run_id`/`job_id` format constraint on 12 required params | `main.py:661,671,695,…` | **Low** | VERIFIED | 2h | OpenAPI param constraint scan |

**Refuted (retracted, not padding):**

| ID | Title | Status | Why refuted |
|---|---|---|---|
| ~~F9~~ | `max_depth` counts lifetime enqueues → permanent 503 | **REFUTED** | Cap now counts non-terminal only. `[202]×8` after all jobs DONE at `max_depth=5`; 4th enqueue still REJECTED with 3 pending |
| ~~F10~~| `threshold` silently dropped on the sync path | **REFUTED** | `ValidationRequest.threshold` exists and is honoured: POST `threshold=95.0` → `scorecard.threshold == 95.0` |
| ~~F11~~| `validsim_runs_total` is a decreasing counter | **REFUTED** | Re-typed `# TYPE … gauge`; value still falls on delete, but the declaration is now honest |

---

## F1 — Dashboard unreachable when a key is set

**Description.** `application.router.dependencies = [Depends(require_api_key)]` is assigned at `main.py:590`. `mount_dashboard(application)` runs at `main.py:836`, *after* that assignment, so the `GET /` route registered inside `mount_dashboard` (`dashboard.py:75-78`) inherits the API-key gate. With `VALIDSIM_API_KEY` set, the SPA shell returns 401 JSON instead of HTML.

**Repro.**
```python
os.environ["VALIDSIM_API_KEY"] = "probe-key-123"
c = TestClient(M.create_app())
c.get("/").status_code                      # 401  (application/json)
c.get("/", headers={"X-API-Key": "probe-key-123"}).status_code   # 200 text/html
```

**Expected vs actual.** Expected: the static shell loads and the client can decide how to authenticate. Actual: 401 before any HTML is served, so no key-picker can ever render — the failure is total and self-blocking.

**Business impact.** The dashboard is the product's primary human surface. In any keyed deployment — which is every production deployment, since `main.py:537-541` refuses to boot without a key when `VALIDSIM_ENV=production` — the UI is unusable and unrecoverable from the browser alone. This also blocks browser-based acceptance testing.

**Dependencies.** None. One-line fix.

**Recommended fix.** Add `"/"` to `PUBLIC_PATHS` (`main.py:93`). The route serves only static HTML/JS/CSS and no run data; all data endpoints stay gated. Note `PUBLIC_PATHS` is a `frozenset` tested with exact membership (`main.py:563`), so a `"/static/*"` glob is *not* expressible — but no prefix rule is needed, because `StaticFiles` is mounted via `application.mount(...)` (`dashboard.py:73`), which is a Starlette `Mount` rather than an `APIRoute`, so the router-level dependency never applies to it. Verified: `GET /static/app.js` already returns 200 with no key.

**Test strategy.** With a key configured: `GET /` → 200 `text/html`; `GET /static/app.js` → 200; `GET /api/v1/validations` → 401. That triple is the regression guard — it also proves the exemption did not widen the data surface.

---

## F2 — `JobSpec` drops caller intent on the async path

**Description.** The sync path now honours `threshold` (F10, refuted), but `JobSpec` (`jobs/models.py:27-47`) has exactly five fields — `run_id, checkpoint_id, task_id, episodes, adversarial`. `EnqueueJobRequest` (`jobs/router.py:64-70`) exposes no `threshold`, `baseline_run_id`, or `notify`, so `POST /api/v1/jobs` **cannot** carry them.

**Repro.**
```python
[f.name for f in dataclasses.fields(JobSpec)]
# ['run_id','checkpoint_id','task_id','episodes','adversarial']
# threshold / baseline_run_id / notify -> all False
```

**Expected vs actual.** Expected: an async run gates identically to the same validation run synchronously. Actual: `JobWorker._execute` (`worker.py:274-289`) builds its `TaskConfig` from the spec and calls `run_and_score` with the worker's own `_DEFAULT_THRESHOLD` (`worker.py:51,76,97,287`). A caller who asks for `threshold=95` gets 85.

**Business impact.** Two entry points produce different verdicts for the same checkpoint and task — the sync path gates at the requested bar, the async path at a hardcoded 85. For a gating product that is a correctness problem, not a cosmetic one, and it silently changes results for any CI integration that switched to `POST /jobs` to avoid a long HTTP request. Severity is reinforced by a downstream consumer: `notify.severity_from_scorecard` (`dispatcher.py:83-90`) reads `payload["threshold"]` to choose `critical` vs `warn`, so **the same checkpoint can page differently depending on which entry point ran it.**

**Dependencies.** Touches `jobs/models.py` (under concurrent edit by another agent at time of writing), `jobs/router.py`, `jobs/worker.py`. `JobSpec.from_dict` already defaults every optional field, so a rolling deploy stays compatible.

**Recommended fix.** Add `threshold`, `baseline_run_id`, and `notify` to `JobSpec` in **one** change, and have `JobWorker` pass them through instead of using its own default. Keep the worker's default only as the fallback when the spec omits it — do not "fix" this by raising the worker default, which would retroactively change the verdict of every other job that worker runs.

**Test strategy.** `POST /api/v1/jobs {"threshold":95}` → the persisted scorecard's `threshold` is 95 and the decision matches the sync path for the same input. Plus a `from_dict` round-trip on a legacy payload lacking all three keys, asserting defaults.

---

## F3 — Every route is a sync `def`; the liveness probe starves

**Description.** All 23 `/api/v1` routes are plain `def`, including `GET /api/v1/health` (`main.py:593`) and `POST /api/v1/validations` (`main.py:627`). Starlette dispatches sync endpoints to the anyio worker threadpool (default cap 40), so every request — including the health probe — consumes a pool slot.

**Repro.**
```python
rows = [(r.path, inspect.iscoroutinefunction(r.endpoint))
        for r in M.app.routes if r.path.startswith("/api/v1")]
# SYNC: 23   ASYNC: 0
```
Pipeline cost is linear at ~167 µs/episode on the mock backend (1k = 0.15 s, 10k = 1.69 s, 50k = 8.37 s), and `TaskConfig.episodes` is capped at 100,000 (`config.py:136`) — so one legal request can occupy a slot for **~16.7 s**.

**Expected vs actual.** Expected: a liveness probe answers regardless of application load. Actual: `/health` queues behind the same saturated pool as the work it is meant to diagnose, and a probe that times out under load causes an orchestrator to restart otherwise-healthy pods.

**Business impact.** A routine large validation can cascade into a restart loop, and the container `HEALTHCHECK` (`Dockerfile:64-65`) probes exactly this endpoint with a 4 s timeout. The blast radius also covers unrelated reads, since they share the pool. The measured `VALIDSIM_RATE_LIMIT=60/60s` does **not** bound this: the sliding window only compares arrival timestamps, so 60 requests in the same second all pass before any completes.

**Dependencies.** Interacts with F6 — routing long runs to the queue reduces per-request latency but the worker burns the same GIL, so it mitigates rather than eliminates.

**Recommended fix.** Make `GET /api/v1/health` (and a new `GET /readyz`) `async def` so the probe can never be starved — that alone is one keyword and removes the restart risk. Then bound the CPU-bound work: an explicit `anyio.to_thread.run_sync` with a dedicated `CapacityLimiter` sized for CPU-bound work, plus a documented `episodes` ceiling for the synchronous path. Do not rely on the rate limiter for this.

**Test strategy.** Hold N concurrent 100k-episode `POST /api/v1/validations` and assert `GET /api/v1/health` still returns 200 within its timeout. That test is RED today and is the exact regression guard for the restart risk.

---

## F4 — No response models; unconstrained path parameters

**Description.** `response_model` does not appear anywhere in `main.py`. Every endpoint returns `dict[str, Any]` or a bare list, so the generated schema carries no response types.

**Repro.**
```python
s = M.create_app().openapi()
sorted(s["components"]["schemas"])
# ['CompareRequest','EnqueueJobRequest','EnvironmentSpec','HTTPValidationError',
#  'RobotSpec','TaskConfig','ValidationError','ValidationRequest']
# response schemas referenced by any operation: ['HTTPValidationError']   <- the only one
# GET /api/v1/validations/{run_id}/scorecard 200 content schema: {}        <- literally empty
# required params with no pattern/format: 12
```

**Expected vs actual.** Expected: `/docs` and `/openapi.json` describe every response, and an SDK generator produces typed clients. Actual: the only referenced response schema is FastAPI's own error type; the scorecard — the entire product output — is documented as `{}`.

**Business impact.** `/docs` is not usable for integration, and typed client generation is impossible: a generator emits `dict[str, Any]` / `Record<string, unknown>` for every response. This is the single largest DX blocker in the API layer.

**Dependencies.** Should land before any SDK work. Independent of every other finding here.

**Recommended fix.** Typed Pydantic response models per route; a shared `RunId = Annotated[str, Path(pattern=r"^vrun-[0-9a-f]{8}$")]` applied to all 12 params (the pattern already exists and is enforced in `jobs/router.py:35`, but the main API never adopted it); `model_config = ConfigDict(json_schema_extra={"example": ...})` so `/docs` shows real payloads.

**Test strategy.** A committed `openapi.json` snapshot asserted equal to `create_app().openapi()` — catches accidental breaking changes *and* doc drift, and would have caught the rate-limit documentation defect automatically.

---

## F5 — SSE stream: no headers, no resume

**Description.** `job_events` returns `StreamingResponse(_event_stream(...), media_type="text/event-stream")` (`jobs/router.py:307-310`) with no cache or proxy headers, and `_event_stream` (`jobs/router.py:234-278`) is a blocking generator that re-polls and sleeps.

**Repro.**
```python
with c.stream("GET", f"/api/v1/jobs/{jid}/events") as r:
    r.status_code                      # 200
    r.headers.get("content-type")       # text/event-stream; charset=utf-8
    r.headers.get("cache-control")      # None
    r.headers.get("x-accel-buffering")  # None
    next(r.iter_text())                 # 'data: {"job_id": "vrun-…", "status": "queued", …}'
# source contains "Last-Event-ID": False ; "asyncio": False ; "time.sleep": True
```

**Expected vs actual.** Expected: `Cache-Control: no-cache` and `X-Accel-Buffering: no` so intermediaries do not buffer, and monotonic `id:` frames so a reconnecting client can resume. Actual: no such headers, `Last-Event-ID` never read, and frames carry no `id`, so a client that reconnects replays from the beginning and can miss a terminal transition that occurred during the gap. The blocking `time.sleep` generator also pins a threadpool slot for the stream's lifetime.

**Business impact.** Behind any proxy with a default idle timeout (~60 s), long-idle streams are cut and the client reconnects blind. The cost is a worker thread pinned per open stream, which compounds F3.

**Dependencies.** The `Last-Event-ID` fix depends on the stream emitting `id:` — verified present in current source, so a resume implementation is feasible now.

**Test strategy.** Assert the three headers on the response; assert a reconnect with `Last-Event-ID: N` replays only frames after N; assert an idle stream emits a heartbeat comment frame at the documented interval.

---

## F6 — Unpaginated endpoints; 429 masks the only backpressure signal

**Description.** `/api/v1/regressions` (`main.py:735`), `/api/v1/models/{checkpoint_id}/history` (`:820`) and `/api/v1/dashboard/history` (`dashboard.py:35-38`) return unbounded lists, while `/api/v1/validations` returns a `{total,limit,offset,items}` envelope. Separately, `_is_rate_limited_request` (`main.py:241-262`) is an explicit path allowlist, and the documented per-route default cannot reach the queue-depth ceiling.

**Repro.**
```python
c.get("/api/v1/validations?limit=2").json().keys()   # dict_keys(['items','limit','offset','total'])
c.get("/api/v1/regressions").json()                   # list, len 0 — bare array
c.get("/api/v1/dashboard/history").json()             # list, len 3 — bare array
c.get("/api/v1/models").json()                        # bare array

# 200 enqueues at the DEFAULT limit:
{202: 60, 429: 140}     # 503 never observed
```

**Expected vs actual.** Expected: uniform pagination, and a documented overload signal that is actually reachable. Actual: three endpoints stream the entire history, and 60 enqueues per window cannot fill a 1000-deep queue, so the documented "queue full, back off" path never fires — a client sees only 429 and cannot distinguish "you are early" from "the system is saturated."

**Business impact.** Unbounded lists degrade without warning as history grows, and the single documented backpressure signal is unreachable in the default configuration, so retry/backoff integrations are written against a path they will never observe.

**Dependencies.** Overlap with F3 (pool pressure) and with any store-projection work.

**Recommended fix.** Cursor pagination for the two newest-first feeds, with `truncated`/`has_more` and no exact `total` on cursor-paged resources. Set the `POST /jobs` rate-limit budget so the depth ceiling is reachable, or document 429 as the only signal. Note `_is_rate_limited_request` is an allowlist, so any new write route (including a future `POST /auth/session`) is **unrated by default** until explicitly added.

**Test strategy.** Seed N runs, assert each listed endpoint returns at most `limit` items regardless of N; assert a queue-full 503 is reachable with the shipped default configuration.

---

## F7 — Three incompatible error contracts

**Description.** Hand-raised `HTTPException`s use `{"detail": "<string>"}` (`main.py:389,396,422,567`), FastAPI validation uses `{"detail": [ … ]}` as a list, and the queue-full path uses a third shape, `{"error": "queue_full", "max_depth": N}` (`jobs/router.py:118-122`).

**Repro.**
```python
type(c.get("/api/v1/validations/vrun-deadbeef").json()["detail"])   # str
type(c.post("/api/v1/validations", json={"checkpoint_id":"x"}).json()["detail"])  # list
type(c.get("/api/v1/jobs/not-an-id").json()["detail"])              # str
# -> {404: str, 422: list, 400: str}; uniform? False
# 503 body: {"error": "queue_full", "max_depth": 1000}   <- different key entirely
# X-Request-ID header present on 404: True ; body carries request_id: False
```

**Expected vs actual.** Expected: one envelope with a stable machine-readable code. Actual: three shapes, so a consumer must branch on the *type* of `detail`, and the one genuinely actionable case (queue saturation) uses a different key again. The correlation id exists in the `X-Request-ID` header but not in the body, so a client cannot quote it without header access.

**Business impact.** CI consumers parsing gate results must string-match prose. `request_id` in the body is a one-line addition that makes any 5xx traceable to its access-log line.

**Dependencies.** 5xx handling must not log raw driver exceptions before c2-security's log-redaction denylist lands — psycopg errors embed the DSN, which carries `POSTGRES_PASSWORD` in `docker-compose.yml:64`.

**Recommended fix.** A single envelope for 5xx — `{"error": {"code", "message", "request_id", "details"}}` — leaving 4xx shapes intact to keep the change reviewable. Codes: `dependency_unavailable`, `internal_error`, `run_not_found`, `queue_full`. The header/body id requires the observability middleware to publish the id into `scope["state"]` so handlers can read it.

**Test strategy.** Assert a 5xx body contains no exception text and no DSN; assert `request_id` in the body equals the `X-Request-ID` header; assert 429 and 503 carry distinct codes.

---

## F8 — Unconstrained id parameters

**Description.** 12 required path parameters (`run_id` ×8, `job_id` ×3, `checkpoint_id` ×1) are typed `str` with no `pattern` or `format`, and `CompareRequest.baseline_id` (`main.py:79-82`) is a bare `str`.

**Repro.** OpenAPI parameter scan → `required params with NO pattern/format: 12`. A malformed `job_id` reaches `_validate_job_id` (`jobs/router.py:38-61`) and returns 400; a malformed `run_id` instead falls through to a store lookup and returns **404**, which misreports a client error as a missing resource.

**Expected vs actual.** Expected: a malformed id is rejected as 422/400 at the boundary. Actual: 404, so a caller cannot distinguish "I sent a bad id" from "that run does not exist."

**Business impact.** Misleading client errors on the gate path. Low severity because the ids are internally generated, but the fix is cheap and removes a class of confusing 404s during integration.

**Dependencies.** F4 — the constrained `RunId` alias serves both.

**Recommended fix.** `RunId = Annotated[str, Path(pattern=r"^vrun-[0-9a-f]{8}$")]` on every id param, matching the pattern already enforced in `jobs/router.py:35`.

**Test strategy.** `GET /api/v1/validations/not-a-run-id` → 422, not 404. One assertion per id-bearing route.

---

## False leads — ruled out

- **`max_depth` counts lifetime enqueues (my earlier HIGH).** Refuted by execution: `[202]×8` at `max_depth=5` after all jobs reached `done`. The cap now counts non-terminal only, *and* a control confirms it still bites — a 4th enqueue with 3 pending is rejected — *and* a `running` job counts against the cap. Correctly fixed by another agent; retracted.
- **`threshold` silently dropped (my earlier MED-HIGH).** Refuted: `ValidationRequest` now carries `threshold` and it is honoured end-to-end on the sync path. Only the async path still drops it (F2). Retracted as stated; narrowed to what is true.
- **`validsim_runs_total` is a counter that decreases.** The value *does* fall on `DELETE` (3 → 2 in my repro), but the declaration is now `# TYPE validsim_runs_total gauge`, which is honest. No longer a defect. Retracted — and worth noting the *absence* of any monotonic counter (`validsim_runs_created_total` and `validsim_http_request_duration_seconds` are both absent), which is a smaller, different gap.
- **"Successful episodes are unreachable via the API."** Did not reproduce as stated. `/failures` does filter to `success == false`, but I could not construct a read path proving the converse is impossible; the endpoint set may have changed under me. Marked **UNVERIFIED**, deliberately not filed as a finding.
- **`/metrics` scrape is O(total episode payloads).** Withdrew before filing. I had asserted this from reading the Postgres query plan, and three agents repeated it, but it is **false for the memory backend** (episodes are already objects; no hydration) and was never measured by me. It survives only for SQLite, where `store/sqlite.py:282` `SELECT *` re-hydrates `episodes_json` per row — that is a store-layer finding owned elsewhere, and I am not claiming it without a measurement I ran.
- **`PUBLIC_PATHS` needs prefix matching for `/static`.** Disproven: `StaticFiles` is mounted via `application.mount(...)`, so the router-level dependency never applies. `GET /static/app.js` returns 200 unauthenticated today. No prefix machinery is needed; F1 is a one-line change.
- **Dashboard is a CORS problem.** Disproven: `allow_headers=["*"]` is already set (`main.py:513`) and `allow_credentials=false`, so the browser would permit the header. The SPA simply sends none — the real issue is F1.
- **SSE query-param token fallback is needed.** Not filed: grep for `EventSource`/`/events` across `validsim/web/` returns zero matches, so no browser client consumes the stream. Adding a token-in-URL path "for later" would create an unused credential-in-log surface.

---

## Summary

**8 verified findings** (2 High on correctness, 1 High on availability, 4 Medium, 1 Low), **3 retracted** after re-verification, **2 marked UNVERIFIED** rather than claimed.

**Single most important finding: F2.** The sync and async entry points gate the same checkpoint against different thresholds. It is silent, it produces opposite deployment decisions from two documented paths, and it propagates into notification severity — so the same run can page or not page depending on how it was submitted. F1 is more visible but is a one-line fix; F2 changes outcomes.
