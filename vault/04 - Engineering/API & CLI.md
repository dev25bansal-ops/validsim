---
tags:
  - engineering
  - api
  - cli
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🔌⌨️ API & CLI

Consolidated reference for the ValidSim REST API surface (v1) and the `validsim` CLI command set. Sources of truth: `validsim/api/main.py` (FastAPI 0.110+) and `validsim/cli.py` (Typer). OpenAPI spec auto-generated; docs ship at `docs.validsim.com` (sprint W6 deliverable). Full design rationale: [[API Design]] and [[CLI Design]].

## Key endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness probe (`{status, version}`) |
| `POST` | `/api/v1/validations` | Submit a new validation run (201; synchronous against mock backend) |
| `GET` | `/api/v1/validations` | Paginated list of run summaries, **newest first** |
| `GET` | `/api/v1/validations/{id}` | Get run summary (no episode payloads) |
| `GET` | `/api/v1/validations/{id}/scorecard` | Full scorecard JSON |
| `GET` | `/api/v1/validations/{id}/scorecard.pdf` | Branded PDF attachment (501 if `reportlab` missing) |
| `GET` | `/api/v1/validations/{id}/failures` | Failure taxonomy + per-failure episode details |
| `POST` | `/api/v1/validations/{id}/compare` | Compare with another stored run |
| `GET` | `/api/v1/models` | Model-registry view: one row per validated checkpoint (paginated) |
| `GET` | `/api/v1/models/{id}/history` | Chronological (oldest-first) summaries for a checkpoint (404 if unknown) |
| `GET` | `/api/v1/regressions` | Runs whose stored comparison flagged significant regressions |

## Pagination

### `GET /api/v1/validations`

| Query param | Type | Default | Constraints |
|---|---|---|---|
| `limit` | int | `100` | `1–500` (422 outside range) |
| `offset` | int | `0` | `≥ 0` |

Response is an envelope — ordering is **newest first**, and `total` is the full stored-run count independent of pagination:

```json
{
  "total": 128,
  "limit": 100,
  "offset": 0,
  "items": [ { "run_id": "vrun-1a2b3c4d", "checkpoint_id": "...", "...": "..." } ]
}
```

`items` are compact run summaries (`StoredRun.summary()`), not full episode payloads.

### `GET /api/v1/models`

Same `limit` (1–500, default 100) / `offset` params, but returns a **plain array** (no envelope — no `total`), paginated over the stable "order of first validation" ordering. Aggregation is computed from the full history first; only the final slice is affected by pagination.

## Auth

| `VALIDSIM_API_KEY` set? | Behavior |
|---|---|
| No | Auth disabled — all `/api/v1` routes open (local dev, tests) |
| Yes | Every `/api/v1` route requires header `X-API-Key: <key>` |

- Wrong/missing key → uniform `401` (`detail: "Missing or invalid API key"`, `WWW-Authenticate: ApiKey`); comparison is constant-time (`secrets.compare_digest`) and never reveals whether the key exists.
- The value is read once at `create_app()` time — restart or rebuild the app to change it.
- Same env var the CLI and GitHub Action use ([[GitHub Actions Integration]]).
- Post-MVP: Auth0/Clerk multi-tenant + RBAC ([[MVP Non-Goals]], [[Pricing Tiers]]).

## CORS

Driven by env var `VALIDSIM_CORS_ORIGINS` (read at `create_app()` time):

- Comma-separated origin allow-list, e.g. `VALIDSIM_CORS_ORIGINS=https://app.validsim.com,https://staging.validsim.com`
- Whitespace around entries is stripped; empty entries are dropped (trailing commas are safe)
- Default `*` → allow-all (previous behavior); `allow_credentials=False`, all methods/headers allowed

## CLI command surface

```bash
# Run a validation locally (mock backend) and persist to the store
$ validsim run \
    --checkpoint gr00t_v42 \
    --task bin_picking \
    --robot franka_panda \
    --environment warehouse_a \
    --episodes 500 \
    --adversarial 24

# Check status / scorecard / gate (exactly one of --run-id or --latest required)
$ validsim status --run-id abc123
$ validsim scorecard --latest

# Human-readable report
$ validsim report --run-id abc123 --format markdown
$ validsim report --latest --format html

# Deploy gate check (exit 1 on BLOCK — CI-ready)
$ validsim gate --run-id abc123 --threshold 85
```

### Notable commands

| Command | Key options | Notes |
|---|---|---|
| `run` | `--task/-t`, `--robot/-r`, `--episodes/-e` (≥1), `--adversarial/-a` (≥0), `--checkpoint/-c`, `--environment/-E`, `--threshold` | `-E` selects the environment/scene name (default `mock-scene`). Persists to the store configured by `VALIDSIM_STORE` (`sqlite` makes runs visible to the API/dashboard; default in-memory keeps local runs side-effect free) and caches the scorecard JSON (`VALIDSIM_CACHE_FILE`, default `.validsim/scorecards.json`) |
| `report` | `--run-id` / `--latest`, `--format markdown\|html` (default `markdown`) | Prints `scorecard_to_markdown` / `scorecard_to_html` output; invalid `--format` → `BadParameter` (exit 2). Reads from the local scorecard cache |
| `status` | `--run-id` / `--latest` | Newest cached run chosen by `created_at` (ties broken by insertion order) |
| `gate` | `--run-id` / `--latest`, `--threshold` (negative = use stored threshold) | Exit `0` = APPROVE, `1` = BLOCK, `2` = unknown run / missing parameter |

### CLI ↔ API mapping

| Command | Calls | Flow ([[Core User Flows]]) |
|---|---|---|
| `run` | `POST /api/v1/validations` (equivalent pipeline) | Flow 1 |
| `status` | `GET /api/v1/validations/{id}` | Flow 1 |
| `scorecard` | `GET /api/v1/validations/{id}/scorecard` | Flows 1, 3 |
| `report` | scorecard render (Markdown/HTML) | Flows 1, 3 |
| `gate` | threshold check (mirrors `deploy_decision`) | Flow 2 |
| `ci` | wraps Actions YAML generation | [[GitHub Actions Integration]] |

> [!important]
> 1. **MVP CLI is local-first**: `run` executes against the mock backend, not the hosted API — the table above maps conceptual equivalents, not live HTTP calls.
> 2. **`--run-id` XOR `--latest`**: supplying both (or neither) is a `BadParameter` (exit 2).
> 3. **Exit codes are the product in CI** ([[Product Principles]] #4): `gate` exits non-zero on `BLOCK`.

Links: [[API Design]] · [[CLI Design]] · [[GitHub Actions Integration]] · [[Module Specs]] · [[Data Flow]] · [[Home]]
