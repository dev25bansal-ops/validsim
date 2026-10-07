---
tags:
  - product
  - flows
  - ux
status: complete
created: 2026-09-18
area: "03 - Product"
---

# 🔀 Core User Flows

Three flows map the three buying roles ([[User Personas]]) onto the platform. Flows 1b, 4 and 5 sit on top of them: the async job queue, the founder's nightly adversarial sweep, and run deletion. Each flow is an acceptance test for the [[8-Week Sprint Plan]] and a scene in the YC demo video ([[YC Countdown]]).

> [!important] Blueprint status — 2026-09-21
> The current REST/CLI surface has no fleet deployment-gate endpoint or immutable audit log. CI gating ships through `validsim gate` and the local composite Actions; validation history is persisted, but runs can be deleted. Dashboard/notification views below mix shipped MVP behavior with target-state screens.

## Flow 1 — Submit & Validate *(ML Engineer)* 🥇

1. Push model checkpoint to registry
2. GitHub Action triggers automatically (or manual CLI — `validsim run`)
3. Validation runs in cloud (**15–45 min for 5,000 episodes**) — the sync `POST /validations` holds the socket open for that whole window; anything you don't want to wait on goes through **Flow 1b**
4. Slack notification: *"Validation complete. Score: 87.3. 2 regressions detected."*
5. Click link → dashboard with full scorecard ([[Scorecard UX]])
6. Review failure taxonomy, watch episode replays
7. Fix regressions, retrain, resubmit

