# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Iteration 6 of ValidSim: an asynchronous validation pipeline (Redis-backed job
queue, worker, REST + SSE surface, CLI commands), full CRUD on stored runs, a
richer health probe, and a dashboard job-queue panel — plus scenario difficulty
control, head-to-head benchmark comparison, SMTP notifications, and a test and
coverage push that closes the loop on the Docker stack.

### Added

- **Async job queue package** (`validsim/jobs/`) — `JobQueue` (thread-safe,
  in-memory) and `RedisJobQueue` (same interface; JSON payloads under
  `validsim:jobs:*` plus a FIFO index list) so the API and out-of-process
  workers share one queue. Selected by `VALIDSIM_JOB_QUEUE=memory|redis` with
  `VALIDSIM_REDIS_URL`; like the optional Postgres store, the `redis` driver is
  imported lazily, so a missing driver surfaces as an actionable error at first
  use rather than at import time.
- **Job worker** (`validsim/jobs/worker.py`) — `JobWorker.run_once()` /
  `run_forever()` claim queued jobs and drive the same engine path as the
  synchronous API, persisting a `StoredRun` under the job's own `run_id`.
  Lifecycle is `queued → running → done|failed`; one bad job fails only itself,
  and seeds derive from `stable_seed()` so results stay deterministic.
