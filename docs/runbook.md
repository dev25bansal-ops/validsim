---
tags: [engineering, operations, runbook, docker, ci]
status: prototype (W6)
---

# 🛠️ ValidSim Operations Runbook

Day-to-day operations for the ValidSim MVP: local stack, environment
configuration, the CI gate, and what to do when things break. This is the
operator-facing counterpart to [[GitHub Actions Integration]] and
[[Isaac Sim GPU Worker — HTTP Contract]].

> [!important] MVP scope
> Everything here runs locally against the mock pipeline
> (`validsim.sim.runner.MockIsaacBackend`). No GPU host is in the loop yet —
> see [docs/isaac-worker.md](isaac-worker.md) for the (contract-only) worker.

## 1. Local development stack

`make docker-up` brings up the full stack defined in
[`docker-compose.yml`](../docker-compose.yml):

| Service | Image | Port | Purpose |
|---|---|---|---|
| `api` | `validsim:local` (built from the Dockerfile) | `8000` | FastAPI app + dashboard v0 |
| `redis` | `redis:7-alpine` | `127.0.0.1:6379` | Future job queue / result backend |
| `postgres` | `postgres:16-alpine` | `127.0.0.1:5432` | System of record for runs/scorecards |

All three services define healthchecks and `restart: unless-stopped`; `api`
waits for its data stores to become healthy before starting.

```bash
make docker-up          # api + redis + postgres
make docker-down        # stop; add ARGS=-v to also drop volumes
```

> [!warning] `POSTGRES_PASSWORD` is required
> Compose aborts with `set POSTGRES_PASSWORD` if the variable is unset or
> empty. Set it in your environment or a `.env` file (never commit real ones).

### Health endpoints

| Endpoint | Service | Healthy when |
|---|---|---|
| `GET http://127.0.0.1:8000/api/v1/health` | `api` | Returns `{"status": "ok", "version": …}` with HTTP 200 |
| `redis-cli ping` (inside `validsim-redis`) | `redis` | `PONG` |
| `pg_isready -U validsim -d validsim` (inside `validsim-postgres`) | `postgres` | `accepting connections` |