> [!important] Flow 1 is the product
> Target: checkpoint → scorecard in **< 1 hour** with **zero config** ([[Product Principles]] #1). Built by sprint weeks 2, 6, 8. The GitHub Action YAML that starts this flow: [[GitHub Actions Integration]].

## Flow 1b — Async Submit & Poll *(ML Engineer, long runs)* ⏳

1. `POST /api/v1/jobs` with `{checkpoint_id, task_id, episodes, adversarial}` → **`202 Accepted`** + `{job_id, status: "queued"}`; the socket closes immediately
2. The job id **is** the run id (`vrun-` + 8 hex) — queue and store share one key, so there is no job→run translation step
3. A `JobWorker` claims the oldest queued job: `queued → running → done`, or `→ failed` when the backend/engine raises (one bad job fails only itself, the worker keeps draining)
4. Watch it: poll `GET /api/v1/jobs/{id}` for the full record, `…/status` for the compact `{job_id, status, updated_at}`, or subscribe to `…/events` — a Server-Sent-Events stream of `data:` frames closed by `event: end`; if the job is still running at the 60 s deadline the stream emits `event: timeout` so the client reconnects instead of pinning a thread
5. On `done`, the record's `result` holds the persisted run id → `GET /api/v1/validations/{run_id}/scorecard` renders the identical scorecard ([[Scorecard UX]])
6. Approve/block from there exactly as in Flow 2 — async changes *when* the engine runs, not what it produces

CLI equivalent: `validsim job enqueue -c <ckpt> -t <task> -e 5000 -a 100`, then `validsim jobs` to list and `validsim worker [--once]` to drain ([[CLI Design]]). Backend is `VALIDSIM_JOB_QUEUE=memory|redis`: memory is per-process, so enqueue and worker only share a queue once Redis is configured ([[Async Job Queue]], [[Job Queue Worker]]).

> [!note] Backpressure is explicit
> A queue at `max_depth` (default 1000) answers `503 {"error": "queue_full", "max_depth": N}` with `Retry-After: 5` — CI backs off and retries instead of seeing an opaque 500.

## Flow 2 — Deployment Gate *(Fleet Operator)* 🚦

1. Receive notification: *"New policy v42 validated. Score: 87.3"*
2. Review scorecard: success rate, safety score, robustness
3. Check regression delta: *"No critical regressions vs v41"*
4. Click **"Approve Deployment"** → fleet update begins
5. Or click **"Block"** → developer notified with failure details

Mechanics (current CLI): `validsim gate --run-id vrun-1a2b3c4d --threshold 85` reads the stored verdict and exits `0` for `APPROVE` or non-zero for `BLOCK`; the planned `POST /api/v1/deployment-gate` and immutable decision log do not exist ([[API Design]], [[CLI Design]], [[Data Flow]]).

## Flow 3 — Compliance Report *(Compliance Officer / Insurer)* 📋

1. Navigate to validation history
2. Select specific validation run
3. Click **"Generate Compliance Report"**
4. Download PDF: **ISO 10218 format**, safety evidence, test scenarios, results
5. Forward to enterprise customer or insurer

> [!note] MVP status
> ISO-format compliance generation is explicitly **out of MVP scope** ([[MVP Non-Goals]]); the MVP ships a branded professional PDF scorecard instead (Week 8 deliverable). Flow 3 is the Year-1 Enterprise upsell ([[Pricing Tiers]], [[Compliance]]).

## Flow 4 — Nightly Adversarial Sweep *(Founder, dogfood)* 🌙

1. `examples/nightly-adversarial-sweep.yml` fires at **03:00 UTC** (`cron: "0 3 * * *"`) — plus `workflow_dispatch` for an on-demand sweep before a release cut
2. `python -m validsim.cli run --episodes 2000 --adversarial 200 --checkpoint nightly-checkpoint` — the deep pass: **2,000 + 200** vs. the **1,000 + 50** the PR workflow keeps for fast feedback ([[GitHub Actions Integration]])
3. `python -m validsim.cli gate --latest` exits **1 on BLOCK** (stored verdict not `APPROVE`, or composite below the threshold), so the job — and anything wired behind it with `needs:` — fails on a bad nightly score. The job exports `VALIDSIM_STORE=sqlite` because the gate decides from the durable store, never the JSON cache
4. Scorecard JSON + JUnit XML are uploaded as artifacts (`retention-days: 30`) under `if: always()`, so evidence survives a block

Same MVP mode as the PR workflow: the CLI drives the local mock pipeline inside the founder's own job — no SaaS backend, no API key. Schedule triggers only fire from the default branch; dispatch works from any branch. This is the "we run our own product nightly" proof point for [[YC Countdown]] and the repro-before-a-customer-sees-it claim in [[Engineering Momentum Log]].

## Flow 5 — Prune a Stale Run *(ML Engineer / Fleet Operator)* 🧹

1. `DELETE /api/v1/validations/{run_id}` → **`204`**, no body; unknown id → **`404`**
2. Auth is stricter than the rest of the API: the route carries its own destructive gate, so a deployment exposing `DELETE` **must** set `VALIDSIM_API_KEY` — the `401` is checked *before* the `404` lookup, so the endpoint can't be probed for run ids ([[Security Hardening]])
3. Removal is remove-and-report in a single step under the store lock: no check-then-delete race, and idempotent (a second delete of the same id is a `404`)
4. Identical on the memory, SQLite and Postgres stores; the run drops out of history, trends and `/models` on the next read

> [!warning] Current deletion surface
> `validsim delete --run-id vrun-<8 hex>` and `validsim delete --latest` are implemented; there is no trash-can button in the [[Scorecard UX]] history table yet. Pruning is available through the CLI or `DELETE /api/v1/validations/{run_id}`.

## Dashboard surfaces *(what the operator actually sees)* 🎛️

The Week-7 single-page dashboard (`/`, served by FastAPI from `validsim/web` — no Node build step) carries four surfaces beyond the scorecard itself ([[Scorecard UX]]):

| Surface | What it shows | Mechanics |
|---|---|---|
| **Job Queue panel** | Enqueue form + newest-first jobs table | Polls `GET /api/v1/jobs` every **3 s**, paused while the tab is hidden; every status badge is icon + literal word (colour is never the only signal); an unreachable queue degrades to "Queue unavailable" instead of throwing |
| **Theme toggle** | Dark ⇄ light in the header | `data-theme` on `<html>`, persisted in `localStorage` (`vs-theme`), falling back to `prefers-color-scheme`; applied pre-paint so there is no flash of wrong theme, and the taxonomy chart re-renders with theme-correct colours |
| **Trends panel** | Latest composite, Δ vs previous run, 3-run moving average, 10-run approval rate | Computed client-side from `/api/v1/dashboard/history`, mirroring `validsim/engine/trends.py` — which also carries `improving` / `volatile` and per-failure-mode `failure_mode_trends` |
| **Failure Taxonomy panel** | Failure modes by count and share of failed episodes for the loaded run | Chart.js horizontal bars from the scorecard's `failure_taxonomy`, with an always-present table fallback for screen readers and for an offline CDN |

Trends answer the CTO's question (*"is it getting worse?"* — [[User Personas]]); the taxonomy answers the engineer's (*"what kind of failure am I chasing?"* — [[Product Principles]] #3).

## Flow → build mapping

| Flow | Sprint weeks | Key endpoints | Persona |
|---|---|---|---|
| 1 Submit & Validate | W2, W3, W6, W8 | `POST /validations`, `GET /validations/{id}` | ML Engineer |
| 2 Deployment Gate | W5, W6, W7 | Shipped CLI: `validsim gate`; planned `POST /deployment-gate` · shipped `POST /validations/{id}/compare` | Fleet Operator |
| 3 Compliance Report | Post-MVP (W8 PDF only) | `GET /validations/{id}/scorecard` | Compliance Officer |
| 1b Async Submit & Poll | W6 · Iter-6 | `POST /jobs`, `GET /jobs/{id}` (`/status`, `/events`) | ML Engineer |
| 4 Nightly Sweep | CI from W2 · Iter-6 | CLI `run` + `gate --latest` (no API) | Founder |
| 5 Prune a Stale Run | W6 · Iter-6 | `DELETE /validations/{id}` | ML Engineer / Fleet Operator |
| — Dashboard surfaces | W7 · Iter-6 | `GET /jobs`, `GET /dashboard/history`, `GET /dashboard/summary` | CTO / VP Eng |

*Iter-6* = shipped after the 8-week plan in the 0.2.0 iteration and tracked in [[Engineering Momentum Log]]: the async queue, run deletion, the job-queue panel and the trend/failure-mode analytics all landed there.

## Failure-path flows (design for these too)

- **Blocked deploy:** developer receives failure analysis + regression details with video clips — "don't just say failed; show the video, classify the failure, suggest the fix" ([[Product Principles]] #3)
- **Timeout/partial run:** webhook still fires with status; queue is retry-safe ([[Module Specs]] Module 1)
- **Statistically insignificant delta:** regression flagged with p-value, e.g. *"Low-light condition: 91% → 87% (p=0.08)"* — surfaced, not hidden ([[Module Specs]] Module 4)
- **Queue full:** `POST /jobs` answers `503 {"error": "queue_full"}` + `Retry-After: 5` — the caller backs off; a full queue is overload, not a server error ([[Async Job Queue]])
- **Job failed:** `queued → running → failed` with `error` on the record only; no scorecard is persisted, so nothing appears in history or trends, and the worker moves on to the next job ([[Job Queue Worker]])
- **SSE `event: timeout`:** the stream's 60 s budget expired, *not* the job — status is still `running`; reconnect or fall back to `GET /jobs/{id}/status`
- **Delete misses:** unknown or already-deleted id returns `404`, never a silent success; missing `X-API-Key` on a keyed deployment returns `401` before any existence check

Links: [[Scorecard UX]] · [[Product Vision]] · [[Data Flow]] · [[8-Week Sprint Plan]] · [[Home]]
