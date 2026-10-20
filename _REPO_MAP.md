# ValidSim — Repository Map and Open Work

*Compiled 2026-10-20 from 19 independent read-only reports over the ValidSim tree at `D:/SIM-TO-REAL`, HEAD `2ffeb6c` (branch `main`). Every claim below is attributed to the chunk that produced it; the load-bearing ones were re-verified against the live tree during compilation.*

---

## 1. What this system is

ValidSim is a Python platform that answers one question for robot-foundation-model teams: *is this checkpoint safe to deploy?* It takes a checkpoint id plus a task spec, runs a seeded batch of simulated episodes against a pluggable backend (a deterministic in-process mock, or a remote Isaac Sim/Isaac Lab GPU worker over HTTP), scores the resulting episodes into a composite, and publishes a signed scorecard with an `APPROVE`/`BLOCK` verdict that a CI pipeline can gate a deploy on. The core claim is narrower and stronger than "it runs simulations": the verdict must be *reproducible* (seeded, `stable_seed(checkpoint_id, task_id)`), *conservative* (unmeasured components abstain rather than scoring 100), *auditable* (a persisted run record with a chain-of-custody SHA-256 of the checkpoint), and *actionable* through several surfaces at once — a `validsim gate` CLI command with CI-meaningful exit codes, a REST API, a JSON/Prometheus metrics scrape, a GitHub Action, an operator dashboard, and a webhook notification. It is sold to ML/robotics engineers who already hold a policy checkpoint and lack a repeatable way to know whether it holds up under perturbation. The repository is unusual in that it contains not just the platform but the *argument* for the platform: an Obsidian strategy vault, a 15-report audit tree, two issue catalogs, six ADRs, a marketing site, and a deployment guide — all of which are, as section 5 shows, substantially out of date with the code they describe.

---

## 2. Architecture

### 2.1 Layer map

| Layer | Responsibility | Owning chunk |
|---|---|---|
| **Entrypoints** | HTTP (`validsim/api/main.py`, FastAPI), CLI (`validsim/cli.py`, Typer), async worker (`validsim/jobs/worker.py`), and the GitHub composite actions (`actions/validate`, `actions/scorecard`) | `read:C-api`, `read:D-jobs-cli`, `read:M-infra` |
| **Orchestration** | `validsim/engine/pipeline.py:run_and_score` — the single shared path used by API, CLI and worker: seed → scenarios → episodes → evaluate/safety → optional baseline compare → scorecard → persist → notify | `read:A-engine` |
| **Simulation runtime** | `validsim/sim/runner.py` (`run_validation`, `MockIsaacBackend`, `stable_seed`), `validsim/sim/isaac_worker.py` (HTTP client), `validsim/sim/shadow.py` (promotion harness, unwired) | `read:E1-sim-core`, `read:E2-sim-llm` |
| **Scenario generation** | `validsim/scenarios/generator.py` (rule-based, 12-category taxonomy), `llm_generator.py` (LLM-backed, **unwired**) | `read:E1-sim-core`, `read:E2-sim-llm` |
| **Scoring engine** | `validsim/engine/{evaluation,safety,regression,scorecard,stats}.py` — `build_scorecard` is the composite/verdict authority; `stats.py` supplies bootstrap CI and the permutation test | `read:A-engine` |
| **Secondary analytics** | `validsim/engine/{trends,anomaly,benchmark}.py` — fully implemented, **no production caller** | `read:A-engine` |
| **Rendering** | `validsim/engine/export.py` (Markdown/HTML), `validsim/engine/pdf.py` (reportlab) | `read:A-engine`, `read:I2-tests-misc` |
| **Store** | `validsim/store/{memory,sqlite,postgres}.py` behind one `ValidationStore` contract; `_frozen.py` for read-path immutability; `__init__.py` env-driven backend factory | `read:B-store-config` |
| **Job queue** | `validsim/jobs/{queue,models,router,worker}.py` — in-memory + Redis (Lua, lease-epoch fenced) backends, SSE progress | `read:D-jobs-cli`, `read:I3-tests-rest` |
| **Notification** | `validsim/notify/{dispatcher,config,egress,email}.py` — webhook dispatch (wired), SMTP email (**unwired**) | `read:F-notify` |
| **Config & logging** | `validsim/config.py` (pydantic v2 models), `project_config.py` / `config_loader.py` (file config, **unwired**), `validsim/_log.py` (JSON logging + redaction) | `read:B-store-config` |
| **API surface** | `validsim/api/{main,dashboard,metrics,store_stats}.py` | `read:C-api` |
| **Dashboard SPA** | `validsim/web/{app.js,view-core.js,index.html,styles.css}` — vanilla JS, no build step | `read:G-web` |
| **Test suite** | `tests/` — 3,254 collected; three meta-gates (`test_wiring_reachability_agent.py`, `test_delivery_pipeline.py`, `test_sim_contract_audit_agent.py`) that police the tree itself | `read:H-tests-backend`, `read:I1/I2/I3-tests-*` |
| **CI/CD & packaging** | `.github/workflows/{ci,nightly,release}.yml`, `pyproject.toml`, `Dockerfile`, `docker-compose{,.prod}.yml`, `Makefile`, `.env.example` | `read:M-infra` |
| **Docs** | `docs/` (API reference, runbook, ADRs, catalogs, deployment guide), root `*.md` (README, ARCHITECTURE, SECURITY, CHANGELOG, CURRENT_STATUS, VALIDATION_SCHEMA), `audit/` (15 reports), `vault/` (67 Obsidian notes) | `read:J1-docs`, `read:N-root-docs`, `read:J2-audit`, `read:K-vault` |
| **Marketing / web** | `site/` (Astro 5 static site), `marketing/index.html` (legacy standalone page), `site/public/` (run video + poster), `design-system/validsim/MASTER.md` | `read:L1-site-src`, `read:L2-site-public` |

### 2.2 End-to-end data flow

```
scenario                simulation                  scoring
──────────────────      ──────────────────          ──────────────────────────────
ScenarioGenerator       run_validation(task,        evaluate(episodes)  -> EvaluationResult
  (pipeline.py:143)       backend, scenarios,        compute_safety(episodes) -> SafetyResult
  12 categories,           seed)  (runner.py:610)   compare(cur, baseline) -> RegressionReport
  rule-based only          │  nominal episodes         build_scorecard(...) -> Scorecard
                           │  seed+i                   (scorecard.py:436-570)
                           │  adversarial episodes      • composite = Σ w·measured / Σ w
                           │  seed+episodes+j           • 0.4 success / 0.3 safety
                           ▼                            / 0.2 robustness / 0.1 regression
                         MockIsaacBackend              • unmeasured components abstain
                           OR IsaacWorkerBackend        • evidence floor ≥ 10 episodes,
                           (POST /episodes/run)           success_count > 0
                                                        • adversarial floor 0.60 (n ≥ 30,
                                                          one-sided binomial)
                                                        • block_reasons[] + APPROVE/BLOCK
scorecard                             storage
─────────────────────────────        ─────────────────────────────────────
Scorecard.to_dict()  (20 keys)       ValidationStore.save / save_exists
  ↓  ↓  ↓  ↓                            (append-only, first-write-wins)
API      CLI      PDF/MD/HTML          memory: dict + freeze()
  ↓       ↓        ↓                  sqlite: 1 row, indexed cols + JSON blobs
read     read     GET .../scorecard.   postgres: JSONB cols, ADD COLUMN IF NOT EXISTS
back     back     .pdf|.md|.html      ON CONFLICT (run_id) DO NOTHING
```

**Delivery:**

- **API** — `POST /api/v1/validations` (the only `async def` route; admission gate → `run_in_threadpool` → `run_and_score`); `GET /api/v1/validations` (paginated); `GET .../scorecard`, `.../failures`, `.../compare`; `DELETE /api/v1/validations/{run_id}` (204); `GET /api/v1/models`, `/models/{id}/history`, `/regressions`; `GET /api/v1/dashboard/history|summary`; `GET /api/v1/health` (public); `GET /metrics` + `/api/v1/metrics` (Prometheus); `POST /api/v1/jobs`, `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/status`, `/jobs/{id}/events` (SSE). Auth: `X-API-Key` via `Depends(require_api_key)`, merged per route at include time; `PUBLIC_PATHS = {/api/v1/health}`.
- **CLI** — `validsim run|validate|status|scorecard|report|gate|delete|models|version|health|compare|jobs|job enqueue|worker`. `gate` reads the **durable store** (not the cache) and exits 0 APPROVE / 1 BLOCK / 2 no-verdict; `status|scorecard|report` read a local JSON cache; `delete` is store-first with cache fallback.
- **Dashboard** — `validsim/web/app.js` polls `/api/v1/health`, `/api/v1/dashboard/history`, `/api/v1/dashboard/summary`, `/api/v1/jobs`; POSTs `/api/v1/validations` and `/api/v1/jobs`. `view-core.js` owns all formatting/coercion so it is testable under Node.
- **Webhook** — `pipeline.py:206` calls `notify_run_completion(scorecard)` on the **new-save path only** (a retry must not re-notify). Severity from `severity_from_scorecard`; dry-run unless `VALIDSIM_WEBHOOKS_LIVE=1`; HMAC-SHA256 in `X-ValidSim-Signature`; 5.0 s whole-fan-out budget.
- **GitHub** — `actions/validate/action.yml` runs the CLI and exports `run-id`/`score`; `actions/scorecard/action.yml` posts the scorecard to a PR comment, fail-closed.

### 2.3 Dependency graph between chunks

The coupling is **inverted from the chunk boundaries**: the runtime dependency graph does not match the reading chunks, because the chunks split by *file kind* while the code splits by *call*.

**Load-bearing (a change here breaks the product):**

- `engine/pipeline.py` is the hub. It is imported by `api/main.py`, `cli.py` and `jobs/worker.py` alike — those three entrypoints share one scoring path by design, which is what keeps API/CLI/worker verdicts identical.
- `engine/scorecard.py` → `store/*` (Scorecard is the persisted artifact), `notify/dispatcher.py` (payload + `block_reasons` → severity), `engine/export.py` + `engine/pdf.py` (renderers), and the dashboard (`view-core.js` mirrors its threshold and weights).
- `sim/runner.py` → `scenarios/generator.py` → `engine/evaluation.py` / `engine/safety.py`. The runner is the root cause of the unmeasurable robustness component (§4, P1-3) and both readers treat `EpisodeResult` as the universal currency.
- `store/memory.py` defines the `ValidationStore` contract that `sqlite.py` and `postgres.py` subclass, plus `reconstruct_kwargs` / `rebind_row_identity` / `_frozen.freeze` — the shared machinery that the SQL backends selectively fail to use (see P0-9).
- `api/main.py` owns everything the API docs describe: `create_app`, the auth gate, the rate limiter, the admission gate, the CORS policy, and the health payload. `api/dashboard.py`, `api/metrics.py` and `api/store_stats.py` all read `app.state.store`.
- `jobs/queue.py` + `jobs/worker.py` are a closed pair; the Redis backend's Lua scripts must stay byte-consistent with the in-memory backend's semantics (a divergence is already live: §4, P1-8).

**Incidental (a change here costs a doc edit, not a break):**

- `docs/`, `audit/`, `vault/`, `README.md`, `SECURITY.md` and the site import nothing and are imported by nothing. They are *coupled by claim*, not by call — which is exactly why they have drifted (§5).
- `project_config.py` / `config_loader.py` are referenced by `notify/config.py` and `api/main.py` in docstrings only; no production importer. Same for `engine/{trends,anomaly,benchmark}.py`, `notify/email.py`, `sim/shadow.py`, `scenarios/llm_generator.py`, `api/store_stats.py:run_summaries`.
- `validsim/web/view-core.js` re-implements `engine/trends.py:compute_trends` in JavaScript and the parity test only checks six of the eight fields (P1-11).

---

## 3. Chunk index

| Chunk | What it owns | Files read | Public surface in one line | Confidence |
|---|---|---|---|---|
| `read:A-engine` | Engine: scoring, evaluation, safety, regression, stats, export, PDF, pipeline | 14 | `validsim.engine` facade + `build_scorecard`/`run_and_score`/`evaluate`/`compute_safety`/`compare`/`scorecard_to_markdown`/`scorecard_to_html`/`scorecard_pdf_bytes` | High |
| `read:B-store-config` | Three store backends, `_frozen`, config models, `project_config`, `_log` | 9 | `create_store()`, `ValidationStore` contract, `RobotSpec`/`TaskConfig`/`ValidationRequest`, `configure_logging`/`get_logger` | High |
| `read:C-api` | FastAPI app, dashboard router, metrics, `store_stats` | 5 | `create_app(store)`, `app`, `PUBLIC_PATHS`, `/api/v1/*` routes, `mount_dashboard` | High |
| `read:D-jobs-cli` | Job queue, worker, router, models, Typer CLI | 6 | `validsim run|gate|jobs|worker|…`, `JobQueue`/`RedisJobQueue`, `JobWorker`, `JobStatus` (5 states) | High |
| `read:E1-sim-core` | `sim/runner.py`, `scenarios/generator.py`, `sim/shadow.py` | 5 | `create_backend()`, `run_validation()`, `EpisodeResult`, `ScenarioGenerator`, `ShadowRunner` (unwired) | High |
| `read:E2-sim-llm` | `sim/isaac_worker.py`, `scenarios/llm_generator.py` | 2 | `IsaacWorkerBackend`, `OpenAICompatibleProvider`, `create_scenario_generator` (unwired) | High |
| `read:F-notify` | `notify/{dispatcher,config,egress,email}.py` | 5 | `notify_run_completion`, `WebhookDispatcher`, `NotifyConfig`/`HookSpec`, `url_rejection_reason` | High |
| `read:G-web` | Dashboard SPA (`app.js`, `view-core.js`, `index.html`, `styles.css`) | 4 | `window.ValidSimView` (13 exports) + the app's DOM/API contract | High |
| `read:H-tests-backend` | Backend-facing tests: API, store, jobs, notify | 64 | The executable spec of the store/auth/queue/egress contracts | High |
| `read:I1-tests-cli-web` | CLI, dashboard, marketing/site, bench tests | 28 | CLI exit-code contract, `view-core` harness, `tests/bench/conftest.py` | High |
| `read:I2-tests-misc` | Engine/scenario/config/sim/security meta-gate tests | 44 | The three meta-gates that police dead code, false-green CI, wire contract | High |
| `read:I3-tests-rest` | API/jobs/notify/store tests (the "rest" of `tests/`) | 75 | The executable spec of API, queue, notify, store contracts | High |
| `read:J1-docs` | `docs/` tree (18 files) | 18 | API reference, runbook, ADRs, two catalogs, deployment guide | High |
| `read:J2-audit` | `audit/` tree (15 reports) | 15 | Point-in-time audit findings, consolidated by `AUDIT_CATALOG_2026-09-26.md` | High (as a snapshot) |
| `read:K-vault` | `vault/` — 67 Obsidian notes | 67 | Human-only; strategy, fundraising, engineering defect register | High (as a snapshot) |
| `read:L1-site-src` | `site/` build config (3 files) | 3 | `astro.config.mjs`, `package.json`, `tsconfig.json` | High |
| `read:L2-site-public` | `marketing/index.html`, `site/public/*`, design-system, site source | 26 | Public web assets and the marketing surface | High |
| `read:M-infra` | CI, packaging, Docker, compose, Makefile, env, examples | 21 | 3 workflows, 2 composite actions, 2 compose files, `pyproject.toml` | High |
| `read:N-root-docs` | Root `*.md` (README, ARCHITECTURE, SECURITY, CHANGELOG, CURRENT_STATUS, VALIDATION_SCHEMA, …) | 15 | The public and contributor-facing documentation contract | High |

---

## 4. Open work, ranked

The 234 reported items were deduplicated against each other and against the code. Merged entries keep the strongest evidence. Items that were restatements of how the system works, pure speculation, or already-closed findings re-reported from a stale doc were cut. **40 items survive.**

### P0 — blocks correctness, security, or deployment

**P0-1 · Packaged wheel/sdist omit the dashboard SPA, so `import validsim.api.main` fails**
- *Area:* packaging / API — `read:M-infra`, `read:C-api`
- *Evidence:* `pyproject.toml:48` declares `packages = { find = { include = ["validsim*"] } }` and no `package-data` / `include-package-data` / `MANIFEST.in`. `validsim/web/` has four files and no `__init__.py`; `validsim/api/dashboard.py:22-24` resolves `WEB_DIR` and `dashboard.py:73` mounts `StaticFiles(directory=str(WEB_DIR))`, which raises `RuntimeError` at import. `validsim/api/main.py:1430` does `app = create_app()` at module scope, so the failure is at **import**, not app creation. Verified during compilation: a freshly built wheel contains 51 entries and **zero** under `validsim/web/`.
- *Impact:* `pip install validsim` produces an app that cannot be imported. `uvicorn validsim.api.main:app` (Makefile, Dockerfile, README:29) and `pip install .` (actions/validate/action.yml:113) are broken for any non-editable install. The CLI-only path still works, so a CLI-only CI is green.
- *Effort:* ~1 h — add `[tool.setuptools.package-data]` + one wheel test.

**P0-2 · `make smoke` and the shipped GitHub Action fail on a nested SQLite path**
- *Area:* store / CI — `read:M-infra`
- *Evidence:* `validsim/store/sqlite.py:261` calls `sqlite3.connect(self._db_path)` with no parent-directory creation (contrast `validsim/cli.py:168`, which *does* `mkdir(parents=True, exist_ok=True)` for the scorecard cache). `Makefile:59` sets `SMOKE_SQLITE_PATH ?= .validsim/smoke.db`; `actions/validate/action.yml:101` pins `VALIDSIM_SQLITE_PATH=${RUNNER_TEMP}/validsim/runs.db` and no step creates `${RUNNER_TEMP}/validsim`; `examples/nightly-adversarial-sweep.yml:43` uses `${{ github.workspace }}/.validsim/runs.db`. **Reproduced during compilation:** `OperationalError: unable to open database file`.
- *Impact:* The first-run path in `make smoke`, the composite CI action and the nightly example all break on a fresh clone. The action's own fallback to `pip install -r requirements.txt` does not help — the failure is at store open, not install.
- *Effort:* ~15 min — one `mkdir` in the SQLite open path.

**P0-3 · `.env.example` ships `VALIDSIM_CACHE_FILE=` empty, crashing `validsim run`**
- *Area:* configuration / operator experience — `read:M-infra`
- *Evidence:* `.env.example:207` ships `VALIDSIM_CACHE_FILE=` (empty). `validsim/cli.py:154` does `Path(os.environ.get(_CACHE_ENV, Path(".validsim")/"scorecards.json"))`; `os.environ.get` returns `""` (not the default) for a set-but-empty var, so the path becomes the empty string. **Reproduced during compilation:** `PermissionError: [Errno 13] Permission denied: '.'`. The file's own header (lines 6-11) tells the operator to copy it to `.env`.
- *Impact:* The documented first-run path — copy `.env.example` to `.env`, source it, run — fails immediately. The sibling case is already handled correctly in `validsim/store/__init__.py:69` (blank means unset), so the fix pattern exists.
- *Effort:* ~15 min.

**P0-4 · `docker-compose.prod.yml` worker healthcheck parses the Redis URL wrongly — it can never pass**
- *Area:* infrastructure / deployment — `read:M-infra`
- *Evidence:* `docker-compose.prod.yml:129` splits the DSN with `.split('/')` and no index; the local `docker-compose.yml:157` uses `.split('/')[0]`. Parsing `redis://:SECRETPASS@redis:6379/0` with the prod expression yields `u = ['redis:6379','0']`, so `cred = ['redis','6379']`, `h = '0'`, `p = ''` — the host resolves to the literal string `"0"` and the script sends `AUTH redis 6379` to it.
- *Impact:* Every prod worker reports unhealthy. `docker compose -f docker-compose.prod.yml up --wait` hangs until timeout, and CI's compose job exercises only the *dev* file, so nothing catches it.
- *Effort:* ~15 min — one-line parse fix plus a `docker compose config` assertion.

**P0-5 · The `validsim` console script does not exist in the Docker image**
- *Area:* packaging / deployment — `read:M-infra`
- *Evidence:* `Dockerfile:25-26` installs only `requirements.txt`; `Dockerfile:57` COPYs the `validsim/` source tree. There is no `pip install .`. `pyproject.toml:44` declares the console script `validsim = "validsim.cli:app"`, but console scripts exist only after a package install. `docker-compose.prod.yml:110` therefore has to use `command: ["validsim", "worker"]` — which cannot start.
- *Impact:* The production compose file's worker service cannot boot. The local file's `command: ["python", "-m", "validsim.cli", "worker"]` (docker-compose.yml:127) works, which is why this is invisible until prod.
- *Effort:* ~30 min — add `pip install --no-deps .` to the image, or change the compose command to the module form.

**P0-6 · The dashboard's Failure Taxonomy panel throws on every run, and takes the PDF download with it**
- *Area:* web — `read:G-web`
- *Evidence:* `view-core.js:263-273` returns `taxonomyRows` as **row objects** (`{mode, rawMode, count, share}`); `app.js:598-632` `renderTaxonomy` still destructures **pairs** — `entries.reduce((sum, pair) => sum + pair[1], 0)` and `pair[0].replaceAll("_", " ")`. **Verified during compilation:** `app.js` line 614 reads `pair[1]`, line 621 reads `pair[0].replaceAll`; on a row object `pair[0]` is `undefined`. Introduced in commit `98cc723`, which flipped the call site to `renderTaxonomy(view.taxonomyRows)` while leaving the function body on the old shape.
- *Impact:* The throw aborts `renderScorecard` at `app.js:451`, so the run is reported as "Validation failed: Cannot read properties of undefined (reading 'replaceAll')" and the operator never sees the taxonomy chart, the taxonomy table, or the block reasons for the run they just triggered. Because the wrapper at `app.js:1222-1233` is downstream of the throw, `#download-pdf-btn` stays at its markup default `disabled` — **the PDF export is unreachable from the primary run flow**. Separately, `app.js:1188` disables the focused button during download and never re-enables it on the success path (`app.js:1211-1212` resets only the label), so a second download is impossible and focus is dropped to `<body>` — the exact WCAG 2.4.3 failure the file itself documents at `app.js:820-835`.
- *Effort:* ~1 h for the shape mismatch; the disabled-button and focus issues are ~30 min more. A test that feeds `view-core.taxonomyRows` into `renderTaxonomy` would have caught it (nothing does today).

**P0-7 · Every scorecard renderer omits `block_reasons`, and the Markdown/PDF verdict sentence can be factually false**
- *Area:* engine / export / PDF — `read:A-engine`, `read:I3-tests-rest`
- *Evidence:* `validsim/engine/export.py:108-109` computes `relation = "meets or exceeds" if sc.deploy_decision == "APPROVE" else "is below"`, printed at `export.py:133`. `grep block_reasons validsim/engine/export.py validsim/engine/pdf.py` returns **nothing** — no renderer reads the reasons that `scorecard.py:549/555/561` computes. A 20-episode run against a 100-episode task scores `composite_score=100.0`, `threshold=85.0`, `deploy_decision='BLOCK'` (reason: insufficient evidence) and the Markdown reads *"Composite score **100.0** is below the deploy threshold of **85.0**"* — self-contradictory on its face. `pdf.py:145-147` carries the same construction. `actions/scorecard/action.yml` (the CI job summary) also omits the reasons, so a CI-visible BLOCK carries none.
- *Impact:* The three human-facing artifacts publish a BLOCK with no reason, and one of them states a falsehood about why. The CLI prints the reasons, so the two surfaces disagree about the same run.
- *Effort:* ~2 h — render `block_reasons` in all three renderers; fix the verdict sentence to branch on the reason rather than the score.

**P0-8 · Chain-of-custody SHA-256 is dropped from the HTML card and the PDF**
- *Area:* engine / export / PDF — `read:A-engine`
- *Evidence:* `export.py:125-126` renders `checkpoint_sha256` for Markdown only. `export.py:221-234` (`scorecard_to_html`) and `pdf.py:75-233` (`_build_story`) never read it. Verified by execution: the 64-char digest is in the Markdown, absent from the HTML string and absent from the extracted PDF text. `grep -c sha256 tests/test_export.py tests/test_pdf.py` returns 0 for both.
- *Impact:* The digest is the artifact that makes a verdict attributable to a specific checkpoint. It is dropped on exactly the two surfaces a recipient reads (both are the email/distribution artifacts `validsim/notify/email.py:86-98` delegates to).
- *Effort:* ~1 h.

**P0-9 · SQL backends crash reading a row whose detail blob has a field this build does not know**
- *Area:* store — `read:B-store-config`
- *Evidence:* `validsim/store/sqlite.py:132` uses `_rebuild(row, "evaluation_json", EvaluationResult)`; `validsim/store/postgres.py:345-351` uses `EvaluationResult(**evaluation_data)` / `SafetyResult(**safety_data)`. Both use a raw `cls(**data)` instead of the shared `reconstruct_kwargs` — which **is** used for the scorecard (`sqlite.py:101`, `postgres.py:197`) and is documented at `validsim/store/memory.py:49-93` as the mechanism that lets a row written before a field existed still load. The module docstring at `memory.py:18-22` declares "Reconstruct, never filter" as a rule inherited by all backends. Reproduced: a blob with an extra key raises `TypeError: EvaluationResult.__init__() got an unexpected keyword argument 'future_field'` in **both** SQLite and Postgres.
- *Impact:* Adding any field to `EvaluationResult` or `SafetyResult` makes every previously-written row unreadable in the two production backends. `tests/test_store_parity.py` only round-trips fields that exist today, so the forward-compatibility gap is uncovered.
- *Effort:* ~1 h — route all four blobs through `reconstruct_kwargs`.

