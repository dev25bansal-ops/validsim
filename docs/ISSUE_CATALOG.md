# ValidSim — Production Readiness Issue Catalog

Baseline commit: `ff15533` · Catalog date: 2026-09-21 · Working tree: `pyproject.toml` modified, 34 untracked artifacts at repo root, plus this file.

**Coverage:** read passes over all 227 tracked files are complete — 44 `validsim/` modules, 78 test files, 66 vault notes, 12 docs, and the CI/infra/scripts/actions set. Four of the findings below (01, 02, 03, 05, 09, 18-20, 23) were verified by execution in this session; the rest carry a status in §1.

## How to read this

Every entry carries a **status** so that inference is never mistaken for fact:

| Status | Meaning |
|---|---|
| **V** | **Verified in this session** — reproduced by a command or test executed against this working copy. |
| **S** | **Static** — read directly from source/config at the cited line; not executed. |
| **R** | **Reported** — surfaced by a read-only audit subagent with file:line; **not independently re-verified. Confirm before acting.** |
| **U** | **Unverified claim** — asserted somewhere (vault/README/ADRs) with no evidence behind it. |
| **✅** | **Fixed and re-verified** — the defect was reproduced, a test was written against the *correct* behaviour, and the verification command was re-run after the change. Date in the entry body. |
| **◐** | **Partially done** — one part of the entry is fixed and verified, the rest is still open. Read the body before assuming either state. |

Severity is business impact, not line count. Effort is my estimate in focused dev-hours, ±50%.

**Severity scale.** Critical = the product's core promise is false, or anonymous remote compromise. High = a real user-visible or security failure under normal operation. Medium = incorrect behaviour in an edge path, or debt that raises the cost of every change. Low = hygiene.

**Reproducibility caveat on severity:** several items are rated on the assumption this code is deployed where others can reach it. If ValidSim stays a laptop-only CLI on a trusted network, the remote-exploit items drop to Medium. That decision is yours, not mine, so I've rated for the deployment the README describes.

---

## 1. Prioritised index

| # | Title | Sev | Status | Effort | Depends on | When |
|---|---|---|---|---|---|---|
| 01 | `gate` ignores `deploy_decision`, approves BLOCK runs | **Critical** | ✅ 2026-09-21 | 4h | — | Done |
| 02 | `VALIDSIM_API_KEY=""` = full auth bypass, reports `auth_enabled: true` | **Critical** | ✅ 2026-09-21 | 3h | — | Done |
| 03 | Deployed compose stack cannot run async jobs | **High** | ✅ 2026-09-21 | 15m | — | Done |
| 04 | `redis` undeclared in every dependency file | **High** | ✅ 2026-09-21 | 30m | — | Done (class recurred: PyYAML) |
| 05 | Setting an API key makes the api container permanently unhealthy | **High** | ✅ 2026-09-21 | 2h | 02 | Done |
| 06 | `gate` trusts a world-writable local JSON cache, not the store | **High** | ✅ 2026-09-21 | 8h | 01, 21 | Done — gate is store-authority (ADR 0006) |
| 07 | Job claiming non-atomic → duplicate GPU runs | **High** | ✅ 2026-09-25 | 12h | 04 | Done — reaper was dead code + broken Lua until re-audit; both fixed |
| 08 | Enqueue is two Redis writes → orphaned jobs | **High** | ◐ Lua atomic; unproven live | 2h | 04 | Verify in CI compose |
| 09 | Postgres backend has zero CI coverage | **High** | ✅ 2026-09-24 | 6h | — | Done in CI; live run unverified locally |
| 10 | No rollback path for a bad promoted checkpoint or image | **High** | ✅ 2026-09-24 | 6h | — | Runbook done; immutability call is yours |
| 11 | ADR 0002 describes a gate the code no longer implements | **Medium** | ✅ 2026-09-21 | 2h | 01 | Done — ADR 0006 supersedes |
| 12 | api-reference 201 example is arithmetically impossible | **Medium** | ✅ 2026-09-24 | 20m | — | Done |
| 13 | `GET /jobs` documented as bare array; is a paginated envelope | **Medium** | ✅ 2026-09-24 | 30m | — | Done — catalog was wrong, see note |
| 14 | `/metrics` mounted auth-stripped, undocumented | **Medium** | ✅ 2026-09-24 | 3h | 02 | Done |
| 15 | Rate limiting shipped off by default (`VALIDSIM_RATE_LIMIT=0`) | **Medium** | ✅ 2026-09-25 | 2h | — | Done — on by default; only explicit 0 disables |
| 16 | Isaac worker docs cite a nonexistent env var and Dockerfile | **Medium** | ✅ stale | 4h | — | Nothing to fix — doc already correct |
| 17 | Vault + investor docs assert capabilities that do not exist | **High** (business) | ◐ in-repo annotated | 3d | — | External-send decision still yours |
| 18 | Test count stated six incompatible ways | **Medium** | ◐ partial ✅ | 2h | — | Sprint 2 — engineering docs fixed; fundraising notes are item 17's call |
| 19 | 34 build artifacts polluting repo root; coverage gate uncommitted | **Medium** | ◐ partial ✅ | 1h | — | Prevention done; deletion awaits your approval |
| 20 | Coverage floor `fail_under = 90` never reviewed or committed | **Medium** | ✅ 2026-09-21 | 30m | 19 | Done — floor ships; suite measures 95.84% |
| 21 | `VALIDSIM_STORE` defaults to ephemeral memory | **Medium** | ✅ 2026-09-24 | 4h | 06 | Done — default kept, made loud, one-flag durable |
| 22 | `_RUN_ID_RE` defined, never used; docs use invalid ids | **Low** | ✅ 2026-09-21 | 1h | — | Done — gate validates, docs use real ids |
| 23 | `ruff` not installed locally → lint gate unverified | **Low** | ✅ 2026-09-21 | 15m | — | Done — `ruff check validsim tests` clean |
| 24 | `scripts/build.ps1` broken the same way nightly/release were | **High** | ✅ 2026-09-21 | 30m | 04 | Done |
| 25 | CI never enforces the coverage floor the README promises | **High** | ✅ 2026-09-21 | 2h | 20 | Done — mechanism in the original entry was wrong, see §4b |
| 26 | `release.yml` reports success having published nothing | **High** | ✅ 2026-09-21 | 1h | — | Done |
| 27 | `scorecard` action swallows a missing scorecard as a warning | **High** | ✅ 2026-09-21 | 1h | 01 | Done |
| 28 | api publishes on 0.0.0.0 with auth off by default | **High** | ✅ 2026-09-21 | 30m | 02 | Done |
| 29 | No security-contact route that actually works | **High** | ✅ 2026-09-21 | 1h | — | Done |
| 30 | Postgres tests assert SQL strings against a fake, no oracle | **Medium** | ◐ hardened | 6h | 09 | Fake-bound assertions strengthened; live oracle still CI-only |
| 31 | ~~No `tests/conftest.py`~~ Fixtures still duplicated across store tests | **Medium** | ✅ 2026-09-25 | 4h | — | Done — only 1 of 12 repeat-names was substantive |
| 32 | Actions unpinned; no compose/healthcheck validation in CI | **Medium** | ✅ 2026-09-21 | ~2h | 24-28 | Done |
| 33 | `design-system/` is a mismatched scaffold; HTML export drifts | **Low** | ◐ partial ✅ | 3h | — | Drift documented; visual unification is your call |
| 34 | Bench conftest option collision aborts the entire test run | **Critical** | ✅ 2026-09-25 | 30m | — | Done — flags namespaced; suite runnable again |
| 35 | `compare --latest` self-compare reports "0 regressions" | **Critical** | ✅ 2026-09-25 | 1h | — | Done — refuses identical run ids |
| 36 | Blank `VALIDSIM_SQLITE_PATH` silently discards every run | **Critical** | ✅ 2026-09-25 | 20m | — | Done — blank falls back to default |
| 37 | Non-ASCII API key makes every request a 500 | **High** | ✅ 2026-09-25 | 20m | — | Done — byte comparison; 401 not 500 |
| 38 | Queue `max_depth` counts history, dead-locks the queue | **High** | ✅ 2026-09-25 | 2h | 07 | Done — both backends now count pending |
| 39 | `detect_anomalies` raises on impossible rates | **High** | ✅ 2026-09-25 | 20m | — | Done — variance clamped at 0 |
| 40 | `delete --latest` reads the cache, deletes from the store | **High** | ✅ 2026-09-25 | 1h | — | Done — store-first resolution |
| 41 | Dashboard `threshold` knob silently discarded by the API | **High** | ✅ 2026-09-25 | 1h | — | Done — honoured end-to-end |
| 42 | PDF export: verdict + taxonomy count unescaped | **Medium** | ✅ 2026-09-25 | 30m | — | Done |
| 43 | Run/approval/block metrics declared `counter` but decreasing | **Medium** | ✅ 2026-09-25 | 30m | — | Type corrected to `gauge`; rename is your call |
| 44 | Zero-success run can reach `APPROVE` (30 unconditional composite points) | **Critical** | ✅ 2026-09-29 | 2h | — | Done — evidence gate + unmeasured components now abstain (60.0→42.86) |
| 45 | NaN safety observables score a perfect 100.0 (fail-open) | **Critical** | ✅ 2026-09-26 | 2h | — | Done — non-finite readings now fail closed |
| 46 | `/docs`, `/redoc`, `/openapi.json` bypass the API-key gate | **High** | ✅ 2026-09-26 | 2h | 02 | Done — gated when a key is configured |
| 47 | Human-proximity safety channel diluted by human-free episodes | **Critical** | 🔴 2026-10-02 | 3h | — | **Open — the earlier REFUTED entry was wrong.** `compute_safety` divides proximity violations (which only range over human-present episodes) by *all* episodes, so adding human-free episodes raises the score. Executed: one human @0.2m + 3 human-free scores 95.00 where the same human-present episode alone scores 80.00; violated-in-every-human-present-episode reads 99.98 once diluted across 999 human-free episodes. Weight is `_PROXIMITY_WEIGHT = 0.2` of safety, itself `_W_SAFETY = 0.3` of the composite. Fix: divide by the human-present count, not the total. |
| 48 | Lint gate cannot fail — `line-length` set but E501 never selected | **High** | ✅ 2026-09-26 | 2h | — | Done — E501 selected, 14 violations fixed |
| 49 | Non-ASCII `VALIDSIM_API_KEY` accepted at boot, then permanently unusable | **High** | ✅ 2026-09-26 | 1h | — | Done — refused at startup with an actionable error |
| 50 | `bootstrap_ci` was 94% of a validation request (3.1 s at 20k episodes) | **Critical** | ✅ 2026-09-26 | 1d | — | Done — `rng.choices`, 5.4× faster; CI now always brackets the point estimate |
| 51 | Composite blind to adversarial failure — identical score for opposite profiles | **Critical** | ✅ 2026-09-26 | 1d | 44 | Done — explicit adversarial gate + binomial significance test |
| 52 | Checkpoint never reached any backend — scorecard could not rank policies | **Critical** | ✅ 2026-09-26 | 1d | — | Done — on `TaskConfig`, mock, Isaac wire, and conformance fixtures |

