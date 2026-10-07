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
| `VALIDSIM_RATE_LIMIT` | `60` when absent | Requests allowed per client IP per window on `POST /api/v1/validations`, `POST /api/v1/validations/{run_id}/compare`, and `POST /api/v1/jobs`. **Protection is ON by default.** An absent, non-numeric, or negative value falls back to `60` — a config typo cannot silently remove the guard. Set `0` **explicitly** to disable the limiter (local development only). `.env.example` ships `60`. |
| `VALIDSIM_RATE_WINDOW_SECONDS` | `60` | Sliding-window length in seconds. Non-numeric or non-positive values fall back to `60`. |
| `VALIDSIM_ENV` | `development` | Deployment mode. `production`/`prod` refuses to start unless `VALIDSIM_API_KEY` is configured, so an unauthenticated API fails closed at boot. |
| `VALIDSIM_PIPELINE_CONCURRENCY` | `40` | Concurrent validation pipelines allowed at once on `POST /api/v1/validations`. Over budget ⇒ immediate `503` + `Retry-After: 5` (`{"error": "overloaded"}`) instead of queueing behind other pipelines. An absent, non-numeric, zero or negative value falls back to `40` — a typo cannot silently remove the gate. |
| `VALIDSIM_THREAD_LIMITER_TOKENS` | `48` | Floor for the worker-thread pool serving every sync route. Effectively `max(48, VALIDSIM_PIPELINE_CONCURRENCY + 8)`, reserving headroom so `/api/v1/health` stays answerable at full saturation. |
| `VALIDSIM_BACKEND` | `mock` | Simulation backend: `mock` \| `isaac` (case-insensitive; unknown names fall back to mock). |
| `VALIDSIM_ISAAC_WORKER_URL` | *(none)* | Base URL of the GPU Isaac worker. No port is hard-coded; the local example in `.env.example` is `http://localhost:8080`. Missing URL raises `ValueError` at *first episode*, not construction. |
| `VALIDSIM_ISAAC_WORKER_KEY` | *(unset)* | Optional bearer token sent as `Authorization: Bearer …` to the worker; omitted when unset. Source it from the deployment environment or secret store; leave empty for local development. |
| `VALIDSIM_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint root (Azure, vLLM, Ollama, NIM…). |
| `VALIDSIM_LLM_API_KEY` | *(unset)* | Bearer token for the LLM provider. **Its presence selects the LLM scenario generator**; unset ⇒ deterministic rule-based generator (no network I/O). |
| `VALIDSIM_LLM_MODEL` | `gpt-4o-mini` | Chat model for adversarial scenario generation. |
| `VALIDSIM_WEBHOOKS_LIVE` | *(unset)* | Set to `1` to enable real webhook HTTP delivery. Unset ⇒ dry-run: deliveries are recorded, no network I/O. |
| `VALIDSIM_CACHE_FILE` | `.validsim/scorecards.json` | Local JSON scorecard cache used by the CLI's `status` / `scorecard` / `report` commands. Human-facing only: `gate` never reads it (see §3), because any process that can write the file can forge a verdict. |
| `VALIDSIM_JOB_QUEUE` | `memory` | Async job queue backend: `memory` \| `redis`. Must match between the API and every worker — if the API enqueues into its private in-memory queue the job sits `queued` forever with no error. |
| `VALIDSIM_JOB_QUEUE_MAX_DEPTH` | `1000` | Maximum jobs the queue holds before `enqueue` raises `QueueFullError` and `POST /api/v1/jobs` returns `503`. Must be a positive integer; malformed raises `ValueError`. |
| `VALIDSIM_JOB_LEASE_SECONDS` | `3600` | How long a worker's claim on a job stays valid before another worker may reclaim it. **Not previously documented.** See the sizing note below — this is the knob that determines how long a job stranded by a dead worker stays stuck. Must be a positive integer; malformed raises `ValueError`. |
| `VALIDSIM_LOG_LEVEL` | `INFO` | Level for the JSON access log emitted by `validsim.logging`. Non-numeric or unparseable values degrade to `INFO` rather than failing startup. |

> **Sizing `VALIDSIM_JOB_LEASE_SECONDS` (read before an incident, not during
> one).** The lease is the dead-worker timeout: a worker stamps a claim when it
> picks a job up, renews it at one third of this interval, and another worker
> reclaims the job only once the deadline passes *and* the status is still
> `running`. Two consequences operators get wrong:
>
> - **A slow job is not a stolen job.** Because the worker renews at a third of
>   the lease, a worker that is alive but slow keeps renewing and is *not*
>   reclaimed. A rising lease-expiry rate means workers are actually dying, not
>   that jobs are merely long — alert on the rate of change, not the absolute
>   count.
> - **The default is too tight for long GPU runs.** The shipped default is
>   `3600`s against a documented 15–45 min target for real GPU validation runs
>   (see [[MVP Success Metrics]]). That leaves roughly three renewal windows
>   before a deadline passes. Set the lease to at least **4× the longest
>   expected job duration**, and raise `VALIDSIM_JOB_LEASE_SECONDS` explicitly
>   whenever you change the expected run length. A lease that is too *short* is
>   not a performance problem; it is duplicate GPU work.

> **Queue metrics.** Queue depth, lease-expiry and requeue counts are not yet
> exported as Prometheus metrics, so a stranded job is currently invisible until
> someone reads the logs. Treat the audit log in §4.3 as the interim signal.

Related (compose-internal, documented for completeness): `VALIDSIM_DATABASE_URL`
(DSN the API container uses) and `VALIDSIM_REDIS_URL`. Note the TLS caveat in
[docker-compose.yml](../docker-compose.yml): do **not** append
`?sslmode=require` until the postgres service itself is configured with SSL —
the `postgres:16-alpine` image ships with SSL disabled, so requiring it fails
every connection. Any connection *leaving the host* must use
`?sslmode=require` or stronger.

> **Rate limiting:** a process-local, client-IP-keyed sliding-window limiter
> protects the three write routes listed above. An over-budget request receives
> HTTP `429`, `{"detail": "rate limit exceeded"}`, and a `Retry-After` header.
> **Protection is on by default** — the shipped template uses `60` requests per
> `60` seconds, and an absent, non-numeric, or negative `VALIDSIM_RATE_LIMIT`
> falls back to `60` rather than to disabled, so a config typo cannot silently
> remove the guard. Set `VALIDSIM_RATE_LIMIT=0` **explicitly** to disable it (local
> development only). The limiter is not shared across application processes: with
> N replicas the effective limit is N × the configured value, and behind a
> reverse proxy every client may share one bucket unless the proxy is trusted
> (`--proxy-headers` + `--forwarded-allow-ips`).

> [!note] Composite score weights
> Composite = 40% success + 30% safety + 20% robustness + 10% regression
> (blueprint §"Semantics of fail-below-score").

## 3. CI gate usage

`validsim gate --run-id <id> --threshold <t>` (or `--latest`) is CI-native.
Exit codes (from [`validsim/cli.py`](../validsim/cli.py)):

| Exit code | Meaning | Effect in Actions |
|---|---|---|
| `0` | `APPROVE` — stored verdict is `APPROVE` and composite ≥ effective threshold | step passes, job continues |
| `1` | `BLOCK` — stored verdict is `BLOCK`, composite below the effective threshold, or no recognised verdict stored | **step and job fail** |
| `2` | misuse — no durable store configured (`VALIDSIM_STORE=memory`), unknown or malformed run id, no stored runs for `--latest`, or both `--run-id` and `--latest` | job fails |

`gate` decides from the **durable store**, never from the JSON scorecard cache
(see `VALIDSIM_CACHE_FILE` above): the cache is a plain file the same job can
rewrite, so a verdict read from it proves nothing. That means `gate` needs
`VALIDSIM_STORE=sqlite` or `postgres` in the environment it runs in, and it
exits `2` rather than falling back to the cache when the store is the default
in-memory one. `validsim run` writes both surfaces, so no extra step is needed
beyond setting the store; the Actions usage in [§5 of
docs/github-actions.md](github-actions.md) and
[`actions/validate/action.yml`](../actions/validate/action.yml) configure it.

`gate` acts on the verdict recorded in the run's scorecard rather than
recomputing it from the composite, so block reasons that are not scores (an
under-delivered run, for one) survive the gate. `--threshold -1` (default) uses
the threshold stored in the scorecard; an explicit value may only **raise** the
bar, never lower it below the stored one — a lower value is ignored, so no CI
flag can turn a `BLOCK` into an `APPROVE`. Gate anything behind the validate job
with `needs:` (full example:
[docs/github-actions.md](github-actions.md) §5).

## 4. Incident playbook

### 4.1 API returns 401s after adding `VALIDSIM_API_KEY`

`VALIDSIM_API_KEY` is read once when `create_app()` builds the app. If that
value changes, rebuilding the app is required for it to take effect. A
non-blank key protects every `/api/v1` route except the public health probe;
requests to protected routes without the matching header receive 401. This
is distinct from the compose container's own `HEALTHCHECK`, which probes only
`/api/v1/health` and therefore stays healthy when auth is enabled.

Fixes, in order of preference:

1. **Clients**: send the header on every protected request:

   ```bash
   curl -H "X-API-Key: $VALIDSIM_API_KEY" \
     http://127.0.0.1:8000/api/v1/validations
   ```

2. **Dashboard/browser**: once the key is set, the served dashboard pages hit
   `/api/v1/*` too and will get 401s. Either inject the header at the proxy
   layer or keep the key unset in pure-local dev and enforce auth only at the
   edge.
3. **Revert the change**: if the operator intentionally set the wrong key,
   restore the previous value and recreate the API container. Do not simply
   unset auth as an incident shortcut. With `VALIDSIM_ENV=production`, an
   unset key makes API startup fail by design; the fix is to restore the
   configured value, not to weaken the mode.

   ```bash
   docker compose up -d --force-recreate api worker
   docker compose ps
   ```

   If the key value must change, set the new `VALIDSIM_API_KEY` in the
   environment or the untracked `.env` file, then use the same recreate
   command. Never put the key in shell history, command output, or committed
   files.

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
   For a transient connection/service failure without an intended config
   change, restart only the affected dependency rather than the whole stack:

   ```bash
   docker compose restart postgres
   docker compose ps
   ```

   If the dependency is healthy but one application connection is still
   broken, recreate `api` and `worker` with
   `docker compose up -d --force-recreate api worker`; do not remove the data
   volumes.

### 4.3 PDF endpoint returns 501

`GET /api/v1/validations/{run_id}/scorecard.pdf` returns **501** with a
`"pip install reportlab"` detail when the optional `reportlab` dependency is
missing. Everything else runs fine without it — only PDF export is gated.

Fix: `pip install reportlab` (or `pip install -r requirements.txt`, which
includes `reportlab>=4.0`) and restart the API. reportlab is imported lazily
inside the render functions, so the fix needs no code change. Note a 501 for
an unknown run id is not possible — unknown runs return 404.

### 4.4 Webhook delivery failures

> [!warning] Nothing in this repository delivers a scorecard for you — yet
> `WebhookDispatcher` and `EmailNotifier` have **no production caller**. The
> configuration surface now exists (`validsim/notify/config.py`, reading
> `VALIDSIM_NOTIFY_ENABLED` + `VALIDSIM_WEBHOOK_URLS` and siblings), but
> `run_and_score` does not import `validsim.notify`, so nothing dispatches. There
> is no `POST /api/v1/webhooks` route and no CLI flag. A `validsim run` therefore
> sends nothing, and setting the notify variables does not change that.
>
> **Before following the steps below, confirm you are actually calling it.** If
> you expected the platform to notify on its own, that capability does not exist
> yet; §4.4 below applies only to code you have written that calls
> `dispatcher.dispatch(scorecard)`. Two independent switches must both be on —
> `VALIDSIM_NOTIFY_ENABLED` *and* at least one `VALIDSIM_WEBHOOK_URLS` — and an
> unrecognised value (`maybe`) counts as off. On top of that, the dispatcher is
> **dry-run unless `VALIDSIM_WEBHOOKS_LIVE=1`**. A dry-run result always reports
> `ok=True`, so a "successful" delivery that nobody received almost always means
> one of those three is off rather than that the endpoint rejected anything.

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

> [!warning] The LLM scenario path is not reachable from a validation run
> `validsim/engine/pipeline.py:131` hardcodes
> `ScenarioGenerator(seed=seed).generate(...)`, so **every shipped run generates
> scenarios deterministically** — the rule-based generator, on the API, the CLI
> and the job worker alike. `create_scenario_generator()` in
> `validsim/scenarios/llm_generator.py` (which selects the LLM backend) has no
> caller outside the test suite, so `VALIDSIM_LLM_ENABLED` and
> `VALIDSIM_LLM_API_KEY` currently have **no effect on a run**.
>
> So "my LLM scenarios are failing" is almost always one of two things: either
> you are calling `LLMScenarioGenerator` directly from your own code (in which
> case the steps below apply), or you expected the platform to use the LLM path
> and it never did. `llm_enabled()` and `current_scenario_backend()` in
> `validsim/scenarios/llm_generator.py` report the effective configuration.

Symptoms when you *are* calling it directly: runs degrade to rule-based
scenarios, or `ScenarioProviderError`.
By design any provider/parse failure **falls back to the deterministic
rule-based generator** — a misbehaving LLM can never take a validation run
down. The factory requires `VALIDSIM_LLM_ENABLED` to be explicitly truthy
(`1`/`true`/`yes`/`on`) **and** `VALIDSIM_LLM_API_KEY` to be non-empty; it
degrades to the rule-based generator rather than raising when the opt-in is on
but unusable. To restore LLM scenarios: check both switches, that
`VALIDSIM_LLM_BASE_URL` points at the deployment root (e.g. `.../v1`), and that
`VALIDSIM_LLM_MODEL` names a model the endpoint actually serves.

### 4.6 Roll back a bad promoted checkpoint or image

ValidSim decides and records evidence; it does not move checkpoints, images, or
a robot fleet. Stop the rollout first through the fleet/image platform that
owns it, then use the steps below to identify approved evidence and recover the
ValidSim service. Those platforms are outside this repository. If the incident
also invalidates a stored run, preserve the current database and a pre-restore
dump before any recovery; do not use the DELETE endpoint to make history look
clean.

#### 4.6.1 Identify the last known-good run

Use the same durable store and `VALIDSIM_PG_URL` environment as the API/worker.
For the bundled Compose stack, set the host to `localhost` in `VALIDSIM_PG_URL`
for host CLI calls; Compose services use `postgres` (§4.2). Keep credentials in
the environment, never in a command or document.

```bash
validsim models

curl -s -H "X-API-Key: $VALIDSIM_API_KEY" \
  "http://127.0.0.1:8000/api/v1/validations?limit=500"
```

`validsim models` shows the latest run for each stored `checkpoint_id`; the API
response exposes the same fields in JSON. For one checkpoint, use the
chronological history endpoint (oldest first):

```bash
curl -s -H "X-API-Key: $VALIDSIM_API_KEY" \
  "http://127.0.0.1:8000/api/v1/models/$CHECKPOINT_ID/history"
```

Choose a recent run whose recorded `deploy_decision` is `APPROVE`, and confirm
that the intended checkpoint artifact is the one that produced it. The repo
records the caller-supplied `checkpoint_id`; an API validation request may also
carry `checkpoint_sha256`, but that field is **accepted and then discarded** —
it is checked for 64-character length only (not for hex), never read, and never
persisted, so the stored run contains no digest. The local pipeline does not
independently hash a checkpoint file either. Verify the artifact digest in the
owning model registry rather than trusting a mutable `latest` alias, and do not
treat anything in the run record as a chain-of-custody proof. Keep the rejected
run id and its backup as incident evidence even when a newer known-good run
exists.

#### 4.6.2 Re-gate that exact run

```bash
validsim gate --run-id "$KNOWN_GOOD_RUN_ID" --json
```

This must read the durable store (`VALIDSIM_STORE=sqlite` or `postgres`), not the
JSON cache. Exit `0` means the stored verdict is exactly `APPROVE` and its
composite meets the effective threshold. Exit `1` blocks; exit `2` means the
store configuration, run id, or run lookup is unusable. An explicit
`--threshold` can only raise the stored threshold. Do not use `--latest` here:
it resolves against the store's current oldest-first history, not against the
known-good artifact being recovered.

A successful re-gate validates evidence; it does not revert a deployment or
create an image.

#### 4.6.3 Recover the ValidSim service/image

The shipped Compose services use the mutable local tag `validsim:local`, and
the repository has no deployment controller or record of the previous image.
For a local source rollback, preserve the current commit and working state in
the operator's normal VCS workflow before switching revisions. A dirty working
tree must not be switched automatically; preserve or archive it first, or leave
this checkout untouched and recover from an archived dump/image elsewhere.
Once that prerequisite is met, stop writers, switch to a known-good revision,
rebuild both application services, and recreate them:

```bash
git rev-parse HEAD                 # record the bad revision
docker compose stop api worker
# After preserving any local work, switch to the known-good source revision.
git switch --detach <known-good-revision>
docker compose build --pull api worker
docker compose up -d postgres redis api worker
docker compose ps
```

Stopping the application before changing revisions prevents an old build from
being assembled from a moving working tree. These commands retain the named
Postgres and Redis volumes. **Do not run `docker compose down -v` during
rollback**: `-v` removes the database volume. If the bad revision changed the
store schema, do not point the old service at that volume; restore a verified
dump into a separate empty database using §5.1 and switch `VALIDSIM_PG_URL` only
after that restore has been checked.

For a released image, this repository records that release automation pushes
`ghcr.io/<owner>/<repository>:<version>` and `latest`, but it does not deploy or
retag that image on any host. The external image/fleet platform's operator must
select the known-good immutable image digest, apply it with that platform's
supported rollback procedure, and verify the running digest. Image availability
in GHCR is not a deployment rollback mechanism by itself. There is no Kubernetes
manifest, Terraform state, deployment controller, or fleet rollback command in
this repository, so do not document or assume one here.

## 5. Backups & recovery

### 5.1 Postgres (`pg_dump`)

> [!warning] The procedure below is the **manual** recovery path
> Today this repository ships **no scheduler and no retention automation**. The
> daily full logical dump retained ≥14 days is the MVP operating procedure, and
> it is *not* executed by anything in this repo — you must schedule it outside
> the repo and enforce retention there. A proposal to automate it
> (scheduled job, S3 destination, restore drill, freshness metric) is in
> [`docs/PLATFORM_PROPOSAL.md`](PLATFORM_PROPOSAL.md) §3.5 / §5; treat that as
> target state, not as something you can run today. Until it lands, the manual
> commands below are the whole procedure.

The `postgres-data` volume holds the stored runs and scorecards. Raise the
cadence only after a point-in-time-recovery and WAL-retention design exists.

The commands below are for Git Bash, WSL, or another POSIX shell. Do not paste
`$(date ...)`, pipes, or `>` into PowerShell unchanged. Run them from the repo
root after `make docker-up`; the directory is created before the archive so the
host redirection has a target. The Compose `postgres` service is named
`postgres`, so use `docker compose exec -T`, not a hard-coded container name.
`POSTGRES_USER` and `POSTGRES_DB` are `validsim` in
[`docker-compose.yml`](../docker-compose.yml); `POSTGRES_PASSWORD` is supplied
to the container and is not placed in the command or archive transcript.

```bash
mkdir -p backups
BACKUP="backups/validsim-$(date +%Y%m%d-%H%M%S).dump"

# -T disables pseudo-TTY allocation, so pg_dump's binary stream is not
# transformed on its way to the host redirect.
docker compose exec -T postgres \
  pg_dump -U validsim -d validsim -Fc > "$BACKUP"

# Keep a known-good pre-restore dump; --clean/--if-exists is destructive.
docker compose exec -T postgres \
  pg_dump -U validsim -d validsim -Fc \
  > "backups/pre-restore-$(date +%Y%m%d-%H%M%S).dump"
```

The backup filename uses the local time of the machine running `date`. The
repository provides no scheduler, retention job, or off-host copy; protect
completed dumps according to the operator's storage policy and never commit
them to git.

For recovery, prefer an **empty, disposable database**. Stop the API and worker
first so they cannot write while it is being restored. `BACKUP` is the host
path created above; the restore command streams that file into the container,
so the host path is not expected to exist inside the container:

```bash
RESTORE_DB=validsim_restore
docker compose stop api worker
docker compose exec -T postgres \
  createdb -U validsim -T template0 "$RESTORE_DB"
docker compose exec -T postgres \
  pg_restore -U validsim -d "$RESTORE_DB" \
  --exit-on-error --single-transaction < "$BACKUP"
```

Verify the restored database through the API or CLI using a temporary
`VALIDSIM_PG_URL` that names `$RESTORE_DB` before making it authoritative. To
rebuild the original database name, stop the writers and, only after the
verified disposable restore, run the corresponding destructive restore against
`validsim`:

```bash
docker compose exec -T postgres \
  pg_restore -U validsim -d validsim \
  --clean --if-exists --single-transaction < "$BACKUP"
```

Keep the pre-restore dump. Run the commands in one shell so `$BACKUP` and
`$RESTORE_DB` retain their values. Do not combine `--single-transaction` with
parallel restore (`--jobs`): the streamed archive is not a regular file inside
the container, and parallel mode requires one. Never add `-v` to a Compose
cleanup command during recovery; it removes the named Postgres volume.

The Postgres `save` path uses `ON CONFLICT (run_id) DO NOTHING`, so re-saving
an existing run id does not silently rewrite it. That is a **write-path
append-only guarantee, not immutable or tamper-evident storage**: the API's
`DELETE /api/v1/validations/{run_id}` handler calls the same store `delete()`
path and permanently removes the row. A backup may also be older than current
data, and the first-write-wins clause does not make missing rows detectable.
Retain dumps and any database audit/access logs that the operator has
configured; this repository does not implement an audit log or retention job.
Those records do not replace a signed or externally anchored audit log. The
optional `checkpoint_sha256` accepted by the API is caller-supplied, not a hash
computed by the validation pipeline, and is not carried into the stored
scorecard.

This immutability trade-off is a product decision, not a backup procedure. The
options are to (a) restrict destructive operations and add an append-only
retraction/deletion ledger with database-enforced write permissions, (b) keep
operator deletion but require stronger authorization and record every deletion
in that separate durable ledger, or (c) accept a mutable evidence store and
retract the immutable/tamper-evident claims everywhere. This runbook does not
select an option or change the endpoint; the first two require a superseding
ADR and implementation, while the third requires a coordinated product-documentation
change.

Redis is used for the development job queue, not the system of record. In this
MVP, no Redis backup is needed to recover scorecards or the CI gate; its
`--save 60 1` setting is a local restart aid, not a documented recovery plan.

### 5.2 Scorecard cache recovery

The CLI cache (`VALIDSIM_CACHE_FILE`, default `.validsim/scorecards.json`) is
what `validsim status` / `scorecard` / `report` read **across processes**.
`gate` is deliberately not in that list — it decides from the durable store
(§3), so losing the cache costs you the human-readable views while the gate
keeps working, and forging the cache cannot move the gate.

The store is the surface whose loss breaks CI. Both failures exit `2`:

* `error: gate needs a durable store, but VALIDSIM_STORE='memory'` — nobody
  set `VALIDSIM_STORE` (`actions/validate/action.yml` sets it for CI users).
* `error: no stored run '<id>' …` / `error: no stored runs …` — the store is
  empty, which in CI usually means validate and gate ran in different jobs,
  since a sqlite file written in one job does not exist in the next (see the
  same-job constraint in [docs/github-actions.md](github-actions.md) §4).

Recovery:

1. **Restore from backup.** The cache is a plain JSON dict keyed by run id —
   drop the backed-up file back at `VALIDSIM_CACHE_FILE` (or the default
   path); no format migration needed. Back up the store with the tooling for
   its backend: §5.1 for Postgres, or `sqlite3 <db> ".backup <dst>"` for
   sqlite (not `cp`, which can catch a half-written page).
2. **Re-derive deterministically.** Runs are seeded (`stable_seed(checkpoint,
   task_id)`), so rerunning `validsim run` with the *same* checkpoint/task/
   episode counts reproduces the same scorecard content (a new run id, but the
   same verdict — good enough to re-run a blocked gate).
3. **Verify integrity.** A corrupted/truncated JSON makes `_load_cache` raise
   on next read; fix by removing the file and re-running step 2. Check
   `created_at` ordering survives: the cache-reading commands pick the newest
   entry by `created_at` (ISO-8601, ties broken by insertion order), while
   `gate --latest` picks the last row of the store's oldest-first `history()`.

Links: [[GitHub Actions Integration]] · [[Isaac Sim GPU Worker — HTTP Contract]] · [[Security Hardening]]
