# SIM-TO-REAL — ValidSim

**GitHub Actions for robots** — continuous validation, regression testing, and safety scoring for robot foundation models before they touch the real world.

This repository contains the ValidSim MVP codebase, the founding blueprint, and a 24/7 continuous build pipeline.

## Repository Layout

| Path | Purpose |
|---|---|
| `project.docx` | Confidential founding document (19-section startup blueprint) |
| `vault/` | Obsidian vault — the full blueprint as interconnected notes (open this folder in Obsidian) |
| `validsim/` | Python platform MVP: config models, simulation runner (mock Isaac backend), adversarial scenarios (difficulty bias + `coverage()`), evaluation/safety/regression/scorecard/benchmark/trends/anomaly engines, async job queue (in-memory + Redis, depth-bounded), FastAPI API (API-key auth + CORS + rate limiting + Prometheus `/metrics` + structured JSON logging + `X-Request-ID` tracing), Typer CLI, and a Slack webhook + SMTP email dispatcher that is **configured but never dispatched** (see [Notification dispatch](#notification-dispatch-configurable-not-yet-dispatched) — no shipped entrypoint calls it) |
| `tests/` | pytest suite covering every module. **No test count is quoted here on purpose** — a hand-copied number goes stale on the next commit, and the figure has already contradicted itself across this repo's docs four separate times (338 / 601 / 1,230 / 1,274 / 1,328 / 1,330). `python -m pytest tests/` prints the current totals; `python -m pytest tests/ --collect-only -q` counts collected tests without running any, and a collection count is not a pass count (skipped and xfailed tests are collected but never pass). Coverage is gated at a 90% floor in CI (`fail_under` in `pyproject.toml`) — treat 90% as the gate and the current `TOTAL` line as the number to keep near, not a fixed target |
| `scripts/build.ps1` | Continuous build entrypoint: venv bootstrap, deps, tests, publishes status to the vault |
| `.github/workflows/` | CI (on push/PR) + nightly validation pipeline |
| `Dockerfile`, `docker-compose.yml` | Container runtime: multi-stage build (slim runtime, non-root, healthcheck) + API + Redis + PostgreSQL + CPU job `worker` service (GPU Isaac worker placeholder) |
| `.env.example` | Environment template (store / job-queue / API / SMTP / LLM vars) — copy to `.env`; drives both the app and Compose |
| `requirements.txt`, `requirements-dev.txt` | Runtime deps; dev/test deps (pytest, pytest-cov, ruff, PyYAML) split out (`pyproject.toml`'s `dev` extra carries pytest + ruff + PyYAML) |
| `builds/` | Local build artifacts (log CSV, JUnit XML) — gitignored |

## Quick Start

```powershell
# Continuous build (installs deps into .venv, runs tests, updates vault status)
powershell -ExecutionPolicy Bypass -File scripts/build.ps1

# Run the API + dashboard v0 (dashboard at http://127.0.0.1:8000/)
.venv\Scripts\python.exe -m uvicorn validsim.api.main:app --reload

# CLI: validate a checkpoint (mock backend in MVP)
.venv\Scripts\python.exe -m validsim.cli run --task bin_picking --episodes 1000 --adversarial 50 --environment mock-scene

# CLI: render a human-readable report for the latest run (markdown | html)
.venv\Scripts\python.exe -m validsim.cli report --latest --format markdown

# CLI: delete a stored run (CLI parity for DELETE; --run-id or --latest, exits 2 if absent)
.venv\Scripts\python.exe -m validsim.cli delete --latest

# CLI: async job queue — enqueue a job, list the queue, run a worker (mock backend)
.venv\Scripts\python.exe -m validsim.cli job enqueue --checkpoint ckpt-42 --task bin_picking --episodes 1000
.venv\Scripts\python.exe -m validsim.cli jobs
.venv\Scripts\python.exe -m validsim.cli worker --once

# Containers (api + redis + postgres + CPU job worker)
docker compose up --build
```

### Validation store

Local library and CLI runs use the in-memory store when `VALIDSIM_STORE` is
unset. It preserves the zero-config promise, but its data disappears when the
process exits; the configured backend and durability are visible in both
`validsim health` and `GET /api/v1/health`.

For durable local history, set one variable before the run and gate commands:

```powershell
$env:VALIDSIM_STORE = "sqlite"
```

SQLite then creates `validsim.db` in the current directory; set
`VALIDSIM_SQLITE_PATH` to choose another location. `VALIDSIM_STORE=postgres`
uses the durable PostgreSQL store when `VALIDSIM_PG_URL` is configured. The
local `actions/validate` action already selects SQLite automatically.

`validsim gate` refuses the memory backend and exits `2` rather than making a
deploy decision from process-local data. For the one-line local fix, run
`$env:VALIDSIM_STORE = "sqlite"` before the earlier `validsim run` and
`validsim gate`; for CI, `actions/validate` sets the same option for you.

### API Hardening & Pagination

The API is deployment-hardened via environment variables, read at `create_app()` time:

- `VALIDSIM_API_KEY` — when set, every `/api/v1` route requires a matching `X-API-Key` header (constant-time comparison, uniform 401). Unset = auth disabled (local dev / tests).
- `VALIDSIM_CORS_ORIGINS` — comma-separated CORS origin allow-list (default `*`).
- `VALIDSIM_RATE_LIMIT` / `VALIDSIM_RATE_WINDOW_SECONDS` — per-client sliding-window budget for write/sensitive routes (default `60` per `60`s; set `VALIDSIM_RATE_LIMIT=0` to disable).

List endpoints are paginated with `limit` (1–500, default 100) and `offset`:

- `GET /api/v1/validations?limit&offset` — newest-first summaries with `{"total", "limit", "offset", "items"}`.
- `GET /api/v1/models?limit&offset` — model registry (one row per checkpoint, latest composite/decision).

### Notification dispatch (configurable, not yet dispatched)

`validsim/notify/` implements two complete, tested channels — `WebhookDispatcher`
(`notify/dispatcher.py`: Slack Block Kit or raw JSON, optional HMAC-SHA256 body
signing via `X-ValidSim-Signature`, severity routing, retry with exponential
backoff) and `EmailNotifier` (`notify/email.py`: SMTP, multipart HTML+text
scorecard bodies, header-injection guards).

**Neither channel is dispatched by a validation run today.** The configuration
surface exists and is documented below, but `run_and_score` — the one function
the API, CLI and job worker all funnel through — does not import `validsim.notify`
and never calls a dispatcher. There is also no `POST /api/v1/webhooks` route and
no CLI flag. So setting any of the variables below configures the layer without
activating it: nothing is sent, and no run is affected.

Once dispatch is wired, the design is fail-closed on two independent switches,
and **both** must be set:

| Variable | Meaning | Default |
|---|---|---|
| `VALIDSIM_NOTIFY_ENABLED` | Master switch. Only an explicitly truthy value turns dispatch on; absent, blank, `0`, `false` or an unrecognised value like `maybe` means **off**. | off |
| `VALIDSIM_WEBHOOK_URLS` | Comma-separated destination URLs. Configuring a destination is deliberately *not* consent to send. | none — no hooks |
| `VALIDSIM_WEBHOOK_SECRETS` | HMAC-SHA256 shared secret per URL (a secret, so it is masked in `validsim config show` and support bundles). | none — unsigned |
| `VALIDSIM_WEBHOOK_FORMATS` | `json` or `slack` per URL. | `json` |
| `VALIDSIM_WEBHOOK_MIN_SEVERITY` | `info` / `warn` / `critical` routing threshold per URL. | `info` (receives everything) |
| `VALIDSIM_WEBHOOKS_LIVE` | `1` switches the dispatcher from dry-run to real HTTP POSTs. | dry-run (records, sends nothing) |
| `VALIDSIM_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS` | SMTP connection for `EmailNotifier(dry_run=False)`. | — |

The three companion lists pair by index, and a shorter list **pads with the safe
default** rather than shifting values, so a single-URL setup cannot accidentally
sign with a neighbour's key. Blank or unparseable entries are dropped, not
fatal.

Because the default is dry-run *and* the master switch is off, the correct way
to prove a configuration works is a dry run: a recorded delivery always reports
`ok=True`, so a "successful" delivery that nobody received almost always means
either the switch is off or the dispatcher is still in dry-run.

### Async Job Queue

Validation runs can be executed out-of-band through a job queue that decouples
"submit" from "run". The backend is selected by `VALIDSIM_JOB_QUEUE`
(`memory` by default, or `redis` via `VALIDSIM_REDIS_URL`); both share one
interface, so the API and a separate worker process can drive the same queue.

- **CLI** — `validsim job enqueue --checkpoint … --task …` adds a job, `validsim jobs` lists the queue, and `validsim worker [--once] [--poll-seconds N]` claims queued jobs, runs the full mock pipeline, and persists the finished run to the configured store.
- **REST** — `POST /api/v1/jobs` (202) enqueues, `GET /api/v1/jobs` lists, `GET /api/v1/jobs/{id}` returns the full record, `GET /api/v1/jobs/{id}/status` a compact `{job_id, status, updated_at}` snapshot, and `GET /api/v1/jobs/{id}/events` streams progress over **Server-Sent Events** until the job reaches `done`/`failed`.
- **Lifecycle** — each job moves `queued → running → done|failed`; on success the record's `result` is the persisted run id (the queue and the store share that key).
- **Pagination** — `GET /api/v1/jobs` accepts `limit` (1–500) and `offset`; supplying either switches the response from the historical bare array to the same newest-first `{total, limit, offset, items}` envelope used by `GET /api/v1/validations` (with neither, the full list is returned unchanged).
- **Backpressure** — the queue is depth-bounded by `max_depth` (default 1000, env `VALIDSIM_JOB_QUEUE_MAX_DEPTH`); when full, `POST /api/v1/jobs` returns `503 Service Unavailable` with `{"error": "queue_full", "max_depth": N}` and a `Retry-After` header rather than an opaque `500`.
- **Graceful shutdown** — `JobWorker.run_forever` installs `SIGTERM`/`SIGINT` handlers by default, so a container stop (or Ctrl-C) drains the in-flight job to its terminal state before the process exits cleanly and the prior handlers are restored.

### Run Lifecycle & Readiness

- **Delete a run** — `DELETE /api/v1/validations/{run_id}` removes a stored run (`204` on success, `404` when unknown), backed by `store.delete()` across the in-memory, SQLite, and PostgreSQL stores. The same operation is available from the CLI via `validsim delete --run-id … | --latest` (exit `0` on success, `2` when the run is absent), giving the destructive endpoint full run-deletion parity.
- **Readiness probe** — `GET /api/v1/health` returns an enriched payload: `status`, `version`, `auth_enabled`, `cors_wildcard`, `rate_limit`, plus `store_backend` and `job_queue_backend` labels derived from the injected instances (e.g. `memory`/`sqlite`/`postgres`, `memory`/`redis`). It performs no I/O, so it is safe to back the container healthcheck.
- **Dashboard** — the SPA at `/` ships a **light/dark theme toggle** (persisted to `localStorage`, follows the OS `prefers-color-scheme` until an explicit choice, applied before first paint to avoid a flash) and a **Job Queue** panel that polls `/api/v1/jobs` every 3s with an inline enqueue form.

### Observability

The API is instrumented for production operation:

- **Prometheus metrics** — `GET /metrics` serves the text exposition format (also mounted auth-gated at `/api/v1/metrics`): `validsim_runs_total`, `validsim_approvals_total`, `validsim_blocks_total`, the `validsim_composite_score` gauge (latest run), `validsim_build_info{version=…}`, and `validsim_http_requests_total{class="2xx|3xx|4xx|5xx"}`. The run/approval/block/composite values are derived from the injected store in a single cheap pass, so a scrape performs no extra I/O.
- **Structured JSON logging** — `validsim.logging.configure_logging()` (called at `create_app()` time, idempotent) attaches exactly one handler that emits a single JSON object per record (`timestamp`, `level`, `logger`, `message` plus any structured `extra` fields); the level is controlled by `VALIDSIM_LOG_LEVEL`.
- **Request tracing** — the outermost ASGI middleware reuses an inbound `X-Request-ID` or mints a `uuid4`, echoes it on every response, and logs one structured `http_request` line (`method`, `path`, `status`, `duration_ms`, `request_id`) while bumping the HTTP counter — so 401/429 produced by inner auth/rate-limit layers are still observed.

## The Continuous Build Process

1. **Local/agent pipeline** — `scripts/build.ps1` bootstraps the environment,
   installs dependencies, runs the full test suite, and regenerates
   `vault/00 - Dashboard/Build Status.md` (linked from the vault Home as
   `[[Build Status]]`). Run it on demand, or let an agent run it as part of a
   work loop. **There is no scheduler in this repository** — nothing here fires
   hourly by itself, and earlier text on this page claiming a 24/7 hourly
   automation was wrong (see `docs/ISSUE_CATALOG.md` item 17).
2. **Remote CI** — `.github/workflows/ci.yml` runs lint + tests (under `pytest-cov`, publishing a `coverage.xml` artifact) + Docker build on every push/PR to `main`; `.github/workflows/nightly.yml` runs a deep validation suite at 03:00 UTC daily.
3. **History** — every `build.ps1` run is appended to `builds/build-log.csv`; the vault note shows the last 15 runs and pass-rate.

## Product Pipeline (what the code does)

```
submit checkpoint → simulate (episodes + a single domain-randomization level)
→ adversarial scenarios (12 categories, deterministic rule-based generator)
→ evaluate (success, safety, robustness) → regression test vs baseline
(bootstrap CI) → scorecard (weighted mean of the *measured* components:
0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression) → APPROVE / BLOCK
```

**Unmeasured components abstain.** Two of the four terms often cannot be
measured at all, and they are excluded from the composite's denominator rather
than counted as a perfect score (see `validsim/engine/scorecard.py`):

- **Robustness** is the dispersion of success rate *across randomization
  groups*, but `run_validation` applies one `task.randomization` level to every
  episode, so there is exactly one group and the metric is unmeasured. The
  scorecard records `robustness_measured: false` and
  `randomization_group_count: 1`; the component then reports `0.0` and its 0.2
  weight leaves the denominator.
- **Regression** is unmeasured when no baseline was supplied
  (`regression_baseline_available: false`) — nothing was compared, so its 0.1
  weight leaves the denominator too.

This matters because the previous flat sum awarded those 30 points
unconditionally. A run in which **every episode failed** (`success_rate = 0.0`)
scored 60.0 and was **APPROVED at threshold 60** — a gate that certified a model
which never completed the task. The same run now scores **42.86 → BLOCK at any
threshold**. A flawless run still reaches 100.0, and the ordering stays
monotonic.

Because abstention changes the denominator, composite values are **comparable
only across runs with the same evidence coverage**; read
`robustness_measured` and `regression_baseline_available` alongside the score.
The full rationale and measured before/after table are in
[`docs/adr/0002`](docs/adr/0002-scorecard-composite-gate.md).

Additionally, a run is `BLOCK`ed when it delivered fewer episodes than requested
or recorded no success at all (`block_reasons`), and — separately from the
composite — when a sufficiently large adversarial segment (**≥ 30 episodes**)
fails a one-sided binomial test against a 60% success floor.

This runs **synchronously** (`POST /api/v1/validations`) or **asynchronously** —
enqueue a job (`POST /api/v1/jobs` / `validsim job enqueue`), let a `worker`
drain the queue, and stream progress over SSE until the run is persisted.

## Milestone Status (per 8-Week Sprint Plan)

- [x] Week 1: repo, Docker, CI/CD pipeline
- [x] Weeks 2–5 skeleton: episode runner (mock), randomization, evaluation, safety, regression, scorecard
- [x] Weeks 4/6: scenario generator, API, CLI, SQLite persistent store, scorecard Markdown/HTML exports
- [~] **Webhook dispatcher** (`notify/dispatcher.py` + `notify/config.py`) — implemented, tested and now *configurable* via `VALIDSIM_NOTIFY_ENABLED` + `VALIDSIM_WEBHOOK_URLS`, but **still not dispatched**: `run_and_score` does not import `validsim.notify`, so no run sends a scorecard. See [Notification dispatch](#notification-dispatch-configurable-not-yet-dispatched).
- [x] Week 6: **GitHub Actions plugin prototype** (`actions/validate`, `actions/scorecard`, `examples/robot-validation.yml`, `docs/github-actions.md`)
- [~] **LLM adversarial scenario generator** (`scenarios/llm_generator.py` — OpenAI-compatible provider, strict schema validation, deterministic rule-based fallback, env-driven factory `create_scenario_generator()`). **Implemented and tested, but not reachable from a validation run**: `engine/pipeline.py:131` hardcodes `ScenarioGenerator(seed=seed).generate(...)`, so scenario generation is deterministic and rule-based on every shipped path (API, CLI, job worker). Setting `VALIDSIM_LLM_ENABLED` + `VALIDSIM_LLM_API_KEY` changes nothing until the pipeline routes through the factory.
- [x] Week 5 upgrade: **PostgreSQL store** (`store/postgres.py` — JSONB + indexed columns, `VALIDSIM_STORE=postgres`)
- [x] Week 7: **Dashboard v0** — dark-theme scorecard/history/failed-mode charts served by FastAPI at `/` (design system: `design-system/validsim/MASTER.md`)
- [x] **Isaac worker adapter** — `sim/isaac_worker.py` HTTP client + `docs/isaac-worker.md` worker contract (`VALIDSIM_BACKEND=isaac`); GPU worker image awaits DGX credits
- [x] Week 8: **branded PDF scorecard** — `engine/pdf.py` + `GET /api/v1/validations/{id}/scorecard.pdf`
- [x] **Model registry endpoints** — `GET /api/v1/models`, `GET /api/v1/models/{id}/history`
- [x] **Packaging** — `pyproject.toml`, `pip install .`, `validsim` console script, CLI `--latest` flag
- [x] **API hardening** — env-driven API-key auth (`VALIDSIM_API_KEY` → `X-API-Key`) + configurable CORS (`VALIDSIM_CORS_ORIGINS`)
- [x] **Paginated list endpoints** — `GET /api/v1/validations` and `GET /api/v1/models` with `limit`/`offset`
- [x] **CLI `report` command** — `validsim report --latest --format markdown|html`; `run` gains `--environment` (scene profile)
- [~] **Slack webhooks** — Block Kit payloads, optional HMAC-SHA256 signing (`X-ValidSim-Signature`), retry with exponential backoff, severity routing, plus an operator-facing config surface. **Not dispatched by any run** — the dispatcher is dry-run by default and nothing in the run path calls it (see [Notification dispatch](#notification-dispatch-configurable-not-yet-dispatched)).
- [~] **LLM scenario category-coverage guarantee** — missing adversarial categories are auto-topped-up from the deterministic fallback. Implemented on the `LLMScenarioGenerator` path, which no run takes; the rule-based generator guarantees full category coverage by construction (it cycles categories by index), so any `n ≥ 12` is already complete.
- [x] **Postgres store full-detail parity** — `VALIDSIM_STORE=postgres` now matches the SQLite store's full-detail output (drop-in replacement)
- [x] **Multi-stage Dockerfile + `requirements-dev.txt`** — slim non-root runtime image; dev deps (pytest, ruff, pytest-cov) split out of the runtime image
- [x] **Async job queue** — `jobs/` package with in-memory and Redis backends (`VALIDSIM_JOB_QUEUE`, `VALIDSIM_REDIS_URL`); CLI `validsim job enqueue` / `jobs` / `worker`, REST `/api/v1/jobs` (enqueue/list/get/status) + SSE progress at `/api/v1/jobs/{id}/events`, and a `JobWorker` that drains the queue and persists finished runs
- [x] **Run deletion** — `DELETE /api/v1/validations/{id}` (204/404) backed by `store.delete()` across the memory, SQLite, and PostgreSQL stores
- [x] **Enriched readiness probe** — `GET /api/v1/health` reports `auth_enabled`, `cors_wildcard`, `rate_limit`, `store_backend`, and `job_queue_backend` (no-I/O, safe for the container healthcheck)
- [x] **Dashboard Job Queue panel** — SPA polls `/api/v1/jobs` every 3s with an inline enqueue form
- [x] **Scenario difficulty bias + `coverage()`** — `ScenarioGenerator(difficulty_bias=…)` eases/hardens sampled difficulty without perturbing the RNG stream; `coverage()` returns per-category counts for a batch
- [x] **Benchmark `compare_scorecards`** — `engine/benchmark.py` head-to-head comparison of two scorecards (composite/success/safety/robustness deltas + winners, overall verdict by composite), exported from the `validsim.engine` facade
- [~] **Email notification channel** — `notify/email.py` `EmailNotifier` (SMTP, dry-run by default, multipart HTML+text scorecard bodies). Implemented and tested, but like the webhook dispatcher it has **no production caller**; live delivery additionally requires `EmailNotifier(dry_run=False)` plus `VALIDSIM_SMTP_*`, which no shipped surface sets.
- [x] **Coverage gate in CI** — the suite runs under `pytest-cov` on every push/PR, publishes `coverage.xml`, and fails the build below 90% line/branch coverage. (The previously quoted "baseline ≈95%" was an undated snapshot; the enforced number is the 90% floor — read the current `TOTAL` line for the real figure.)
- [x] **`.env.example` + Docker `worker` service** — committed environment template; `docker-compose.yml` adds a CPU job worker that drains the Redis queue into the shared store
- [x] **CLI `delete` command** — `validsim delete --run-id … | --latest` removes a stored run from the configured store (exit `0` on success, `2` when absent), giving the destructive `DELETE` endpoint full CLI parity
- [x] **Observability** — Prometheus `/metrics` (store-derived run/approval/block/composite gauges + in-process `validsim_http_requests_total` counters), idempotent structured JSON logging (`validsim.logging`, `VALIDSIM_LOG_LEVEL`), and `X-Request-ID` correlation echoed on every response via the outermost ASGI middleware
- [x] **Dashboard light/dark theme toggle** — persisted to `localStorage`, follows the OS `prefers-color-scheme` until an explicit choice, applied before first paint (no flash); the failure-mode chart re-renders with theme-aware colours
- [x] **Worker graceful shutdown** — `JobWorker.run_forever` installs `SIGTERM`/`SIGINT` handlers by default, draining the in-flight job to its terminal state before exiting cleanly and restoring the prior handlers
- [x] **Jobs pagination + queue backpressure** — `GET /api/v1/jobs` accepts `limit`/`offset` (newest-first `{total, limit, offset, items}` envelope, bare array preserved when neither is given); a full queue (`max_depth`, default 1000) makes `POST /api/v1/jobs` return `503` with `Retry-After` instead of a `500`
- [x] **Per-failure-mode trends + anomaly detection** — `engine/trends.py` `failure_mode_trends()` reports each mode's rate change (rising/falling/flat) across a history; `engine/anomaly.py` `detect_anomalies()` flags statistically unusual spikes (z-score vs a baseline combining run dispersion, bootstrap standard error, and binomial noise) as `warning`/`critical`; both exported from the `validsim.engine` facade
- [ ] Real Isaac Sim/Lab GPU worker image (contract + client are done; needs DGX credits)
- [ ] Timescale retention, LLM scenarios on real models, Next.js production dashboard, MIT open-source CLI (Phase 2)

See the vault: `05 - Execution/8-Week Sprint Plan.md`

---
*Status: CONFIDENTIAL — Founding Document + MVP scaffold. Target: YC W27 (Nov 2, 2026) + NVIDIA Inception.*