- **Job endpoints** — `POST /api/v1/jobs` (202, returns `job_id` + status),
  `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, the compact
  `GET /api/v1/jobs/{id}/status`, and `GET /api/v1/jobs/{id}/events`, which
  streams status over Server-Sent Events until the job is terminal. The router
  is wired into `create_app()` and inherits the `X-API-Key` gate.
- **CLI job commands** — `validsim job enqueue` (checkpoint/task/episodes/
  adversarial flags), `validsim jobs` (id, status, checkpoint, created table),
  and `validsim worker [--once] [--poll-seconds N]`, which wires the configured
  queue, store and mock backend together.
- **Run deletion across all stores** — `delete(run_id) -> bool` on the memory,
  SQLite and Postgres stores (remove-and-report in a single step, so there is no
  check-then-delete race), exposed as `DELETE /api/v1/validations/{run_id}`
  returning 204, or 404 for an unknown run.
- **Dashboard job-queue panel** — enqueue form plus a jobs table on `/`, polling
  `/api/v1/jobs` every 3 s (paused while the tab is hidden), newest first, with
  icon-and-label status badges and an offline-graceful "queue unavailable" state.
- **Scenario difficulty bias + `coverage()`** — `ScenarioGenerator` accepts
  `difficulty_bias` in `[-1, 1]`, shifting sampled difficulty easier/harder
  without consuming RNG draws (ids, categories and params are unchanged, and the
  default `0.0` reproduces the previous output exactly); `coverage(task_id, n)`
  reports per-category counts across all twelve adversarial categories.
- **`compare_scorecards()`** (`validsim/engine/benchmark.py`) — head-to-head
  comparison of two scorecard dicts over composite, success rate, safety and
  robustness, returning per-metric deltas, winners and an overall verdict decided
  by `composite`; missing or non-numeric metrics degrade to `None`/`tie`.
- **Email notification channel** (`validsim/notify/email.py`) — `EmailNotifier`
  sends messages or a scorecard as multipart HTML + plain-text email, configured
  via `VALIDSIM_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`. Dry-run by default (no
  socket I/O); live mode captures failures on `EmailDelivery` instead of raising.
- **`.env.example`** — commented template of every supported variable (store, job
  queue, API hardening, backend, Isaac worker, LLM, SMTP), shared by the
  application and `docker-compose.yml`.
- **`worker` service in Docker Compose** — CPU (mock-backend) worker running
  `python -m validsim.cli worker` against the Redis queue and the shared Postgres
  store, reusing the api image; the GPU `worker-gpu` placeholder stays documented
  but out of the stack.
- **Concurrency and edge-case test suites** — `tests/test_stats_edge.py`
  (degenerate samples, tiny `n`, extreme confidence levels, seed sensitivity,
  p-value monotonicity, performance guard), `tests/test_store_concurrency.py`
  (barrier-released multi-thread `save` on the memory and SQLite stores), and
  coverage for the queue, worker, SSE, CLI job commands, deletion, health probe,
  dashboard panel, difficulty bias, benchmark compare and email notifier.
- **Enforced coverage floor in CI** — the test job now runs under `pytest-cov`
  and fails the build when line/branch coverage drops below 90% (baseline
  measured at ~95%; the floor leaves headroom for normal measurement jitter).

### Changed

- **Enriched `GET /api/v1/health`** — alongside the stable `status` and `version`
  keys, the probe now reports the effective configuration: `auth_enabled`,
  `cors_wildcard`, `rate_limit`, `store_backend` and `job_queue_backend`. Every
  field is read from `app.state`, so the route still performs no I/O and remains
  safe as a container healthcheck.

### Fixed

- **Postgres was never wired into the Docker stack** — the `api` service now sets
  `VALIDSIM_STORE` (defaulting to `postgres`) and `VALIDSIM_PG_URL`, so
  `docker compose up` persists runs to the `postgres` service instead of silently
  falling back to the in-memory store.

## [0.2.0] - 2026-02-06

Iteration 5 of ValidSim: hardening the API surface (auth, pagination), expanding the
CLI and webhook integrations, improving LLM scenario generation, and production-ready
packaging and containerization.

### Added

- **API key authentication** — requests can be authenticated with an API key
  (`VALIDSIM_API_KEY`), protecting the validation and model registry endpoints.
- **Configurable CORS** — allowed origins are configurable via environment
  variables instead of a hardcoded policy.
- **Pagination endpoints** — list endpoints (validations, models, history)
  now support `limit` / `offset` pagination parameters.
- **CLI `report` command** — generate a scorecard/report for a past validation
  run directly from the command line, with a new `--environment` flag to
  select the target environment (e.g. sim vs. real-hardware profile).
- **Slack webhook integration** — webhook dispatcher can post scorecards to
  Slack via a dedicated Slack message formatter, secured with HMAC signature
  verification and automatic retry on delivery failure.
- **LLM scenario category-coverage top-up** — the LLM adversarial scenario
  generator now detects missing adversarial categories and issues follow-up
  generations until all categories are covered.
- **PostgreSQL store full-detail parity** — the Postgres store now returns the
  same full validation detail as the SQLite store, making `VALIDSIM_STORE=postgres`
  a drop-in replacement.
- **Multi-stage Dockerfile** — slimmer production images via a multi-stage build
  (build deps no longer leak into the runtime layer).
- **`requirements-dev.txt` split** — development dependencies (pytest, ruff)
  are now separated from runtime dependencies; also exposed as the `dev` extra
  in `pyproject.toml`.
- **Memory-store test coverage** — the in-memory store backend now has
  dedicated tests covering persistence behavior.
- **Engine branch tests** — additional tests for evaluation/safety/regression
  engine edge branches (boundary scores, fallback paths).
- **Engine facade exports** — the `validsim.engine` package now exports a
  clean public facade; dead code removed from the engine modules.

### Changed

- Packaging cleanup: shipped package contents limited to the `validsim` tree;
  runtime dependencies pinned in `pyproject.toml` and kept in sync with
  `requirements.txt`.

## [0.1.0]

### Added

- **MVP platform** (190 tests): config models, simulation runner (mock Isaac
  backend), adversarial scenarios, evaluation/safety/regression/scorecard
  engines, FastAPI API, and Typer CLI.
- **CI/CD pipeline** — GitHub Actions CI (lint + tests + Docker build on
  push/PR) and a nightly deep-validation workflow.
- **Dashboard v0** — dark-theme scorecard/history/failed-mode charts served
  by FastAPI at `/`.
- **LLM adversarial scenario generator** (`validsim/scenarios/llm_generator.py`)
  — OpenAI-compatible provider with strict schema validation and a
  deterministic rule-based fallback.
- **PostgreSQL store** (`validsim/store/postgres.py`) — JSONB + indexed
  columns, selected via `VALIDSIM_STORE=postgres`.
- **Isaac worker adapter** — HTTP client (`validsim/sim/isaac_worker.py`) plus
  the worker contract (`docs/isaac-worker.md`), enabled with
  `VALIDSIM_BACKEND=isaac`.
- **Branded PDF scorecard** — `validsim/engine/pdf.py` and
  `GET /api/v1/validations/{id}/scorecard.pdf`.
- **Model registry endpoints** — `GET /api/v1/models` and
  `GET /api/v1/models/{id}/history`.
- **Packaging + CLI `--latest`** — `pyproject.toml`, `pip install .`,
  `validsim` console script, and a `--latest` flag for the CLI.
- **GitHub Actions plugin prototype** — `actions/validate`, `actions/scorecard`,
  and `examples/robot-validation.yml`.
- **Obsidian vault** — the full founding blueprint as interconnected notes,
  including the 8-week sprint plan and build-status dashboard.

[Unreleased]: https://example.com/validsim/compare/v0.2.0...HEAD
[0.2.0]: https://example.com/validsim/compare/v0.1.0...v0.2.0
[0.1.0]: https://example.com/validsim/releases/tag/v0.1.0