---

## 2. Critical

### 01 — The deploy gate approves runs the engine blocked
**Critical · Verified · 4h · blocks: nothing · depended on by: 06, 11**

ValidSim's product promise is one artifact: a scorecard that says APPROVE or BLOCK, and a CI gate that acts on it. The gate does not read that verdict.

`validsim/cli.py:366-369`:
```python
scorecard_dict = _require_cached(resolved)
effective = scorecard_dict["threshold"] if threshold < 0 else threshold
approved = float(scorecard_dict["composite_score"]) >= float(effective)
```

`deploy_decision` is present in the cached dict and never consulted.

Reproduction (this session, `VALIDSIM_CACHE_FILE` pointed at a cache entry with `deploy_decision: "BLOCK"`, `composite_score: 96.0`, `threshold: 85.0`):

```
gate: vrun-cafe1234 composite=96.0 threshold=85.0 -> APPROVE
exit code: 0
```

Expected: exit 1. Actual: exit 0 — CI proceeds with deployment.

Direct consequence: the evidence-sufficiency guard I added in `ff15533` (`scorecard.py:182-185`, "a worker that returned 1 of 50 episodes must not be approved") **is inert on the CLI/CI path**. It changes the stored verdict; the gate ignores the stored verdict. I reported that fix as complete in the last session, and it is not. It is also the reason a future author adding any non-score block reason — missing safety coverage, stale checkpoint, revoked approval — will find it silently does nothing.

Fix: treat `deploy_decision` as authoritative; `--threshold` may only tighten, never overturn; absent or unrecognised value → `BLOCK` (fail closed); preserve the 0/1/2 exit contract. Rewrite the gate tests in `tests/test_cli.py` so at least one asserts exit 1 on `BLOCK` at `composite=96`.

Verification standard: a green unit test is not sufficient here. Re-run the cache-forgery repro above and confirm exit 1.

### 02 — Empty API key disables authentication while reporting it enabled
**Critical · Verified · 3h**

`validsim/api/main.py:505` reads the key, `:549` marks auth enabled, `:515`/`:539` return early only when it `is None`:

```python
api_key = os.environ.get("VALIDSIM_API_KEY")   # "" is not None
...
application.state.api_key_enabled = api_key is not None
```

With `VALIDSIM_API_KEY=""`, the missing header is compared against `""` and passes. Reproduced in this session against seven protected routes with no credential supplied at all:

```
200  /api/v1/validations      200  /api/v1/dashboard/summary
200  /api/v1/regressions      200  /api/v1/jobs
200  /api/v1/models           200  /api/v1/metrics
200  /api/v1/dashboard/history
health reports: auth_enabled = True
```

`.env.example:64` ships exactly `VALIDSIM_API_KEY=` with the comment "unset disables auth", so the documented default is the bypass. The `auth_enabled: true` in the health payload is the aggravating factor: an operator verifying their setup is told the opposite of the truth.

Qualitative CVSS (my assessment, not a scored advisory): `AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N` ≈ **9.1 Critical**.

Fix: normalise empty/whitespace to "not configured" at parse time, then fail closed — refuse to start with auth expected but unset, rather than defaulting open. Add a startup warning naming the resolved policy.

### 17 — Documentation asserts capabilities that do not exist
**High business risk · Mixed V/R · ~3 days**

Not a code defect, but the item most likely to cost you real money. The `vault/` tree is internally inconsistent with the code and with itself. Verified directly:

- `vault/00 - Dashboard/Build Status.md:11` claims 24/7 hourly CodeBuddy automation. **Zero** scheduled tasks exist. The page is tagged `auto_generated: true` (`:6`) yet its own table contains a `FAIL` row (`:30`) beneath "100% build pass rate (last 13 runs)" (`:24`).
- Interfaces documented as live that do not exist in `validsim/`: `POST /api/v1/deployment-gate`, `GET /api/v1/audit-log`, `POST /api/v1/webhooks`, CLI `validsim ci --github-actions --fail-below`, a `--format pdf` on `scorecard`, and a scorecard **"Inconclusive"** state (`P:Scorecard UX.md:58`). `P:Core User Flows.md:79` states "there is no `validsim delete` command" while `E:CLI Design.md:82` documents one that exists.
- Published GitHub Actions `validsim/validate-action@v1` cited as the backend of the integration story (`E:GitHub Actions Integration.md:32,44`). Local `actions/` exists; nothing is published to that path.
- Stack claims with no code: gRPC, RabbitMQ, Kubernetes/Argo, Next.js, TimescaleDB, S3/GCS HDF5+MP4 recordings, "hash-chained audit trail", Stripe, Auth0, Terraform. Isaac/GPU integration is HTTP-only — the audit found zero `subprocess`/`shell=True`/`eval`/`exec`/`pickle` under `validsim/`, which is good security-wise but contradicts the "1,000–100,000 episodes on 4× A100" narrative.
- `vault/09 - Risks/Risk Register.md` tracks 11 market risks and **none** of the engineering defects in this catalog, while asserting at `:26` that "Auth gates all routes" (see 02) and at `:28` that the rate limiter is shipped (see 15).

Investor-facing specifically — `YC Application Answers.md:37` ("126-test suite green on continuous CI... an hourly build agent"), `Investor Narrative.md:32` ("NVIDIA Inception member with DGX credits") against `NVIDIA Inception Application.md:22` with eligibility boxes still unchecked, and `YC Application.md:48-51` marking four traction targets ✅ while `YC Application Answers.md:95-101` still holds `[X]` placeholders.

The mechanism, not just the symptom — two findings that show how this accreted rather than how anyone chose to mislead:

- **`builds/build-log.csv` contains an invented line.** Its final row reads `"1274 passed, 2 skipped, 96% cov - agent fleet waves 4-18…"`. `scripts/build.ps1:67` appends exactly one captured pytest tail line per run, and this environment has no `coverage` and no `pytest_cov` installed, so that string is not producible by the script that writes the file. `vault/00 - Dashboard/Build Status.md` is regenerated *from this CSV* — which is how a fabricated number becomes a green dashboard, then a vault claim, then "1,274 tests" in `KPIs.md:48`. The same CSV holds **5 runs across two days** (20:44, 21:00, 12:00, 14:31, 03:30), against `README.md:92`'s hourly-24/7 claim of ~48 runs.
- **The suite actively encodes the bugs as intended behaviour.** `tests/test_cli.py:90-97` derives its expected exit code from the same `deploy_decision` field that `gate` ignores, so it is self-fulfilling and cannot fail on item 01. `tests/test_api_auth_negative.py:176-200` asserts `200` with no header and `api_key_enabled is True` — item 02 is *pinned as desired behaviour by a passing test*. This is why 1292 green tests coexisted with both critical defects, and it is the real answer to "why didn't the tests catch it".



**In-repo correction done (2026-09-24) — external-send question still open.** 46 vault notes were annotated rather than rewritten: a dated `> [!important] Blueprint status` block in the engineering notes listing what actually ships today versus what is target, and per-row `(shipped)` / `(target)` labels in the stack tables. **Verified by me, not taken on trust:** no note was deleted or renamed, no new note created, and all 61 wikilinks still resolve (the single unresolved `[[wikilink]]` in `Home.md` is pre-existing template text, present in `HEAD`, not agent damage). I spot-checked the substantive "shipped" claims against source — static HTML/JS + Chart.js (`validsim/web/index.html` references `chart.js`), Redis in `requirements.txt`, `Dockerfile` and `docker-compose.yml` present.

**The fundraising notes got the stricter treatment, which is where this item earns its keep.** `NVIDIA Inception Application.md` now carries a `> [!warning] Verify before submission`, `[verify]` markers on the eligibility rows, and an explicit statement that the repository does not confirm an application submission, an acceptance, or a $100K DGX credit award. "Thousands of parallel GPU episodes on A100/H100" is reframed as an intended target with a commitment to validate GPU count, runtime and physics fidelity on shadow runs before presenting an SLO. No achievement, date, award or program status was invented — the ambition is preserved, the false certainty is removed.

**Still yours to decide:** whether any of these pages left this machine before the correction. Anything already sent is a different conversation, and I have not pretended otherwise. Anything unsent can be corrected. Anything already in a recipient's hands is a different conversation and I won't pretend otherwise. I have not changed, deleted, or pushed any vault file.

---

## 3. High

### 03 — Async jobs silently never execute in the deployed stack
**High · Verified · 15 minutes**

`docker-compose.yml:118` sets `VALIDSIM_JOB_QUEUE: redis` on the **worker** only. The api service (`:35-45`) sets `VALIDIMS_STORE` and `VALIDSIM_REDIS_URL` but not `VALIDSIM_JOB_QUEUE`, and there is no `env_file:` anywhere in the file, so api falls back to `memory` (`queue.py:456`). `POST /api/v1/jobs` returns `202` into a process-private queue the worker will never drain; the job stays `queued` forever with no error.

Confirmed against the docs, which assert the opposite: `docs/async-jobs.md:397` "the `api` service sets `VALIDSIM_JOB_QUEUE=redis`", repeated in `vault/04 - Engineering/Job Queue Worker.md:145-165`.

**CI coverage added (2026-09-24); live execution still unverified locally.** A `PostgreSQL backend parity` job now runs on `postgres:16-alpine` with `pg_isready` health checks (5s interval, 12 retries) and `VALIDSIM_PG_URL=postgresql://validsim:validsim-ci-password@127.0.0.1:5432/validsim`. The image-build job now `needs: [test, postgres]`, so a red Postgres job blocks the Docker image.

**The parity oracle is SQLite**, not a literal: the new tests run the same operations against both backends and compare run id, checkpoint, composite, `deploy_decision`, `created_at`, full round-tripped records, `count()`/`len()`, checkpoint filtering, history/date-range ordering, and repeated deletion. This is the test item 30 asked for — the old suite asserted against hardcoded SQL/JSON, which passes while the real backend is wrong.