**P0-10 · The PostgreSQL backend does not override `save_many`, so the batched-write path does not exist on the production default**
- *Area:* store / performance — `read:B-store-config`, `read:H-tests-backend`, `read:I3-tests-rest`
- *Evidence:* `grep "def save_many" validsim/store/*.py` returns **only** `memory.py:348` and `sqlite.py:343`. `PostgresValidationStore` inherits the base loop that calls `self.save(run)` per run. The base docstring at `memory.py:348-373` states the cost difference it exists to avoid ("SQLite commits (and therefore fsyncs) inside every save, so seeding 100 000 runs one at a time measures ~15 minutes, while the same rows in a single transaction take under a second"). Postgres is the shipped default: `docker-compose.yml:51` and `:133` set `VALIDSIM_STORE: "${VALIDSIM_STORE:-postgres}"`, `docker-compose.prod.yml:85`/`:115` set `postgres`, `.env.example:33` says `postgres`.
- *Impact:* A 100k-row seed on the production backend costs the full ~15 minutes the method was written to avoid. `tests/test_store_save_many_agent.py` covers only memory and SQLite; `tests/conftest.py:284` documents the omission as by-design.
- *Effort:* ~3 h — a batched `executemany` insert mirroring `sqlite.py:343`.

**P0-11 · `validsim/notify/dispatcher.py` signs with a blank HMAC secret, producing a publicly forgeable signature**
- *Area:* notification / security — `read:F-notify`, `read:H-tests-backend`, `read:I3-tests-rest`
- *Evidence:* `dispatcher.py:428-429` signs whenever `secret is not None`; `register()` (`dispatcher.py:310`) accepts `secret: str | None = None` with no emptiness check, so `""` or `" "` produces a well-formed `X-ValidSim-Signature` any attacker can recompute. The env path is safe — `notify/config.py:273` does `_at(secrets, index, "") or None`, turning `""` into `None` — so only the direct programmatic API is exposed. `tests/test_notify_security_agent.py:264-296` proves the forgery ("a receiver cannot distinguish a real send from an attacker's forgery") but is explicitly **create-only audit evidence** so the suite stays green whether or not it is ever fixed.
- *Impact:* Any consumer that constructs a `WebhookDispatcher` in-process gets a signature that authenticates nothing. The audit test pins the defect as current behaviour rather than flagging it.
- *Effort:* ~30 min — reject blank secrets in `register()`.

**P0-12 · A captured webhook `(body, signature)` pair replays forever**
- *Area:* notification / security — `read:J2-audit`
- *Evidence:* `audit/cat2-c2-security.md` S-02 (lines 97-149) records that `_sign_body` signs the serialized body and nothing else. Still live: `validsim/notify/dispatcher.py:429` emits only `headers[_SIGNATURE_HEADER] = _sign_body(body, secret)`, and `_sign_body` is `hmac.new(secret, body, sha256).hexdigest()` over the body alone — no timestamp, no nonce, no delivery id, no signature version. `grep -i "timestamp|nonce|replay" validsim/notify/dispatcher.py` returns zero hits. `tests/test_notify_security_agent.py:243-259` pins the absence.
- *Impact:* A stale APPROVE replayed into a deploy channel after a genuine BLOCK is a human-in-the-loop safety failure. `_MAX_RETRIES` widens the window. Note the wiring landed (P1-2), which makes the replay window live rather than hypothetical.
- *Effort:* ~2 h — sign `timestamp.nonce.body` and expose the timestamp for receiver-side window checks.

**P0-13 · URL credential redaction leaks the password tail when the password contains `@` or `/`**
- *Area:* security / config — `read:B-store-config`
- *Evidence:* `validsim/project_config.py:246` — `_URL_USERINFO_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<creds>[^/@]+)@")`. The `[^/@]+` class stops at the **first** `@` or `/`, not the last. **Reproduced during compilation:** `mask_secret('postgresql://validsim:p@ssw0rd@db.internal:5432/validsim')` returns `'postgresql://***REDACTED***@ssw0rd@db.internal:5432/validsim'` — the tail `ssw0rd` survives in cleartext. The docstring at `project_config.py:286-291` claims the password is removed, and `project_config.py:20-22` states redaction "is a security boundary". `tests/test_project_config.py:105-118` only exercises `hunter2`/`s3cr3t`, which contain neither character.
- *Impact:* A DSN with a password containing `@` or `/` — common in generated credentials — is partially logged in cleartext. This is the only defect in the whole report set that I re-reproduced byte-for-byte and that the code's own docstring denies.
- *Effort:* ~30 min — anchor the userinfo match on the **last** `@` before the host.

**P0-14 · `validsim delete` does not invalidate the local scorecard cache, so a deleted run still reports**
- *Area:* CLI / cache — `read:D-jobs-cli`, `read:I1-tests-cli-web`
- *Evidence:* `validsim/cli.py:780-789` (`delete`) calls `store.delete()` and never touches the cache written by `_save_to_cache` (`cli.py:165-171`). Reproduced end-to-end: after `validsim delete --run-id <id>` exits 0 printing "Deleted run <id>", `validsim status --run-id <id>` still exits 0 and prints the full scorecard, and `validsim scorecard --latest` still returns that deleted run. The cache is what `status`/`scorecard`/`report`/`compare --latest` read (`cli.py:567, 586, 612, 1037-1038`). `delete`'s own docstring (`cli.py:770-778`) claims CLI parity with the API-only deletion.
- *Impact:* Evidence that the system of record says is gone is still reported by four of the CLI's read commands — including the human-facing status path a CI job reads.
- *Effort:* ~30 min — drop the cache entry in `delete`.

**P0-15 · `compare --baseline-latest` / `--candidate-latest` resolve from the cache, not the durable store**
- *Area:* CLI / compare — `read:I1-tests-cli-web`
- *Evidence:* `validsim/cli.py:1037-1038` still calls `_resolve_run_id` (cache) for both sides, while the same class of defect was already fixed for `delete` (`cli.py:298-332` `_resolve_deletable_run_id`, store-first) and for `gate` (`cli.py:335-367` `_resolve_stored_run_id`). Reproduced: with a SQLite store holding only `vrun-00000001` and a cache whose newest entry is `vrun-cccccccc`, `validsim compare --baseline-latest --candidate vrun-00000001` exits 2 with *"error: no stored run 'vrun-cccccccc'"*.
- *Impact:* The comparison can name a run the store never contained, then fail against it — or, in the reverse case, compare against a stale run. The fix pattern is already in the file.
- *Effort:* ~30 min.

**P0-16 · The CLI worker never reaps expired leases — only `run_forever` does**
- *Area:* worker / CLI — `read:D-jobs-cli`
- *Evidence:* `validsim/jobs/worker.py:234` calls `self._queue.reap_expired()` **only inside `run_forever`**. The CLI's other two worker paths never reap: `cli.py:1224` (`worker --once` → `run_once()`) and `cli.py:1168` inside `_drain_worker` (`worker --watch` / `--max-jobs`). Reproduced: with `lease_seconds=1`, a claimed-then-abandoned job stayed `RUNNING` after `validsim worker --once` reported "no queued jobs" and exited 0, while a direct `queue.reap_expired()` in the same process reclaimed it.
- *Impact:* `docker-compose.yml:127` runs exactly this path (`python -m validsim.cli worker`). A job whose worker died is stranded in `running` forever in the deployed stack — it is neither retried nor dead-lettered.
- *Effort:* ~30 min — reap in `run_once`.