Quick check from the host:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
docker compose ps   # all three services should be "healthy"
```

### Non-Docker development

`make run` serves the FastAPI app on `:8000` with autoreload; `make install`
installs runtime + dev deps; `make test` / `make lint` mirror CI. On Windows
use WSL/Git-Bash for `make`, or run the equivalent commands from each recipe
directly (see the README's PowerShell quick start).

## 2. Environment variable reference

All configuration is env-driven and read at use time (or at `create_app()` /
`create_store()` / `create_backend()` time), never at import time.

| Variable | Default | Description |
|---|---|---|
| `VALIDSIM_STORE` | `memory` | Validation store backend: `memory` \| `sqlite` \| `postgres` (case-insensitive; anything else falls back to memory). |
| `VALIDSIM_SQLITE_PATH` | `validsim.db` | SQLite database path when `VALIDSIM_STORE=sqlite`. |
| `VALIDSIM_PG_URL` | *(none)* | libpq DSN for the Postgres store (`postgresql://user:pw@host:5432/validsim`). Required when `VALIDSIM_STORE=postgres`; missing DSN + installed driver fails fast with `ValueError`. |
| `VALIDSIM_API_KEY` | *(unset)* | When set, every `/api/v1` route requires an `X-API-Key` header matching it (401 otherwise). Unset ⇒ auth disabled (local dev / tests). |
| `VALIDSIM_CORS_ORIGINS` | `*` | Comma-separated CORS origin allow-list for the API. Empty entries are dropped; `*` keeps allow-all. |
| `VALIDSIM_BACKEND` | `mock` | Simulation backend: `mock` \| `isaac` (case-insensitive; unknown names fall back to mock). |
| `VALIDSIM_ISAAC_WORKER_URL` | *(none)* | Base URL of the GPU Isaac worker (`http://worker-gpu:8090`). Missing URL raises `ValueError` at *first episode*, not construction. |
| `VALIDSIM_ISAAC_WORKER_KEY` | *(unset)* | Bearer token sent as `Authorization: Bearer …` to the worker; omitted when unset. |
| `VALIDSIM_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint root (Azure, vLLM, Ollama, NIM…). |
| `VALIDSIM_LLM_API_KEY` | *(unset)* | Bearer token for the LLM provider. **Its presence selects the LLM scenario generator**; unset ⇒ deterministic rule-based generator (no network I/O). |
| `VALIDSIM_LLM_MODEL` | `gpt-4o-mini` | Chat model for adversarial scenario generation. |
| `VALIDSIM_WEBHOOKS_LIVE` | *(unset)* | Set to `1` to enable real webhook HTTP delivery. Unset ⇒ dry-run: deliveries are recorded, no network I/O. |
| `VALIDSIM_CACHE_FILE` | `.validsim/scorecards.json` | Local JSON scorecard cache used by the CLI's `status` / `scorecard` / `gate` / `report` commands. |

Related (compose-internal, documented for completeness): `VALIDSIM_DATABASE_URL`
(DSN the API container uses) and `VALIDSIM_REDIS_URL`. Note the TLS caveat in
[docker-compose.yml](../docker-compose.yml): do **not** append
`?sslmode=require` until the postgres service itself is configured with SSL —
the `postgres:16-alpine` image ships with SSL disabled, so requiring it fails
every connection. Any connection *leaving the host* must use
`?sslmode=require` or stronger.

> [!note] Composite score weights
> Composite = 40% success + 30% safety + 20% robustness + 10% regression
> (blueprint §"Semantics of fail-below-score").

## 3. CI gate usage

`validsim gate --run-id <id> --threshold <t>` (or `--latest`) is CI-native.
Exit codes (from [`validsim/cli.py`](../validsim/cli.py)):

| Exit code | Meaning | Effect in Actions |
|---|---|---|
| `0` | `APPROVE` — composite ≥ threshold | step passes, job continues |
| `1` | `BLOCK` — composite < threshold | **step and job fail** |
| `2` | misuse — unknown run id, empty cache, or both `--run-id` and `--latest` | job fails |

`--threshold -1` (default) uses the threshold stored in the run's scorecard;
pass an explicit value to override. Gate anything behind the validate job with
`needs:` (full example: [docs/github-actions.md](github-actions.md) §5).

## 4. Incident playbook

### 4.1 API returns 401s after adding `VALIDSIM_API_KEY`

Auth is enabled the moment `VALIDSIM_API_KEY` exists at `create_app()` time.
Every `/api/v1` route then requires `X-API-Key: <key>` — including the health
probe used by the compose healthcheck, which will fail until the healthcheck
sends the header (or you exempt the route).

Fixes, in order of preference:

1. **Clients**: send the header on every request:

   ```bash
   curl -H "X-API-Key: $VALIDSIM_API_KEY" http://127.0.0.1:8000/api/v1/validations
   ```

2. **Dashboard/browser**: once the key is set, the served dashboard pages hit
   `/api/v1/*` too and will get 401s. Either inject the header at the proxy
   layer or keep the key unset in pure-local dev and enforce auth only at the
   edge.
3. **Rollback**: unset `VALIDSIM_API_KEY` and restart the API — auth is
   disabled entirely when the variable is absent.

The 401 response is uniform (missing, empty, and wrong keys are
indistinguishable) and carries `WWW-Authenticate: ApiKey`.

### 4.2 Postgres connection failures

Symptoms: `OperationalError` / `Connection refused` on first store use;
`validsim run` or the API failing at `store.save()`.

1. **Is the container healthy?** `docker compose ps` → `pg_isready` check.
   Restart: `make docker-down && make docker-up`.
2. **Wrong DSN?** `VALIDSIM_PG_URL` must point at the right host/port/creds.
   Inside the compose network use `postgres:5432`, not `localhost:5432`.
3. **DSN missing?** `VALIDSIM_STORE=postgres` without `VALIDSIM_PG_URL`
   (and the driver installed) fails fast with `ValueError` mentioning
   `VALIDSIM_PG_URL`. Set it or drop back to `VALIDSIM_STORE=sqlite`.
4. **`sslmode` mismatch?** See the TLS note in §2 — `sslmode=require` against
   a stock `postgres:16-alpine` fails every connection.
5. **Driver missing?** The psycopg import is lazy; a missing driver surfaces
   as `RuntimeError("pip install 'psycopg[binary]'")` at first use.
   `psycopg[binary]` is already in `requirements.txt` for container installs.

### 4.3 PDF endpoint returns 501

`GET /api/v1/validations/{run_id}/scorecard.pdf` returns **501** with a
`"pip install reportlab"` detail when the optional `reportlab` dependency is
missing. Everything else runs fine without it — only PDF export is gated.

Fix: `pip install reportlab` (or `pip install -r requirements.txt`, which
includes `reportlab>=4.0`) and restart the API. reportlab is imported lazily
inside the render functions, so the fix needs no code change. Note a 501 for
an unknown run id is not possible — unknown runs return 404.

### 4.4 Webhook delivery failures

Symptoms: scorecards are not reaching Slack/HTTP endpoints.

1. **Is live mode on?** Delivery defaults to **dry-run** (nothing is sent).
   Set `VALIDSIM_WEBHOOKS_LIVE=1` (or construct `WebhookDispatcher(live=True)`).
   Dry-run results always look `ok=True` — a silently-unsent scorecard is
   almost always this.
2. **Check `DeliveryResult`.** Failures are captured on the result
   (`ok=False`, `status_code`, `error`) and never propagate to callers —
   inspect `dispatcher.sent` or the hook's logs.
3. **Retries.** Live sends retry up to 2 extra times with exponential backoff
   (base 0.1 s) on transport errors and non-2xx responses, with a 5 s timeout
   per request. A deterministic `4xx/5xx` (bad URL, revoked Slack token) will
   fail all three attempts.
4. **Signature mismatch?** Hooks registered with a `secret` send
   `X-ValidSim-Signature` (HMAC-SHA256 of the JSON body). Receivers must
   verify against the raw body bytes.
5. **Slack format?** `format="slack"` hooks send Block Kit payloads, not raw
   JSON — pointing a `json` receiver at a Slack URL (or vice versa) yields
   receiver-side 4xx.

### 4.5 LLM scenario generation failing

Symptoms: runs degrade to rule-based scenarios, or `ScenarioProviderError`.
By design any provider/parse failure **falls back to the deterministic
rule-based generator** — a misbehaving LLM can never take a validation run
down. To restore LLM scenarios: check `VALIDSIM_LLM_API_KEY` is set (absent ⇒
LLM path never activates), `VALIDSIM_LLM_BASE_URL` points at the deployment
root (e.g. `.../v1`), and `VALIDSIM_LLM_MODEL` names a model the endpoint
actually serves.

## 5. Backups & recovery

### 5.1 Postgres (`pg_dump`)

The `postgres-data` volume is the system of record for runs, scorecards, and
the audit trail. Cadence for the MVP: **daily full dump, retained ≥ 14 days**
(hoist to hourly + WAL archiving once real fleet verdicts land).

```bash
# Full logical backup (run on the host or any box with pg client access)
docker exec validsim-postgres \
  pg_dump -U validsim -d validsim -Fc \
  > backups/validsim-$(date +%Y%m%d-%H%M).dump

# Restore into a fresh cluster
cat backups/validsim-YYYYMMDD-HHMM.dump | \
  docker exec -i validsim-postgres \
  pg_restore -U validsim -d validsim --clean --if-exists
```

Notes:

* `-Fc` (custom format) enables selective restore and compression.
* The store's `save` uses `ON CONFLICT (run_id) DO NOTHING` — the verdict log
  is append-only, so a partial restore cannot silently overwrite newer
  verdicts; it can only leave them out.
* Redis holds no persistence-critical data (dev-local queue placeholder) —
  no backup needed; its `--save 60 1` snapshot is sufficient.

### 5.2 Scorecard cache recovery

The CLI cache (`VALIDSIM_CACHE_FILE`, default `.validsim/scorecards.json`) is
what `validsim status` / `scorecard` / `gate` / `report` read **across
processes** — losing it breaks `gate` with exit code 2 (`no cached run …
(run 'validsim run' first)`), and in CI it means validate + scorecard ran in
different jobs (see the same-job constraint in
[docs/github-actions.md](github-actions.md) §4).

Recovery:

1. **Restore from backup.** The cache is a plain JSON dict keyed by run id —
   drop the backed-up file back at `VALIDSIM_CACHE_FILE` (or the default
   path); no format migration needed.
2. **Re-derive deterministically.** Runs are seeded (`stable_seed(checkpoint,
   task_id)`), so rerunning `validsim run` with the *same* checkpoint/task/
   episode counts reproduces the same scorecard content (a new run id, but the
   same verdict — good enough to re-run a blocked gate).
3. **Verify integrity.** A corrupted/truncated JSON makes `_load_cache` raise
   on next read; fix by removing the file and re-running step 2. Check
   `created_at` ordering survives: `--latest` picks the newest entry by
   `created_at` (ISO-8601, ties broken by insertion order).

Links: [[GitHub Actions Integration]] · [[Isaac Sim GPU Worker — HTTP Contract]] · [[Security Hardening]]