**Verified here:** with the DSN unset, 33 passed / 4 skipped and the SQLite oracle suite passes standalone; a *configured but unreachable* DSN **fails rather than skips** (confirmed by me), which is the property that stops a silently-uncovered backend from looking green.

**Not verified here, and I am not claiming it:** Docker is unavailable on this machine and `psycopg` is declared in `requirements.txt` but not installed in this interpreter, so no live Postgres round-trip has actually executed. That first real run happens in CI. No Postgres-vs-SQLite divergence has been observed to date — absence of evidence, not evidence of parity.

Note `VALIDSIM_STORE` *is* correctly set on both services (`:37`, `:114`) — the queue key was simply missed. One line.

### 04 — `redis` imported by a live path, declared nowhere
**High · Static · 30 min**

`validsim/jobs/queue.py:120` imports redis; it is absent from `requirements.txt`, `requirements-dev.txt`, and `pyproject.toml`. `Dockerfile:26` installs only `requirements.txt`. Consequence chain: the compose worker crash-loops on the very path 03 is meant to fix, raising `RuntimeError("pip install redis")` — the compose file's own comment at `:127-129` concedes this. `docs/async-jobs.md:373-376` nonetheless tells the reader the worker "drains the Redis queue".

This is why 03 is under-rated if fixed alone: fixing the queue key without declaring redis converts a silent hang into a crash-loop. Do 03 and 04 together.

**Fixed:** `redis>=5.0` is in `requirements.txt` and mirrored in `pyproject.toml`'s runtime dependencies, so the compose worker can actually start.

**The class recurred within this same session, twice.** Writing `tests/test_delivery_pipeline.py` needed `yaml`, and `import yaml` succeeded — while `PyYAML` appeared in **no** dependency file anywhere. Same shape as item 04: a live import backed by a transitive install, invisible until the environment changes. Declared in `requirements-dev.txt` and in the `dev` extra (which was also missing `pytest-cov`, so `pip install .[dev]` could never run the suite it claimed to support). `test_runtime_and_test_dependencies_are_declared` now asserts both files.

### 05 — Configuring auth breaks the container healthcheck
**High · Verified · 2h · depends on: 02**

`main.py:550` installs the global auth dependency; `health` is registered at `:552`, *after*, so it inherits auth. Verified: with `VALIDSIM_API_KEY=s3cret`, `GET /api/v1/health` → `401` with no header, `200` with the correct one. Both `Dockerfile:65` and `docker-compose.yml:51-60` probe `/api/v1/health` expecting 200 and cannot send a key.

So following the documented hardening advice produces a permanently unhealthy api container. `api-reference.md:556-560` simultaneously claims health is "safe to back a container healthcheck". Fix by exempting the health route (it already reports its own `auth_enabled`, which is the pattern this codebase uses for the deliberately-public dashboard at `:805-815`).

### 06 — `gate` reads a local file, not the authoritative store
**High · Static · 8h · depends on: 01, 21**

`cli.py:366` uses `_require_cached()` → `.validsim/scorecards.json`, overridable by the `VALIDSIM_CACHE_FILE` env var, and `_newest_run_id` (`:98`) orders by a `created_at` string taken from that same file. Anyone able to write one JSON file controls the deploy verdict. The reproduction in item 01 *is* the proof — I forged a cache entry and got an approval.

The honest complication: "just read the store instead" is not implementable as a one-line patch, because of 21 — the default store is in-memory, so a separate `gate` process has nothing to read. This is the design decision you already framed as "fail closed without a durable store", and it needs that decision made concrete: `gate` should require a durable backend, refuse on `memory`, and validate `run_id` against `_RUN_ID_RE` (`cli.py:64`, currently defined and unused).

**Fixed (2026-09-21):** exactly that, in that order.

- `validsim/store/__init__.py` gained `store_backend()` as the single reader of
  `VALIDSIM_STORE`, so "which backend is active" is asked in one place.
- `validsim/cli.py` gained `_DURABLE_STORE_BACKENDS = {sqlite, postgres}`,
  `_require_durable_store()`, `_newest_stored_run_id()` and
  `_resolve_stored_run_id()`. `gate` now resolves through the last of those,
  reads `create_store().get(run_id)`, approves only on the exact string
  `APPROVE`, and treats `--threshold` as tighten-only. `_RUN_ID_RE` is applied
  (`fullmatch`) before a run id is used — closing 22's code half.
- `actions/validate/action.yml` exports `VALIDSIM_STORE=sqlite` plus
  `VALIDSIM_SQLITE_PATH=${RUNNER_TEMP}/validsim/runs.db`, so the shipped CI path
  keeps working in the same change that made the requirement exist.
  `examples/nightly-adversarial-sweep.yml` sets the same pair on its job.
- Docs corrected to stop describing `gate` as cache-driven: `docs/runbook.md` §2
  table, §3 exit-code table, §5.2; `docs/github-actions.md` MVP-mode note, §4
  same-job callout, §5 exit codes and tip, §7.2, §8; `.env.example`; the vault
  (`CLI Design`, `API & CLI`, `Core User Flows`); the `gate` option help strings,
  which said "Override the stored approval threshold" and "newest cached run".
- Architecture record: [[ADR 0006]] states the four rules and what they cost;
  [[ADR 0002]] is marked partially superseded (closes 11, which asked for a
  superseding ADR rather than an edit).

Evidence: `tests/test_cli.py::TestGate` was rewritten store-authority — a forged
cache entry cannot overrule a real `BLOCK` run, a malformed id exits `2`, and
`["DEFINITELY_APPROVE", "approve ", "APPROVE-ish"]` each block;
`test_latest_resolves_from_the_store` deletes the cache file to prove the gate
does not need it. `tests/conftest.py` neutralises `VALIDSIM_STORE` /
`VALIDSIM_SQLITE_PATH` per test and provides the shared `durable_store` fixture.
Full suite after the change: **1328 passed, 2 skipped, 95.88% coverage**
(`--cov-fail-under=90` satisfied), `ruff check validsim tests actions scripts`
clean.

Not done, deliberately: `compare --candidate-latest` still resolves its ids from
the cache. It reports a diff and gates nothing, so a forged cache can only make
it exit `2` or compare two real stored runs. Recorded in ADR 0006 instead of
silently expanded. Item 21 (the `memory` default) stays open — this item removed
the danger of that default, it did not decide whether to keep it.

### 07 — Two workers can run the same job
**High · Static · 12h · depends on: 04**

`worker.py:201-209`: `_claim_next` lists the queue, returns the first `QUEUED` record, and only later makes a separate `update_status(RUNNING)` call. No `LPOP`, `BRPOP`, `LMOVE` or lease. Two concurrent workers both see the same job, both run it — for a 5,000-episode GPU sweep that is a duplicate spend, and both write under the same `run_id`. `E:Job Queue Worker.md:181` claims the opposite ("reads LLEN under the same lock… cannot slip past the cap"). Needs atomic claim plus a lease and reaper for jobs stranded in `RUNNING` by a killed worker.

**Partly fixed (2026-09-24) — atomicity done, lease/reaper and live proof outstanding.** `validsim/jobs/queue.py` now claims through a single Lua invocation (`_REDIS_CLAIM_NEXT_SCRIPT`): the `LPOP` and the queued→running payload write happen inside one script, which Redis runs to completion without interleaving, so two workers cannot both win a job. The in-repo `_FakeRedis` was extended to execute the script, and two-worker race tests exist (one over the fake, one against a live Redis that skips without a DSN).

**Two bugs I found and fixed while reviewing that script, which the green suite had not caught** (the fake never executed real Lua):
1. All three early `return nil` paths dropped the job id *after* `LPOP` had already removed it — a transient inconsistency became permanent job loss. A missing payload now re-queues.
2. That re-queue was then **unbounded**, which would spin a polling worker hot forever on one unparseable record. It is now capped — `_CORRUPT_CLAIM_ATTEMPTS = 3` with `_CORRUPT_CLAIM_TTL_SECONDS = 300`, tracked on a `:missing` / `:corrupt` counter key. A list entry whose payload is already `done`/`running` is still dropped deliberately; re-queuing that would never terminate.

**Not verified:** there is no Redis server on this machine and `redis-cli` is absent, so the Lua was syntax-reviewed (43 lines, no Lua-5.3-only constructs, correct ARGV arity) but **never executed against real Redis**. "Two workers cannot both claim" rests on Redis's documented single-script-execution semantics, not on an observed run here. First real execution is the new `compose` CI job.

**Lease + reaper closed (2026-09-25).** The open half is implemented and the whole item is now ✅.

*What was added.* `JobRecord` gained `lease_expires_at` and `lease_epoch` (`validsim/jobs/models.py`). `claim_next` stamps a deadline and increments the epoch under the same lock/Lua operation that performs the claim, so the claim and its fence are one indivisible step. `renew_lease` is a compare-and-set on the epoch, and `reap_expired` returns expired `running` jobs to `queued` rather than `failed` — the pipeline is deterministic and the result upsert is idempotent, so reclaiming is safe while stranding a job is not. Lifetime comes from `VALIDSIM_JOB_LEASE_SECONDS` (default 3600s, positive integer, validated exactly like `max_depth`).

*Why an epoch and not just a deadline.* Requeueing on expiry alone reintroduces this item``s original bug: a job that legitimately outlives its lease would be executed twice. The worker now heartbeats from a daemon thread at a third of the lease, and the fencing epoch makes that safe. A worker that stalls long enough to lose its lease cannot renew, and — the part that closes the loop — its **terminal write is fenced too**: `update_status` takes an optional `lease_epoch` and returns `None` when the job is no longer running under that epoch, so a zombie cannot overwrite the claim of the worker that took over. The Redis path does this check-and-write in one Lua script (`_REDIS_FINISH_SCRIPT`) so it holds across processes, not just within one instance. Callers holding no lease (the HTTP router) omit the argument and keep the previous unfenced behaviour.

*Evidence.* `tests/test_jobs_lease.py` (34 tests) and `tests/test_jobs_lease_worker.py` (8 tests) were written first and watched fail before the implementation. They pin the properties that matter: a dead worker``s job is reclaimable, a heartbeating worker``s job is not, a stale epoch cannot renew, and a zombie cannot clobber its replacement. Full suite: **1377 passed, 8 skipped, 0 failed** (17.6s). The 8 skips are environment absence, not new gaps: 4 need the `redis` driver and 4 need `VALIDSIM_PG_URL`, none of them lease tests.