**P0-17 · A blank field in `VALIDSIM_WEBHOOK_URLS` misaligns every positional companion for hooks after it**
- *Area:* notification / config — `read:F-notify`
- *Evidence:* `validsim/notify/config.py:249` does `urls = _split(raw_urls)`, and `_split` (`config.py:171-177`) **drops blank fields**, while the companions are parsed with `_split_positional` (`config.py:180-196`) which **preserves blanks as placeholders**. `config.py:257` then iterates `enumerate(urls)` — indexing the companions by the *compacted* index. Verified: `VALIDSIM_WEBHOOK_URLS="https://a.example/h,,https://c.example/h"` with `SECRETS="s1,s2,s3"` resolves `a→s1, c→s2` — the "c" hook is signed with the secret meant for the dropped slot. With `SECRETS="s1,,s3"` (the docstring's own advice at `config.py:48-50`) it resolves `c→None` — **the hook is left UNSIGNED**. An *invalid* URL is dropped *after* enumeration, so its index *is* preserved and the asymmetry is the defect. `tests/test_notify_wiring_agent.py:188` is the only test with a blank URL and it asserts only the surviving URL.
- *Impact:* A webhook can be signed with the wrong key, or not signed at all, purely because an unrelated URL field was blank. This is a security-relevant misrouting of credentials.
- *Effort:* ~1 h — keep blank URL placeholders, or re-index the companions against the original positions.

**P0-18 · `allow_private_hosts=True` disables the entire egress policy, not just the host exemption**
- *Area:* notification / egress — `read:F-notify`
- *Evidence:* `validsim/notify/dispatcher.py:337-340` skips the **whole** `url_rejection_reason()` check when `allow_private_hosts` is true, and `validsim/notify/egress.py:135-155` is the only place the scheme allowlist `ALLOWED_SCHEMES={http,https}` is enforced. Verified: `WebhookDispatcher(live=True, allow_private_hosts=True).register("h", "file:///etc/passwd")` succeeds, as do `gopher://x/`, `http://metadata.google.internal/x` and `http://foo.internal/x`; the default dispatcher correctly refuses all of them.
- *Impact:* The flag's name and docstring (`dispatcher.py:271-279`) promise only a *host* exemption ("permitting loopback, link-local and RFC1918 destinations"); in fact it also disables the scheme allowlist and the cloud-metadata hostname block. httpx then raises `UnsupportedProtocol` on `file://`, so the practical exposure is bounded — but the flag is a much larger hole than it is named.
- *Effort:* ~1 h — split the scheme/metadata checks out of the host check.

**P0-19 · `release.yml` publishes the image to a GHCR owner the prod compose does not pull from**
- *Area:* CI/CD / release — `read:M-infra`
- *Evidence:* `.github/workflows/release.yml:126-127` tags `ghcr.io/${{ github.repository }}:latest` and `:${{ github.ref_name }}` — the fork owner's namespace. `docker-compose.prod.yml:73` and `:106` hardcode `image: ghcr.io/abhijeet1267/validsim:${IMAGE_TAG:-latest}`, and `docs/production-deployment.md:15` documents that same pull. The current origin is `https://github.com/dev25bansal-ops/validsim.git` (commit `a2511bc` records a 403-denied push to that target).
- *Impact:* The production stack pulls an image from a namespace the project no longer controls, while the release workflow publishes to a namespace the stack does not reference. The two are disjoint — the prod deploy is broken by construction.
- *Effort:* ~30 min — parameterise the image reference from one source.

**P0-20 · The `notify` "warn" severity band is unreachable — `min_severity="warn"` behaves identically to `"critical"`**
- *Area:* notification / scoring — `read:F-notify`
- *Evidence:* `validsim/notify/dispatcher.py:154` returns `"warn"` only when `composite >= threshold`. The only three `block_reasons` `validsim/engine/scorecard.py:549,555,561` can emit are (a) *"adversarial success rate …"*, (b) *"insufficient evidence …"*, (c) *"composite {x} is below the configured threshold {y}"*. Reasons (a) and (b) are exactly the two `_CRITICAL_BLOCK_REASON_PREFIXES` at `dispatcher.py:101-104`; (c) is appended only when `composite < threshold` — so a BLOCK with no critical-prefix reason is **logically impossible**. Verified during compilation by reading `scorecard.py:549-563` and `dispatcher.py:101-156` together. Empirically, over 2688 generated scorecards the distribution was info 558 / critical 2130 / warn 0.
- *Impact:* A three-level severity contract documented at `README.md:149` and `validsim/config.py:39` has a dead middle tier. A hook registered with `min_severity="warn"` fires on exactly the same events as `min_severity="critical"` — an operator cannot route "a plain threshold miss" separately from "the adversarial suite failed".
- *Effort:* ~1 h — the fix is a product decision about which reasons are "critical"; a fourth reason class is the likely shape.

**P0-21 · The `_memory_summaries` pagination path returns the OLDEST records when the requested page runs past the end**
- *Area:* API / store_stats — `read:C-api`, re-verified by execution during compilation
- *Evidence:* `validsim/api/store_stats.py:420-422` — `page = runs[len(runs) - offset - limit:] if offset + limit < len(runs) else runs`. The `else` branch (taken when `offset + limit >= len(runs)`) hands back the **whole** history, and the next line then trims `offset` items off the *end*, leaving the `offset` **oldest** records.
- *Re-verified:* with n=10, `limit=3, offset=15` returns `['vrun-4','vrun-3','vrun-2','vrun-1','vrun-0']` (5 items) where the reference (`newest_first[offset:offset+limit]`) returns **[]**. The divergence needs `offset < len(runs) < offset + limit`; `offset >= len(runs)` correctly returns empty, and `offset + limit < len(runs)` takes the correct branch. The SQL branch (`store_stats.py:444-446`) applies `LIMIT ? OFFSET ?` and is right in every case.
- *Why the suite misses it:* `tests/test_api_obsstats_agent.py:191` parametrizes `[(None,0),(1,0),(2,0),(5,1),(100,0),(3,35)]` against n ∈ {1,2,5,37}. Only `(3,35)` has `offset > 0` with `offset + limit > n`, and at n=37 that case (35+3 > 37) lands in the `else` branch yet coincidentally matches, because the trim still leaves the right count. The genuinely divergent window — `offset < n < offset + limit` — is never sampled.
- *Reachability:* `run_summaries` has **no production caller** — `git grep run_summaries -- validsim/*` finds only its own definition and docstrings. The repo's own `tests/test_wiring_reachability_agent.py:129` records it as "not yet routed". The live `/api/v1/validations` route paginates itself (`api/main.py:1182-1207`) and is **correct** (verified: `offset=99` on a 4-run store returns 0 items). So the defect is currently latent — it is a trap for whoever routes `run_summaries` into a live endpoint, which is exactly what the module docstring says the function is for.
- *Impact:* The moment this function is wired to a route, a client paging past the end of the history gets the oldest records instead of an empty page — the opposite of what "past the end" means, and different from what the SQL backend returns for the same request.
- *Effort:* ~30 min — one branch fix plus a parametrization that samples `offset < n < offset + limit`.
**P0-22 · `SECURITY.md` claims an audit trail that "makes tampering detectable"; the stores provide none**
- *Area:* security / compliance — `read:J2-audit`, `read:N-root-docs`, `read:K-vault`
- *Evidence:* `SECURITY.md:290-293` — *"a persisted run/scorecard record in PostgreSQL (JSONB + indexed columns) or SQLite, giving an audit trail that makes tampering detectable."* **Verified during compilation: that text is still at `SECURITY.md:290-293`.** But `validsim/api/main.py:1219-1240` exposes `DELETE /api/v1/validations/{run_id}`, which physically removes the row (`validsim/store/memory.py:399`), and the append-only guarantee is a **write-path** property only (`ON CONFLICT (run_id) DO NOTHING` at `sqlite.py:187`, `postgres.py:505`, `memory.py:345` setdefault) — there is no hash chain and no immutability. `docs/runbook.md:499-511` states the accurate version ("a write-path append-only guarantee, not immutable or tamper-evident storage … this repository does not implement an audit log"). `SECURITY.md` is the outlier against an accurate sibling document. The vault's own defect register already scheduled this as a "<1 day, zero-risk fix" (`vault/06 - Business/Technical Moat Features.md:1330`).
- *Impact:* `SECURITY.md` is the document a reporter, an auditor, or a buyer reads to calibrate the security posture. It overstates a control that does not exist — and a competitor or a reporter will find that out.
- *Effort:* <1 h — a doc fix, not a build.

**P0-23 · `docs/production-deployment.md` documents env vars, defaults, and deployable artifacts that do not exist**
- *Area:* deployment docs — `read:J1-docs`
- *Evidence:* `docs/production-deployment.md:231` documents `VALIDSIM_WORKER_CONCURRENCY` (default `4`) and `:232` documents `VALIDSIM_RATE_LIMIT_MAX` (default `100`) — a repo-wide grep returns only this document; the real names are `VALIDSIM_RATE_LIMIT` / `VALIDSIM_RATE_WINDOW_SECONDS` and there is no worker-concurrency env var at all. `:230` gives `VALIDSIM_REDIS_URL` a default of `memory://`, a scheme that exists nowhere in `validsim/jobs/queue.py` (the memory backend is selected by `VALIDSIM_JOB_QUEUE`). `:227` states `VALIDSIM_API_KEY` requires "min 32 chars"; `validsim/api/main.py:1025-1033` enforces no length and the app boots with `VALIDSIM_API_KEY='short'`. `:132-203` and `:205-217` instruct the operator to `helm install validsim ./charts/validsim` and `kubectl apply -f k8s/manifests/*.yaml` — **neither `charts/` nor `k8s/` exists** (verified: `ls` fails for both). `:538-539` says "All 3086 tests passing" and "100 req/min default".
- *Impact:* The deployment guide is the document a first production operator follows. Following it produces a pod that CrashLoopBacks, a rate limit set by a variable nothing reads, and a secret that is not validated as a secret.
- *Effort:* ~2 h to correct, or mark as a proposal.

**P0-24 · The `validsim` CLI accepts episode counts the engine will reject, and the failure is deferred to the worker**
- *Area:* CLI / jobs — `read:D-jobs-cli`, `read:I1-tests-cli-web`
- *Evidence:* `validsim/config.py:164` bounds `episodes` to `le=100000` and `adversarial` to `le=1000`; the HTTP route bounds them too (`validsim/jobs/router.py:81-82`). But `validsim/cli.py:1116-1118` declares `episodes: int = typer.Option(1000, "--episodes", "-e", min=1)` with **no upper bound**, and `validsim/jobs/models.py:72-73` declares `episodes: int = 1000` / `adversarial: int = 0` with **no bounds at all** (the file contains zero `ge=`/`le=` constraints). Verified: `validsim job enqueue --episodes 200000` exits 0 and stores the spec; the worker then fails all three attempts with a pydantic `ValidationError`. `validsim job enqueue --adversarial 5000` likewise. **Confirmed during compilation:** `cli.py:1116-1118` has `min=1` and no max; `models.py` has no bounds.
- *Impact:* A bad CLI value burns the entire retry budget and terminates as `failed` with a config error rather than being rejected at enqueue — and a CLI-only enqueue is the path a CI job uses.
- *Effort:* ~30 min — add `le=100000` / `le=1000` to the CLI options and to `JobSpec`.

**P0-25 · `validsim job enqueue` crashes with an unhandled `QueueFullError`**
- *Area:* CLI / queue — `read:D-jobs-cli`
- *Evidence:* `validsim/cli.py:1138` `record = queue.enqueue(spec)` has no `except QueueFullError`, unlike the router (`validsim/jobs/router.py:142-147` maps it to 503 + `Retry-After`). Verified: with `max_depth=1`, a second `validsim job enqueue` exits 1 with an empty stdout and an uncaught `QueueFullError` traceback. `grep QueueFullError tests/test_cli*.py` returns no hits.
- *Impact:* A full queue turns a normal backpressure condition into a stack trace for the operator and a red CI step.
- *Effort:* ~30 min.

**P0-26 · `create_app` replaces the router lifespan context, silently dropping app-level startup/shutdown handlers**
- *Area:* API — `read:C-api`
- *Evidence:* `validsim/api/main.py:996` does `application.router.lifespan_context = _tune_worker_pool`, replacing FastAPI's `_DefaultLifespan` — which is what runs `router.on_startup` / `on_shutdown`. Verified: a startup handler registered on an app built by `create_app` never fires, while the same handler on a stock FastAPI app does (control test). `_tune_worker_pool` (`main.py:983-994`) yields without running any startup/shutdown handler.
- *Impact:* Latent today only because no production code registers one (grep for `on_startup`/`add_event_handler` in `validsim/` returns nothing) — but it silently breaks the standard FastAPI extension point for the next person who adds one, and the override cannot simply be removed because it is what makes the pool tuning possible.
- *Effort:* ~1 h — wrap rather than replace the default lifespan.

### P1 — real gaps that erode trust or scale

**P1-1 · The LLM scenario generator is unreachable from every shipped run path**
- *Area:* scenario generation — `read:E2-sim-llm`, `read:N-root-docs`, `read:K-vault`, `read:I2-tests-misc`
- *Evidence:* `validsim/engine/pipeline.py:143` is `scenarios = ScenarioGenerator(seed=seed).generate(task.task_id, task.adversarial_count)` — **verified during compilation**. `git grep create_scenario_generator -- validsim/` matches only `validsim/scenarios/__init__.py` (re-export) and the definition. `validsim/scenarios/llm_generator.py:769-775` reads `VALIDSIM_LLM_ENABLED` but nothing in the pipeline imports the factory. `tests/test_scenarios_agent.py:104-125` pins this deliberately; `tests/test_wiring_reachability_agent.py:170-208` allow-lists the whole module.
- *Impact:* The full LLM path — schema validation, category-coverage top-up, 296 lines of batching tests — is shipped and inert. `VALIDSIM_LLM_ENABLED` + `VALIDSIM_LLM_API_KEY` change nothing about any run. This is the single most consistently reported item across six chunks, and the vault explicitly says "do not build before Nov 2" (`Technical Moat Features.md:1255`).
- *Effort:* ~1 day (one call-site change plus a default-preserving flag).

**P1-2 · The `notify` package docstring says the layer is not wired — the code wires it**
- *Area:* notification / docs — `read:F-notify`, `read:I3-tests-rest`
- *Evidence:* `validsim/notify/__init__.py:3-10` — **verified during compilation, the text is still there** — says the package is "implemented, configurable and tested, but **not yet wired into the shipped run path**", that "`run_and_score` does not call it yet — its only callers are in `tests/`", and that the work is "tracked as `PENDING OWNER`". Meanwhile `validsim/engine/pipeline.py:42` imports `notify_run_completion` and `:206` calls it; `tests/test_notify_wiring_agent.py:109-110` now says "WIRED (was PENDING OWNER; both wirings landed)"; `docs/notify_wiring_patches.md:1-8` says "all three LANDED".
- *Impact:* The package docstring is the first thing a reader of `validsim.notify` sees, and it tells them the feature is dead. `CONTRIBUTING.md:199` repeats the error in the other direction — it tells contributors the whole notification layer is unreachable, when only the *email* channel is.
- *Effort:* <1 h.

**P1-3 · Robustness is unmeasurable in every production run, and the named root cause was never fixed**
- *Area:* scoring / simulation — `read:A-engine`, `read:E1-sim-core`
- *Evidence:* `validsim/engine/scorecard.py:158-164` states it plainly: *"`run_validation` applies a single `task.randomization` level to every episode … so **every production run has exactly one randomization group and cannot produce a robustness measurement at all**. The runner, not this module, is the root cause and is where a fix belongs."* **Verified during compilation:** `validsim/sim/runner.py:623-631` passes `randomization_level=task.randomization` on both the nominal loop and the adversarial loop. The runner was never changed.
- *Impact:* The 0.2-weight robustness dimension of the composite is permanently unavailable. The scorecard absorbs it by renormalising over measured components, so the composite is not *wrong* — but one of the four advertised components is always zero/absent, and the dashboard (`validsim/web/view-core.js:424-465`) has no robustness field at all.
- *Effort:* ~1 week — vary `randomization_level` across groups in the runner and update the group-count contract.

**P1-4 · `compute_safety` awards a perfect 100.0 to an empty run, contradicting the fail-closed rule the same module applies to proximity**
- *Area:* safety — `read:A-engine`
- *Evidence:* `validsim/engine/safety.py:88-95` returns `SafetyResult(safety_score=100.0)` for zero episodes, documented only as "nothing unsafe was observed". The same module applies the opposite rule to the proximity channel (`safety.py:129-144`): with no human present the channel "is unmeasured, and abstaining (0.0) is the fail-closed choice -- a perfect 0.0 would be a fail-open on the one channel that exists to catch a robot running into a person."
- *Impact:* Safety carries 0.3 of the composite weight. An all-failed run banks 30 points from a channel that observed nothing. The module contains both the rule and its contradiction, which makes the 100.0 a bug rather than a policy.
- *Effort:* ~1 h to align; needs a policy decision on whether the safety component abstains like robustness does.

**P1-5 · The store's `history()` has no tiebreaker, and two consumers resolve "latest" as `runs[-1]`**
- *Area:* store / ordering — `read:H-tests-backend`, `read:I3-tests-rest`
- *Evidence:* `tests/test_store_divergence_agent.py:471-499` records that neither `history` nor `list_for_checkpoint` carries a secondary sort key: "Both backends happen to break the tie the same way today; nothing in the contract requires them to, and PostgreSQL's tie order for `ORDER BY created_at` over a plain table is not contractually rowid-ordered the way SQLite's is." The precondition is reachable: `test_div5e_created_at_is_second_precision_so_ties_are_reachable` (`:547`) proves 200 calls collapse into fewer than 200 distinct stamps. Two consumers resolve "latest" as `runs[-1]` — `validsim gate --latest` (cli.py) and `/api/v1/models`' `latest_composite` (api/main.py:1362).
- *Impact:* A same-second run can be signed off on the wrong record with no error anywhere. The gap is one engine/planner change away from mattering.
- *Effort:* ~2 h — `ORDER BY created_at ASC, run_id ASC` plus the matching Python sort key.

**P1-6 · `run_and_score` writes every run to the store twice and returns a mutable candidate, not the frozen record**
- *Area:* pipeline — `read:A-engine`
- *Evidence:* `validsim/engine/pipeline.py:194-202` — `if not store.save_exists(candidate): … return stored` followed unconditionally by `stored = store.save(candidate)`. `save_exists` already performed the insert; `save()` re-inserts the same `run_id`. In the memory backend `save()` calls `_snapshot(run)` eagerly (`memory.py:345`) even when the id is taken — instrumented `_snapshot` shows 2 full structural freezes of the episode list per pipeline call (0.254 s at 20k episodes, 1.375 s at 100k). In SQLite, `save()` issues a second `INSERT … ON CONFLICT DO NOTHING` plus a second `conn.commit()`. Separately, `pipeline.py:126-133` claims "Either way the returned run is the one the store holds, never a verdict it never accepted" — verified false: the returned object's `.episodes` is a plain mutable list while the stored record's is a `FrozenList`. **Verified during compilation** that `pipeline.py:202` is the unconditional `store.save(candidate)`.
- *Impact:* Doubles the write cost of every run and doubles the freeze cost, which is the dominant cost at 100k episodes. The two return paths (line 200 re-read vs line 202 save) return different kinds of object from the same function, and the docstring describes neither.
- *Effort:* ~1 h.

**P1-7 · `save_exists` in SQLite re-inlines the INSERT statement instead of reusing the shared constants**
- *Area:* store / SQL duplication — `read:B-store-config`
- *Evidence:* `validsim/store/sqlite.py:176-188` defines `_INSERT_SQL` with the comment "The single append-only insert, shared by `save` and `save_many` so the two cannot drift", and `_insert_params` (`:191-207`) is used by both. But `save_exists` (`:374-413`) re-inlines the full INSERT statement and the full 12-value parameter tuple — the source of `SqliteValidationStore.save_exists` contains neither `_INSERT_SQL` nor `_insert_params`.
- *Impact:* A future column addition must be applied in three places in this file, and the drift the comment warns about is already latent. The Postgres backend avoids this by factoring out `_insert_statement(table, returning)` (`postgres.py:281-308`) and `card_params` (`:232-255`).
- *Effort:* ~1 h.

**P1-8 · The in-memory queue leaves a stale `retry_after` on a queued transition that Redis clears**
- *Area:* queue — `read:D-jobs-cli`
- *Evidence:* `validsim/jobs/queue.py:1475-1476` applies `retry_after` only `if status is JobStatus.QUEUED and retry_after is not None`; otherwise the previously-set backoff survives. The Redis equivalent (`queue.py:690-693`, `_REDIS_SET_QUEUED_SCRIPT`) sets `cjson.null` when `ARGV[3]==''` — it always clears the backoff on a queued transition. The module docstring at `queue.py:5-7` promises the two backends "expose an identical interface".
- *Impact:* The same transition means two different things depending on the backend. A stale future backoff can delay a job that should be immediately claimable.
- *Effort:* ~1 h.

**P1-9 · The worker marks a job `failed` when the attempt budget is spent, but emits a dead-letter signal — `dead` is never reached on that path**
- *Area:* queue / worker — `read:D-jobs-cli`
- *Evidence:* `validsim/jobs/worker.py:200-206` calls `_report_dead_letter(job_id, "attempt-budget-exhausted", …)` and then `self._finish(job_id, epoch, JobStatus.FAILED, error=error)`. **Verified during compilation: the status written is `JobStatus.FAILED`, never `DEAD`.** `validsim/jobs/models.py:21-27` states `dead` exists precisely because "`failed` ran and reported an error", whereas attempt-budget exhaustion *is* a dead-letter case. The queue's own `claim_next` path (`queue.py:1237-1242`) settles the same condition as `JobStatus.DEAD`. The doc table at `docs/async-jobs.md:84-85` splits the two meanings the other way.
- *Impact:* Two paths for the same failure disagree on the terminal status. `JobRecord.is_terminal` (`models.py:161-164`) has no production caller, so the dead-letter semantics are not surfaced on the API, the CLI, or the queue's own list.
- *Effort:* ~1 h — a product decision on which status a budget-exhausted job gets.

**P1-10 · The Redis dead-letter channel has no subscriber in the repository**
- *Area:* queue / observability — `read:D-jobs-cli`, `read:I3-tests-rest`
- *Evidence:* `validsim/jobs/queue.py:242, 295, 505, 559` publish to `validsim:jobs:claim-failed` and `validsim:jobs:dead-letter`. `grep -rn "pubsub|\.subscribe(" validsim/` finds no subscriber anywhere in the package — only `tests/test_jobs_redis_deadletter_agent.py:78-80` subscribes, to assert the message is published. `queue.py:429-431` says the dead-letter publish "is the only signal the failure mode had".
- *Impact:* A Redis deployment loses the only observability signal its backend has. The in-memory path emits a structured `validsim_job_dead_lettered` log record; the Redis path emits into a channel nothing reads.
- *Effort:* ~2 h (a subscriber/alerting path) or ~1 h (a documented `GET /jobs?status=dead` surface).

**P1-11 · The dashboard's JS trend mirror silently drops two of the six server-side `TrendSummary` fields**
- *Area:* web / trends — `read:A-engine`, `read:I1-tests-cli-web`
- *Evidence:* `validsim/engine/trends.py:76-77` computes `improving` and `volatile` and ships them on `TrendSummary`. The JS mirror `validsim/web/view-core.js:428-464` (`computeTrends`) returns only `latestScore/delta/ma3/approvalRate/totalRuns/window3Size/window10Size/newestCheckpoint`. The parity test at `tests/test_web_presentation_agent.py:511-545` compares exactly the six numeric fields plus the window sizes, so the two dropped fields are invisible to it. `engine/trends.py` has no production importer at all (`tests/test_wiring_reachability_agent.py:83-88` allow-lists it), so the server-side flags reach no consumer by any route.
- *Impact:* The dashboard tells an operator a trend is improving or volatile when the engine has decided neither — and the parity test that exists to catch exactly this cannot see the gap.
- *Effort:* ~2 h.

**P1-12 · The `store_stats` O(1) projection has a passing test suite and no production caller, so the dashboard still hydrates every run**
- *Area:* API / performance — `read:C-api`, `read:H-tests-backend`, `read:I3-tests-rest`
- *Evidence:* `validsim/api/store_stats.py:354` defines `run_summaries`; the only non-test reference is the acknowledgement in `tests/test_wiring_reachability_agent.py:129-139` ("no production caller yet — exercised only by tests, while the list/dashboard-history readers it would replace are still not routed here"). The readers still scan the whole table: `validsim/api/main.py:1202-1206` (`st.history(...)` then slice) and `validsim/api/dashboard.py:38` (`[run.summary() for run in reversed(store.history())])`. Measured at n=2000 on SQLite: the dashboard-summary body via `history()` took 193.63 ms vs 9.9 ms for `store_totals` — a ~20× gap. The committed baseline records `api_api_v1_dashboard_history_n100000: 826.03 ms` against `api_api_v1_validations_n100000: 43.45 ms`. The projection is proven byte-identical by `tests/test_api_obsstats_agent.py:180-216`.
- *Impact:* The dashboard and list endpoints scale linearly with total history. Wiring it is mechanical — but `store_stats`'s SQL path supports no `since`/`until` filter (grep for `since`/`until` in `store_stats.py` returns nothing), so `list_validations` needs the filter extended first.
- *Effort:* ~4 h including the filter.

**P1-13 · `store_stats` claims a PostgreSQL fast path it does not have**
- *Area:* API / performance — `read:C-api`, `read:I3-tests-rest`
- *Evidence:* `validsim/api/store_stats.py:47-50` claims "SQLite / Postgres -- a SELECT over the indexed, promoted columns", and `:72-73` says the equivalence is asserted "for the in-memory, SQLite and fake-connection Postgres backends". Neither is true: `_sqlite_conn` (`store_stats.py:216-224`) accepts only a `sqlite3.Connection`, and the SQL constants (`:122-137`) hardcode the table name `validations` and SQLite's `?` placeholder, so they cannot run on Postgres (configurable table name at `postgres.py:432`, `%s` placeholders). `strategy_for(PostgresValidationStore)` returns `'generic'`. The test file confirms it: `grep -ci postgres tests/test_api_obsstats_agent.py` returns **0**.
- *Impact:* Postgres — the shipped production backend — falls to the generic `list(store.history())` branch in `metrics.py:155`, hydrating every JSON blob of every stored run on the `/metrics` scrape path. The exact O(n) cost `store_stats` exists to remove.
- *Effort:* ~4 h.

**P1-14 · `VALIDSIM_THREAD_LIMITER_TOKENS` is published on `app.state` but has no effect on the real thread pool**
- *Area:* API — `read:C-api`
- *Evidence:* `validsim/api/main.py:1009-1012` reads `VALIDSIM_THREAD_LIMITER_TOKENS` into `application.state.thread_limiter_tokens`, but `:993` calls `_apply_thread_limiter_headroom(None, application.state.pipeline_concurrency)` — the token value is never used to size the pool. `_apply_thread_limiter_headroom` (`:696`) computes `floor = max(DEFAULT_THREAD_LIMITER_TOKENS, budget + THREAD_LIMITER_HEADROOM)`, ignoring the operator's setting. Verified: with `VALIDSIM_THREAD_LIMITER_TOKENS=6`, `state.thread_limiter_tokens == 6` but the real anyio pool goes 40 → 48, not 6. With `=200` the pool only reaches 48.
- *Impact:* The env var is documented at `docs/api-reference.md:862` and `docs/runbook.md:80` as sizing the worker pool, so the doc contradicts the behaviour. The raised floor is the safe direction, so this is a silently-ignored knob rather than a safety hole.
- *Effort:* ~1 h.

**P1-15 · `_SlidingWindowRateLimiter.sweep_stale()` has no production caller, so the stale-bucket reclaim never happens on a quiet instance**
- *Area:* API / observability — `read:H-tests-backend`, `read:I3-tests-rest`
- *Evidence:* `validsim/api/main.py:352` defines `sweep_stale` with the explicit rationale that it exists "so an instance can be swept off a clock as well as off request traffic". A repo-wide search finds it referenced only at `main.py:352` and in `tests/test_api_rate_limit_scope.py:298,324`. The only sweep that ever runs is the request-driven one in `check` (`main.py:394`, `:415`), gated on `self._operations >= _SWEEP_INTERVAL` (1024, `main.py:196`). The lifespan hook (`main.py:981-995`) only calls `_apply_thread_limiter_headroom`; no timer is installed.
- *Impact:* The exact case the method was written for — a low-traffic instance that never reaches 1024 checks — is the case where it never runs. The suite is green because the test calls `sweep_stale()` directly.
- *Effort:* ~2 h (a lifespan timer).

**P1-16 · The notify package emits no logs, no metrics, and the pipeline discards the delivery results**
- *Area:* notification / observability — `read:F-notify`
- *Evidence:* `validsim/engine/pipeline.py:206` calls `notify_run_completion(scorecard)` and discards the returned list. The dispatcher object is constructed inside `notify_run_completion` (`dispatcher.py:523`) and never escapes it, so `WebhookDispatcher.sent` (`dispatcher.py:298-301`) is unreachable from the shipped path — a grep for `.sent` under `validsim/` returns only docstring references. There is no logger, no metric counter and no `print` anywhere in the package (grep for `get_logger`/`logging`/`Counter`/`print` in `validsim/notify/` returns nothing). `docs/runbook.md:281` nevertheless directs operators to "inspect `dispatcher.sent` or the hook's logs" — a log that does not exist.
- *Impact:* A failed webhook delivery is completely invisible to an operator. The failure is captured on a `DeliveryResult` that nobody reads.
- *Effort:* ~2 h.

**P1-17 · The shadow promotion gate measures one aggregate success rate; a fully deaf worker passes it 77.5% of the time**
- *Area:* simulation / promotion gate — `read:I2-tests-misc`, `read:E1-sim-core`
- *Evidence:* `tests/test_sim_contract_audit_agent.py:880-1041` (`TestShadowGatePower`) measures that a worker which ignores `checkpoint_id`, `randomization_level` and every scenario passes the shipped gate 77.5% of the time (1549/2000 trials at episodes=20, tolerance=0.15) — and `docs/isaac-worker.md:169` ("only after N consecutive shadow runs agree within tolerance") makes promotion *more* likely (0.775³ ≈ 47%, 0.775⁵ ≈ 28%). The same file pins that `2·sd(0.7 at n=20) = 0.205 >` the 0.15 tolerance, so the band sits inside the sampling noise. `validsim/sim/shadow.py:214-239` still returns a single `passed` flag from one aggregate comparison, and `grep consecutive validsim/` finds the word only in the docstring at `shadow.py:19`. Independently, `docs/isaac-worker.md:165-168` requires per-adversarial-category deltas, the failure-taxonomy distribution and the safety observables; `ShadowReport` (`shadow.py:84-118`) carries none of them.
- *Impact:* The platform's own robustness claim has no executable gate. A worker that ignores every scenario it is handed can be promoted to source of truth.
- *Effort:* ~1 week (per-category deltas + a widened band) plus an owner decision.

**P1-18 · The Isaac worker contract is documented so that a conforming worker is rejected**
- *Area:* simulation / wire contract — `read:E1-sim-core`, `read:E2-sim-llm`
- *Evidence:* `docs/isaac-worker.md:62-68` instructs the worker to "Load the policy named here, and echo it back on each episode so the client can confirm the right artifact ran." The client cannot accept that echo: `validsim/sim/isaac_worker.py:65` derives `_EPISODE_FIELDS` from the `EpisodeResult` dataclass, which has no `checkpoint_id` field (`validsim/sim/runner.py:361-371`), and the parser rejects unknown keys at `isaac_worker.py:288-294`. Reproduced: a reply containing `"checkpoint_id": "ckpt-v41"` is rejected with *"sent unknown field(s) 'checkpoint_id'"*. An envelope-level echo is accepted but never compared to `task.checkpoint_id` — a worker echoing `A-DIFFERENT-CHECKPOINT` is silently accepted. The request side was already fixed (`isaac_worker.py:660` emits `checkpoint_id`).
- *Impact:* The one control the doc calls the only way to confirm the right artifact ran is missing, and the written contract is unfollowable. A worker could be fully conformant, pass the §5 gate, get promoted, and be structurally incapable of validating a checkpoint.
- *Effort:* ~1 day.

**P1-19 · `IsaacWorkerBackend.run_episodes` (the batched POST) has no production caller**
- *Area:* simulation / cost — `read:I2-tests-misc`
- *Evidence:* `grep run_episodes validsim/` shows only the definition (`validsim/sim/isaac_worker.py:556`) and two calls inside the unwired `validsim/sim/shadow.py` (`:312`, `:517`). The production path `validsim/sim/runner.py:625` calls `backend.run_episode(...)` once per episode. `docs/isaac-worker.md:150-153` states the contract is batch-shaped and "Today `run_episode` uses the degenerate batch-of-one, which costs a round trip per episode — fine for shadow runs, worth revisiting at scale." The batched path is fully implemented and tested (`tests/test_isaac_batching.py`, 296 lines).
- *Impact:* A GPU run at production scale costs one HTTP round trip per episode, against a 30 s default per episode timeout. This is the dominant cost of the Isaac path.
- *Effort:* ~2 h to switch the runner over, plus the timeout knob (P1-21).

**P1-20 · The Isaac backend's HTTP client is never closed on the API/pipeline path**
- *Area:* backend lifecycle — `read:E1-sim-core`
- *Evidence:* `create_backend()` (`validsim/sim/__init__.py:38-58`) constructs a new `IsaacWorkerBackend` per call, and the API path calls it per validation request (`engine/pipeline.py:144` via `api/main.py:795`). The pipeline never closes it: `inspect.getsource(run_and_score)` contains no `close()`. `IsaacWorkerBackend` does have `close()` (`isaac_worker.py:511-515`) and a context manager (`:517-523`) — but no production caller uses either; only `shadow.py:320` closes it, in its own `finally`. `httpx.Client` has no `__del__`.
- *Impact:* Pooled connections are left to the garbage collector rather than to a deterministic close on every request. Reported as lifecycle hygiene rather than a confirmed leak — the reviewing agent could not measure a residual socket locally.
- *Effort:* ~2 h.

**P1-21 · The Isaac worker timeout has no environment knob, though the docs say the 30 s default must be raised for real batches**
- *Area:* simulation / operations — `read:E2-sim-llm`
- *Evidence:* `validsim/sim/isaac_worker.py:468` — `timeout: float = 30.0`, applied to connect and read (`:508`). There is no `VALIDSIM_ISAAC_WORKER_TIMEOUT` (or equivalent) in `.env.example`, in `validsim/project_config.py:175-200` (`INFRA_KEYS`), or anywhere in `validsim/`. `docs/isaac-worker.md:154-155` states "a single PhysX episode can take minutes, so raise it well above the 30 s default for real batches" — but `create_backend()` (`validsim/sim/__init__.py:55-58`) constructs `IsaacWorkerBackend()` with no arguments, so the only production path cannot raise it.
- *Impact:* The documented operational requirement is unreachable. A real PhysX batch fails on timeout with no operator control.
- *Effort:* ~1 h.

**P1-22 · `is_alive()` can raise `httpx.InvalidURL`, breaking its documented never-raise promise**
- *Area:* simulation / health probe — `read:E2-sim-llm`
- *Evidence:* `validsim/sim/isaac_worker.py:626` catches only `httpx.HTTPError`. `httpx.InvalidURL` is not a subclass of `HTTPError` (MRO: `InvalidURL → Exception`), and httpx raises it while building the request for a malformed port. Reproduced: `IsaacWorkerBackend(base_url='http://host:notaport').is_alive()` raises `httpx.InvalidURL: Invalid port: 'notaport'`, and `run_episode` raises the same raw httpx error rather than a `SimWorkerError`. The docstring at `isaac_worker.py:616-622` promises "this is a monitoring probe, so it must never raise".
- *Impact:* A malformed base URL turns a monitoring probe into an unhandled exception, and surfaces a raw httpx error to callers instead of the module's own error type.
- *Effort:* ~30 min.

**P1-23 · The API exposes zero response models and 12 unconstrained id path parameters**
- *Area:* API / DX — `read:J2-audit`
- *Evidence:* `grep -c response_model validsim/api/main.py` returns **0** — no route declares a response model. `create_app().openapi()['components']['schemas']` yields only `['CompareRequest','EnqueueJobRequest','EnvironmentSpec','HTTPValidationError','RobotSpec','TaskConfig','ValidationError','ValidationRequest']`. A live OpenAPI parameter scan finds 12 required params with no pattern/format (8× `run_id`, 3× `job_id`, 1× `checkpoint_id`), so a malformed `run_id` returns 404 ("run vrun-deadbeef not found") rather than 422.
- *Impact:* The scorecard — the entire product output — is documented to clients as `{}`. Generated clients get no schema for the thing they are integrating against.
- *Effort:* ~1 day.

**P1-24 · Four list endpoints return unbounded arrays while `/api/v1/validations` is paginated**
- *Area:* API / scale — `read:J2-audit`
- *Evidence:* A live probe shows `/api/v1/validations` → envelope, but `/api/v1/regressions` → bare list, `/api/v1/dashboard/history` → bare list, `/api/v1/models` → bare list. `validsim/jobs/router.py:200-202` returns the bare array when neither `limit` nor `offset` is supplied. These grow without warning as history grows, and there is no `has_more`/`truncated` signal a client can branch on. The dashboard polls two of them (`app.js:789`, `app.js:1005`) with no limit parameter — the client half of `docs/ISSUES_AND_REMEDIATION_CATALOG.md` PERF-002 is still owed.
- *Impact:* The response size of the dashboard's own polling endpoints is unbounded; `docs/ISSUES_AND_REMEDIATION_CATALOG.md` records a measured 172 ms / ~975 KB for `/api/v1/dashboard/history` at 5,000 runs.
- *Effort:* ~4 h.

**P1-25 · Three incompatible error-body contracts, with no machine-readable code and no `request_id` in the body**
- *Area:* API / DX — `read:J2-audit`
- *Evidence:* Hand-raised `HTTPException` returns `{"detail": "<string>"}`; FastAPI validation returns `{"detail": [ … ]}` as a **list**; the queue-full path returns `{"error": "queue_full", "max_depth": N}`. A live probe confirms all three shapes. The correlation id is published in the `X-Request-ID` response header (`main.py:612`, `:623`) but never in the body, so a client cannot quote it without header access.
- *Impact:* A client needs three parsers. `{"detail": [...]}` as a list in particular breaks naive `detail` string handling.
- *Effort:* ~1 day.

**P1-26 · The regression comparison is seeded differently on every surface, so the same comparison disagrees**
- *Area:* reproducibility / CLI+API — `read:J2-audit`
- *Evidence:* The CLI seeds from `stable_seed(candidate_id, baseline_id)` (`validsim/cli.py:1056`), the API from `stable_seed(run_id, body.baseline_id)` (`validsim/api/main.py:1276`), and the engine pipeline from `stable_seed(checkpoint_id, task.task_id)` (`validsim/engine/pipeline.py:142`). `git grep derive_comparison_seed` returns zero hits — the audit's proposed single helper was never introduced.
- *Impact:* The same pair of runs yields different p-values depending on the surface asked, and neither matches the stored scorecard's own comparison. A CI gate and an operator's local `validsim compare` can disagree about the same regression.
- *Effort:* ~3 h (a shared helper plus a migration of the stored p-values).

**P1-27 · The exact binomial tail in `_adversarial_significant` overflows at ~1169 episodes**
- *Area:* scoring / numerical robustness — `read:A-engine`
- *Evidence:* `validsim/engine/scorecard.py:320-323` — `p_value = sum(math.comb(total, k) * (floor**k) * ((1.0-floor)**(total-k)) for k in range(observed_ok+1))`. `math.comb(1169, k)` exceeds float range, so the multiply raises `OverflowError`. Binary-searched threshold: the tail overflows at `total >= 1169`. Reproduced end-to-end: `build_scorecard` with 1210 episodes raises `OverflowError: int too large to convert to float`. `TaskConfig.adversarial_count` is currently capped at 1000 (`validsim/config.py:166`), so the config path is safe *today*.
- *Impact:* A cliff, not a wrong answer — the implementation is numerically accurate where it does not overflow (verified against an independent log-space reference, ratio 1.0000 at total = 100/500/1000). But the guard is a config value that can change, and a crash in the gate is a fail-closed wrong answer.
- *Effort:* ~2 h — a log-space tail.

**P1-28 · The exact permutation test is O(n) per resample and costs 40 s at the maximum episode count**
- *Area:* statistics / performance — `read:A-engine`, `read:J2-audit`
- *Evidence:* `validsim/engine/stats.py:134-140` reshuffles the pooled 0/1 sample and re-sums it on every iteration. `TaskConfig` allows up to 100,000 episodes (`validsim/config.py:164`). Measured on this tree: 2,000 episodes → 1.0 s, 10,000 → 3.4 s, 20,000 → 7.8 s, 100,000 → **40.0 s**. The sibling `bootstrap_ci` path was explicitly optimised for exactly this workload (`stats.py:66-78` documents the `rng.choices` change); the permutation test was not. The audit (`cat2-c2-perf.md` PF-3) measured 8,523 ms at n=20,000 and explicitly leaves it open because every equivalent rewrite it tried was slower or wrong, and reducing `n_resamples` changes reported confidence resolution.
- *Impact:* A baseline comparison at the top of the supported episode range adds ~40 s to a gate that is expected to be fast. This is an owner decision on the statistics, not a code change.
- *Effort:* ~1 week of statistical work, or a documented cap.

**P1-29 · The live-Redis test fixture's cleanup pattern never matches the keys it writes**
- *Area:* tests / redis — `read:D-jobs-cli`, `read:I3-tests-rest`
- *Evidence:* `tests/test_jobs.py:703-704` builds the cleanup pattern as `f"{namespace}:jobs:*"` while the queue writes `{prefix}:{job_id}`, `{prefix}:index` and `{prefix}:ready` (`validsim/jobs/queue.py:1616-1626`) — the namespace **is** the key prefix, so the correct pattern is `f"{namespace}*"`. The fixture's initial cleanup at `:716` uses the right form (`validsim:jobs:*`), which is why only the namespaced keys leak. Verified against the live Redis at 127.0.0.1:6379/15: 573 keys matching `validsim:test:*` accumulated from 278 distinct PIDs, and a re-run adds 2 more while deleting nothing. The namespace includes `os.getpid()`, so a recycled PID can collide with a namespace whose keys were never deleted — the suite failed once with `ValueError: job vrun-cafe1234 already exists`.
- *Impact:* A shared local Redis degrades indefinitely, and PID reuse makes the suite intermittently fail for a reason that looks like a product bug.
- *Effort:* ~30 min.

**P1-30 · `config_to_env` silently drops every non-scalar top-level section that `_validate_sections` accepts**
- *Area:* config — `read:B-store-config`
- *Evidence:* `validsim/project_config.py:427-482` renders only keys for which `classify_key(key) == "policy"`, i.e. the members of `POLICY_KEYS` (`:136-151`). But `_KNOWN_TOP_LEVEL` (`:157-169`) additionally accepts `schema_version`, `tasks`, `defaults`, `checkpoints`, `adversarial`, `store`, `backend`, `notify`, `junit` — seven sections that `_validate_sections` (`:338-356`) accepts and `config_to_env` then drops. Verified: `config_to_env({"threshold": 90.0, "store": {...}, "backend": {...}, "notify": {...}, "junit": {...}, "tasks": [...], "defaults": {...}, "adversarial": {...}})` returns `{'threshold': '90.0'}` only. The function's own docstring (`:455-458`) frames the restriction as being about *unclassified* keys, which is defensible for the display path but not for a committed config file.
- *Impact:* A `[store]`, `[backend]` or `[notify]` section in `validsim.config.toml` validates and is then ignored, with no diagnostic. This is a latent trap for whoever wires the file-config path.
- *Effort:* ~2 h (either emit them or reject them at validation).

**P1-31 · The `notify` `summary()` reads `VALIDSIM_WEBHOOKS_LIVE` from `os.environ`, not from the mapping it was given**
- *Area:* notification / config — `read:F-notify`
- *Evidence:* `validsim/notify/config.py:204-210` defines `_live_from_env()` as `os.environ.get(_LIVE_ENV, "").strip() == "1"`, and `NotifyConfig.summary()` (`:163-168`) calls it directly, ignoring the `environ` mapping `notify_config_from_env()` was given. Verified: `notify_config_from_env({"VALIDSIM_NOTIFY_ENABLED":"1","VALIDSIM_WEBHOOK_URLS":"https://a.example/h","VALIDSIM_WEBHOOKS_LIVE":"1"}).summary()` reports `'live': 0`. The dispatcher re-reads `os.environ` at dispatch time by design (`dispatcher.py:250-260`), so the pattern is deliberate for the dispatcher — but `summary()` is documented (`:152-153`) as "the effective notification configuration" and is served on the unauthenticated `/api/v1/health`.
- *Impact:* The health payload can report a different live state than the config it summarizes. Only observable for the explicitly-parameterised call, which is the call the config module's own docstring (`:5-8`) advertises for testing and auditing.
- *Effort:* ~1 h.

**P1-32 · The health probe reflects an injected store's class name verbatim on an unauthenticated endpoint**
- *Area:* API / health — `read:I3-tests-rest`
- *Evidence:* `validsim/api/main.py:869-892` derives the store label purely from `type(obj).__name__.lower()` with no allow-list, and `/api/v1/health` is in `PUBLIC_PATHS`. `tests/test_api_characterization_agent.py:474-485` marks this SUSPECTED-WRONG: "unauthenticated reflection of internal type names, and a deployment that injects a bespoke store leaks its implementation detail on every health probe". The same file records the cosmetic sibling at `:465-472` (a module-private subclass reports `_opaquestore`).
- *Impact:* Low severity, but it is a live information disclosure on the one route that needs no credential.
- *Effort:* ~30 min.

**P1-33 · The health payload does not report the SMTP email configuration, so the email channel is invisible to operators**
- *Area:* API / health — `read:I3-tests-rest`
- *Evidence:* `validsim/api/main.py:1146` returns `"notify": notify_config_from_env().summary()` and the health path is in `PUBLIC_PATHS`. There is no `EmailNotifier`/`EmailDelivery`/`scorecard_email_body` usage anywhere in the API, CLI or worker outside `validsim/notify` itself. `tests/test_wiring_reachability_agent.py:156-160` allow-lists `validsim.notify.email`/`EmailNotifier` as "SMTP email is not mounted on the API, CLI or job worker".
- *Impact:* An operator who sets `VALIDSIM_SMTP_*` gets no health signal that the email path exists, or that it is unreachable.
- *Effort:* ~1 h.

**P1-34 · The `EmailNotifier` channel is unreachable and its six `VALIDSIM_SMTP_*` variables are advertised in `.env.example`**
- *Area:* notification — `read:F-notify`, `read:J2-audit`, `read:N-root-docs`
- *Evidence:* Grepping the whole repo for `EmailNotifier`/`EmailDelivery`/`SmtpSettings` outside `validsim/notify/` returns only test files. `validsim/engine/pipeline.py:42` imports only the dispatcher; `validsim/api/main.py:120` imports only `notify_config_from_env`. `grep -rn "EmailNotifier(" --include=*.py validsim/` returns zero hits. `README.md:276` labels it `[~]` with the honest note that it "still has no production caller" — but `.env.example:177-184` still presents six `VALIDSIM_SMTP_*` variables under "Notifications: webhooks + email".
- *Impact:* Roughly half of the notify package (323 of 1363 lines) is unreachable from any shipped surface, and the two channels have entirely separate activation, safety and observability stories. The `tests/test_delivery_pipeline.py:191-198` "every advertised env var has a reader" gate passes only because it scans for `VALIDSIM_*` string literals *anywhere* in `validsim/` — including dead code — so it cannot distinguish a reader on the shipped path from a literal in an unreachable module.
- *Effort:* ~1 day to wire, or ~1 h to remove the env block.

**P1-35 · The `EmailNotifier` is not covered by the wiring-reachability gate at all**
- *Area:* tests / governance — `read:N-root-docs`
- *Evidence:* `tests/test_wiring_reachability_agent.py:70-145` lists `validsim.config_loader`, `validsim.engine.trends`, `validsim.engine.anomaly`, `validsim.engine.benchmark`, `validsim.engine.pdf.render_scorecard_pdf`, `validsim.sim.shadow` and `validsim.api.store_stats` as `UNWIRED_BY_DESIGN` — each with the reason "no production importer". `validsim/notify/email.py` is **not** in the list, which means `EmailNotifier`'s dead status is asserted only in prose (`README.md:13`) and never in an executable check.
- *Impact:* A capability can be dead without the gate noticing, because the gate's coverage of "dead" is manual. The same applies to `validsim.scenarios.generator.coverage` and `difficulty_bias` (P2-6).
- *Effort:* ~1 h.

**P1-36 · `JsonLogFormatter` lets an extra field overwrite the canonical timestamp/level/logger keys**
- *Area:* logging / observability — `read:B-store-config`
- *Evidence:* `validsim/_log.py:252-266` builds the payload with `timestamp`/`level`/`logger`/`message` first, then loops over `record.__dict__` and writes `payload[key] = …` for every non-reserved extra. `_RESERVED_ATTRS` (`_log.py:67-71`) is derived from a stdlib `LogRecord`, which contains none of these four names, so `extra={"logger": …, "timestamp": …, "level": …}` overwrites them. Verified: `logger.info("real message", extra={"logger": "spoofed", "timestamp": "1999-01-01T00:00:00Z", "level": "NOTALEVEL"})` emits exactly the spoofed values. The stdlib guards only `message`/`asctime` — it permits `extra={"timestamp": …}`.
- *Impact:* The canonical fields a log pipeline indexes on are caller-controlled. This protection is specific to this formatter and absent everywhere else, so a caller that uses plain `logging` gets none of it.
- *Effort:* ~30 min.

**P1-37 · `configure_logging`'s re-configure path updates the logger level but not the handler level**
- *Area:* logging / observability — `read:B-store-config`
- *Evidence:* `validsim/_log.py:314-316` sets `handler.setLevel(resolved)`, but the early-return path at `:303-306` only calls `logger.setLevel(resolved)` and leaves the handler at its previous level. Verified: after `configure_logging(force=True, level="WARNING")`, a later `configure_logging(level="DEBUG")` leaves logger level DEBUG but handler level WARNING, and the DEBUG record is dropped by the handler.
- *Impact:* A silent half-applied configuration change — the docstring at `:304` says "honour a level change but keep the single handler", and only half the change is honoured.
- *Effort:* ~30 min.

**P1-38 · The in-memory backend does not rebind scorecard identity, so it reports the blob's ids while the SQL backends report the row's**
- *Area:* store / backend parity — `read:B-store-config`
- *Evidence:* `validsim/store/memory.py:324-346` (`save`) stores `_snapshot(run)` verbatim; `rebind_row_identity` (`memory.py:99-153`) is called only from the two SQL read paths (`sqlite.py:124`, `postgres.py:331`) and is never referenced by `ValidationStore` itself. Verified: saving a `StoredRun` whose top-level `run_id="COLUMN-ID"` carries a scorecard with `run_id="BLOB-ID"` yields `store.get("COLUMN-ID").scorecard.run_id == "BLOB-ID"` while `store.get("BLOB-ID")` is `None` and `list_for_checkpoint("BLOB-CK")` is empty — the exact "split" that `memory.py:99-126` says the fix closed. `tests/test_store_divergence_agent.py:191-240` asserts only that the *outer* `checkpoint_id` matches; `test_div2d` (`:305`) asserts the inner `scorecard.run_id` only for SQLite.
- *Impact:* The three backends disagree about which id is authoritative. `get()` and `list_for_checkpoint()` can disagree about the same record depending on the backend.
- *Effort:* ~2 h.

**P1-39 · `freeze()` leaves a plain mutable container reachable once the depth cap is hit**
- *Area:* store / immutability — `read:B-store-config`
- *Evidence:* `validsim/store/_frozen.py:171-172` — `if _depth >= MAX_FREEZE_DEPTH: return value` returns the value **unchanged**, so a container below the cap is not frozen at all. `MAX_FREEZE_DEPTH = 12` (`_frozen.py:65`). Verified: a dict nested 12 levels leaves a plain mutable `dict` reachable from the "frozen" result (11 → fully frozen; 12 → mutable reachable). The module docstring at `_frozen.py:30-33` claims "The store keeps a record with no mutable container reachable from it, so sharing it cannot leak", and `memory.py:264-270` states the guarantee holds for every backend. `tests/test_store_snapshot_agent.py:479-492` only asserts that the call returns, not that the result is frozen.
- *Impact:* Real records are 3-4 levels deep so the cap is not reached in practice, and the cap is documented as "a safety valve, not a working limit" (`:62-65`) — but the failure mode when it *is* reached is a silently mutable record rather than a refusal.
- *Effort:* ~1 h — raise the cap and/or refuse at the boundary.

**P1-40 · The asset-path validator accepts a Windows drive-relative path that escapes the asset root**
- *Area:* config / path validation — `read:B-store-config`
- *Evidence:* `validsim/config.py:23-34` `_is_absolute_path` uses `os.path.isabs` plus `PurePosixPath`/`PureWindowsPath`. Windows' drive-relative form `C:foo` is not absolute by any of those checks (`os.path.isabs('C:foo')` is `False`; `PureWindowsPath('C:foo').is_absolute()` is `False`), and `Path('D:/root') / 'C:foo\\bar.urdf'` yields the drive-relative path `C:foo\bar.urdf`, which `Path.resolve()` turns into `C:\foo\bar.urdf` — outside the asset root. Verified: `RobotSpec(name="franka", urdf_path="C:foo\\bar.urdf")` is accepted. The escape is caught later by `resolve_asset_path` (`config.py:62-93`) — but that function has no production caller (documented at `:65-78` and allow-listed in `tests/test_wiring_reachability_agent.py:209`), so the field validators are the only live containment check.
- *Impact:* A Windows-only path-escape shape passes the only live validation. The secondary defence exists but is not wired.
- *Effort:* ~1 h.

**P1-41 · The CLI's `job enqueue` cannot express `threshold` or `baseline_run_id`, unlike the HTTP route**
- *Area:* CLI / API parity — `read:D-jobs-cli`
- *Evidence:* `validsim/cli.py:1110-1142` (`job_enqueue`) exposes only `--checkpoint/--task/--episodes/--adversarial` and builds a `JobSpec` with no `threshold` and no `baseline_run_id`. `validsim/jobs/router.py:83-93` (`EnqueueJobRequest`) exposes both. The worker honours both when present (`validsim/jobs/worker.py:349`, `:357`), and `validsim/jobs/models.py:57-66` documents that carrying the gate on the spec is what makes the async path agree with the synchronous one.
- *Impact:* A CLI-enqueued job is always scored at the worker's default 85.0 and is never regression-compared — exactly the divergence the field was added to prevent. The CLI can reach only the deprecated half of the contract.
- *Effort:* ~1 h.

**P1-42 · `validsim run --environment` is accepted, threaded into `TaskConfig`, and has no effect on any result**
- *Area:* CLI / options — `read:I1-tests-cli-web`
- *Evidence:* `validsim/cli.py:448` constructs `EnvironmentSpec(name=environment)` and that is the only consumer of the value; it is never consulted by `validsim/sim/runner.py` or `validsim/engine/pipeline.py` (only `validsim/sim/isaac_worker.py:659` forwards it to a remote worker). Verified by execution: `validsim run --environment warehouse-bay` and `--environment lab-room` both produce composite 56.9 / success rate 50.0% — identical. It is also not persisted: neither `Scorecard` nor `StoredRun` carries an environment field. `README.md:262` lists `run` gaining `--environment` (scene profile) as a shipped `[x]` feature.
- *Impact:* An accepted flag whose value has no observable effect in the default backend and no record afterwards.
- *Effort:* ~1 day to implement, or ~1 h to remove.

**P1-43 · `gate --json` documents two reason tokens no code path can produce, and the flag-pair error is not JSON at all**
- *Area:* CLI / diagnostics — `read:I1-tests-cli-web`
- *Evidence:* `tests/test_cli_gate_diagnosis.py:27-32` documents the reason vocabulary as `below_threshold, engine_blocked, not_found, invalid_run_id, no_runs, ephemeral_store, missing_run_id, ambiguous_run_selector`. Grepping every `_gate_usage_error` call site (`validsim/cli.py:249, 273, 292, 360`) plus the block-reason branches (`:710, 715, 718, 720`) shows only `not_found`, `ephemeral_store`, `no_runs`, `invalid_run_id`, `below_threshold` and `engine_blocked` are ever emitted; `missing_run_id` and `ambiguous_run_selector` appear nowhere in the production tree. The actual missing-flag case is handled by Click before the command body runs: `validsim gate --json` with no flags prints a usage message to stdout — **not JSON** — so a machine consumer gets a parse error rather than a diagnosable payload. The test at `test_cli_gate_diagnosis.py:275-280` acknowledges this.
- *Impact:* The `--json` contract "always emits exactly one JSON object on stdout, for every outcome" (`test_cli_gate_diagnosis.py:22-24`) does not hold. CI parses this output.
- *Effort:* ~2 h.

**P1-44 · The queue has no way to remove a job, so terminal records accumulate without bound**
- *Area:* queue / API surface — `read:D-jobs-cli`
- *Evidence:* `validsim/jobs/queue.py` defines no delete/purge/prune method (`dir(JobQueue)` has no removal method), and the router exposes only `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/status`, `GET /jobs/{id}/events` (`router.py:117, 160, 212, 232, 310`) — no cancel and no delete. `queue.py:1140-1149` notes terminal records are deliberately excluded from the depth cap, so a long-lived queue accumulates `done`/`failed`/`dead` records without limit.
- *Impact:* Unbounded growth in the queue's own storage, and no way for a client to remove a job it no longer cares about.
- *Effort:* ~4 h (a cancel endpoint plus a retention policy).

**P1-45 · The `renew_lease` timestamp bypasses the queue's own clock seam**
- *Area:* queue — `read:D-jobs-cli`
- *Evidence:* `validsim/jobs/queue.py:1358` computes `lease_expires_at=_lease_deadline(_utc_now(), self._lease_seconds)` while every other queue timestamp goes through `self._now()` (`queue.py:1291-1305`, which applies the per-queue `_clock_offset_seconds` test seam). Verified: with `lease_seconds=100` and `advance_clock(200)`, the queue's own clock reads 200 s ahead but `renew_lease` stamps a deadline that `_is_expired(renewed, queue.now())` reports as `True` — the reaper would immediately reclaim a lease the heartbeat just renewed.
- *Impact:* In production `_clock_offset_seconds` is always 0, so this is a test-seam inconsistency rather than a live bug — but it is the exact class of seam error the `now()` docstring at `queue.py:1296-1304` says the seam exists to prevent.
- *Effort:* ~30 min.

**P1-46 · The mock cannot distinguish two checkpoints that share a tier, contradicting the documented per-(checkpoint, seed) contract**
- *Area:* simulation / mock fidelity — `read:E1-sim-core`
- *Evidence:* `validsim/sim/runner.py:95-111` maps a `checkpoint_id` to one of 5 tiers via `zlib.crc32(checkpoint_id) % 5`, and the docstring (`:24-25`) justifies this as "the checkpoint shifts the *probability*, never the stream, so (checkpoint, seed) still reproduces exactly". Because the tier is a pure function of the id and the RNG stream is seeded from `seed` alone, two *different* checkpoints in the same tier produce the same success probability and consume the same RNG draws. Measured: among 200 ids `ckpt-0`…`ckpt-199`, 45 share `ckpt-0`'s tier. Verified: `ckpt-0` and `ckpt-3` produce `EpisodeResult`s that are identical (`==`) across 200 different seeds. `docs/isaac-worker.md:106-107` states "the same seed against a *different* checkpoint is a different episode, and must be allowed to differ".
- *Impact:* The shadow harness's comparison (P1-17) therefore cannot detect a worker that ignores the checkpoint — the mock gives it nothing to notice.
- *Effort:* ~1 day — seed the stream from `(checkpoint_id, seed)` as well.

**P1-47 · The mock silently ignores one sampled scenario parameter, contradicting its own documented contract**
- *Area:* simulation / mock fidelity — `read:E1-sim-core`
- *Evidence:* `validsim/sim/runner.py:287-288` states the contract for `_PARAM_STRESS_BY_CATEGORY`: "every parameter named by `validsim.scenarios.generator._PARAM_SAMPLERS` for that category is consumed." It is not: `generator.py:212` samples `color_temperature_k` for the `lighting_change` category, but `_stress_lighting` (`runner.py:168-178`) reads only `lux` and `flicker_hz`. Verified empirically: two `lighting_change` scenarios identical except `color_temperature_k` (2500 vs 7500) produce the same `param_stress` (0.25196612311994704) and bit-identical `EpisodeResult`s across all 30 seeds tested.
- *Impact:* A documented invariant with no test backing it, and one parameter silently ignored — the adversarial scenario varies a quantity the simulation never sees.
- *Effort:* ~1 h.

**P1-48 · `AdversarialScenario` and `EpisodeResult` are declared frozen but carry a mutable dict, and are unhashable**
- *Area:* scenario generation / determinism — `read:E1-sim-core`
- *Evidence:* `validsim/scenarios/generator.py:68-90` declares `@dataclass(frozen=True)` for `AdversarialScenario`, but `params: dict[str, Any] = field(default_factory=dict)` is a plain mutable dict. Verified: `s.params['x'] = 1` succeeds on a frozen instance. The class also has no usable `__hash__` — `hash(s)` raises `TypeError: unhashable type: 'dict'`, and so does `hash(EpisodeResult)` (`runner.py:337-371`, same dict field `joint_states_summary`). Contrast the store, which goes to real trouble to freeze nested dicts on the read path (`validsim/store/_frozen.py:149-187`) and explicitly names `joint_states_summary` as one of the mappings it protects.
- *Impact:* The protection is applied at the *store* boundary only, not at the producing boundary — a scenario can be mutated between generation and execution, and the "frozen" marker is a promise the dataclass does not keep.
- *Effort:* ~2 h.

**P1-49 · `compare_scorecards` can emit a bare `NaN` token into JSON and silently report a NaN composite as a tie**
- *Area:* benchmark / invalid JSON — `read:A-engine`
- *Evidence:* `validsim/engine/benchmark.py:43-56` uses `as_float`, and `validsim/engine/_coerce.py:54-57` documents that inf/nan "still pass through, because float(inf) succeeds". Verified: `compare_scorecards({'composite_score': nan, …}, {…})` returns `winner='tie'` for the composite metric, `delta=nan`, and `json.dumps` of the result emits the bare token `NaN` — not valid JSON. The module docstring (`benchmark.py:9-10`) states the overall verdict is "decided by the composite metric alone", so a NaN composite silently yields "tie". The module is currently unwired, so this is latent rather than live.
- *Impact:* A latent invalid-JSON producer. Worth fixing before the module is wired, because it is on the export path for every scorecard that embeds a comparison.
- *Effort:* ~1 h.

**P1-50 · The Postgres store cannot support a gapless append-only chain, which gates the audit ledger and the flywheel**
- *Area:* store / data integrity — `read:K-vault`
- *Evidence:* `validsim/store/postgres.py:474` opens `psycopg.connect(self._dsn, autocommit=True)` and the module docstring at `:393` acknowledges the "autocommit connection guarded by a threading.Lock (psycopg" pattern; `grep -n "advisory" validsim/store/postgres.py` returns nothing. Because the API and the job worker are separate processes (ADR 0003), a SELECT-head/INSERT append races.
- *Impact:* The vault's own conclusion is that this must be solved once for both the compliance ledger and the "flywheel" (per-episode outcome mining), and that `episode_outcomes` must not ship append-only before it lands.
- *Effort:* ~1 week (a Postgres advisory lock or a serialisable transaction).

### P2 — quality and documentation drift

**P2-1 · `api-reference.md` documents the composite formula and the APPROVE rule wrongly**
- *Area:* docs — `read:A-engine`, `read:J1-docs`
- *Evidence:* `docs/api-reference.md:262-265` gives the flat formula `0.4*success + 0.3*safety + 0.2*robustness + 0.1*regression` with no renormalisation; `:272` says "`regression_baseline_available: false` means the regression component scored `100.0`" but `validsim/engine/scorecard.py:285-297` returns `_unmeasured_component_credit()` = 0.0; `:284-286` says APPROVE requires only `task.episodes > 0`, `episode_count >= task.episodes` and `composite_score >= threshold`, but `scorecard.py:523-527` also requires `total_episodes >= _MIN_EVIDENCE_EPISODES` (10) and `success_count > 0`; the worked example at `:241-257` claims `composite_score 95.3` and `robustness_score: 100.0` where the code produces 0.0 for robustness and the arithmetic gives 93.29. **Verified during compilation** — the text at `:262-265`, `:272` and `:284-286` is as quoted. `README.md:224-226` and `VALIDATION_SCHEMA.md:327-341` describe the abstain policy correctly.
- *Impact:* The canonical API reference misdescribes the scoring formula, the abstention policy, and the approval rule — the three things a client integrating against the gate most needs to know. A 5/5 perfect run is APPROVE per the doc, BLOCK per the code.
- *Effort:* ~2 h to re-derive the section.

**P2-2 · `api-reference.md` says `checkpoint_sha256` is "accepted, then discarded" — it is persisted end to end**
- *Area:* docs — `read:J1-docs`
- *Evidence:* `docs/api-reference.md:201-219` states the field is "accepted but unused", that "`Scorecard` has no such field … The stored run therefore carries **no** digest"; `docs/runbook.md:356-360` repeats it. All four clauses are false: `validsim/engine/scorecard.py:422` declares the field, `:593` populates it; `validsim/engine/pipeline.py:88/171` takes it as a parameter, `validsim/api/main.py:786` passes it through, `validsim/cli.py:427/459/514` exposes `--checkpoint-sha256`, `validsim/engine/export.py:125-126` renders it. Commit `9eb12db` did the work.
- *Impact:* A feature that works is documented as absent, and the doc advises readers to "treat it as reserved-but-unwired". *(Note the API path validates length only — `validsim/config.py:185`, no hex pattern — while the CLI path validates hex via `validsim/cli.py:392` `_normalise_sha256`. That asymmetry is itself undocumented.)*
- *Effort:* ~1 h.

**P2-3 · `api-reference.md` and `docs/runbook.md` both list three rate-limited write routes; the code also rate-limits DELETE**
- *Area:* docs — `read:J1-docs`
- *Evidence:* `docs/api-reference.md:149-156` says "`*` marks the three **write** routes covered by the IP-keyed sliding-window limiter — `POST /validations`, `POST /validations/{run_id}/compare`, and `POST /jobs`" plus "Every read route is unlimited", and `:164` lists DELETE with "Rate limit | None". `validsim/api/main.py:447-479` (`_is_rate_limited_request`) also rate-limits `DELETE /api/v1/validations/{run_id}`. Verified empirically with `VALIDSIM_RATE_LIMIT=3`: six DELETEs returned `[404, 404, 404, 429, 429, 429]`. The same drift is in `docs/runbook.md:75` and `:126`; `docs/api-reference.md:928-930` omits DELETE from the 429 row.
- *Impact:* The doc understates the control in one place and contradicts it in another.
- *Effort:* ~1 h.

**P2-4 · `api-reference.md`'s health-payload table and `ValidationRequest` field table are both missing live fields**
- *Area:* docs — `read:J1-docs`
- *Evidence:* `docs/api-reference.md:626-648` gives the health response and a field table with exactly seven fields (status, version, auth_enabled, cors_wildcard, rate_limit, store_backend, job_queue_backend). The live payload has an eighth: `{"notify": {"enabled": false, "hook_count": 0, "live": 0, "hooks": []}}` (added by `docs/notify_wiring_patches.md` §2, which explicitly instructed adding it to the health docstring). Confirmed at runtime. Separately, `docs/api-reference.md:196-233`'s `ValidationRequest` field table omits `threshold`, which **is** in the request model (`validsim/config.py:186-189`) and **is** in the OpenAPI schema — confirmed by posting `threshold: 10.0` and getting a scorecard with `threshold: 10.0`.
- *Impact:* The threshold knob is the subject of `docs/ISSUE_CATALOG.md` item 41 ("Dashboard threshold knob was silently discarded — Done"), so its absence from the field table is the same class of defect that item closed.
- *Effort:* ~1 h.

**P2-5 · `docs/async-jobs.md` describes a queue depth check the code no longer performs**
- *Area:* docs — `read:D-jobs-cli`, `read:J1-docs`
- *Evidence:* `docs/async-jobs.md:346-347` says "the in-memory backend checks `len(jobs) >= max_depth` under its lock; the Redis backend checks `LLEN validsim:jobs:index >= max_depth`". Both halves are wrong now: the in-memory check counts only *pending* work (queued+running) at `validsim/jobs/queue.py:1156-1166`, and the Redis check is `LLEN KEYS[3]` — the *ready* list — at `queue.py:620` in `_REDIS_ENQUEUE_SCRIPT`, deliberately changed from the index with a long justification comment at `:606-615`. The caveat at `:350-355` (that the cap is "best-effort" under multi-process load) is also stale: the check is now a single server-side Lua operation, atomic across processes. `docs/ISSUE_CATALOG.md:69` (item 38) already says "both backends now count pending", so the two documents contradict each other inside `docs/`. **Verified during compilation.**
- *Impact:* An operator sizing `VALIDSIM_JOB_QUEUE_MAX_DEPTH` from the doc will reason about the wrong quantity and the wrong atomicity guarantee.
- *Effort:* ~1 h.

**P2-6 · `ScenarioGenerator.coverage()` and `difficulty_bias` are documented as shipped features with no production caller, and the dead-code gate does not flag them**
- *Area:* scenarios / docs — `read:E1-sim-core`, `read:I2-tests-misc`
- *Evidence:* `ScenarioGenerator.coverage(task_id, n)` (`validsim/scenarios/generator.py:160-184`) has no caller anywhere in `validsim/`. `difficulty_bias` (`:107-124`) is likewise unreachable: `grep -rn "difficulty_bias" validsim/config.py validsim/api/main.py validsim/cli.py` returns nothing, and `validsim/engine/pipeline.py:143` constructs `ScenarioGenerator(seed=seed)` with no bias. `README.md:274` marks "[x] Scenario difficulty bias + `coverage()`"; `docs/scenario-taxonomy.md:252-275` has a full section with a worked example. Unlike the LLM generator and the shadow harness, these two symbols are **not** on the `UNWIRED_BY_DESIGN` allowlist in `tests/test_wiring_reachability_agent.py`, so the gate does not flag them.
- *Impact:* Two documented features are inert in every shipped run, and the repo's own dead-code gate cannot see it.
- *Effort:* ~2 h to wire, ~1 h to allow-list and relabel.

**P2-7 · `render_scorecard_pdf` (the path-writing PDF variant) has no caller, and the CLI has no PDF option**
- *Area:* PDF / CLI — `read:I2-tests-misc`
- *Evidence:* `grep render_scorecard_pdf validsim/` returns only `validsim/engine/pdf.py:291` (the definition), the `__all__` entry at `:30`, and the `validsim/engine/__init__.py` re-export. The only production consumer of the PDF renderer is `validsim/api/main.py:116` and `:1303` (`scorecard_pdf_bytes`, for `GET /api/v1/validations/{run_id}/scorecard.pdf`). `validsim/cli.py`'s `report` command (`:591-606`) accepts only `--format markdown|html` and `--json`; `grep pdf validsim/cli.py` returns nothing. `tests/test_wiring_reachability_agent.py:107-109` already lists it as unwired.
- *Impact:* A CI job that wants a PDF artifact on disk cannot produce one through the CLI; only the API route can.
- *Effort:* ~1 h.

**P2-8 · No CI workflow builds, type-checks, or deploys the Astro site**
- *Area:* CI / site — `read:M-infra`, `read:L1-site-src`, `read:L2-site-public`
- *Evidence:* Grepping all three workflow files for `npm`, `astro` or `site/` returns **zero** matches. `site/package.json` declares `astro ^5.1.0`, `gsap ^3.12.5`, `typescript ^5.7.2` and the scripts `dev`/`build`/`preview`/`check`; `site/src/scripts/readoutScrub.ts` imports gsap and registers ScrollTrigger, and `site/src/scripts/runRecording.ts` drives a `<video>` overlay, so the site is not purely static markup. The Makefile has no site target. `site/dist/` is gitignored (`.gitignore:29`), so a fresh CI checkout has no built page, and `tests/test_site_astro_integrity_agent.py:259-261` begins `if not _DIST.exists(): return` — the guard silently no-ops.
- *Impact:* The one test that checks what a visitor actually sees never runs in CI. A broken `.astro` file or a TS type error ships unnoticed. Combined with P2-9 (the typecheck gate is a no-op), the site has no automated verification at all.
- *Effort:* ~3 h (a CI job + the missing package).

**P2-9 · `npm run check` is a no-op that exits 0, so the declared typecheck gate does not gate**
- *Area:* site / build tooling — `read:L1-site-src`
- *Evidence:* `site/package.json:11` declares `"check": "astro check"`, but `@astrojs/check` is in neither `package.json` dependencies/devDependencies nor `site/package-lock.json` (`'@astrojs/check' in d['packages']` → `False`). `astro/dist/cli/check/index.js:19-24` logs an ERROR and returns `undefined`; `astro/dist/cli/index.js:228` then runs `process.exit(typeof checkServer === "boolean" && checkServer ? 1 : 0)` — `undefined` is not a boolean, so the exit code is 0. Reproduced in a clean `npm ci`: `npm run check` printed `[ERROR] [check] The @astrojs/check and typescript packages are required` and exited 0. (`typescript` 5.9.3 *is* in the lock, so only `@astrojs/check` is missing.)
- *Impact:* The site has a typecheck script that reports success while doing nothing.
- *Effort:* ~30 min.

**P2-10 · `gsap` is a devDependency but is required at build time**
- *Area:* site / dependency classification — `read:L1-site-src`
- *Evidence:* `site/package.json:17` lists `gsap` in `devDependencies`, but `site/src/scripts/readoutScrub.ts:30-31` imports it at runtime and the built bundle (`site/dist/_astro/index.astro_astro_type_script_index_0_lang.BBe3fLvg.js`, 116,292 bytes) contains GSAP + ScrollTrigger. Reproduced: `npm ci --omit=dev` followed by `astro build` fails with `[ERROR] [vite]: Rollup failed to resolve import "gsap"` and exit 1.
- *Impact:* Any static host that installs production dependencies only — the standard for a Docker build stage — cannot build this site.
- *Effort:* ~15 min (move `gsap` to `dependencies`).

**P2-11 · Four of seven `@container` rules in the standalone marketing page can never match**
- *Area:* marketing / CSS — `read:L2-site-public`
- *Evidence:* `marketing/index.html:297` (`@container (width > 56rem) { .hero-grid {…} }`) and `:327` sit inside no element that declares `container-type` (only `.stages`/`.method`/`.faq`/`.tiers` do, at `:378/389/414/424`). Worse, `:390` and `:415` target the container element itself, which per CSS Containment can never query its own size. Measured in Chromium at 1440×900: `.hero-grid`/`.readout`/`.method`/`.tiers` all compute `grid-template-columns: 1160px` (one column) instead of `2fr 1fr` / `auto 1fr` / `repeat(3,1fr)` / `repeat(4,1fr)`. The page's own header comment (`:117-119`) claims `@container` as one of the features that replaced the JS, and `tests/test_marketing_css_agent.py:62-71` asserts `@container` is present — both are string checks that pass while the effect is dead.
- *Impact:* The page ships a single-column layout that contradicts its own comments and its own passing test. The hero's documented asymmetry (the honesty note beside the h1) is absent.
- *Effort:* ~2 h.

**P2-12 · The public run video and poster record a different run than the page claims**
- *Area:* site / public assets — `read:L2-site-public`
- *Evidence:* Decoding the webm (frame 200, 1280×720) and reading the poster PNG both show `Run ID: vrun-dd87bc21`; the page text says `vrun-ed0c150a` (`marketing/index.html:582`; `site/src/data/content.ts:34,60`). The video's terminal line reads `composite 72.69 is below the configured threshold 85.00`; the page reads `72.71` (`marketing/index.html:592`; `content.ts:70`). `vrun-dd87bc21` is a well-formed id (`validsim/jobs/queue.py:1137` mints `vrun-<8 hex>`), so this is a genuinely different run, not a formatting variant. `site/src/components/Recording.astro:58-59` claims "Nothing here is staged: the run id is the one the Action gated on", and `:9` claims "the same run ends up in three places with the same verdict". `grep 'site/public|validsim-run|poster'` across `tests/` returns nothing.
- *Impact:* The site's central authenticity claim — that the shown run is the real gated run — is contradicted by the assets it ships. No test covers either binary.
- *Effort:* ~2 h to re-record, plus a test that decodes the asset.

**P2-13 · Two GitHub Actions workflows pin the same action to different major versions, and CI installs its dev tools inline**
- *Area:* release process / supply chain — `read:N-root-docs`
- *Evidence:* `.github/workflows/ci.yml:240` uses `docker/build-push-action@10e90e3645… # v6.19.2`; `.github/workflows/release.yml:121` uses `docker/build-push-action@ca052bb54a… # v5.4.0`. Both are SHA-pinned (good) but a major version apart, so the release pipeline builds with a different action major than the CI pipeline that gates it. Separately, `ci.yml` installs `ruff`/`pytest-cov` inline rather than from `requirements-dev.txt` (which contains PyYAML), so a test importing `yaml` would pass in `nightly.yml`/`release.yml` and fail in CI.
- *Impact:* A divergence in the gate that is supposed to be authoritative.
- *Effort:* ~1 h.

**P2-14 · The nightly's slow benchmark tier can never fail**
- *Area:* CI / benchmark gate — `read:M-infra`, `read:I2-tests-misc`
- *Evidence:* `tests/bench/conftest.py:160-172` explicitly `continue`s when a benchmark name is absent from the baseline. Confirmed against `tests/bench/baseline.json`: neither `run_and_score_episodes50000` nor `run_and_score_episodes100000` (the two slow-tier benchmarks at `tests/bench/test_bench_pipeline.py:41`) is in the baseline, and the third slow test (`tests/bench/test_bench_stability.py:75`) records no baseline name at all. The committed baseline holds 55 fast-tier names. `nightly.yml:134-139` runs the slow tier and its `REGRESSION` grep can never fire for it.
- *Impact:* The slow tier of the benchmark gate is structurally incapable of failing. The conftest's own comment names the missing artefact (a per-tier baseline) as the fix.
- *Effort:* ~2 h (generate a slow-tier baseline).

**P2-15 · The nightly benchmark gate's `grep '^REGRESSION '` anchor may not match pytest's coloured output**
- *Area:* CI / benchmark gate — `read:M-infra`
- *Evidence:* `nightly.yml:159` greps `'^REGRESSION '` against the tee'd log. `tests/bench/conftest.py:297` writes it via `terminalreporter.write_line(f"REGRESSION {f}", red=True)`, which routes through pytest's terminal writer and wraps the text in an ANSI escape sequence when colour is enabled. The surrounding output was confirmed ANSI-wrapped in this environment.
- *Impact:* The nightly's failure path depends on a literal anchor matching a colour-wrapped line.
- *Effort:* ~30 min (match on the unanchored marker, or disable colour).

**P2-16 · The CI postgres job can pass vacuously**
- *Area:* CI / test coverage — `read:M-infra`
- *Evidence:* `ci.yml:143-148` runs `pytest tests/test_store_postgres.py tests/test_store_differential_agent.py` with `VALIDSIM_PG_URL` set. `tests/test_store_postgres.py:661` and `:699` gate the two live classes on `@pytest.mark.skipif(not _postgres_is_configured())` (`:587`). With no DSN, that file reports 4 skips. The **redis** job (`ci.yml:196-218`) has an explicit anti-vacuous-pass probe asserting `tests>10` and `skipped==0`; the postgres job has no equivalent.
- *Impact:* A DSN misconfiguration (service container healthy, DSN pointing at the wrong host) yields 4 skipped tests and a green job — the production backend silently untested.
- *Effort:* ~1 h (copy the redis job's probe).

**P2-17 · `docs/testing.md` says `ci.yml` has four jobs; it now has five**
- *Area:* docs / CI — `read:J1-docs`
- *Evidence:* `docs/testing.md:417-427` says "`.github/workflows/ci.yml` has four jobs" and lists test / postgres / docker / compose, with `docker` and `compose` both declaring `needs: [test, postgres]`. The workflow has five: `ci.yml:30` (test), `:108` (postgres), `:150` (redis — "Redis queue integration"), `:228` (docker), `:248` (compose), and both `:231` and `:251` declare `needs: [test, postgres, redis]`.
- *Impact:* The Redis-backed production queue with a Lua claim/finish/reap path — the component with the most concurrency subtlety in the product — is omitted from the CI story.
- *Effort:* ~30 min.

**P2-18 · `docs/github-actions.md` and `docs/api-reference.md` describe a four-state job lifecycle; the code has five**
- *Area:* docs / jobs — `read:J1-docs`, `read:K-vault`
- *Evidence:* `docs/github-actions.md:369` says "the four-state job lifecycle"; `docs/api-reference.md:750` says "`status` ∈ `queued | running | done | failed`" and `:971` repeats it. The shipped enum has five: `validsim/jobs/models.py:18-34` — `QUEUED/RUNNING/DONE/FAILED/DEAD` — with `TERMINAL_STATUSES` at `:38-41` including DEAD. `docs/async-jobs.md:85` documents the `dead` state correctly, so the docs contradict each other. The vault's `04 - Engineering/Job Queue Worker.md:101-116` also still presents the four-state lifecycle and calls the richer sketch "target" — but the dead-letter queue and the leases are shipped; only priority/evaluating/reported remain target.
- *Impact:* A consumer documenting against the four-state set will mishandle a `dead` job — and `dead` is the state a job reaches when its retry budget is spent.
- *Effort:* ~1 h.

**P2-19 · ADR 0004 states the opposite of the code on the store's most consequential property**
- *Area:* ADR / store contract — `read:J1-docs`
- *Evidence:* `docs/adr/0004-store-abstraction-sqlite-postgres.md:1` titles itself "SQLite (overwrite) and PostgreSQL (append-only)"; `:56-76` "### Append-only Postgres vs. overwrite SQLite" / "**SQLite — overwrite.** `save` issues `INSERT OR REPLACE`"; `:95-98` "**`save` is not idempotent across tiers.** Re-saving the same `run_id` updates SQLite/memory but is a no-op on Postgres." The code has since changed: `validsim/store/sqlite.py:183-187` is `ON CONFLICT (run_id) DO NOTHING` and `validsim/store/memory.py:345` is `self._runs.setdefault(run.run_id, _snapshot(run))` — **verified during compilation**. Commit `8a25810` unified all three on append-only. ADR 0001 (`docs/adr/0001-record-architecture-decisions.md:39-40`) requires a superseding ADR, not an edit, so this needs a new ADR.
- *Impact:* An Accepted, load-bearing ADR states the opposite of the code on the write contract of the system of record. Every downstream document that reasoned from the old split is affected (P2-20).
- *Effort:* ~2 h (a superseding ADR).

**P2-20 · The same stale save-semantics claim is repeated in three more documents**
- *Area:* docs — `read:J1-docs`, `read:H-tests-backend`
- *Evidence:* `docs/ISSUE_CATALOG.md:304` — "memory (`store/memory.py:81`) and SQLite (`INSERT OR REPLACE`, `store/sqlite.py:198`) **overwrite**, while Postgres uses `ON CONFLICT (run_id) DO NOTHING`" — and again at `:635-638`. The cited line numbers point at unrelated code (`memory.py:81` is inside `reconstruct_kwargs`' docstring; `sqlite.py:198` is a tuple element of `card_params`). `docs/PLATFORM_PROPOSAL.md:1345` and `:1367-1371` assert the same split, with a correction note at `:1512-1516` ("memory/SQLite overwrite with attempt #N. **Both under-bill.**") and an appendix row at `:1808`. The suite already pins the true contract (`tests/test_store_divergence_agent.py:661-671` asserts the conflict clause in both files; `tests/test_store_parity.py:84-198` pins append-only on every read path). **Verified during compilation:** `docs/ISSUE_CATALOG.md:304` reads as quoted.
- *Impact:* The doc contradicts both the code and the tests. A "Platform Proposal" that reasons from the wrong write contract mis-prices every retry.
- *Effort:* ~2 h.

**P2-21 · `docs/ISSUE_CATALOG.md` is a point-in-time snapshot that is not marked as one, and cross-references a section that does not exist**
- *Area:* docs / internal consistency — `read:J1-docs`
- *Evidence:* `docs/ISSUE_CATALOG.md:542` and `:613` point at "§7.1", which does not exist — §7 (line 617) is "Product decisions this work cannot make for you", a numbered list, not a subsection. The catalog's header (line 3) pins "Baseline commit: ff15533 … 227 tracked files" against a tree now at HEAD `2ffeb6c` with 395 tracked files, and its §1 status table (lines 32-83) marks 50+ items "✅ Done" while its body sections still read as live findings. The sibling `docs/ISSUES_AND_REMEDIATION_CATALOG.md` (assessment 2026-10-15, baseline `98cc723`) is the current catalog, and its DOC-006 entry (line 355-370) already recommends "Label the old document as a historical snapshot" — the label was never applied. Separately, `docs/ISSUE_CATALOG.md:639-641` still says "WebhookDispatcher/EmailNotifier are a library only" (the webhook is wired — P1-2).
- *Impact:* The reader who trusts this catalog will believe the product's core gate is broken, and will lose the trail on a cross-reference.
- *Effort:* ~1 h.

**P2-22 · `docs/ISSUES_AND_REMEDIATION_CATALOG.md` still ranks a fixed defect as P0 #1**
- *Area:* docs / severity claims — `read:J1-docs`
- *Evidence:* `docs/ISSUES_AND_REMEDIATION_CATALOG.md:671-699` — "SEC-001 — API authentication fails open by default … **Severity:** High", `:605-604` "**CVSS v3.1:** 9.8", with remediation "Fail closed by default for network-serving profiles". The shipped code refuses to start: `validsim/api/main.py:1046-1051` raises `RuntimeError` when `VALIDSIM_API_KEY` is None and `VALIDSIM_ENV` is production. The catalog's §1 ranked table still lists SEC-001 at rank 1, severity "High*", priority P0, effort 2-4 h, with no remediation banner — the banner (lines 9-32) covers only SEC-007, SEC-008, BUG-002 and BUG-003.
- *Impact:* The current remediation roadmap starts with a fixed defect, so the ranked table misdirects the reader's first hour.
- *Effort:* ~30 min.

**P2-23 · `docs/PLATFORM_PROPOSAL.md`'s runbook line citations no longer resolve**
- *Area:* docs — `read:J1-docs`
- *Evidence:* `docs/PLATFORM_PROPOSAL.md:16` and `:1786` cite "`docs/runbook.md:345`" for "There is no Kubernetes manifest, Terraform state, deployment controller, or fleet rollback command in this repository" — that text is now at `runbook.md:417`; `:20` and `:1787` cite "`docs/runbook.md:350-356`" for the no-scheduler claim, now at `runbook.md:425-433`; `:554-555` uses the same stale numbers. The proposal's own header (line 4) promises "Every 'today' claim is file:line verifiable" — that guarantee no longer holds.
- *Impact:* The proposal's central credibility claim is broken by its own citation drift.
- *Effort:* ~1 h.

**P2-24 · `ARCHITECTURE.md` documents modules, files, exit codes and pages that do not exist**
- *Area:* docs — `read:N-root-docs`
- *Evidence:* `ARCHITECTURE.md:157` documents a "**Store Interface Contract** (`validsim/store/base.py`)" with methods `create_run`/`update_status`/`complete_run`/`get_run`/`history`/`delete`/`close`. No `validsim/store/base.py` exists; the contract is `validsim/store/memory.py:250` (`class ValidationStore`) with `save`/`save_many`/`save_exists`/`get`/`delete`/`list_for_checkpoint`/`history`/`count`/`close`. `ARCHITECTURE.md:250-265` shows a `create_store()` that returns `MemoryStore()` and raises on an unknown backend; the real `validsim/store/__init__.py:46-80` returns `ValidationStore()` for anything unrecognised and never raises — **verified during compilation.** `ARCHITECTURE.md:83-86` lists `validsim/api/auth.py`, `validsim/api/ratelimit.py` and `validsim/api/access.py` as "Key Files"; none exists (the code is in `validsim/api/main.py`). `ARCHITECTURE.md:341-349` documents CLI exit codes "3 | Validation blocked (gate failed) | 4 | API/network error" — the code has no exit code 3 or 4: `validsim/cli.py:98` `_GATE_USAGE_EXIT = 2`, `:100` `_GATE_BLOCKED_EXIT = 1` — **verified during compilation**; a BLOCK exits 1, not 3. `ARCHITECTURE.md:322-324` gives the console script as `validsim = "validsim.cli:main"` while `pyproject.toml:44` says `validsim = "validsim.cli:app"`, and no `main` symbol exists in `cli.py`. `ARCHITECTURE.md:365-371` lists five dashboard pages under `public/` (`validations/index.html`, `validations/[id].html`, `models/index.html`, `compare/[id].html`) — the shipped dashboard is a single-page SPA (`validsim/web/` = `app.js`, `index.html`, `styles.css`, `view-core.js`; `validsim/api/dashboard.py:59-62` serves `INDEX_PATH` for both `/` and `/api/v1/dashboard/`), and there is no `public/` directory in the repo. `CURRENT_STATUS.md:173-230` and `:276` repeat the same `public/` tree.
- *Impact:* The contributor-facing architecture reference describes a different system. The exit-code error is the load-bearing one: `actions/validate/action.yml` and `docs/github-actions.md` parse those exit codes, and the doc tells a reader the BLOCK code is 3 when it is 1.
- *Effort:* ~4 h.

**P2-25 · `ARCHITECTURE.md`'s store-divergence and close-semantics notes are stale**
- *Area:* docs — `read:N-root-docs`
- *Evidence:* `ARCHITECTURE.md:176-179` says "Close semantics (SQLite raises `ProgrammingError` on double-close; others are idempotent)". The code has converged on idempotent close: `validsim/store/sqlite.py:500-517` — "Idempotent (a second `close()` is a no-op) and safe on a never-opened store" — with transparent reopen — **verified during compilation.** `ARCHITECTURE.md:640-644` tells a contributor that if `test_store_divergence_agent.py::test_div4b_double_close_is_harmless_everywhere` fails, the fix is "Test expectations wrong; skip test with `--deselect` flag" — that test is now a plain passing test (`tests/test_store_divergence_agent.py:450-459`) whose docstring records that the old opposite assertion was retracted. `ARCHITECTURE.md:277-279` says "No migrations implemented yet" while the stores already do versionless ALTER-on-open (`validsim/store/sqlite.py:78-85` `_MIGRATION_COLUMNS`, `validsim/store/postgres.py:114-120`).
- *Impact:* The troubleshooting advice would have someone deselect a healthy test, and the migrations section sends a reader to build a parallel system rather than to add `_schema_version` to the DDL that already exists.
- *Effort:* ~2 h.

**P2-26 · `VALIDATION_SCHEMA.md` points at a migration script that does not exist, and its DDL does not match the code**
- *Area:* docs — `read:N-root-docs`
- *Evidence:* `VALIDATION_SCHEMA.md:577` says "3. **Document in this file** under 'Schema Evolution' 4. **Run migration** for existing databases (`validsim/migrate.py`)". No `validsim/migrate.py` exists and nothing imports it; the real mechanism is the ALTER-on-open DDL. The spec's own header says "**Version:** 1.0 / **Last Updated:** 2026-10-08". Its storage DDL (`:491-518` and `:530-558`) is not the DDL the code creates: the real tables are `run_id, checkpoint_id, task_id, composite_score, deploy_decision, created_at, scorecard_json` (`validsim/store/sqlite.py:52-66`) and `…, scorecard JSONB` (`validsim/store/postgres.py:96-104`) — with no per-field columns for `success_rate`, `safety_score`, `robustness_score`, `block_reasons`, `confidence_interval_low/high` or `adversarial_*` at all. Line references are also wrong: `:26` says `EpisodeResult` is at `validsim/sim/runner.py:30` (it is at `:338`), `:56` says `EvaluationResult` is at `validsim/engine/evaluation.py:15` (it is at `:17`), `:256` says `Scorecard` is at `validsim/engine/scorecard.py:328` (it is at `:346`).
- *Impact:* The canonical reference third parties are pointed at describes a schema the product does not use, and a migration procedure a contributor cannot execute.
- *Effort:* ~3 h.

**P2-27 · `SECURITY.md` §6 says the CORS default is `*`; the code's default is an empty allow-list**
- *Area:* security disclosure — `read:N-root-docs`
- *Evidence:* `SECURITY.md:250-253` — "The default is still `*`, which is an unsafe default by the §3 definition — narrowing it is tracked as open work, so a report saying 'the CORS default is a wildcard' is already known and does not need a PoC." The code contradicts it: `validsim/api/main.py:930-939` — "The default is an EMPTY allow-list, deliberately not `*`" — with `raw_cors = os.environ.get("VALIDSIM_CORS_ORIGINS")` and `""` as the fallback, plus `main.py:1050-1054` refusing `*` when `VALIDSIM_ENV=production`. **Verified during compilation** — `main.py:937` is the empty-allow-list read. Commit `5796dc7` (2026-10-17) landed the fix; SECURITY.md was never updated.
- *Impact:* §3 tells reporters a CORS-wildcard report "does not need a PoC" and §6 tells them the default is unsafe. Both are now false, in the document a reporter reads to calibrate.
- *Effort:* <1 h.

**P2-28 · `CONTRIBUTING.md` tells contributors the notification layer is unreachable; the webhook channel is wired**
- *Area:* contributor onboarding — `read:N-root-docs`
- *Evidence:* `CONTRIBUTING.md:199` — "Two capabilities are implemented and tested but **not reachable from any entrypoint** … the webhook/SMTP notification layer (`validsim/notify/`) and the LLM scenario generator … `run_and_score` never constructs either". This is half false: `validsim/engine/pipeline.py:42` imports `notify_run_completion` and `:206` calls it on the new-save path. `README.md:122-138` and `:254` document the webhook correctly; only the *email* channel (`EmailNotifier`) is caller-less.
- *Impact:* The doc instructs contributors to describe a shipped feature as non-existent — the opposite error from the one it is trying to prevent.
- *Effort:* <1 h.

**P2-29 · `CURRENT_STATUS.md` and `WORK_PROGRESS.md` violate the repo's own no-hardcoded-test-count policy**
- *Area:* status reporting — `read:N-root-docs`
- *Evidence:* `README.md:14` states "**No test count is quoted here on purpose** — a hand-copied number goes stale on the next commit, and the figure has already contradicted itself across this repo's docs four separate times (338 / 601 / 1,230 / 1,274 / 1,328 / 1,330)"; `CONTRIBUTING.md:77` repeats the policy, and `docs/ISSUE_CATALOG.md:344` records its adoption as the resolution of item 18. But `CURRENT_STATUS.md:20, :48, :98, :104, :162, :163` and `:309` hardcode "3,083 tests" and "95.83% coverage" in six places, and `WORK_PROGRESS.md:48/:154/:159/:183` do the same. The current collection is 3,254 (`pytest tests/ --collect-only`, verified during compilation), so the quoted figure is already stale.
- *Impact:* The policy exists because the number contradicted itself seven times; the two status documents still carry the eighth contradiction.
- *Effort:* ~1 h.

**P2-30 · `CURRENT_STATUS.md` is pinned to a branch and commit that are no longer the tree, and contradicts itself on the comparison UI**
- *Area:* status reporting — `read:N-root-docs`
- *Evidence:* `CURRENT_STATUS.md:5` — "**Branch:** `feature/website-simulation-completion` @ `aabbef0`" — while the repo is on `main` at `2ffeb6c`. `CURRENT_STATUS.md:17-22` lists "✅ Astro-based marketing dashboard v0" as Founder 1's work while `:43` lists "Dashboard | 🟡 PARTIAL | `site/src/` (Astro) | Both". `:238-266` lists "Comparison UI … Needs visualization of difference metrics" and "Model Comparison Page" under "Low Priority (Next Work)", then `:274-282` marks "Comparison UI … [✅ COMPLETED]" under "High Priority" — the same feature, both ways, in one document. `:108` still lists "comparison UI still TODO" in the UX score justification.
- *Impact:* The status document is the first thing a new contributor reads, and it cannot be believed about anything it asserts.
- *Effort:* ~2 h.

**P2-31 · `CHANGELOG.md`'s `[Unreleased]` section omits every fix landed in the last ten days, and its release links point at example.com**
- *Area:* release process — `read:N-root-docs`
- *Evidence:* `CHANGELOG.md:176-178` — "[Unreleased]: https://example.com/validsim/compare/v0.2.0...HEAD / [0.2.0]: https://example.com/validsim/releases/tag/v0.1.0 / [0.1.0]: https://example.com/validsim/releases/tag/v0.1.0". `SECURITY.md:183-186` says "Nothing has been published yet: `validsim.__version__` is `0.2.0` and the repository carries no git tags" — confirmed, `git tag` returns empty. The `[Unreleased]` section omits every fix since 2026-10-11: notify dispatch wiring (`12237b3`), the CORS/metrics/SSRF closure (`5796dc7`), store record freezing (`8a25810`), checkpoint_sha256 persistence + the 1-episode APPROVE close (`9eb12db`), the benchmark baseline (`59f31c1`), and the logging rename + notification dedup (`4f411c5`, 2026-10-18).
- *Impact:* A reader of the changelog alone concludes the shipped `/metrics` and CORS behaviour, the store's append-only contract, and the notification wiring do not exist.
- *Effort:* ~1 h.

**P2-32 · The vault's flagship engineering-audit note is a quarter stale and prescribes a fix that already shipped**
- *Area:* strategy / engineering — `read:K-vault`
- *Evidence:* `vault/06 - Business/Technical Moat Features.md:86-157` names D9 ("the gate APPROVES a checkpoint failing 100% of its adversarial suite", "the most serious finding in this document") as the #1 unowned open defect (`:1326` — "Gate on adversarial success (D9) | 2-4 days | Most damaging finding"). The fix shipped differently: `validsim/engine/scorecard.py:107` `_ADVERSARIAL_SUCCESS_FLOOR = 0.60`, `:125` `_ADVERSARIAL_MIN_SAMPLES = 30`, `:300` `_adversarial_significant()` (one-sided exact binomial), `:327` `_adversarial_segment()` (positional split), `:533-554` the block that appends the reason. The scorecard exposes `adversarial_episode_count` and `adversarial_success_rate` (`:402-403`), which the vault never mentions — **verified during compilation.**
- *Impact:* The vault's pre-Nov-2 work plan is built around fixing a shipped defect, and will burn founder-hours on it. The same note's D7 (`EpisodeResult` lacks `scenario_category`) is also moot: the shipped fix derives the split positionally (`scorecard.py:327-341`), so no schema change was needed.
- *Effort:* ~2 h (update the note).

**P2-33 · Eight vault notes state that robustness and regression score 100.0 when unmeasured; the code scores 0.0 and abstains their weight**
- *Area:* strategy / engineering — `read:K-vault`
- *Evidence:* `vault/06 - Business/Technical Moat Features.md:41-66`, `:337-356` and `:1449-1450` say the unmeasured components score 100.0 and that "30 of the 100 points were awarded unconditionally" via "0.4·success + 0.3·safety + 30.0". The code does the opposite: `validsim/engine/scorecard.py:168-196` `_unmeasured_component_credit()` returns 0.0 ("absence of evidence earns nothing") and `:479-482` passes the `measured` flag per component so the weight leaves the denominator. The vault contradicts itself between two of its own notes — `vault/04 - Engineering/Module Specs.md:114-119` says "**Since 2026-09-29 an unmeasured component abstains** … It now scores **42.86 → BLOCK at any threshold**". The consequence: the vault's own D9 floor derivation (`0.4·s + 0.3·100 + 30.0 ≥ 85 ⇒ s ≥ 62.5`, `Technical Moat Features.md:95` and `:368`) is arithmetically wrong against shipped code, so the headline "approval floor is exactly 62.5% success" does not describe the product.
- *Impact:* The vault's headline quantitative claim about the product is wrong, and it is wrong in the direction that flatters the product.
- *Effort:* ~2 h.

**P2-34 · The vault's Build Status note claims CI regenerates it via `build.ps1`; no workflow invokes that script**
- *Area:* engineering / automation — `read:K-vault`, `read:M-infra`
- *Evidence:* `vault/00 - Dashboard/Build Status.md:11-15` says it is "Regenerated whenever the script runs: locally on demand, and in CI via `.github/workflows/ci.yml` (push/PR) and `nightly.yml`" and `:77` says "Do not edit manually." But `grep -rn build.ps1` across the repo returns only `scripts/build.ps1`'s own heredoc (line 129) — neither `ci.yml` nor `nightly.yml` invokes it, and `ci.yml`'s steps are lint/pytest/Postgres-parity/Redis only. `scripts/build.ps1:126-152` also emits a *simpler* Build Status than the committed note: it writes "**Pass rate (last $totalCount runs): $rate%**", whereas the committed note at `:48-50` says "**Pass rate: not 100%**" and flags the historical pass-rate line as wrong — so the generator, if it ever ran, would overwrite the careful hand-written note with the misleading computed percentage. `builds/` is gitignored (`.gitignore:24`), so the generator's input does not exist either. `docs/ISSUE_CATALOG.md:372` and `docs/ISSUES_AND_REMEDIATION_CATALOG.md:244` already record this contradiction.
- *Impact:* The "auto-generated, read-only" note is a frozen hand-edited artifact whose row is ~4 weeks stale, and the generator would corrupt it if it ran.
- *Effort:* ~1 h.

**P2-35 · Four vault notes state the rate limiter is off by default; the code turns it on by default (60/60s)**
- *Area:* engineering / security — `read:K-vault`
- *Evidence:* `vault/04 - Engineering/Security Hardening.md:73` lists "Distributed (Redis-backed) rate limiting" as roadmap and `vault/04 - Engineering/API Design.md:121` says "`0` disables, which is also the default"; `vault/09 - Risks/Risk Register.md:29` says "the IP-keyed process-local limiter is implemented but **off by default** (`VALIDSIM_RATE_LIMIT=0`)"; `vault/07 - Fundraising/Engineering Momentum Log.md:62` repeats it. The code: `validsim/api/main.py:37-40` — "Protection is **on by default** (`60` per `60`s); an absent, unparsable, or negative value falls back to that default, and only an explicit `0` disables it" — with `:178` `DEFAULT_RATE_LIMIT = 60`. `docs/ISSUE_CATALOG.md` item 15 records the change ("Done — on by default; only explicit 0 disables", 2026-09-25).
- *Impact:* The vault understates its own security posture in four notes, one of which is a risk-register row — the artifact a due-diligence reader reads first.
- *Effort:* ~1 h.

**P2-36 · The vault's IP Strategy claims the MIT CLI is "a thin client over the proprietary API"; the CLI runs the whole engine in-process**
- *Area:* legal / IP — `read:K-vault`
- *Evidence:* `vault/08 - Team & Legal/IP Strategy.md:31` — "The plugin is a thin client over the proprietary API anyway" — and `:22` ("CLI tool + GitHub Actions plugin | Open-source (MIT) for adoption"); `vault/06 - Business/Go-to-Market.md:32` repeats it. False of the shipped code: `validsim/cli.py:45` imports `run_and_score` and `:454` calls it in-process, and `actions/validate/action.yml` runs `pip install .` of the whole repo. `pyproject.toml:11` still reads `license = { text = "Proprietary" }`. The vault's own audit found this (`Technical Moat Features.md:1260-1304`, D1) but nothing was updated, and no client/core split exists in the tree.
- *Impact:* Publishing under MIT would disclose `validsim/engine/scorecard.py:57` (`_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1`), the safety weights, and `validsim/scenarios/generator.py` — the core IP. This is a legal-exposure item sitting in a note that says the opposite.
- *Effort:* ~2 h (a product/licensing decision, not a code change).

**P2-37 · `ICORPS_FIT_ANALYSIS.md` and `TECHSTARS_APPLICATION_ANSWERS.md` tell the founders to fix a scoring defect that is already fixed**
- *Area:* fundraising / application accuracy — `read:N-root-docs`
- *Evidence:* `ICORPS_FIT_ANALYSIS.md:68` — "The counterexample I reproduced earlier — **every episode fails, `success_rate = 0.0`, and the run still APPROVES at threshold 60** … is a *real* scoring defect you found yourself" — and `:98` "**Fix the composite-score defect**". `TECHSTARS_APPLICATION_ANSWERS.md:162` repeats it: "finding that our own deploy gate could approve a run where every simulation episode failed. We're fixing that". The defect is fixed: `validsim/engine/scorecard.py:526` requires `evaluation.success_count > 0`, and `:523-525` adds a `_MIN_EVIDENCE_EPISODES` floor so a 1-episode run cannot approve either (commit `9eb12db`).
- *Impact:* Both documents instruct the founders to tell an application panel they are still fixing a defect they have already fixed. The framing still works rhetorically — "we found and fixed our own gate's failure mode" — but the present-tense claims are false.
- *Effort:* ~1 h.

**P2-38 · `AUDIT_CATALOG_2026-09-26.md` and the `audit/` tree are not marked as historical snapshots**
- *Area:* audit hygiene — `read:N-root-docs`, `read:J2-audit`
- *Evidence:* `AUDIT_CATALOG_2026-09-26.md:52-54` says "## Priority 1 — CRITICAL, verified, still OPEN … They are **not** fixed because each changes what a score *means*" — listing items 51, 51b, 51d, 51c, 52, 53, 54. Every one has since been fixed (51-fix is documented as FIXED at `:75-111`; 51c-fix at `:155-194`; 51d is now `ON CONFLICT DO NOTHING` at `validsim/store/sqlite.py:187`; 53 is now an abstention at `scorecard.py:474`; 54 is now emitted at `validsim/engine/anomaly.py:235-250`). The P2 table at `:215-218` lists 13 HIGH findings of which at least 7 are closed (55, 59, 60, 62, 64, 66, and half of 67). Its "What still needs your decision" list (`:344-350`) asks for decisions on items that no longer exist as open questions. 48 commits have landed since the audit, and no file in the `audit/` tree carries a liveness marker. `cat3-c3-api.md` F10 is listed as "REFUTED", but `cat5-c5-ml.md` C5-09 and `cat5-c5-platform.md` still assert the opposite ("grep -c threshold validsim/api/main.py -> 0").
- *Impact:* A reader who trusts the catalog will believe the product's core gate is broken. This is the document a new contributor or reviewer is most likely to read as current.
- *Effort:* ~2 h (a dated banner plus per-item status).

**P2-39 · `project_config` documents a `validsim.settings` module and three CLI commands that do not exist**
- *Area:* config / spec drift — `read:B-store-config`
- *Evidence:* `validsim/project_config.py:20-22` says "this module's public API is the contract", and `:39-42` says "Those are separate concerns owned by :mod:`validsim.settings`". No `validsim/settings.py` exists; a repo-wide grep for `validsim.settings` returns exactly one hit — the reference inside `project_config.py:40` itself. The `effective_to_env` docstring (`:494-496`) describes its input as "the ``VALIDSIM_*``-named mapping produced by ``Settings.load(config, environ)``" — a function that does not exist. Separately, `project_config.py:61` says "This is the serializer ``validsim config show`` and ``validsim support-bundle`` must use", and `:436-437` names "``validsim config show``, ``validsim run --print-effective-config`` and ``validsim support-bundle``". The actual CLI commands are: run, validate, status, scorecard, report, gate, delete, models, version, health, compare, jobs, worker — no `config` group, no `support-bundle`, no `--print-effective-config` flag. `README.md:147` and `docs/notify_wiring_patches.md:128` both describe the masking as a property of a command that does not exist. Separately, `project_config.py:33-34` describes `config_to_env` as yielding "a ``VALIDSIM_*`` string mapping" while `POLICY_KEYS` (`:136-151`) are TOML-native names — `config_to_env({"threshold": 90.0})` returns `{'threshold': '90.0'}`.
- *Impact:* A consumer landing on this contract has no defined merge/precedence implementation to call and no command to call it from. The docstrings also mislabel the key namespace twice.
- *Effort:* ~2 h.

**P2-40 · Python 3.10 is declared as supported but the `tomli` fallback dependency is declared nowhere**
- *Area:* config / Python version support — `read:B-store-config`
- *Evidence:* `validsim/project_config.py:95-98` does `if sys.version_info >= (3, 11): import tomllib else: import tomli as tomllib`, and `:419-421` tells the user to "add 'tomli; python_version<\"3.11\"' for Python 3.10". `pyproject.toml:13` declares `requires-python = ">=3.10"`, but `tomli` appears in neither `pyproject.toml`'s `dependencies` (lines 18-27) nor `requirements.txt`. Verified by simulating the 3.10 branch: `ImportError: No module named 'tomli'`. CI runs only 3.12 (`.github/workflows/ci.yml:40,134,172`), so the declared 3.10 floor is untested. Already logged as BUILD-002 in `docs/ISSUES_AND_REMEDIATION_CATALOG.md:130-145`.
- *Impact:* A declared support floor that fails at import on the version it declares. Currently masked because the file-config path is itself unwired (P2-39).
- *Effort:* ~30 min.

**P2-41 · Two different pages render the same figure number, "Fig 0.6"**
- *Area:* marketing site — found by direct inspection during compilation (not reported by any chunk)
- *Evidence:* `site/src/components/Pricing.astro:9` renders `<p class="fig-label">Fig 0.6 — pricing</p>`, and `site/src/pages/privacy.astro:23` renders `<p class="fig-label">Fig 0.6 — privacy</p>`. These are on **different pages**: `Pricing.astro` is imported by `site/src/pages/index.astro`, `privacy.astro` is its own route. The other six rendered labels are unique (0.1 blocked, 0.2 method, 0.3 recording, 0.4 pipeline, 0.5 field, 0.7 faq), so this is the only collision. A *separate*, harmless instance of the same numbering: `site/src/components/Recording.astro:3` carries a stale `Fig 0.4` in a comment while its rendered label is `Fig 0.3`.
- *Impact:* The page's figure numbers are its own visual argument — "Fig 0.x" is a running index a reader can cite. Two different figures sharing a number on two different pages breaks that index, and a reader cross-referencing "Fig 0.6" gets an ambiguous target. Cosmetic, and low-traffic (the privacy page is not linked from any figure), so P2 rather than P1.
- *Effort:* ~10 min — renumber privacy to 0.7 and the FAQ to 0.8, or drop the numbering from the privacy page.

### Items cut as not-real-work

For the record, the following categories were removed during curation: (a) restatements of how the system works (e.g. "the store is append-only", "the composite renormalises over measured components", "`JobStatus` has five states") where the report was the chunk describing its own domain rather than a defect; (b) items already fixed in code and re-reported from a stale catalog without the reporter noticing the fix — e.g. SEC-001 (auth fail-open, fixed at `api/main.py:1046-1051`), the 1-episode APPROVE (fixed at `scorecard.py:523-525`), the SQLite-overwrite divergence (fixed at `sqlite.py:187`), the zero-success APPROVE (fixed at `scorecard.py:526`), the unwired-controls finding (now enforced by `tests/test_wiring_reachability_agent.py`); (c) pure speculation with no reproducible signal — e.g. "the obstacle `centred` term is dominated" (a modelling observation, not a defect), "the `centred` term saturates 95% of the time" (a distribution fact about a synthetic generator); (d) test-file cosmetics with no contract impact — e.g. a truncated comment in `tests/bench/test_bench_overload.py:76-77`, a whitespace-only line at `validsim/engine/pipeline.py:201` (ruff's selected rules `["E4","E5","E7","E9","F"]` exclude W291), `validsim/_log.py:12-15` still describing itself as `validsim.logging` (a renamed module's stale docstring, and `tests/test_logging_redaction.py:308-310` asserts the old name now raises); (e) the two near-duplicate entries merged into P0-20 and P0-26. Roughly 194 of the 234 reported items were cut or merged in this way.

---

## 5. Where the docs and the code disagree

Each contradiction below cites **both** sides. Grouped by cluster.

### Cluster A — scoring policy

| # | Doc says | Code says |
|---|---|---|
| A1 | `docs/api-reference.md:262-265` — composite is the flat `0.4*success + 0.3*safety + 0.2*robustness + 0.1*regression`; `:264` "With no baseline, the regression component is `100`" | `validsim/engine/scorecard.py:168-196` `_unmeasured_component_credit()` returns 0.0; `:479-482` excludes unmeasured components from **both** numerator and denominator |
| A2 | `docs/api-reference.md:272` — "`regression_baseline_available: false` means the regression component scored `100.0`" | `scorecard.py:285-297` `_regression_component(None)` returns 0.0 |
| A3 | `docs/api-reference.md:241-257` — worked example: `composite_score 95.3`, `robustness_score: 100.0` with `robustness_measured: false` | `scorecard.py:228-246` returns 0.0 for robustness below 2 groups; the example's own inputs recompute to 93.29, and the API returns `robustness_score: 0.0` and `composite_score: 70.25` for those inputs |
| A4 | `docs/api-reference.md:284-286` — APPROVE iff `task.episodes > 0 and episode_count >= task.episodes and composite_score >= threshold` | `scorecard.py:523-527` also requires `total_episodes >= _MIN_EVIDENCE_EPISODES` (10) **and** `success_count > 0` — a 5/5 perfect run is APPROVE per the doc, BLOCK per the code |
| A5 | `docs/api-reference.md:267-271` — "the `robustness_score: 100.0` … means the number is a constant" | The constant is now 0.0, not 100.0 (`scorecard.py:228-246`); the doc's warning is backwards |
| A6 | `vault/06 - Business/Technical Moat Features.md:41-66`, `:337-356`, `:1449-1450` — unmeasured components score 100.0; "30 of the 100 points were awarded unconditionally"; D9 floor is `0.4·s + 0.3·100 + 30.0 ≥ 85 ⇒ s ≥ 62.5` (`:95`, `:368`) | `scorecard.py:168-196` returns 0.0 and abstains the weight; `vault/04 - Engineering/Module Specs.md:114-119` (the same vault) says "an unmeasured component abstains … 42.86 → BLOCK at any threshold" |
| A7 | `vault/06 - Business/Technical Moat Features.md:86-157`, `:1326` — D9 ("the gate APPROVES a checkpoint failing 100% of its adversarial suite") is the #1 unowned open defect | `scorecard.py:107` `_ADVERSARIAL_SUCCESS_FLOOR = 0.60`, `:125` `_ADVERSARIAL_MIN_SAMPLES = 30`, `:300` `_adversarial_significant()`, `:327` `_adversarial_segment()`, `:533-554` — shipped, in a different shape; `scorecard.py:402-403` exposes `adversarial_episode_count`/`adversarial_success_rate`, which the vault never mentions |
| A8 | `vault/06 - Business/Technical Moat Features.md:250-257`, `:1327` — D7 (`EpisodeResult` lacks `scenario_category`) blocks the D9 fix | The shipped fix derives the split **positionally** (`scorecard.py:327-341`), so no schema change was needed; `grep -rn scenario_category validsim/` returns nothing, correctly |
| A9 | `ICORPS_FIT_ANALYSIS.md:68`, `:98`; `TECHSTARS_APPLICATION_ANSWERS.md:162` — "every episode fails … the run still APPROVES at threshold 60 … We're fixing that" | `scorecard.py:526` requires `success_count > 0`; `:523-525` adds the 10-episode evidence floor (commit `9eb12db`) |
| A10 | `docs/api-reference.md:201-219` — `checkpoint_sha256` is "accepted but unused"; "`Scorecard` has no such field … The stored run therefore carries **no** digest"; `docs/runbook.md:356-360` repeats it | `scorecard.py:422` declares it, `:593` populates it; `pipeline.py:88/171`, `api/main.py:786`, `cli.py:427/459/514`, `export.py:125-126` all carry it (commit `9eb12db`) |
| A11 | `README.md:149`; `validsim/config.py:39` — a three-level `info`/`warn`/`critical` severity contract | `validsim/notify/dispatcher.py:154` — `warn` is unreachable: every BLOCK carries either a `_CRITICAL_BLOCK_REASON_PREFIXES` reason (`dispatcher.py:101-104`) or a sub-threshold composite (`scorecard.py:561`), so the middle tier never fires |
| A12 | `docs/isaac-worker.md:165-168` (§5 step 3) — track success-rate delta per adversarial category, the failure-taxonomy distribution and the safety observables | `validsim/sim/shadow.py:84-118` `ShadowReport` carries one aggregate success rate; `shadow.py:214-239` compares one number |

### Cluster B — notification

| # | Doc says | Code says |
|---|---|---|
| B1 | `CONTRIBUTING.md:199` — the webhook/SMTP notification layer is "not reachable from any entrypoint"; "`run_and_score` never constructs either" | `validsim/engine/pipeline.py:42` imports `notify_run_completion`; `:206` calls it (webhook wired; only `EmailNotifier` is caller-less) |
| B2 | `validsim/notify/__init__.py:3-10` — "not yet wired into the shipped run path … `run_and_score` does not call it yet — its only callers are in `tests/` … tracked as `PENDING OWNER`" | `pipeline.py:42`/`:206`; `tests/test_notify_wiring_agent.py:109-110` now says "WIRED (was PENDING OWNER; both wirings landed)"; `docs/notify_wiring_patches.md:1-8` says "all three LANDED" |
| B3 | `docs/ISSUE_CATALOG.md:639-641` — "`WebhookDispatcher`/`EmailNotifier` are a library only, while docs mark the feature `[x]`" | `pipeline.py:206` dispatches; `tests/test_notify_wiring_agent.py:112-133` enforces the wiring |
| B4 | `docs/runbook.md:281` — "inspect `dispatcher.sent` or the hook's logs" | `WebhookDispatcher.sent` (`dispatcher.py:298-301`) is unreachable from the shipped path (the dispatcher never escapes `notify_run_completion`); there is no logger anywhere in `validsim/notify/` |
| B5 | `docs/runbook.md:85` — "`VALIDSIM_LLM_API_KEY` … **Its presence selects the LLM scenario generator**" | `validsim/scenarios/llm_generator.py:88` `_ENABLE_ENV = "VALIDSIM_LLM_ENABLED"`; the factory requires **both** a truthy enable flag and a non-empty key. `docs/runbook.md:300-308` (same file) states the opposite correctly, and `VALIDSIM_LLM_ENABLED` appears in neither the runbook's §2 env table (`:68-92`) nor `.env.example` |
| B6 | `.env.example:160-162` — "Set a real key to enable LLM scenarios" | `llm_generator.py:769-775` `llm_enabled()` requires an explicit opt-in; `tests/test_llm_generator.py:358-366` pins "api_key_alone_does_not_enable_the_llm_path" |
| B7 | `.env.example:177-184` — six `VALIDSIM_SMTP_*` variables under "Notifications: webhooks + email" | `grep -rn "EmailNotifier(" --include=*.py validsim/` returns zero hits — the SMTP path has no dispatcher call at all, so no unset host can change any behaviour |
| B8 | `docs/ISSUE_CATALOG.md:344` — the `.env.example` honesty test guarantees "every advertised env var has a reader" | `tests/test_delivery_pipeline.py:174-181` collects every `VALIDSIM_*` literal appearing **anywhere** under `validsim/`, with no reachability filter — the literals live in the unwired `notify/email.py`, `project_config.py` and `scenarios/llm_generator.py` |
| B9 | `ARCHITECTURE.md:575-578` — the notify package is "ready for dispatch (not yet active)", with a 4-step "how to build a dispatcher" recipe | `validsim/notify/dispatcher.py:343-401` `WebhookDispatcher.dispatch` exists and is called by `pipeline.py:206` |
| B10 | `validsim/project_config.py:166` accepts a `[notify]` TOML table | `project_config.py:427-482` `config_to_env` drops every non-policy section, including `notify` (P1-30) |

### Cluster C — store

| # | Doc says | Code says |
|---|---|---|
| C1 | `docs/adr/0004-store-abstraction-sqlite-postgres.md:1` — title "SQLite (overwrite) and PostgreSQL (append-only)"; `:56-76` "**SQLite — overwrite.** `save` issues `INSERT OR REPLACE`"; `:95-98` "`save` is not idempotent across tiers" | `validsim/store/sqlite.py:183-187` `ON CONFLICT (run_id) DO NOTHING`; `validsim/store/memory.py:345` `self._runs.setdefault(run.run_id, _snapshot(run))` — all three append-only (commit `8a25810`) |
| C2 | `docs/ISSUE_CATALOG.md:304` — "memory (`store/memory.py:81`) and SQLite (`INSERT OR REPLACE`, `store/sqlite.py:198`) **overwrite**" (repeated `:635-638`); the cited lines point at unrelated code | `sqlite.py:187` `ON CONFLICT DO NOTHING`; `memory.py:345` setdefault; `postgres.py:505` the same clause. `tests/test_store_divergence_agent.py:661-671` and `tests/test_store_parity.py:84-198` pin append-only |
| C3 | `docs/PLATFORM_PROPOSAL.md:1345`, `:1367-1371`, `:1512-1516` ("Both under-bill"), `:1808` — "memory / SQLite overwrite — last write wins" | Same as C1/C2 |
| C4 | `ARCHITECTURE.md:176-179` — "Close semantics (SQLite raises `ProgrammingError` on double-close; others are idempotent)" | `validsim/store/sqlite.py:500-517` — "Idempotent (a second `close()` is a no-op) and safe on a never-opened store", with transparent reopen |
| C5 | `ARCHITECTURE.md:640-644` — if `test_div4b_double_close_is_harmless_everywhere` fails, "skip test with `--deselect` flag" | `tests/test_store_divergence_agent.py:450-459` is a plain passing test whose docstring records that the old opposite assertion was retracted |
| C6 | `ARCHITECTURE.md:157` — "**Store Interface Contract** (`validsim/store/base.py`)" with `create_run`/`update_status`/`complete_run`/`get_run`/`history`/`delete`/`close` | No `validsim/store/base.py` exists; the contract is `validsim/store/memory.py:250` `class ValidationStore` with `save`/`save_many`/`save_exists`/`get`/`delete`/`list_for_checkpoint`/`history`/`count`/`close` |
| C7 | `ARCHITECTURE.md:250-265` — `create_store()` returns `MemoryStore()` and raises on an unknown backend type | `validsim/store/__init__.py:46-80` returns `ValidationStore()` for anything unrecognised and never raises |
| C8 | `ARCHITECTURE.md:277-279` — "No migrations implemented yet" | `validsim/store/sqlite.py:78-85` `_MIGRATION_COLUMNS` and `validsim/store/postgres.py:114-120` do ALTER-on-open (versionless) |
| C9 | `VALIDATION_SCHEMA.md:577` — "**Run migration** for existing databases (`validsim/migrate.py`)" | No `validsim/migrate.py` exists and nothing imports it; the real mechanism is the ALTER-on-open DDL |
| C10 | `VALIDATION_SCHEMA.md:491-518`, `:530-558` — the stored DDL, with per-field columns | `validsim/store/sqlite.py:52-66` and `validsim/store/postgres.py:96-104` create `run_id, checkpoint_id, task_id, composite_score, deploy_decision, created_at, scorecard_json` / `scorecard JSONB` — no per-field columns for `success_rate`, `safety_score`, `robustness_score`, `block_reasons`, `confidence_interval_*` or `adversarial_*` |
| C11 | `docs/ISSUE_CATALOG.md:32-83` (item 10, "✅ 2026-09-24") — "Runbook done; immutability call is yours"; `:314-318` lays out three options and says "I did not change the behaviour" | All three backends are append-only now, but `DELETE /api/v1/validations/{run_id}` (`validsim/api/main.py:1219-1240`) still physically removes the row — the decision is half-made and the ✅ hides it |

### Cluster D — config

| # | Doc says | Code says |
|---|---|---|
| D1 | `validsim/project_config.py:39-42` — "Those are separate concerns owned by :mod:`validsim.settings`" | No `validsim/settings.py` exists; a repo-wide grep for `validsim.settings` returns exactly one hit — the reference inside `project_config.py:40` itself |
| D2 | `validsim/project_config.py:61`, `:436-437` — `validsim config show`, `validsim support-bundle`, `validsim run --print-effective-config` are the intended consumers | None of the three exists; the CLI commands are run, validate, status, scorecard, report, gate, delete, models, version, health, compare, jobs, worker. `README.md:147` and `docs/notify_wiring_patches.md:128` describe the masking as a property of a command that does not exist |
| D3 | `validsim/project_config.py:494-496` — `effective_to_env` takes "the ``VALIDSIM_*``-named mapping produced by ``Settings.load(config, environ)``" | No `Settings` class and no `.load()` exist in the tree |
| D4 | `validsim/project_config.py:35` — `config_to_env` yields "a ``VALIDSIM_*`` string mapping" | `POLICY_KEYS` (`:136-151`) are TOML-native names; `config_to_env({"threshold": 90.0})` returns `{'threshold': '90.0'}` — no prefix. `:33-34` and `:455-458` are also inconsistent with each other on this |
| D5 | `validsim/project_config.py:157-169` `_KNOWN_TOP_LEVEL` accepts `store`, `backend`, `notify`, `junit`, `tasks`, `defaults`, `adversarial`, `schema_version` | `config_to_env` (`:427-482`) emits only `POLICY_KEYS`; the other eight validate and are then silently dropped |
| D6 | `pyproject.toml:13` — `requires-python = ">=3.10"` | `validsim/project_config.py:95-98` needs `tomli` on 3.10, which is in neither `pyproject.toml` dependencies nor `requirements.txt`; CI runs 3.12 only (`.github/workflows/ci.yml:40,134,172`) |
| D7 | `validsim/project_config.py:286-291` — "A URL has only its userinfo section replaced … while removing the password"; `:20-22` — "redaction is a security boundary" | `_URL_USERINFO_RE` (`:246`) `[^/@]+` stops at the **first** separator: `mask_secret('postgresql://validsim:p@ssw0rd@db.internal:5432/validsim')` → `'postgresql://***REDACTED***@ssw0rd@db.internal:5432/validsim'` |
| D8 | `validsim/config.py:62-93` `resolve_asset_path` — the containment guarantee | No production caller; allow-listed at `tests/test_wiring_reachability_agent.py:209`. The live check is `config.py:23-34` `_is_absolute_path`, which accepts the Windows drive-relative `C:foo` |
| D9 | `docs/production-deployment.md:230` — `VALIDSIM_REDIS_URL` default `memory://` | No `memory://` scheme exists in `validsim/jobs/queue.py`; the memory backend is selected by `VALIDSIM_JOB_QUEUE` |
| D10 | `docs/production-deployment.md:231` — `VALIDSIM_WORKER_CONCURRENCY` (default `4`); `:232` — `VALIDSIM_RATE_LIMIT_MAX` (default `100`) | A repo-wide grep finds both names only in this document. The real names are `VALIDSIM_RATE_LIMIT` / `VALIDSIM_RATE_WINDOW_SECONDS`; there is no worker-concurrency env var |
| D11 | `docs/production-deployment.md:227` — `VALIDSIM_API_KEY` "min 32 chars" | `validsim/api/main.py:1025-1033` rejects only non-ASCII; the app boots with `VALIDSIM_API_KEY='short'` |
| D12 | `docs/production-deployment.md:538-539` — "All 3086 tests passing"; "Rate limiting enforces limits (100 req/min default)" | `pytest tests/ --collect-only` prints 3,254 collected (verified during compilation); the shipped default is 60/60s (`docs/runbook.md:75`, `.env.example`) |
| D13 | `docs/api-reference.md:862`; `docs/runbook.md:80` — `VALIDSIM_THREAD_LIMITER_TOKENS` sizes the worker pool | `validsim/api/main.py:1009-1012` stores it on `app.state` but `:993`/`:696` never use it — with the var set to 6 the real anyio pool goes 40 → 48 |
| D14 | `vault/04 - Engineering/API Design.md:121`; `vault/09 - Risks/Risk Register.md:29`; `vault/07 - Fundraising/Engineering Momentum Log.md:62`; `vault/04 - Engineering/Security Hardening.md:73` — the rate limiter is "off by default (`VALIDSIM_RATE_LIMIT=0`)" | `validsim/api/main.py:37-40` — "Protection is **on by default** (`60` per `60`s)"; `:178` `DEFAULT_RATE_LIMIT = 60`. `docs/ISSUE_CATALOG.md` item 15 records the change (2026-09-25) |

### Cluster E — API / CLI / jobs

| # | Doc says | Code says |
|---|---|---|
| E1 | `ARCHITECTURE.md:341-349` — CLI exit codes "3 \| Validation blocked (gate failed) \| 4 \| API/network error" | `validsim/cli.py:98` `_GATE_USAGE_EXIT = 2`, `:100` `_GATE_BLOCKED_EXIT = 1`; the only `typer.Exit` calls are 1 and 2. A BLOCK exits **1**, not 3 — and `actions/validate/action.yml` and `docs/github-actions.md` parse these codes |
| E2 | `ARCHITECTURE.md:322-324` — `validsim = "validsim.cli:main"` | `pyproject.toml:44` — `validsim = "validsim.cli:app"`; no `main` symbol exists in `validsim/cli.py` |
| E3 | `ARCHITECTURE.md:365-371`; `CURRENT_STATUS.md:173-230`, `:276` — a multi-page dashboard under `public/` (`validations/index.html`, `validations/[id].html`, `models/index.html`, `compare/[id].html`) | The shipped dashboard is a single-page SPA: `validsim/web/` holds `app.js`, `index.html`, `styles.css`, `view-core.js`; `validsim/api/dashboard.py:59-62` serves `INDEX_PATH` for both `/` and `/api/v1/dashboard/`. There is no `public/` directory in the repo |
| E4 | `docs/api-reference.md:149-156`, `:806-808` — "the three **write** routes" are rate-limited; "Every read route is unlimited"; `:164` lists DELETE with "Rate limit \| None" | `validsim/api/main.py:447-479` `_is_rate_limited_request` also rate-limits `DELETE /api/v1/validations/{run_id}` — verified: six DELETEs under `VALIDSIM_RATE_LIMIT=3` returned `[404,404,404,429,429,429]`. `docs/runbook.md:75`, `:126` carry the same drift; `docs/api-reference.md:928-930` omits DELETE from the 429 row |
| E5 | `docs/api-reference.md:626-648` — health response has seven fields | The live payload has an eighth: `"notify": {"enabled": false, "hook_count": 0, "live": 0, "hooks": []}` (added per `docs/notify_wiring_patches.md` §2) |
| E6 | `docs/api-reference.md:196-233` — the `ValidationRequest` field table | It omits `threshold`, which **is** in the model (`validsim/config.py:186-189`) and in the OpenAPI schema — posting `threshold: 10.0` returns a scorecard with `threshold: 10.0` |
| E7 | `docs/async-jobs.md:346-347` — the in-memory backend checks `len(jobs) >= max_depth`; the Redis backend checks `LLEN validsim:jobs:index >= max_depth`; `:350-355` — the cap is "best-effort" under multi-process load | `validsim/jobs/queue.py:1156-1166` counts pending work only (queued+running); `queue.py:612-626` is one Lua script reading the **ready** list (`KEYS[3]`), atomic server-side. `docs/ISSUE_CATALOG.md:69` (item 38) already says "both backends now count pending" — the two docs contradict each other |
| E8 | `docs/github-actions.md:369` — "the four-state job lifecycle"; `docs/api-reference.md:750` and `:971` — "`queued \| running \| done \| failed`" | `validsim/jobs/models.py:18-34` — `QUEUED/RUNNING/DONE/FAILED/DEAD`, with `TERMINAL_STATUSES` (`:38-41`) including DEAD. `docs/async-jobs.md:85` documents `dead` correctly |
| E9 | `vault/04 - Engineering/Job Queue Worker.md:101-116` — the four-state lifecycle is what the worker drives; the richer sketch is "target" | `jobs/models.py:18-35` has five states; `jobs/worker.py:232-234` reaps expired leases, `:243-253` renews them, `:200` reports dead-letters. The vault's own `04 - Engineering/Async Job Queue.md:15` says the dead-letter queue and priority aspects are shipped |
| E10 | `docs/testing.md:417-427` — `ci.yml` has four jobs (test, postgres, docker, compose) with `needs: [test, postgres]` | `.github/workflows/ci.yml` has five: `:30` test, `:108` postgres, `:150` redis, `:228` docker, `:248` compose; `:231` and `:251` declare `needs: [test, postgres, redis]` |
| E11 | `docs/production-deployment.md:132-203`, `:205-217` — `helm install validsim ./charts/validsim`, `kubectl apply -f k8s/manifests/*.yaml` (7 files) | Neither `charts/` nor `k8s/` exists in the repository |
| E12 | `docs/production-deployment.md:416-438`, `:510-516` — a `pg_dump \| gzip` cron backup and a `redisincr` rate-limit dependency | Neither exists in the codebase; the rate limiter is a process-local sliding window (`validsim/api/main.py:352`) |
| E13 | `docs/ISSUE_CATALOG.md:542`, `:613` — "is a product decision — see §7.1" | §7 (line 617) is a numbered list, not a subsection 7.1; there is no §7.1 |
| E14 | `docs/ISSUES_AND_REMEDIATION_CATALOG.md:671-699`, `:605-604`, §1 ranked table — "SEC-001 — API authentication fails open by default", CVSS 9.8, rank 1, priority P0, effort 2-4 h | `validsim/api/main.py:1046-1051` raises `RuntimeError` when `VALIDSIM_API_KEY` is None and `VALIDSIM_ENV` is production |
| E15 | `docs/PLATFORM_PROPOSAL.md:16`, `:20`, `:554-555`, `:1786-1787` — cites `docs/runbook.md:345` and `:350-356` for the no-K8s and no-scheduler claims; the header promises "Every 'today' claim is file:line verifiable" | The text is now at `runbook.md:417` and `:425-433` — the citations no longer resolve |
| E16 | `docs/production-deployment.md:15`, `:107`; `docker-compose.prod.yml:73`, `:106` — the image is `ghcr.io/abhijeet1267/validsim` | `.github/workflows/release.yml:126-127` pushes `ghcr.io/${{ github.repository }}` — the fork owner's namespace. The origin is `dev25bansal-ops/validsim` |
| E17 | `vault/00 - Dashboard/Build Status.md:11-15`, `:77` — "Regenerated … in CI via `.github/workflows/ci.yml` (push/PR) and `nightly.yml`"; "Do not edit manually" | `grep -rn build.ps1` across the repo returns only `scripts/build.ps1`'s own heredoc (line 129). `ci.yml`'s steps are lint/pytest/Postgres-parity/Redis only. `scripts/build.ps1:126-152` would also emit "**Pass rate (last $totalCount runs): $rate%**", contradicting the committed note at `:48-50` |
| E18 | `vault/04 - Engineering/Module Specs.md:56-61`; `vault/04 - Engineering/Solution Architecture.md:114`; `vault/04 - Engineering/Data Flow.md:46-50`; `vault/03 - Product/MVP Scope.md:28` — the LLM generator's status and its `pipeline.py` pointer | `validsim/engine/pipeline.py:143` hardcodes `ScenarioGenerator(seed=seed).generate(...)`; `grep -rn "LLMScenarioGenerator\|create_scenario_generator" validsim/` matches only `validsim/scenarios/__init__.py`. The vault's pointer is to `pipeline.py:131`; the real line is 143 — the same stale citation appears in `llm_generator.py:13`, `:729`, `docs/runbook.md:296`, `docs/scenario-taxonomy.md:377`, `README.md:256` and five vault notes |
| E19 | `README.md:256` — "`engine/pipeline.py:131` hardcodes `ScenarioGenerator(seed=seed).generate(...)`" | The line is 143; `awk 'NR==131' validsim/engine/pipeline.py` returns a docstring line, not the generator call |
| E20 | `docs/isaac-worker.md:62-68` — the worker must "echo it back on each episode so the client can confirm the right artifact ran" | `validsim/sim/isaac_worker.py:65` derives `_EPISODE_FIELDS` from `EpisodeResult`, which has no `checkpoint_id` (`runner.py:361-371`); `:288-294` rejects unknown keys — a conforming worker is rejected with "sent unknown field(s) 'checkpoint_id'". An envelope-level echo is accepted but never compared |
| E21 | `docs/isaac-worker.md:154-155` — "a single PhysX episode can take minutes, so raise it well above the 30 s default" | `validsim/sim/isaac_worker.py:468` `timeout: float = 30.0`; no env var or config knob exists; `create_backend()` (`sim/__init__.py:55-58`) constructs the backend with no arguments |
| E22 | `docs/isaac-worker.md:150-153` — the contract is batch-shaped; "a future run_validation-equivalent can submit a whole verdict in one POST" | `validsim/sim/runner.py:625` calls `backend.run_episode(...)` once per episode; `run_episodes` (the batched path, `isaac_worker.py:556`) has no production caller |
| E23 | `docs/isaac-worker.md:106-107` — "the same seed against a *different* checkpoint is a different episode, and must be allowed to differ" | `validsim/sim/runner.py:95-111` maps a checkpoint to one of 5 tiers by `zlib.crc32(id) % 5`; the RNG is seeded from `seed` alone — `ckpt-0` and `ckpt-3` produce identical `EpisodeResult`s across 200 seeds |
| E24 | `docs/ISSUE_CATALOG.md` item 12 — "Fixed (2026-09-24)" for the composite-formula doc error | The doc error was re-introduced and compounded by the 2026-09-29 abstention amendment (ADR 0002); `docs/api-reference.md` was never re-derived |
| E25 | `validsim/_log.py:12-15` — "This module is named ``logging`` inside the ``validsim`` package" | The file is `validsim/_log.py`; commit `025890c` renamed it precisely to stop the stdlib shadowing, and `tests/test_logging_redaction.py:308-310` asserts `__import__("validsim.logging")` raises `ModuleNotFoundError` |
| E26 | `validsim/store/memory.py:80-83` — `reconstruct_kwargs` "Raises: TypeError … if data carries a key that is not a field of cls" | The implementation (`:85-89`) iterates `dataclasses.fields(cls)` and skips unknown keys, so it can never raise. `tests/test_store_edgecases_agent.py:738-755` documents the contradiction and asserts the permissive behaviour |
| E27 | `tests/test_notify_ssrf_timeout_agent.py:21-29` — the per-hook timeout ceiling and the missing overall deadline are "**Still open**", quoting "measured: 15.8s for one, 31.5s for two" | `validsim/notify/dispatcher.py:80` `_DISPATCH_BUDGET_S = 5.0`, taken once for the whole fan-out at `:370` and re-clamped per attempt at `:394`/`:440`. The file's own test at `:398-410` still asserts `budget == 15.0` — pinned to the pre-budget arithmetic |
| E28 | `tests/test_store_differential_agent.py:27-31` — "see ``test_store_pg_differential_agent.py``, which skips with that exact reason" | No such file exists; the live-PG gate is in the same file at `:542-615` (`live_postgres_dsn`, `pg_store`, `test_live_postgres_gate_reports_its_reason_plainly`) |
| E29 | `tests/test_queue_depth.py:3-4` — the Redis depth test "uses a live local Redis and skips cleanly when unavailable" and the in-memory half uses "small stand-ins" | The section header at `:175` reads "Redis-backed (fake client — no server)"; the `live_redis_depth` fixture (`:45-68`) connects to `redis://127.0.0.1:6379/15` — a live server, not a stand-in. The two prose descriptions contradict each other and the fixture |
| E30 | `tests/test_cli_delete.py:98-116` — "RED regression: … Both tests encode the DESIRED behaviour, so they land red today … ACTUAL: exit 2, 'no cached runs'" | All 6 tests pass. The fix landed in `validsim/cli.py:298-332` `_resolve_deletable_run_id` (store-first with cache fallback); the docstring was not updated |
| E31 | `tests/test_cli_gate_diagnosis.py:27-32` — the reason vocabulary includes `missing_run_id` and `ambiguous_run_selector` | Neither token appears anywhere in the production tree. Also `validsim gate --json` with no flags prints a Click usage message, **not** JSON — so the "always emits exactly one JSON object" contract (`:22-24`) does not hold |
| E32 | `tests/test_web_browser_agent.py:26` — `pytest.mark.flaky(reruns=1, reruns_delay=1)` | `pytest.ini` registers only `benchmark` and `slow`; `pytest-rerunfailures` is not in `requirements-dev.txt` and not installed. Every run emits `PytestUnknownMarkWarning` and the intended retry never happens |
| E33 | `tests/test_cli_version.py:29-34` — asserts the literal `"0.2.0"` appears in the version output | `validsim/__init__.py:9` `__version__ = "0.2.0"`, but `CHANGELOG.md:9` carries an entire `[Unreleased]` Iteration 6 — the next version bump fails this test on a stale literal even though the command is correct |
| E34 | `tests/test_cli_health_readiness.py:115-125` — `_BrokenQueue` proves "A job queue that cannot reach its backend" | `validsim/cli.py:910-929` `_probe_queue` probes with `len(queue)`, not `ping()`; `_BrokenQueue` has no `__len__`, so the failure is `TypeError: object of type '_BrokenQueue' has no len()'` — the transport error it was written to produce never occurs. The same file's `_FailingFactory` (`:128-135`) is dead code |
| E35 | `tests/bench/test_bench_api.py:15` — `os.environ.setdefault("VALIDSIM_RATE_LIMIT", "0")` "so latency is measured in isolation" | `tests/conftest.py:62` lists `VALIDSIM_RATE_LIMIT` in `_ISOLATED_ENV`; the autouse `_isolate_deployment_env` fixture (`:230-234`) deletes it before every test. Inside the benchmark the var is `None` and `app.state.rate_limit_config` is `{'requests': 60, 'window_seconds': 60.0}` — the default limit is in force |
| E36 | `tests/test_site_astro_integrity_agent.py:15-18` — "A `dist` staleness check is added separately below" | No such check exists; the only dist assertion (`:252-268`) checks the run-id count and explicitly no-ops when `dist/` is absent (`:259-261`) |
| E37 | `tests/test_marketing_refusals_agent.py:141` — `entry["name"] in html` validates the JSON-LD against the visible page | `html` is the whole file including the JSON-LD block the name came from, so the block satisfies itself. Proven by mutation: rewriting the visible `<summary>Does ValidSim run on real robots?</summary>` still passes. 3 of 5 JSON-LD questions differ from the visible copy |
| E38 | `site/astro.config.mjs:23-27` — "Fail the build on a broken link or a malformed component rather than shipping it" | There is no link-checking option in astro 5.18.2 (`grep -rn 'checkLinks' package/dist/` over the published tarball returns nothing); injecting a broken `<a href>` exits 0 and emits the page. `build.format: 'directory'` is Astro's own default (`core/config/schemas/base.js:16`) |
| E39 | `site/astro.config.mjs:11-13` — "Exactly one component … is marked `client:visible` … a reviewer can verify this" | No `client:*` directive exists anywhere in `site/src`; `grep -rn 'client:'` returns only two comment mentions in `src/scripts/readoutScrub.ts:16-17` explaining that `client:*` was **removed**. The script is a plain eager Astro `<script>` module (`src/pages/index.astro:63-66`) |
| E40 | `site/astro.config.mjs:7` — "both recorded in `../../.impeccable.md`" | From `site/`, the correct relative path is `../.impeccable.md`; `ls site/../../.impeccable.md` fails, `ls site/../.impeccable.md` succeeds |
| E41 | `site/src/layouts/Base.astro:82` sets canonical from `Astro.url.href`; `:87` sets `og:url` from `site.domain` in `src/data/content.ts` | The same fact is stored twice (`site/astro.config.mjs:20` and `src/data/content.ts:17`). Changing the config's `site` changes the canonical but not `og:url` — verified by rebuild. `site/src/styles/tokens.css:53-59` declares `--split-narrow/-wide/-hero` that no rule references, while 10 hardcoded `@media` remain across `site/src/components/` |
| E42 | `site/src/styles/tokens.css:44` — `--font: "Space Grotesk", ui-sans-serif, system-ui, sans-serif` | `site/src/layouts/Base.astro:109-117` declares a metric-matched `@font-face "Space Grotesk Fallback"` that no font stack ever names — the built CSS contains zero occurrences of it. The legacy `marketing/index.html:171` *does* include it, so the newer page regressed a guard the older one has |
| E43 | `site/src/components/Recording.astro:58-59` — "Nothing here is staged: the run id is the one the Action gated on"; `:9` — "the same run ends up in three places with the same verdict" | The webm and poster show `vrun-dd87bc21` / composite 72.69; the page says `vrun-ed0c150a` / 72.71 (`marketing/index.html:582`, `:592`; `site/src/data/content.ts:34,60,70`) |
| E44 | `design-system/validsim/MASTER.md:8-14` — the "Source-of-truth map" lists exactly three surfaces (FastAPI dashboard, HTML scorecard, PDF scorecard); `:16-19` — "There is currently no shared machine-readable token file" | Neither `marketing/index.html` nor the Astro site appears anywhere in MASTER.md (`grep -i 'astro|site/|marketing|webm|poster'` returns nothing), and `site/src/styles/tokens.css` **is** a machine-readable token file. The doc is accurate about every profile it does cover — the defect is scope, not accuracy |
| E45 | `design-system/validsim/MASTER.md:40-42` — "the `pages/` directory is empty" | `design-system/validsim/pages/` exists on disk but git tracks no file in it; git does not track empty directories, so in a fresh clone the directory is absent |
| E46 | `.impeccable.md:76-78` — "**Dark only.** Matches the operator dashboard" | `README.md:181` and `:281` describe a persisted light/dark toggle, and `validsim/web/styles.css` contains a full `html[data-theme="light"]` token override block |
| E47 | `marketing/index.html:188-189` — `--rhythm: 1.5rem;` … "Vertical rhythm: every block gap is a multiple of this" | `grep 'var(--rhythm)'` returns nothing; every block gap is a hardcoded rem/clamp value |
| E48 | `marketing/index.html:15`; `site/src/layouts/Base.astro:88` — `<meta name="twitter:card" content="summary_large_image">` | Neither declares `og:image` or `twitter:image` (`grep` returns 0 matches in both files and in `site/dist/index.html`); `site/public/` contains only the run poster and webm, so no shareable brand image exists |
| E49 | `tests/test_marketing_refusals_agent.py:146-148` — the JSON-LD offers are exactly `{Developer, Team, Pro}` | The visible pricing table (`marketing/index.html`, the `<div class="tiers">` block) renders four tiers including "Enterprise — Custom", and the Astro site has the identical four (`site/src/data/content.ts:120-124`) with a three-offer JSON-LD (`Base.astro:61-66`). Defensible, but the test encodes the three-tier set with no explanation |
| E50 | `validsim/api/store_stats.py:47-50` — "SQLite / Postgres -- a SELECT over the indexed, promoted columns"; `:72-73` — equivalence is asserted "for the in-memory, SQLite and fake-connection Postgres backends" | `_sqlite_conn` (`:216-224`) accepts only a `sqlite3.Connection`; the SQL constants (`:122-137`) hardcode `validations` and `?`, so Postgres falls to `'generic'`. `grep -ci postgres tests/test_api_obsstats_agent.py` returns 0 |
| E51 | `validsim/store/memory.py:49-93` — `reconstruct_kwargs` is "the mechanism that lets a row written before a field existed still load"; `memory.py:18-22` — "Reconstruct, never filter" is a rule inherited by all backends | `validsim/store/sqlite.py:132` and `validsim/store/postgres.py:345-351` reconstruct the evaluation/safety/regression blobs with a raw `cls(**data)` — a blob with a new field raises `TypeError` on read in both SQL backends |
| E52 | `validsim/notify/dispatcher.py:271-279` — `allow_private_hosts` opts out of the egress policy "permitting loopback, link-local and RFC1918 destinations" | `dispatcher.py:337-340` skips the **entire** `url_rejection_reason()` check, including the `ALLOWED_SCHEMES` allowlist and the cloud-metadata hostname block, both in `egress.py:135-155` |
| E53 | `validsim/notify/config.py:152-153` — `summary()` is "the effective notification configuration" | `config.py:204-210` `_live_from_env()` reads `os.environ` directly, ignoring the `environ` mapping the function was given |
| E54 | `validsim/api/main.py:869-892` — the health payload's `store_backend` label | It is derived purely from `type(obj).__name__.lower()` with no allow-list, on an unauthenticated route — `tests/test_api_characterization_agent.py:474-485` marks this SUSPECTED-WRONG |
| E55 | `validsim/sim/runner.py:287-288` — "every parameter named by `_PARAM_SAMPLERS` for that category is consumed" | `generator.py:212` samples `color_temperature_k`; `_stress_lighting` (`runner.py:168-178`) reads only `lux` and `flicker_hz` |
| E56 | `validsim/engine/scorecard.py:158-164` — "**every production run has exactly one randomization group and cannot produce a robustness measurement at all** … The runner, not this module, is the root cause" | The runner was never changed: `validsim/sim/runner.py:623-631` still passes `randomization_level=task.randomization` on both loops |
| E57 | `validsim/engine/safety.py:129-144` — proximity abstains at 0.0 because "a perfect 0.0 would be a fail-open" | `safety.py:88-95` awards `safety_score=100.0` to a **zero-episode** run — the opposite rule, on a channel that carries 0.3 of the composite weight |

### Cluster F — packaging / CI

| # | Doc says | Code says |
|---|---|---|
| F1 | `pyproject.toml` (the packaging contract) | `:48` declares no `package-data`/`include-package-data`, and there is no `MANIFEST.in` — so the wheel ships 51 entries and **zero** under `validsim/web/`, while `validsim/api/dashboard.py:73` mounts `StaticFiles(directory=WEB_DIR)` and `main.py:1430` builds the app at import time |
| F2 | `Dockerfile:57` copies `validsim/` into the image | `:25-26` installs only `requirements.txt` — there is no `pip install .`, so the `validsim` console script (`pyproject.toml:44`) does not exist in the image, yet `docker-compose.prod.yml:110` runs `command: ["validsim", "worker"]` |
| F3 | `Makefile:59` — `SMOKE_SQLITE_PATH ?= .validsim/smoke.db`; `actions/validate/action.yml:101` — `VALIDSIM_SQLITE_PATH=${RUNNER_TEMP}/validsim/runs.db` | `validsim/store/sqlite.py:261` `sqlite3.connect(self._db_path)` creates no parent directory. **Reproduced:** `OperationalError: unable to open database file` |
| F4 | `.env.example:207` — `VALIDSIM_CACHE_FILE=` | `validsim/cli.py:154` `os.environ.get(_CACHE_ENV, default)` returns `""` for a set-but-empty var, so the path becomes empty. **Reproduced:** `PermissionError: [Errno 13] Permission denied: '.'` — while `validsim/store/__init__.py:69` already handles the identical case correctly |
| F5 | `docker-compose.prod.yml:129` — the worker healthcheck parses the Redis URL | The expression `.split('/')` with no index yields host `"0"` from `redis://:SECRETPASS@redis:6379/0`. `docker-compose.yml:157` uses the correct `.split('/')[0]` |
| F6 | `docs/ISSUE_CATALOG.md:372`; `docs/ISSUES_AND_REMEDIATION_CATALOG.md:244` — the workflows do not invoke `scripts/build.ps1` | `vault/00 - Dashboard/Build Status.md:12-13` and the note's own generator (`scripts/build.ps1:129-133`) both claim CI regenerates it |
| F7 | `scripts/build.ps1:18` — `[int]$ResampleBudget = 500` "Mirrors _CI_N_RESAMPLES in validsim/engine/scorecard.py; override to widen or narrow the interval" | `$ResampleBudget` appears only at `build.ps1:18` — it is never read. `validsim/engine/scorecard.py:72` hardcodes `_CI_N_RESAMPLES = 500` with no env or config override. Already recorded at `audit/cat2-c2-quality.md:357` and `audit/cat2-c2-bugs.md:305` |
| F8 | `validsim/api/main.py:174` — `RATE_LIMIT_DISABLED` | A named, documented sentinel that nothing references; the logic tests `rate_limit > 0` inline at `:923`/`:925` |
| F9 | `docs/ISSUES_AND_REMEDIATION_CATALOG.md` BUILD-005 (`:181-194`) — the site has no CI job | Still true — but the site *also* has no working typecheck gate (P2-9) and `site/dist/` is gitignored (`.gitignore:29`), so `tests/test_site_astro_integrity_agent.py:259-261` no-ops in any fresh checkout |
| F10 | `.dockerignore` — the build context exclusions | No entry for `site/`, `node_modules/`, `audit/`, `marketing/`, `.codebuddy/`, `.workbuddy-ai/`. `site/node_modules` measures 49M, `site/` 51M — the whole context ships to the daemon on every build |
| F11 | `site/package.json:11` — `"check": "astro check"` | `@astrojs/check` is in neither `package.json` nor `site/package-lock.json`; `astro/dist/cli/index.js:228` exits 0 when the check server returns `undefined` |
| F12 | `site/package.json:17` — `gsap` in `devDependencies` | `site/src/scripts/readoutScrub.ts:30-31` imports it at runtime; the built bundle contains GSAP. `npm ci --omit=dev && astro build` fails to resolve the import |

### Cluster G — root docs

| # | Doc says | Code says |
|---|---|---|
| G1 | `SECURITY.md:250-253` — the CORS default "is still `*`, which is an unsafe default by the §3 definition" | `validsim/api/main.py:930-939` — "The default is an EMPTY allow-list, deliberately not `*`"; `:937` reads `""` as the fallback; `:1050-1054` refuses `*` in production (commit `5796dc7`) |
| G2 | `SECURITY.md:290-293` — the persisted record gives "an audit trail that makes tampering detectable" | `validsim/api/main.py:1219-1240` `DELETE` physically removes the row; there is no hash chain. `docs/runbook.md:499-511` states the accurate version ("a write-path append-only guarantee, not immutable or tamper-evident storage … this repository does not implement an audit log") |
| G3 | `SECURITY.md:183-186` — "Nothing has been published yet: `validsim.__version__` is `0.2.0` and the repository carries no git tags" | Accurate. But `CHANGELOG.md:176-178` publishes release-comparison links to `https://example.com/...` for v0.1.0 and v0.2.0 |
| G4 | `README.md:14`; `CONTRIBUTING.md:77`; `docs/ISSUE_CATALOG.md:344` — "No test count is quoted here on purpose" | `CURRENT_STATUS.md:20, :48, :98, :104, :162, :163, :309` hardcode "3,083 tests" and "95.83% coverage"; `WORK_PROGRESS.md:48/:154/:159/:183` do the same. The current collection is 3,254 |
| G5 | `CURRENT_STATUS.md:5` — "**Branch:** `feature/website-simulation-completion` @ `aabbef0`" | The repo is on `main` at `2ffeb6c` |
| G6 | `CURRENT_STATUS.md:238-266` (next work) vs `:274-282` (completed) | The same "Comparison UI" feature is both. `:108` still lists "comparison UI still TODO" |
| G7 | `CURRENT_STATUS.md:17-22` — "✅ Astro-based marketing dashboard v0" (Founder 1); `:43` — "Dashboard \| 🟡 PARTIAL \| `site/src/` (Astro)" | Two contradictory accounts of one component in one document |
| G8 | `CHANGELOG.md:9-…` — an `[Unreleased]` section for Iteration 6 | It omits every fix since 2026-10-11: `12237b3` (notify dispatch wiring), `5796dc7` (CORS/metrics/SSRF), `8a25810` (store record freezing), `9eb12db` (checkpoint_sha256 + 1-episode APPROVE close), `59f31c1` (benchmark baseline), `4f411c5` (logging rename + notification dedup) |
| G9 | `VALIDATION_SCHEMA.md:26` — `EpisodeResult` at `validsim/sim/runner.py:30` | It is at `:338`. `:56` — `EvaluationResult` at `engine/evaluation.py:15`; it is at `:17`. `:256` — `Scorecard` at `engine/scorecard.py:328`; it is at `:346`. The spec's header says "Version 1.0 / Last Updated 2026-10-08" |
| G10 | `VALIDATION_SCHEMA.md:16` — consumers include "Database migrations (SQLite, PostgreSQL backends)" | The DDL it documents (`:491-518`, `:530-558`) is not the DDL the code creates (C10 above) |
| G11 | `AUDIT_CATALOG_2026-09-26.md:52-54` — "Priority 1 — CRITICAL, verified, still OPEN … not fixed" (items 51, 51b, 51d, 51c, 52, 53, 54) | All fixed: `scorecard.py:526` (48), `scorecard.py:474` (53), `anomaly.py:235-250` (54), `sqlite.py:187` (51d), the binomial adversarial gate (51c/52) |
| G12 | `AUDIT_CATALOG_2026-09-26.md:215-218` — a P2 table of 13 HIGH findings | At least 7 are closed: 55 (append-only), 59 (`jobs/models.py:57-66`), 60 (`cli.py` exit docs), 62 (`Makefile:59-63`), 64 (`_log.py:100-174` redaction), 66 (regression-gated by `tests/test_wiring_reachability_agent.py`), 67 (webhook now wired) |
| G13 | `_FULL_READ_REPORT.md:212-215`; `_FULL_READ_REPORT_2026-10-16.md:212-224`; `_IMPROVEMENT_RESEARCH_2026-10-16.md:32`, `:173` — "robustness = 100.0 always and regression = 100.0 on CLI/worker"; "1-episode run → composite 100 / APPROVE"; "the composite is … but on the CLI/worker path **robustness is always 100.0**" | `scorecard.py:474-482` renormalises over measured components; `:543` adds the binomial adversarial gate; `:523-525` closes the 1-episode APPROVE; `validsim/store/_frozen.py` (commit `8a25810`) fixed the store read-path aliasing. The 2026-10-16 report was committed on 2026-10-17 — the same day several of the fixes it flags as open were landing |
| G14 | `vault/08 - Team & Legal/IP Strategy.md:31` — "The plugin is a thin client over the proprietary API anyway"; `:22` and `vault/06 - Business/Go-to-Market.md:32` — MIT for the CLI + Action | `validsim/cli.py:45`/`:454` run the whole engine in-process; `actions/validate/action.yml` does `pip install .` of the whole repo; `pyproject.toml:11` still reads `license = { text = "Proprietary" }` |
| G15 | `vault/10 - Data & Sources/Sources.md:45` — "Store PDF/screenshot snapshots of each citation in `99 - Attachments` named `SRC-01…SRC-12`" | `vault/99 - Attachments/` contains only `.gitkeep` |
| G16 | `vault/02 - Problem & Market/Market Sizing (TAM SAM SOM).md:41` — the SAM row sums to $15M-$80M | `vault/02 - Problem & Market/Buyer Tiers.md:36` lists Tier 2 as "~200-500 companies by 2028" and Tier 3 as "1,000+ fleet operators by 2029" — these do not line up with the SAM table's 2028/2030 columns. Recorded in the vault, not closed |
| G17 | `vault/06 - Business/Technical Moat Features.md:470-499` — a proposed `episode_outcomes` table with `scenario_id`, `scenario_category`, `taxonomy_version`, `compute_seconds` | `grep -rn episode_outcomes validsim/` returns nothing; `grep -rn "wall_clock\|compute_seconds\|gpu_seconds" validsim/` also returns nothing — D4 (no compute-time instrumentation) is confirmed open, and the vault's §8 pricing/throughput claims depend on both |
| G18 | `vault/06 - Business/Technical Moat Features.md:303-319` (D10), `:1334` — the Postgres store "cannot support a gapless append-only chain" | Confirmed: `validsim/store/postgres.py:474` `psycopg.connect(..., autocommit=True)`; `grep -n "advisory" validsim/store/postgres.py` returns nothing |
| G19 | `vault/04 - Engineering/Async Job Queue.md:15` — the dead-letter queue and priority aspects are "target-state design" | `validsim/jobs/worker.py:200` `_report_dead_letter`; `:232-234` `reap_expired`; `:243-253` lease renewal; `jobs/models.py:18-35` the DEAD state — all shipped |
| G20 | `vault/03 - Product/MVP Scope.md:28`; `vault/04 - Engineering/Data Flow.md:46-50` — the LLM generator is unwired | Correct (see E18) — but `vault/06 - Business/Technical Moat Features.md:1255` also names it as "do not build before Nov 2", so the vault contradicts its own priority ordering |

---

## 6. Coverage and confidence

**Files read.** The 19 reports cover **347 distinct tracked files out of the repository's 395 tracked files at HEAD `2ffeb6c` (87.8%)**, plus one untracked-but-present artifact (`site/dist/index.html`). The reports' own `fileCount` fields sum to 399, inflated by 73 files claimed by more than one chunk — most of the overlap is in `tests/` (`read:H-tests-backend` and `read:I3-tests-rest` both list the API/store/jobs/notify test files, and `read:I1-tests-cli-web`, `read:I2-tests-misc` and `read:I3-tests-rest` overlap on the meta-gate files). I have de-duplicated by path; the 347 figure is the union.

**What was NOT read (48 tracked files), and why:**

- **`.codebuddy/` (24 files)** — agent-tooling configuration from a different agent harness (`teams/`, `automations/`, `simtoreal-fleet.md`). Not product surface; no production module, workflow or doc references them.
- **`site/src/` (10 files)** — the Astro *pages and components* (`index.astro`, `privacy.astro`, `Blocked.astro`, `Close.astro`, `Field.astro`, `Footer.astro`, `Gate.astro`, `Masthead.astro`, `Pricing.astro`, `Stages.astro`). The Astro **config** was read (`astro.config.mjs`, `package.json`, `tsconfig.json`) and the **content/data layer** was read (`src/data/content.ts`, `src/styles/tokens.css`, `src/layouts/Base.astro`, `src/components/{Faq,Hero,Method,Recording}.astro`, `src/styles/global.css`), which is where the reported findings live (the font-fallback, split-token, canonical-vs-og:url, `gsap` and `client:*` claims all sit in read files). **All ten were read directly during compilation** (they are presentational markup, and reading them is what surfaced P2-41).
- **`.workbuddy-ai/memory/2026-10-16.md` (1)** — a second agent harness's scratch memory.
- **`.pytest_tmp/**/scorecards.json` (2)** — committed test scratch output.
- **Root-level scratch/binaries (9)**: `run1.txt`, `run2.txt`, `s.txt`, `full.txt`, `full2.txt`, `out_prop.txt`, `coverage.json`, `project.docx` (binary), `site/package-lock.json`.
- **Root `conftest.py` and `pytest.ini` (2)** — read indirectly: `pytest.ini`'s marker list was checked by the `read:I1-tests-cli-web` chunk (which cites its `benchmark`/`slow` registrations), and root `conftest.py` was cited by two chunks but no chunk lists it in its own `filesRead`. **Both were read directly during compilation**: `pytest.ini` declares `testpaths = tests`, `python_files = test_*.py`, `addopts = -q --tb=short` and the `benchmark`/`slow` markers; `conftest.py` inserts the repo root on `sys.path` so `validsim` resolves under pytest, and activates the symlink-free `tmp_path` shim (`pytest_symlink_free_tmp.py`) on hosts that cannot traverse symlinks.
- **`pytest_symlink_free_tmp.py` (1)** — a repo-root pytest shim.

**Coverage caveat on binaries.** `site/public/validsim-run.webm` and `validsim-run-poster.png` were read by decoding frames and the PNG directly, so the run-id/composite mismatch (P2-12) is a real observation, not an inference. `project.docx` was not opened.

**Chunks that read thinly.** In absolute file count, `read:E2-sim-llm` (2 files) and `read:L1-site-src` (3 files) are the thinnest, and `read:C-api` (5), `read:F-notify` (5) and `read:E1-sim-core` (5) are small. In terms of *depth per file*, however, none of these is thin: `read:E2-sim-llm` reports two files with 13 reproduced defects and explicit verification method; `read:C-api` reports "verified every claim by execution rather than by reading alone" and includes measured numbers; `read:F-notify` reports 465 tests run, 97% coverage on the owned package, and a severity distribution measured over 2688 generated scorecards. The genuinely thin areas are the **ten unread Astro page/component files** and the **root `conftest.py` / `pytest.ini`**, and the chunks that could not see them compensate by grepping the surrounding tree read-only.

**What I could NOT verify.**

1. **I did not re-verify all 40 curated findings by execution.** I re-verified, against the live tree at `2ffeb6c`, the load-bearing claims behind P0-1 (built a wheel: 51 entries, zero under `validsim/web/`), P0-2 (`OperationalError: unable to open database file`), P0-3 (`PermissionError: [Errno 13] Permission denied: '.'`), P0-4 (parsed the prod DSN expression by hand — host resolves to `"0"`), P0-6 (read the `pair[0]`/`pair[1]` destructuring on both sides), P0-7 (read the `relation` ternary and confirmed `block_reasons` appears nowhere in `export.py`/`pdf.py`), P0-9 (the raw `cls(**data)` reconstruction paths), P0-10 (`def save_many` present in only two of three backends), P0-13 (executed `mask_secret` — the leak reproduces byte-for-byte), P0-14 (read the `delete` body), P0-16 (`reap_expired` present only in `run_forever`), P0-20 (read the three `block_reasons` producers against the two `_CRITICAL_BLOCK_REASON_PREFIXES`), P0-21 (read `_memory_summaries` against the SQL branch), P0-22 (read `SECURITY.md:290-293` and the `DELETE` route), P0-24 (read the CLI options and `JobSpec`), plus §5's cross-checks (ADR 0004's title, `ISSUE_CATALOG.md:304`, `PLATFORM_PROPOSAL.md:1345`, `docs/testing.md:417`, `docs/async-jobs.md:346`, `docs/api-reference.md:262-286`, `docs/production-deployment.md:230-232`, the `ghcr.io` namespaces, `SECURITY.md` CORS text, `notify/__init__.py`, the arch docs, `MASTER.md`, the `site` config). The remaining findings rest on the reporting chunks' own reproductions, which were performed read-only against the same commit.
2. **I did not run the test suite.** The reports record the state of their own cohorts (e.g. `read:I3-tests-rest`: "API cohort 310/310 pass; jobs cohort 285 pass + 1 xfail + 1 skip; store cohort 414 pass + 4 skip"; `read:H-tests-backend`: "exits 0: 2 strict xfails, 5 skips"), but I did not re-run them, so the suite's green state is reported, not independently confirmed. I did confirm the collection count: 3,254 tests.
3. **I did not re-verify the live-backend findings** (Postgres parity, Redis queue behaviour, the Isaac worker wire contract, the 2688-scorecard severity sweep, the shadow-gate 77.5% measurement, the 573 leaked Redis keys). These need a live Postgres, a live Redis, and an Isaac worker respectively.
4. **The web findings (P0-6) rest on a browser reproduction I did not repeat.** The chunk verified the crash in Chromium against a real uvicorn server; I confirmed the *shape mismatch* in source, which is sufficient to establish the defect but not to re-verify the exact rendered strings ("NaN failed episodes · 7 modes") or the Playwright click-timeout behaviour.
5. **The marketing-site CSS finding (P2-11)** rests on the chunk's Chromium computed-style measurement. I did not re-measure; the source-level diagnosis (four `@container` rules with no `container-type` ancestor, two of them querying their own container) is confirmed by reading the file, but the exact 1160px single-column computation is not mine.
6. **The video/poster run-id mismatch (P2-12)** rests on the chunk's frame decode. The `site/src/data/content.ts` and `marketing/index.html` side of the contradiction is confirmed by reading; the binary side is not independently re-decoded.
7. **Audit-tree and vault findings are point-in-time by construction.** I confirmed the specific claims they are used for (ADR 0004's title, `ISSUE_CATALOG.md:304`, `PLATFORM_PROPOSAL.md:1345`, the vault's D9/D7 blocks) but did not re-audit the 15 `audit/` reports or re-derive the vault's 67 notes' defect register.
8. **Doc line-number citations may drift.** Where a doc cited a code line, I re-read the code at the current HEAD. Where a *code* line is cited, it is accurate as of `2ffeb6c`. Where a *doc* line is cited, it is accurate as of `2ffeb6c` — but these files are edited frequently, and the repo has a documented history of them drifting.
9. **Two chunks reported scope-resolution ambiguity.** `read:J2-audit` found its pattern (`git ls-files tests/ | grep audit/`) matched nothing and resolved to the root `audit/` tree instead; `read:I3-tests-rest` reconstructed its 75-file set from a sibling chunk's regexes in a workflow journal rather than from a resolve command. `read:N-root-docs` similarly found `ROOTMD` matched no tracked file and resolved to root `*.md`. The resolutions are documented in each chunk's notes and appear correct, but they were inferred, not specified.
10. **A first-pass map file (`_REPO_MAP_pass1.md`) was written at the repo root during compilation** and then deleted, once the second pass (19 chunks) superseded it. It was not part of any chunk's owned set.

**Point-in-time statement.** All of the above describes `D:/SIM-TO-REAL` at **HEAD `2ffeb6c`** on branch `main`, working tree clean except for the untracked `_REPO_MAP_pass1.md`. The 19 reports were produced against that tree; several of them explicitly note that the tree was moving during their own pass (`read:J2-audit`: "48 commits have landed since" the 2026-09-26 audit; `read:J1-docs`: the catalogs are snapshots "by design" but unmarked). Findings here will drift as the code moves — particularly the doc-vs-code clusters in §5, which are exactly the kind of contradiction that a single commit can create or destroy. Re-running this map should prioritise the P0 items, whose signatures (a missing `package-data` key, a `sqlite3.connect` with no `mkdir`, an empty `VALIDSIM_CACHE_FILE=`, a `.split('/')` with no index, a `pair[0]` on a row object) are cheap to grep for.