---
tags:
  - engineering
  - security
  - audit
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🔐 Security Hardening

Findings and fixes from the security audit of the ValidSim MVP stack (API surface, webhooks, deployment compose). Context: [[Solution Architecture]] · endpoint surface: [[API Design]] · execution entry points: [[CLI Design]] · threat framing: [[Risk Register]].

> [!important] Implementation boundary — 2026-09-21
> Auth and HMAC signing are implemented in the source, but auth is opt-in, `/api/v1/health` is public, bare `/metrics` is intentionally unauthenticated, and the live process-local rate limiter is disabled by default. HMAC signing belongs to the webhook dispatcher library; `POST /api/v1/webhooks` is still planned, so no public webhook-registration surface ships today.

## 1. API key auth — enforced via `VALIDSIM_API_KEY` ✅

Implemented in `validsim/api/main.py` (`create_app`, dependency `require_api_key`):

- When `VALIDSIM_API_KEY` is set at app-build time, `/api/v1` routes require an `X-API-Key` header matching it, except the public `/api/v1/health` probe; bare `/metrics` is also intentionally public.
- Comparison is **constant time** (`secrets.compare_digest`) — no timing oracle on the key.
- Missing, empty, and wrong values all raise a **uniform 401** ("Missing or invalid API key") with `WWW-Authenticate: ApiKey` — the response does not reveal whether the key exists.
- When the env var is unset, auth is disabled entirely; this is deliberate for local development. `VALIDSIM_ENV=production|prod` refuses to start without a configured key.

Verified by `tests/test_api_auth.py`.

## 2. CORS moved to env allow-list ✅

Previously hard-coded allow-all. Now driven by `VALIDSIM_CORS_ORIGINS` (comma-separated, parsed in `_parse_cors_origins`, whitespace stripped, empty entries dropped):

```bash
VALIDSIM_CORS_ORIGINS="https://dashboard.validsim.com,https://app.validsim.com"
```

- Default remains `"*"` (the old behavior) so local dev is not broken — **production must set an explicit origin list**.
- `allow_credentials=False` (the API is key-header authenticated, not cookie-based, so credentialed CORS is neither needed nor safe to enable).

## 3. HMAC-signed webhooks ✅

Implemented in `validsim/notify/dispatcher.py`:

- Hooks registered with a `secret` get an **`X-ValidSim-Signature`** header on live sends: hex **HMAC-SHA256** of the exact JSON body, keyed by the secret (`_sign_body`).
- Receivers verify by recomputing the HMAC over the raw request body and comparing (ideally constant-time) — protects against forged scorecards and replayed/tampered payloads on the wire.
- Applies to both `json` and `slack` payload formats; dry-run sends (default) carry no network I/O and thus no signature. The public webhook configuration endpoint is not implemented.

## 4. Compose credentials — password now required ✅

The stack ([[Solution Architecture]] L1/L5 services) must not ship a working default password. Target state:

- `POSTGRES_PASSWORD` is **required** — the API's `VALIDSIM_PG_URL` and the postgres service both read it from the environment / `.env`; no committed default.
- Redis stays loopback-published for local debugging; Compose persists its volume, but the job queue is not a compliance system of record.

> [!success] Resolved (2026-09-20)
> The former `${POSTGRES_PASSWORD:-validsim_dev_only}` dev fallback has been removed from every service in `docker-compose.yml`; the stack now uses `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}`, so `docker compose up` fails fast when the password is unset instead of silently starting with a known one. The api service was also corrected to set `VALIDSIM_STORE` + `VALIDSIM_PG_URL` (it previously set an unused `VALIDSIM_DATABASE_URL`, so Postgres was never actually wired).

## 5. SSL-mode note

Postgres connections go through `VALIDSIM_PG_URL`. Inside the compose network, plaintext is acceptable (private bridge network); **any connection leaving the host** (managed Postgres, DGX worker, cross-VPC) must append `?sslmode=require` or stronger:

```
postgresql://validsim:<pw>@db.example.com:5432/validsim?sslmode=require
```

`sslmode=require` validates encryption but not the server certificate; prefer `verify-full` + pinned CA for the system-of-record ([[Risk Register]] #5 data-integrity exposure).

## 6. Remaining roadmap

| Item | Why it matters | Where |
|---|---|---|
| **Per-client API keys** | Single shared key cannot be rotated per customer or revoked on compromise; blocks multi-tenancy ([[API Design]] "Auth & tenancy") | Post-MVP, with Auth0/Clerk RBAC |
| **Distributed (Redis-backed) rate limiting** | The shipped limiter is process-local (IP-keyed sliding window on `POST /validations`, `/compare`, `/jobs` — audit H1–H3); a multi-replica deployment needs a shared Redis counter so the budget is per-client across the fleet | `validsim/api/main.py` limiter backend |
| **`urdf_path` validation before the real Isaac worker** | Currently a free-form optional string in `RobotSpec` (`validsim/config.py`); the mock ignores it, but `IsaacWorkerBackend` (see `docs/isaac-worker.md`) will ship it to a GPU container — path traversal / arbitrary-file-read must be blocked (extension allow-list, sandboxed base dir, no absolute paths) before `VALIDSIM_BACKEND=isaac` promotion step 4 | `validsim/config.py` + worker contract conformance suite |

## Verification status

- Fixes 1–4 were checked against the current source/config during the 2026-09-21 catalog pass; no tests were run for this note.
- The public webhook-registration route, distributed limiter, and Auth0/Clerk remain roadmap work; do not read the dispatcher implementation as proof of a public integration endpoint.

Links: [[Solution Architecture]] · [[API Design]] · [[CLI Design]] · [[Risk Register]] · [[Home]]