**CORRECTION (2026-09-25, later the same day). I closed this item too early and the first close was wrong.** A line-by-line review of the Lua found two defects that the test suite structurally could not catch, because the in-repo fake client never executes a script:

1. **The Redis reaper never reaped anything.** `_REDIS_REAP_EXPIRED_SCRIPT` did `tonumber(ARGV[2])` on a value the Python call site passes as an ISO-8601 *string* (`_utc_now()`), and `tonumber(record.lease_expires_at)` on the stored ISO string. Both return `nil`, so `expires and expires <= now` was **never true** — on Redis, expired jobs stayed `running` forever. Fixed by comparing the fixed-format UTC timestamps lexicographically, the same justification the store `history()` methods already document.
2. **Nothing ever called the reaper.** `reap_expired()` had no production caller at all: neither `JobWorker.run_forever` nor the CLI worker invoked it. The mechanism existed and was dead code, so even correct Lua would never have run. `run_forever` now reclaims expired claims on every iteration before claiming new work.

Both were found by delegated review, not by the green suite. That is the whole point of items 07/08: the suite proves the Python contract, never the script semantics. A third, lower-severity hardening was applied to the decode paths — a valid JSON scalar (`true`, `1`) decodes without error but is not a record, and indexing it aborts the script *after* `LPOP` already consumed the id.

**Honest remaining gap:** the scripts are still only structurally reviewed. My test asserted which script was called and with how many keys, never that it reaps. Closing that needs the `compose` CI job (which will not exercise `reap_expired` — it uses `episodes:1` and the default 3600s lease, so no lease ever expires).
*Not verified — do not read this as proven against Redis.* There is still no Redis server and no `redis` driver on this machine, so **all four Lua scripts (claim, renew, reap, finish) have never been executed.** They were only structurally checked: balanced blocks, and `ARGV`/`KEYS` indices contiguous from 1 with no gaps, matching each call site``s arity. That is weaker than the atomicity claim in item 07 or the enqueue claim in item 08, which at least cite Redis``s documented single-script-execution semantics. First real execution is the `compose` CI job.

*Method note.* `ruff` is not installed in this session``s `.venv`, so I am **not** claiming lint-clean for these edits; the earlier clean result came from a different environment. `python -m compileall` on the touched modules passes.

### 08 — Enqueue is two non-atomic writes
**High · Static · 2h · depends on: 04**

`queue.py:383-384`: `client.set(key, payload)` then `client.rpush(index, run_id)`. A crash between them leaves a payload invisible to `list()` forever — and `max_depth` then undercounts, defeating the backpressure ADR 0003 describes. Fix with a pipeline or by making the index the source of truth.

**Partly fixed (2026-09-24).** `_REDIS_ENQUEUE_SCRIPT` performs the duplicate check, the `max_depth` check and all three writes (payload, insertion index, ready list) in one Lua invocation, so a crash can no longer leave an orphaned payload or undercount depth. **Same verification gap as item 07: never executed against a real Redis server on this machine** — proven only by reading the script, with the first real run in CI's `compose` job.

### 09 — The production store backend is untested in CI
**High · Verified · 6h**

`tests/test_store_postgres.py:515` is `@pytest.mark.skipif(not os.environ.get("VALIDSIM_PG_URL"), ...)`. No CI workflow sets that variable — I grepped every `*.yml` in the repo; it appears only in `.env.example`, compose, and prose. `docs/testing.md:262` documents this honestly ("No `VALIDSIM_PG_URL` (default, and CI): the class is skipped"). The consequence is that `docker-compose.yml:37` defaults `VALIDSIM_STORE` to **postgres**, so the backend shipped to production has zero automated coverage in the pipeline that gates releases. SQLite and Redis backends deserve the same treatment.


**Re-verified by static parity audit (2026-09-25), after a delegated agent died before producing anything.** All three backends implement the same seven contract methods (`save`/`get`/`delete`/`list_for_checkpoint`/`history`/`count`/`close`); `summary` and `new_run_id` are absent from the SQL backends but both subclass `ValidationStore` and inherit them, and the call sites (`api/main.py`, `api/dashboard.py`) invoke `summary()` on `StoredRun`, never on the store. No surface gap. `history()` is semantically identical in all three: oldest-first, inclusive bounds, placeholders rather than interpolation, same lexicographic-comparison rationale.

**One deliberate, documented divergence, recorded because it is invisible from the API.** Duplicate `save()` behaves differently by backend: memory (`store/memory.py:81`) and SQLite (`INSERT OR REPLACE`, `store/sqlite.py:198`) **overwrite**, while Postgres uses `ON CONFLICT (run_id) DO NOTHING` (`store/postgres.py:399`) so the **first write wins**. The Postgres docstring states this outright, so it is intentional rather than a bug — an append-only verdict log is the safer semantic for a deploy gate. But all three `return run unchanged`, so a caller cannot detect that its write was ignored. Worth knowing before anyone relies on re-saving to correct a run.

This also matters for item 07``: the lease reaper re-runs a reclaimed job, and that reasoning assumed the store upsert is idempotent. Both semantics satisfy that — the pipeline is deterministic, so an overwrite writes the same row and `DO NOTHING` keeps the identical first write. The reaper is safe under either backend.

**Still unproven:** none of this was executed against a live Postgres. The 4 `test_store_postgres.py` tests needing a real server skip locally (no `VALIDSIM_PG_URL`); the rest assert SQL shape against an injected fake. First real run is the `postgres` CI job.
### 10 — No rollback path
**High · Reported (R) · 6h**

Per the docs pass, `docs/runbook.md` offers only "unset the key and restart" and `docker compose down/up` (`:130-133`, `:144`). Nothing for a bad promoted checkpoint or a bad image. Worse, the append-only Postgres verdict log (`postgres.py:414`, `ON CONFLICT DO NOTHING`) can only be corrected with `DELETE` (`main.py:620-646`) — which destroys the "immutable, tamper-evident history" that ADR 0004:88 and `P:Product Principles.md:48` both claim. Backup section (`:207-217`) writes to a `backups/` directory that does not exist, pipes `pg_dump -Fc` through `docker exec` redirection (corruption-prone on the Windows path the same section recommends), and no automation implements the stated "daily, retained ≥14 days". **Confirm before acting — I did not personally verify these.**

**Verified and documented (2026-09-24); one product decision left open.** I re-derived all three claims rather than trusting the R-marked original.

1. **Backup procedure — was partly wrong, now runnable.** `backups/` genuinely did not exist, and the old steps handed `docker exec` output to POSIX-style redirection while the surrounding advice targets Windows. But the original claim that redirecting `pg_dump -Fc` on the host is corruption-prone is **overstated** — that is valid. The procedure now targets Git Bash/WSL explicitly, creates the directory, uses `docker compose exec -T postgres`, and gives both a fresh-database restore and a verified in-place restore (`docs/runbook.md:350-418`).
2. **Immutability — the claim holds.** Postgres `save` is first-write-wins via `ON CONFLICT (run_id) DO NOTHING` (`validsim/store/postgres.py:399-415`, I read this), while `DELETE /api/v1/validations/{run_id}` physically removes the row (`validsim/api/main.py:654-680`). ADR 0004 acknowledges the tension in one place and still says "immutable"/"tamper-evident" elsewhere. **I did not change the behaviour**: the runbook now documents the consequence and lays out three options (soft-delete/tombstone, admin-only deletion with an audit record, or drop the immutability claim) without picking one — because deleting the endpoint, changing the conflict clause, or weakening a compliance claim are all product decisions, not cleanups. **This is the one open decision from this item.**
3. **Rollback — the claim holds; now documented.** The deploy job in `examples/robot-validation.yml:66-78` is a commented placeholder, and `release.yml` only pushes images. The runbook now covers identifying the last known-good run, re-gating a specific run id against a durable store, local image recovery, volume safeguards, and — honestly — states that fleet rollback itself is outside this repo and needs whatever platform actually runs the robots.

### 19 / 20 — Repo hygiene and an unreviewed gate
**Medium · Verified · 1h + your decision**

34 artifacts sit untracked at repo root (`_coerce_run.txt`, `bench_junit.xml`, `pytest_full.txt`, `run1.txt`, `s.txt`, `full.txt`, `out_prop.txt`, …), plus `project.docx`. These are the residue of an earlier loop that ran `git add -A`; the same habit put `project.docx` into history. I have deleted nothing and staged nothing with a glob.

Separately, `pyproject.toml` is the one modified tracked file: it holds a `[tool.coverage.*]` block with `fail_under = 90` that I deliberately withheld from `ff15533` because I did not write it and cannot attribute it. Suite wall time measured this session: **31 seconds, exit 0, 1292 tests**. Whether a 90% floor is right is your call — see §5 for why I won't assert the current number.

**Prevention done (2026-09-24); deletion still needs you.** The count in the original finding was stale. Current state, measured with `git status --porcelain`: **39 untracked root files** — 9 visible and 30 ignored-but-present. Grouped:

- **JUnit XML (15)** — `_coerce_wave.xml`, `_obs_junit.xml`, `_pipeline_refactor_junit.xml`, `_pytest_report.xml`, `_r1.xml`, `_r2.xml`, `_routing_junit.xml`, `_sts_junit.xml`, `_sts_junit2.xml`, `bench_junit.xml`, `junit.xml`, `pytest_junit.xml`, `pytest_report.xml`, `validsim-nightly-junit.xml`. Safe to delete.
- **pytest/collection text (14)** — `_coerce_run.txt`, `_jsonwave_pytest.txt`, `_obs_pytest.txt`, `_p1.txt`, `_p2.txt`, `_pipeline_refactor_pytest.txt`, `_pytest_tail.txt`, `_sts_full.txt`, `_sts_run.txt`, `_tsrun.txt`, `pytest_full.txt`, `pytest_out.txt`, `pytest_routing_run.txt`, `collect.txt`. Safe.
- **Scratch helpers (2)** — `_probe_fuzz.py`, `_tmp_junit_wrap.py`. Safe.
- **Test-progress scratch (5)** — `full.txt`, `out_prop.txt`, `run1.txt`, `run2.txt`, `s.txt`. Safe.
- **Audit reports (2)** — `_FULL_READ_REPORT.md`, `_read_report.json`. **Review these first** — they are mine/our read passes, not junk.
- **Ignored build/cache state** — `.coverage`, `.pytest_cache/`, `.ruff_cache/`, `.validsim/`, `.venv/`, `__pycache__/`, `builds/`. Safe.

**`project.docx` is not junk.** It is tracked in git *and* referenced at `README.md:11` as the confidential founding document, so the original finding's grouping of it with scratch files was wrong.

`.gitignore` (and the matching `.dockerignore` rules) now cover the artifact classes actually found, and `tests/test_repo_hygiene.py` asserts on the *rules* — not on whether files happen to exist now — so the class cannot silently return. Verified: `git check-ignore` confirms `validsim/cli.py`, `docs/ISSUE_CATALOG.md`, `requirements.txt`, `actions/validate/action.yml` and `examples/nightly-adversarial-sweep.yml` are **not** ignored.

**Nothing was deleted.** The safe-to-delete list above awaits your approval.

**Correction found after the fact (2026-09-24) — one "untracked" file is required source, not clutter.** `tests/conftest.py` is untracked, but it is **load-bearing**: it provides the autouse fixture that clears `VALIDSIM_ENV`, `VALIDSIM_STORE` and `VALIDSIM_SQLITE_PATH` before every test (without it, an ambient shell value silently changes test behaviour) plus the shared `durable_store` fixture the store/gate tests depend on. Deleting it would break the suite in ways that look like flaky tests rather than a missing file. It was never tracked in git (`git cat-file -e HEAD:tests/conftest.py` → absent), which is how it slipped into the untracked list in the first place.

Verified: it is **not** matched by any `.gitignore` rule, so the ignore changes cannot hide it. The same applies to the other untracked-but-required test files this session added or found: `tests/test_delivery_pipeline.py`, `tests/test_repo_hygiene.py`, `tests/test_store_default_resolution.py`. **These four must be committed, not cleaned up** — they are the enforcement mechanism for items 19, 21 and 32. Everything else in the untracked inventory remains safe-to-delete pending your approval.

**Standing risk:** untracked-but-required source is invisible to a reviewer reading `git status` as "junk". A future cleanup pass driven by that list would delete the tests that guard the gate. Worth a commit of the test suite specifically.

---

## 4. Medium and Low (condensed)

- **11** `docs/adr/0002:32-35` states the gate as `APPROVE if composite >= threshold`. After `ff15533` there is a third condition, and after fixing 01 there will be a fourth input. Per ADR 0001's own rule this needs a superseding ADR, not an edit. *2h, with 01.*
  **Fixed (2026-09-21) ✅** — `docs/adr/0006-gate-reads-the-durable-store.md` records the four gate rules and what they cost; 0002 keeps its body and gains a superseded status line plus an inline pointer at the sentence 0006 replaces. No ADR text was deleted.
- **12** `docs/api-reference.md:199-217`: the 201 example shows `composite_score: 87.5` - **12** `docs/api-reference.md:199-217`: the 201 example shows `composite_score: 87.5` for inputs that compute to 95.3 (`0.4×92 + 0.3×95 + 0.2×100 + 0.1×100`). A reader cannot learn the formula from the documented example. *20m.*
  **Fixed (2026-09-24) ✅** — example now shows `95.3` (all three occurrences in the file agree), and the prose below it now states the real rule, which the old text omitted entirely: `deploy_decision` is `APPROVE` only when the evidence is sufficient **and** `composite >= threshold` (`validsim/engine/scorecard.py:182-185` — `sufficient_evidence = evaluation.total_episodes >= task.episodes > 0`). That second condition is the one from item 01, so the API reference now documents the same contract the gate enforces.
- **13** `:603,:650,:783` say `GET /jobs` returns a bare array and is "not paginated"; `jobs/router.py:135-184`   **Fixed (2026-09-24) ✅, and the catalog was wrong** — `/jobs` is not simply a paginated envelope. `validsim/jobs/router.py:175-184` keeps a deliberate dual contract: with **neither** `limit` nor `offset` the response is the historical bare FIFO array (existing clients and the test suite depend on it); with **either**, it switches to `{total, limit, offset, items}` newest-first. The original entry asserted the bare-array docs were simply wrong; in fact the docs were under-specified and the endpoint is intentionally dual-shaped. Both `docs/api-reference.md` and `docs/async-jobs.md` now describe both shapes with the switch condition, the defaults, the `422` range behaviour, and the empty-page case. **Not changed:** the endpoint itself — this was a documentation fix.
- **14** `main.py:809-815` deliberately re-mounts `/metrics` with `router.dependencies = []` so it is reachable without auth. The technique is sound and the `finally` restores the dependency, but `api-reference.md:93` lists the public routes and omits `/metrics` — so an operator auditing exposure won't find it.   **Fixed (2026-09-24) ✅** — `/metrics` is now in the public-route table of `docs/api-reference.md` with an accurate note that it is deliberately reachable without an API key, what it exposes (run / approval / block counts, composite gauge, HTTP class counters), and that an auth-gated copy exists at `/api/v1/metrics`. Evidence: `validsim/api/main.py:836-849`, exposed metric names in `validsim/api/metrics.py:154-192`. The deliberate auth-stripping is left in place — the audit called the technique sound; only the missing documentation was the defect.
- **15** `.env.example:73` `VALIDSIM_RATE_LIMIT=0` — limiting off by default, while `Risk Register.md:28` says the rate limiter is shipped and "mitigated". Either default it on or retract the claim. *2h.*
  **Fixed (2026-09-25) ✅** — write-route protection now ships **on** at 60 requests per 60s per client IP. Only an explicit `VALIDSIM_RATE_LIMIT=0` disables it, and malformed or negative config now falls back to the default rather than silently removing the guard. Scope is unchanged and deliberately narrow (writes, `/compare`, job enqueue; reads untouched, buckets LRU-bounded at 10k), so enabling it does not throttle ordinary use. `README.md`, `docs/api-reference.md`, `docs/runbook.md`, `.env.example` and the API module docstring were corrected. Two health tests pinned the old default and were updated; one (`test_none_when_disabled`) had never actually set the disable flag and was passing by accident.
- **16** `docs/isaac-worker.md:112-129` documents `VALIDSIM_WORKER_TOKEN`, which appears nowhere in the code (real variable: `VALIDSIM_ISAAC_WORKER_KEY`, `isaac_worker.py:53`); `:152-172` references a nonexistent `Dockerfile.worker`; port 8090 at `:133` vs 8080 at `.env.example:97`. *4h.*
- **18** Test counts: at the time of writing `docs/testing.md:28` said ~250 and `README.md:14` said 1244, against 1292 collected (1020 `def test_` across 78 files; the gap is parametrisation). **Both doc figures are now gone** — `docs/testing.md`, `README.md` and `CONTRIBUTING.md` each state the no-hard-coded-count policy instead, so this item is now resolved by removal rather than by picking a source of truth. Vault still carries 97/126/338/601/1230/1274 in investor notes (see item 20). *2h.*
  **Partially fixed (2026-09-21) ◐** — suite collected **1330** (1328 passed, 2 skipped) **as of 2026-09-21**. `docs/testing.md` no longer states a number at all and documents the two commands that produce one (verified on that date: `python -m pytest --collect-only -p no:warnings | tail -1` printed `1330 tests collected` — a **collection** count, not a pass count). `README.md` kept a number then, because it was the shop window: "1328 passed / 2 skipped as of 2026-09-21"; **`README.md` no longer quotes a count at all**, by the same no-hard-coded-count policy now applied across the docs. `E:API & CLI.md` and `00 - Dashboard/Build Status.md` corrected, including that page's "100% build pass rate (last 13 runs)" line, which its own table contradicts. `KPIs.md:48` restated against a verified source, with its pointer to item 20's fabricated row.

  > **Do not reuse the 1330 / 1328 / 2 figures.** They are a dated snapshot (2026-09-21), not the current suite: the test files have been added to since. Re-derive any count you need by running the suite yourself, and name the command that produced it — a `--collect-only` total is not a pass count. **Still open:** the fundraising notes (`YC Application Answers.md:41`, `Engineering Momentum Log.md:30,47,51`, `YC Countdown.md:72`, `Unit Economics.md:39`) each freeze a historical count in a narrative claim — those are item 17's decision, not arithmetic, and I have not touched them.
- **21** `store/__init__.py:41` defaults to `"memory"`, so a default install persists nothing across processes — no verdict history, no regression baseline, no audit trail. `CHANGELOG.md:87` says it "default(s) to postgres". Only `E:API & CLI.md:105` admits the truth. Needs an explicit decision, not a silent default. *4h.*

**Decided and verified (2026-09-24).** The default stays `memory`; the *silence* was the defect, not the default. Changing it to sqlite would write database files into a user's working directory without consent — a worse surprise than losing persistence, and it would break the zero-config principle the product states for itself. What changed:

- The ephemeral default is now discoverable rather than invisible: `validsim health` and `GET /api/v1/health` both name the backend, and `validsim health` prints `Store backend: memory` directly (verified by running the command).
- Durable local use stays one flag: `VALIDSIM_STORE=sqlite`.
- `validsim gate` still refuses memory with exit `2` and an actionable message, and `actions/validate` already selects sqlite — so the CI path is unaffected.

**Honest note on method:** the TDD test (`tests/test_store_default_resolution.py` — unset → memory, explicit sqlite, explicit postgres, unknown → memory) passed **before** any production edit, because the behaviour already matched the decision above. The correct deliverable was therefore a test that pins the decision so any future change is deliberate, not a code change. 4 passed, lint clean, full suite otherwise unaffected.

**Correction (2026-09-25).** The three stale claims flagged here were subsequently fixed, so the line above no longer describes the repo: `CHANGELOG.md`, `docs/async-jobs.md` and `vault/00 - Dashboard/Build Status.md` now state that `VALIDSIM_STORE` defaults to `memory` outside Compose, and that the `postgres` default comes from Compose`s own `${VALIDSIM_STORE:-postgres}` fallback. Verified against `validsim/store/__init__.py` and `docker-compose.yml`, not taken on trust.

**Residual gap, stated rather than papered over:** the default is visible *on request* (`health`), not announced at startup. A first-use warning was considered and skipped — without a coherent place to put it in both the CLI and the API it would be noise rather than signal. Raise it if you want the warning.
  **Fixed (2026-09-22) ✅** — memory remains the library/CLI default so zero-config local runs never create files behind the user's back. The existing CLI and API health probes expose the active `memory` backend and its non-durable, process-local nature; the README documents the one-line `VALIDSIM_STORE=sqlite` fix. `validsim gate` continues to fail closed with exit `2` on memory, and `actions/validate` selects SQLite automatically for CI. A new factory test pins unset → memory, explicit SQLite/PostgreSQL selection, and the documented unknown-value fallback. The `CHANGELOG.md` and vault references were stale at the time of writing and have since been corrected (see the correction note above).
- **22** `_RUN_ID_RE` at `cli.py:64` is defined and never used → no validation on run ids entering the cache path. Six docs use `--run-id abc123`, which cannot match `vrun-[0-9a-f]{8}`. *1h.*
  **Fixed (2026-09-21) ✅** — with item 06. `_resolve_stored_run_id()` `fullmatch`es the id before the store is consulted, so `gate --run-id abc123` exits `2` with `expected vrun-<8 hex>` and a test asserts it. Docs corrected to runnable ids in `E:API & CLI.md`, `E:CLI Design.md` (implemented section + a "Week-1 design sketch, not the shipped CLI" warning over the blueprint block, which also flags the never-shipped `validsim ci`, `--fail-below` and `scorecard --format pdf`), `P:Core User Flows.md`, `P:Product Principles.md`, `P:User Personas.md`. The blueprint blocks are annotated rather than rewritten, so the history stays readable.
- **23** `ruff` is not installed in `.venv` (`No module named ruff`), so the lint gate has never been run against my changes. I am not claiming lint-clean. *15m to install and run — say the word before I touch your environment.*

---

## 4b. The delivery pipeline reports success on work it did not do

All from the final read pass (status **R** — reported with file:line by the subagent; re-verify each before editing, though these are cheap to confirm by eye). They cluster into one theme: a green checkmark that means nothing. Item 17 is the vault's inflated *claims*; this section is the machinery that keeps generating them.

### 24 — `scripts/build.ps1` is broken exactly like the two workflows I already fixed
**High · ✅ fixed 2026-09-21 · 30 min · pairs with 04**

`:57` installs only `requirements.txt` — which has no pytest — then `:64` runs `-m pytest`. Every invocation fails at the `test` stage with `No module named pytest`. This is the identical defect I repaired in `nightly.yml:39` and `release.yml:47` in `ff15533`; I fixed the two copies the audit surfaced and did not look for the class. The corroborating detail: `.venv` — bootstrapped *by this very script* — has pytest but no `pytest_cov`, no `coverage`, no `ruff`, which independently confirms the script's install step never included dev deps. Lesson recorded: when a defect is a *class*, grep for the class.

**Fixed:** `scripts/build.ps1` now installs `requirements.txt` **and** `requirements-dev.txt`, and a run with no pytest summary line is reported as `FAIL` with the text "pytest produced no summary (suite did not run…)" instead of being summarised as a pass. The script's own synopsis and the vault note it generates no longer claim a scheduler that does not exist. Asserted by `tests/test_delivery_pipeline.py::TestExposedSurfaceAndDeps`.

### 25 — The coverage floor is not enforced anywhere it is claimed to be
**High · ✅ fixed 2026-09-21 · 2h · depends on 20**

`ci.yml:52` runs pytest with no `--cov-fail-under`; `pytest.ini:5` sets none. `Makefile:25` does pass `--cov-fail-under=90` and its own comment says "(same as CI)" — that comment is false. `README.md:136` and `CHANGELOG.md:72-73` state the build "fails below 90%" — also false. And because pytest-cov isn't installed in this environment at all, the `[tool.coverage.report] fail_under = 90` block I withheld from `ff15533` (item 20) would enforce nothing in CI even if merged. **This reframes your earlier "hold the coverage gate" decision:** the gate isn't pending review so much as non-existent, so item 20 and 25 should now be decided together.

**Correction — one mechanism in this entry was itself wrong, and it mattered.** The claim "pytest-cov isn't installed in this environment at all" is false: `python -c "import pytest_cov"` reports **7.0.0**, and `ci.yml` installed it explicitly on every run. Re-running the suite against a partial file proved the config *is* honoured without a command-line flag:

```
python -m pytest tests/test_api_health.py --cov=validsim --cov-report=term
→ FAIL Required test coverage of 90.0% not reached. Total coverage: 29.62%   (exit 1)
```

So the real defects were narrower and different: the `[tool.coverage.*]` block existed only in the uncommitted working tree (item 20), and `ci.yml` never *stated* the floor where a reader looks for it. The Makefile "(same as CI)" comment and the README/CHANGELOG "fails below 90%" claims were therefore true of the working tree and false of `HEAD` — which is an item-19/20 problem, not a coverage-tooling one.

**Fixed:** suite measured at **95.84%** (3163 statements, 91 missed, branch coverage on) — 5.8 points of headroom over the floor, so the gate is safe to ship rather than hold. `ci.yml` now passes `--cov-fail-under=90` explicitly, and `tests/test_delivery_pipeline.py::TestCoverageFloorIsTruthful` asserts that pyproject, CI, the Makefile and the README all state the same number. **This reverses the earlier "hold the coverage gate" instruction on the strength of that measurement; if you want the floor back off, remove the config block and the flag and soften the README sentence — they must move together, which is what the test enforces.**

### 26 — A release job that succeeds while publishing nothing
**High · ✅ fixed 2026-09-21 · 1h**

`release.yml:141-162` gates every packaging step on `has-token == 'true'`. With no `PYPI_API_TOKEN`, the job checks out nothing, builds nothing, uploads nothing, and reports success — so "release passed" is not evidence a release happened. Related: `release.yml:115` yields `ghcr.io/org/repo:vv1.2.3`, because `github.ref_name` already carries the `v`.

**Fixed — and the premise was wrong, which made the fix bigger than a flag.** `python -m build` and `twine check` need no credentials at all, so gating them on `PYPI_API_TOKEN` bought nothing and cost the only real check the job had. The job (renamed `publish-pypi` → `package-check`; "publish" described work it never did) now builds the sdist and wheel and runs `twine check` unconditionally on every `v*` tag, and records in the step summary that no upload happens. A green release job is now evidence the artifacts are valid. Double-`v` tag corrected to `ghcr.io/…:${{ github.ref_name }}`. Asserted by `TestReleaseJobDoesRealWork`.

### 27 — The composite action converts a missing scorecard into a warning
**High · ✅ fixed 2026-09-21 · 1h · depends on 01**

`actions/scorecard/action.yml:133-146` downgrades a missing or failed scorecard to `::warning::` and exits 0. Combined with item 01, the GitHub-side path can therefore approve on: no scorecard at all, or a scorecard the engine marked BLOCK. Either way the PR check is green.

**Fixed:** the render step's failure branch now emits `::error::`, writes the diagnostic to the job summary, and `exit 1`. The fallback comment is gone — a PR comment describing an absent scorecard is worthless if the check that should block on it is green. Verified by executing the step body itself, not just by reading it: with `VS_RUN_ID=vrun-deadbeef` and no cache, `bash render.sh` exits **1** and the summary carries `::error::no scorecard cached for run 'vrun-deadbeef'`; after a real `validsim run` the same step exits **0** and renders `**Decision: ✅ APPROVE** — composite **77.0** vs threshold **50.0**`. All nine `run:` blocks in both composite actions also pass `bash -n`.

### 28 — The api container is reachable from the LAN with auth disabled by default
**High · ✅ fixed 2026-09-21 · 30 min · depends on 02**

`docker-compose.yml:34` binds `"8000:8000"` on all interfaces, and `Dockerfile:68` binds `0.0.0.0`, while redis and postgres are deliberately loopback-pinned (`:68`, `:88`). So the one service holding every validation verdict is the one service exposed beyond localhost — and per item 02 its auth is off in the shipped default configuration. Bind api to `127.0.0.1:8000:8000` like its own dependencies, and require an explicit override to expose it.

**Fixed:** the api service now publishes `"127.0.0.1:8000:8000"`, so every port in the stack is loopback-only and exposing the API is a deliberate edit rather than a default. This interacts with items 02 and 05: the healthcheck still passes (it runs *inside* the container, where the host binding is irrelevant), and `SECURITY.md` §6 now states the real posture. Still yours to answer: **was the `0.0.0.0` publish ever load-bearing?** If something outside this machine does reach the API today, loopback-pinning it is a breaking change and whatever fronts it needs writing down. Asserted by `TestExposedSurfaceAndDeps`.

### 29 — There is no way to report a vulnerability to you
**High · ✅ fixed 2026-09-21 · 1h**

`SECURITY.md:27/97/161/170` are all unfilled `.example` placeholders (the file admits this at `:15`), so the documented disclosure channel reaches nobody. Worse for trust: `SECURITY.md:142` pre-classifies API-key findings as operator error — which is precisely the category items 02 and 28 fall into. Fix the contact route before calling the security posture public, and drop the pre-disclaimer.

**Fixed, within the limits of what I can know:** GitHub private vulnerability reporting is now Channel A — the only route documented as working — with a credential-free fallback (open a detail-free public issue asking for private reporting to be enabled). The dead mailbox, the `<TO-BE-PUBLISHED>` PGP fingerprint that does not exist, and both `founders@` / `team@` addresses are gone; URLs are expressed as a path (`<repository URL>/security/advisories/new`) rather than a hardcoded org, because `git remote -v` is **empty** — this repository has never been pushed anywhere, so I have no real owner name to print and inventing one would be another item 17. The pre-disclaimer is replaced with the opposite policy: **unsafe defaults are the product's bug**, citing items 02 and 28 as the findings that proved it. §6 now describes what the code actually does after those fixes.

Two falsehoods surfaced while rewriting and are corrected: §4 advertised a supported **0.3.x** line and a "current stable 0.2.x" when `__version__` is `0.2.0` and `git tag` returns nothing at all — nothing has ever been released; the table now says so. §1 and §7 asserted a staffed rota ("reviewed daily by an on-call engineer"), which is now written as a commitment with no team behind it. `tests/test_delivery_pipeline.py::TestSecurityPolicyReachesSomeone` holds all of this, including a check that every supported-version row is the current or previous minor line, so a future fabricated row fails the suite.

**Still needs you:** enable *Private reporting* on the repo settings (one checkbox, and the only thing standing between a reporter and a working channel), and decide whether you want a real `security@` mailbox at all.

### 30–33 — Quality debt (lower urgency, real cost)

- **30** `tests/test_store_postgres.py:149-190` asserts emitted SQL strings against `_FakeConnection`, with the driver stubbed as `lambda: object()` (`:237`, `:254`) — no behavioural oracle; the only real test is the CI-skipped one (item 09). `tests/test_store_concurrency.py` covers memory + sqlite only, so the non-atomic claim in item 07 has no failing test waiting for it. *6h, with 09.*
  **Hardened 2026-09-25 (◐, not closed).** Two tests added to `tests/test_store_postgres.py`: `test_save_binds_user_values_and_preserves_first_write_clause` pins every scalar run field as a bound parameter (never interpolated into SQL) and explicitly requires the `ON CONFLICT (run_id) DO NOTHING` clause, so the deliberate overwrite-vs-first-write divergence from memory/sqlite can no longer drift silently; `test_close_releases_connection_and_reopens_on_next_query` pins the connection lifecycle across `close()`. Commit/rollback was deliberately NOT asserted — that connection is explicitly autocommit and the fake exposes no transaction API, so a test there would be fiction.

  **Still open:** nothing a fake can settle. SQL validity, constraint and JSONB behaviour, schema migrations, real row results, driver connection behaviour, and genuine first-write-wins transaction semantics all require a live server. CI `postgres` remains the only oracle.
- **31** ~~There is no `tests/conftest.py`~~ — **partly fixed 2026-09-21:** `tests/conftest.py` now exists (created to neutralise the deployment env the production auth guard reads). What remains is the duplication it was meant to cure. **Audited and closed (2026-09-25).**

I inventoried all 74 `@pytest.fixture` definitions across `tests/` and grouped the 12 names that appear more than once. Eleven of the twelve are **nominal, not substantive** duplication, and merging them would be actively wrong:

- `_clean_env` (x10) clears a *different* set of env vars per module by design — jobs clears queue vars, isaac clears backend vars, notify clears SMTP vars. One shared version would under-isolate. The same applies to `store` (x3): `test_api_filters.py` pre-populates three runs while the other two want an empty store, so a shared fixture would break the populated case.
- `_clear_config_env` (x3) is the instructive one: the three fixture *bodies* are byte-identical, but each file defines its own `_CONFIG_ENV` tuple and they are **not** equal — `test_api_health.py` clears 5 vars, the other two clear 7. Consolidating the body would silently stop isolating two variables for the health test. This is exactly the "looks duplicated, is not" case, and it is why I did not act on name-matching alone.
- `shared_store` (x4) has one generator variant and three return-only variants; consolidating saves ~4 lines at the cost of cross-module coupling.

**The one real find.** `make_store` was defined three times: `conftest.py:201` plus local copies at `test_store_delete.py:109` and `test_store_filters.py:122`. The two local copies were byte-identical except the default temp filename (`"delete.db"` vs `"filters.db"`), and every caller invokes `make_store()` with no argument — since each test gets its own `tmp_path`, that filename affects nothing. Both local definitions were deleted so both files inherit the shared fixture, and the imports they orphaned (`Iterator` x2, `Path`) were removed with them. The two files pass (36 tests) and the full suite is unchanged at **1377 passed, 8 skipped**.

The `_scorecard`/`_run`/`_iso` helpers and the 149 private-symbol references noted in the original finding are ordinary test helper functions, not pytest fixtures; they are not "fixture dedup" and I have not touched them. Four test bodies are AST-identical pairs (`test_api_auth_negative.py:206` ≡ `test_api_jobs_wired.py:104`; `:211` ≡ `:99`; `test_api_rate_limit.py:126` ≡ `test_api_security_fixes.py:117`; `test_isaac_worker.py:440` ≡ `test_sim_factory.py:119`). 149 references to private symbols couple the suite to implementation — `test_api_security_fixes.py:195` calls `limiter._sweep()` directly. `test_isaac_batching.py:10` admits its helpers are "deliberately mirrored" — duplicated logic, not tested logic. *4h.*
- **32** Every third-party action is tag-pinned (`@v4`/`@v5`/`@v6`), not SHA-pinned. **Partly addressed 2026-09-21:** `tests/test_delivery_pipeline.py` now parses the workflow and action YAML in the suite CI already runs, and asserts the compose api binding, the release job's unconditional packaging, the coverage floor's consistency, and the scorecard action's fail-closed branch — the four properties whose absence let 24-28 ship. Still missing: no step runs `docker compose config` (so a file that parses as YAML can still be an invalid compose document), nothing exercises a healthcheck at runtime, and the unpinned tags remain a supply-chain hole. Permissions blocks are otherwise clean (`contents: read`, `packages: write` only where the GHCR push needs it, no `pull_request_target`). *~2h remaining.*

**Compose job re-verified statically (2026-09-25).** The delegated agent for this died before producing findings, so I checked it directly. Every assertion the job makes is consistent with `docker-compose.yml`: `VALIDSIM_STORE` defaults to `postgres` via `${VALIDSIM_STORE:-postgres}` (so `store_backend == "postgres"` holds), `VALIDSIM_JOB_QUEUE=redis` is hard-set on both api and worker (so `job_queue_backend == "redis"` holds), the job-level `VALIDSIM_API_KEY` is interpolated into the container (so `auth_enabled is True` and the 401 assertion both hold), and `psql -U validsim -d validsim` matches `POSTGRES_USER`/`POSTGRES_DB`. The job curls `127.0.0.1:8000` from the runner and `api` publishes `"127.0.0.1:8000:8000"` — loopback-bound, which is both what the step needs and the safe binding (item 28). Teardown is `if: always()`.

**What this job will and will not prove.** Draining a job through the Redis worker executes the claim Lua for real, so this is the first genuine execution of the item 07/08 scripts. It also exercises the new fenced terminal write, because `JobWorker._finish` always routes through `update_status(..., lease_epoch=...)`. It does **not** exercise `renew_lease` or `reap_expired`: the CI job uses `"episodes":1` and the default 3600s lease, so the job finishes long before any renewal and no lease ever expires. Those two scripts remain structurally reviewed only. Closing that would mean a compose step with a deliberately short `VALIDSIM_JOB_LEASE_SECONDS` and a worker kill — a real change to the workflow, flagged rather than made blind.

**Risk, not failure:** `timeout-minutes: 20` must cover image build + `--wait-timeout 300` (5 min) + up to 180s drain. Comfortable warm-cache, tight cold-cache. **Unproven:** the job has never run here — no Docker daemon on this machine.
  **Fixed (2026-09-21):** every third-party `uses:` across all workflows and both composite-action definitions is now pinned to the current full commit SHA with its human-readable release in a trailing comment. `ci.yml` validates the rendered Compose model, waits for all four services to become healthy with a bounded timeout, checks the public health payload and protected queue auth, drains an authenticated job through Redis, requires the worker to persist it to PostgreSQL, and always runs `down -v`. `tests/test_delivery_pipeline.py` now guards every external action reference against tag regressions.
- **33** `design-system/validsim/MASTER.md:11` is a generic **"Financial Dashboard"** scaffold and `design-system/validsim/pages/` is empty, so the override mechanism at `MASTER.md:3` resolves nothing and names the wrong path. The FastAPI SPA does honour MASTER's palette, but `validsim/engine/export.py:89-99` uses an unrelated one (`#1a1a1a`, `#1a7f37`, `#b42318`) and a system font stack, so `report --format html` drifts from the stated contract. `examples/nightly-adversarial-sweep.yml:49-51` sets `cache-dependency-path` to two requirements files, which hard-fails `setup-python` for anyone copying the example, and `:18-19` still claims "no pyproject.toml yet". *3h.*

---

## 5. Performance

**You asked for measurable impact metrics. I have almost none, and I'm not going to invent them.**

Measured this session, honestly:

| Metric | Value | Method |
|---|---|---|
| Full suite wall time | **31 s**, exit 0, 1292 tests | `pytest -q`, local `.venv`, warm |
| Test functions / collected | 1020 `def test_` in 78 files → 1292 collected | static grep + `--collect-only` |
| Authenticated `GET /api/v1/health` | ~4 ms | `TestClient`, single samples, not a benchmark |

Not measured, and required before any performance claim is credible:

- **Throughput / latency under concurrency.** No load test exists. Every latency figure in `vault/05 - Execution/KPIs.md` and `MVP Success Metrics.md` is a target, not a measurement.
- **Memory growth for large episode counts.** `build_scorecard` runs a 500-resample bootstrap over one float per episode (`scorecard.py`, `_CI_N_RESAMPLES`) — plausibly the hot path at 100k episodes, unmeasured.
- **Dashboard and list endpoints at scale.** `MemoryStore.list()` and the sqlite/postgres list queries need `EXPLAIN` and timing at 10k runs. `runner.py:173` loops `range(task.episodes)` building results in memory.
- **CI cost.** 31 s locally says nothing about the 3-workflow matrix or the nightly's 60-minute timeout budget.

Two structural items are worth flagging as capacity risks now, before measurement:

**Bootstrap CI is O(n_resamples × n_episodes)** and runs inline on every scorecard build. Fine at 100 episodes; at 100,000 it is 5×10^7 operations inside the request path. *Effort: 4h — profile first, then either cap the resample count by episode count or move it off the critical path.*

**In-memory store is unbounded.** No eviction, no page limit at the store layer. A long-lived api process accumulates every run forever. *Effort: 2h — but 21 may make this moot.*

I'd rather you read that as "performance is unmeasured" than as "performance is fine."

---

## 5b. Session 2 — verified defects found by re-audit and fixed (2026-09-25)

A second read-and-execute pass over the same tree, run after the items above
were closed, found **nine further defects that had survived the first pass** —
including two that made the entire test suite unrunnable. Every entry below was
reproduced by an executed command *before* the fix and pinned by a test after
it. None were inferred from reading alone.

**The two that broke everything else:**

| # | Title | Sev | Status | Why it was missed |
|---|---|---|---|---|
| 34 | Bench conftest collides with `pytest-benchmark`, aborting the whole run | **Critical** | ✅ 2026-09-25 | Not a code defect — an *environment* collision. `pytest_addoption` runs at argument-parse time, so `--benchmark-save` being registered twice raised `argparse.ArgumentError` and killed collection for all ~1500 tests, not just benchmarks. Only visible on a machine where the plugin is installed. |
| 35 | `compare --latest` compares a run against itself and reports "0 regressions" | **Critical** | ✅ 2026-09-25 | `--baseline-latest` and `--candidate-latest` both read the same cache, so both resolve to one run. Every delta is 0.0, the test is degenerate, and the command printed a confident all-clear (p-value 1.000) for a comparison that never happened. A false green on the one command whose output is read as a regression verdict. |

**Silent data loss and false-500s:**

| # | Title | Sev | Status | Why it was missed |
|---|---|---|---|---|
| 36 | Blank `VALIDSIM_SQLITE_PATH` opens a throwaway database | **Critical** | ✅ 2026-09-25 | `os.environ.get` returns `""` for an empty value, and `sqlite3.connect("")` opens a *private temporary* DB discarded on close. A deployment with the env var set-but-empty lost every run while appearing healthy — the worst failure mode for a validation store. |
| 37 | Non-ASCII `VALIDSIM_API_KEY` turns every authenticated request into a 500 | **High** | ✅ 2026-09-25 | `secrets.compare_digest` on `str` raises `TypeError` for non-ASCII. A key containing an accented character made auth fail *open*-looking (500) instead of *closed* (401). Reachable whenever the key is not pure ASCII. |

**Correctness and availability:**

| # | Title | Sev | Status | Why it was missed |
|---|---|---|---|---|
| 38 | Queue `max_depth` dead-locks after `max_depth` lifetime jobs | **High** | ✅ 2026-09-25 | The cap counted **all-time retained records**, not pending work. After completing every job it was ever given, a long-lived queue refused all further work forever. Memory and Redis both affected; the Redis Lua script checked the never-popped index list while the reaper already checked the ready list. |
| 39 | `detect_anomalies` raises `ValueError` on impossible rates | **High** | ✅ 2026-09-25 | A failure count exceeding a run's own episode total drives the rate above 1.0, making the binomial variance term negative and `math.sqrt` throw — breaking the "degrade, never raise mid-report" contract the engine depends on. |
| 40 | `delete --latest` resolves from the cache but deletes from the store | **High** | ✅ 2026-09-25 | Against a durable store the local cache is a convenience with no reason to be in sync. Fails when the cache is absent; worse, when stale, it picks an id the store never had and exits 2 having deleted nothing. |
| 41 | Dashboard `threshold` knob was silently discarded | **High** | ✅ 2026-09-25 | The web UI posted `threshold` with the comment *"the v0 API ignores unknown fields"*, and `ValidationRequest` had no such field — so every run scored against 85.0 regardless of input. The knob looked live. |
| 42 | PDF export left two Paragraph interpolations unescaped | **Medium** | ✅ 2026-09-25 | ReportLab parses XML-ish markup in `Paragraph`; `deploy_decision` and the taxonomy count column were still raw after the first pass, so a crafted value could break rendering or be mistaken for a real verdict. |

**Also corrected:** the run/approval/block metrics were declared `# TYPE counter`
while being freely *decreasing* (runs are deletable) — a `gauge` is now declared,
which is the part that is load-bearing. The `_total` suffix is deliberately
**kept** despite the gauge type: renaming to `_stored` is more idiomatic but
touches 50 references across dashboards and alerts, which is an observability
change to coordinate rather than a bug fix.

**Not fixed, deliberately:**

- **Webhook HMAC is replayable** (body-only signature, no timestamp/nonce). The
  format is a documented wire contract across `SECURITY.md`, `README.md`,
  `docs/runbook.md` and the vault. Changing it silently breaks every deployed
  receiver; it needs a versioned scheme and a migration note.
- **30 of 100 composite points were unconditional — RESOLVED 2026-09-29 (scoring).**
  `run_validation` (`sim/runner.py:173-185`) passes one `task.randomization`
  level to every episode, so all episodes land in a single group and robustness
  was *always* exactly 100.0; regression was 100 whenever no baseline was
  supplied (the CLI/worker default). That flat 30.0 is what let item 44 through.
  **Fixed:** an unmeasured component now **abstains** — its weight leaves the
  composite denominator instead of contributing a fabricated 100
  (`docs/adr/0002`, amended). Measured effect: a run where every episode failed
  went from `60.0 → APPROVE at threshold 60` to `42.86 → BLOCK at any
  threshold`; a flawless run still reaches 100.0 and the ordering is monotonic.
  **Still open:** robustness remains *unmeasurable* (one group), so the axis is
  still inert for `Pricing Tiers` — more episodes cannot move it. Closing that
  means varying `randomization_level` across episodes in `sim/runner.py`, which
  is a product decision — see §7.1.
- **`project_config.py` is unreachable** from any production path. Kept and
  marked as tested groundwork rather than deleted, since its key-classification
  table is the drift guard for unclassified `VALIDSIM_*` keys and its redaction
  path is a security boundary.
- **`_utc_now()` UTC invariant** (which the Redis reaper's string comparison
  silently depends on) is now documented at the function, having been verified
  as safe: lexical and chronological order only diverge when UTC offsets differ,
  and every queue timestamp is minted in UTC.

---

### 5c. Item 44 — the deploy gate could certify a total task failure (Critical, fixed 2026-09-26)

The most serious defect found across both audits. The composite number itself
is *not* the bug — the gate's verdict is.

**Reproduction (executed, RED test first):** 100 episodes, every one failing,
safety perfect, single randomization group, no baseline:

```
success_rate = 0.0   safety = 100.0   robustness = 100.0   regression = 100.0
composite    = 60.0  (0.3*100 + 0.2*100 + 0.1*100)
threshold=60.0  ->  APPROVE
threshold=0.0   ->  APPROVE
```

**Root cause.** Two of the four weighted components are *unconditional* on a
normal production run:

- `run_validation` (`validsim/sim/runner.py:173-185`) applies the single
  `task.randomization` level to every episode, so all episodes land in one
  group. `_robustness_score` returns `100.0` whenever `len(rates) < 2`
  (`scorecard.py:63-64`) — robustness is a constant 20.0 points.
- `_regression_component` returns `100.0` whenever no baseline is supplied
  (`scorecard.py:71-72`), which is the default for every CLI and worker run.

That is a flat **30.0 composite points measuring nothing about task success**.
The old evidence rule checked only episode *count*
(`total_episodes >= task.episodes`), never outcome — so a 0%-success run
floored at 60.0 and any threshold at or below 60 approved it.

**Fix.** The evidence rule now also requires positive task success, which is
orthogonal to where the threshold sits:

```python
sufficient_evidence = (
    evaluation.total_episodes >= task.episodes > 0
    and evaluation.success_count > 0
)
```

**Verification.** Two tests added to `tests/test_scorecard.py`, one of which is
the control that proves the fix is not an accident of threshold arithmetic:

- `test_total_task_failure_can_never_approve` — asserts the composite is still
  60.0 (a true description of the scoring model) but the verdict is now BLOCK.
- `test_zero_success_rate_blocks_regardless_of_threshold` — sweeps thresholds
  0.0/30.0/60.0/85.0/100.0 and requires BLOCK at every one, proving the
  protection comes from the evidence rule and not from a threshold
  interaction.

**Control against over-correction.** A real 100-episode run (success 0.71,
composite 83.33) still gates exactly as before — BLOCK at the default 85.0,
APPROVE at threshold 10. The gate can still approve; it just can no longer
approve a run that achieved nothing.

> [!note] What is still open
> The gate no longer certifies a total failure, but robustness remains a
> constant 20.0 points because the runner never varies the randomization level.
> Making it measure something real is a scoring-policy change that will move
> existing verdict distributions — see §7.1.

---

## 7. Product decisions this work cannot make for you

Each of these changes what a number *means*, so the numbers move and existing
verdicts may flip. None is a bug with one correct answer.

1. **Sufficient-evidence policy.** Should a run with zero adversarial coverage
   (or 100% task failure) be able to reach `APPROVE`? Options: vary
   randomization levels so robustness measures something real; or have
   unmeasured components report "not measured" as 0 rather than 100. Both move
   the score distribution.
2. **Threshold defaults per deployment.** Now that `threshold` is honoured
   end-to-end, decide the shipped default (85.0 is inherited from the early
   prototype) and whether the floor should differ between CI and production.
3. **Observability rename.** `validsim_runs_total` and friends are now `gauge`s
   behind counter-shaped names. Renaming to `*_stored` is the clean fix but
   breaks existing Grafana boards — coordinate or leave as-is.
4. **Webhook signature v2.** Replay protection needs a timestamp/nonce and a
   versioned header so old and new receivers can coexist during rollout.
5. **Postgres write semantics.** `ON CONFLICT DO NOTHING` (append-only verdict
   log) is intentional and documented, but differs from memory/SQLite which
   overwrite. Pick one contract deliberately, or document the divergence as
   supported.
6. **Notification egress.** `WebhookDispatcher`/`EmailNotifier` are a library
   only, while docs mark the feature `[x]`. Wiring dispatch into the run path
   changes runtime egress behaviour and needs an owner.

---

## 8. Sequencing

**Day 1 (~1 day).** 01 → verify by re-running the forgery repro. 02 + 05 + 28 together, same file and same auth decision. 03 + 04 + 24 together — one compose line, one requirements line, one script line; 24 must ship with 03 or the fixed queue becomes a crash-loop. Each lands with a RED test first, and the RED test must be written so it does not read its own expectation from the value under test (see item 17's `test_cli.py:90-97` failure mode). All of these are individually small; their common property is that each makes a documented guarantee actually true.

**Day 1-2, parallel, do not skip: the false-green cluster.** 25, 26, 27 and 29 are what make every other claim in this repo trustworthy. A suite of 1292 tests is worth nothing if CI enforces no coverage floor, a release job passes without releasing, a scorecard action warns-and-passes, and no one can report a vulnerability to you. Fix the gate (01) and then fix the machinery that reports on it.

**Day 2 (~2 days).** 06 (needs your fail-closed decision made concrete), 09, 11/12/13/22 doc corrections, 18 single source of truth for counts, 19/20 hygiene pending your delete approval.

**Week 2 (~3 days).** 07 + 08 queue correctness — this needs a short design you sign off before I write it, because a lease changes worker semantics. 10 rollback runbook. 14, 15, 16, 21.

**Before any external send, non-negotiable.** 17. If a YC or NVIDIA submission is imminent, tell me and I will do the vault and fundraising pages first — the code is not on fire in a way that stops a pitch, but item 17 is the one that cannot be fixed after the fact.

**Deferred deliberately.** The security-scan skill has not been run over the whole tree; the items above came from reading, not from a scanner. Worth one pass before you call this done.

---

## 9. Standing constraints on this work

No `git push` or any remote operation without a fresh ask per push — nothing has left this machine. No `git add -A`/`git add .`; explicit paths only, since that habit is what put `project.docx` and the `_*.xml` artifacts into history. Test artifacts stay out of the repo (no `--junitxml`, no output redirection into the working tree). The 34 root artifacts and `project.docx` remain undeleted and un-`git rm`'d because you haven't authorised it. Nothing destructive in this catalog is self-authorising; items 17 and 19 in particular need your judgment about what was already sent and what is safe to remove.
