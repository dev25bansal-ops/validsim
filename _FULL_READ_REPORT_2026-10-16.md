# ValidSim (D:/SIM-TO-REAL) — Full Read Report (fresh)

**Date:** 2026-10-16 · **Scope:** the entire workspace · **Deliverable:** successor to `_FULL_READ_REPORT.md` (2026-09-22).

**Coverage: 565/565 files read** (every tracked file in the workspace, excluding only `.venv/` (4,783 third-party files), `.git/` internals, `__pycache__/`, and tool caches). Nothing was skipped or sampled. Binaries (SQLite DBs, PNG/WebM, `.coverage`) were opened and characterized rather than read as text; `project.docx` was unzipped and its text extracted.

Read method: the tree was partitioned into 17 disjoint groups and read by 17 parallel readers; 7 files that fell between group boundaries were read directly by the orchestrator. Per-group coverage lines are preserved below as evidence.

---

## 1. Repository layout

| Group | Files | What it is |
|---|---|---|
| validsim/engine | 13 | Pure-Python validation core: evaluate, safety, regression, scorecard gate, stats, trends, anomaly, benchmark, pipeline, export/PDF |
| validsim/api + web | 9 | FastAPI app (`/api/v1`), metrics, store_stats, dashboard router; single-page dashboard (app.js/index.html/styles.css/view-core.js) |
| validsim/jobs + notify | 9 | Async job queue (memory + Redis + Lua, leasing, dead-letter), worker; webhook + email notify layer |
| validsim/store + sim + scenarios + root | 17 | memory/SQLite/Postgres stores; mock + Isaac HTTP + shadow sim backends; rule + LLM scenario generators; CLI, config, logging |
| tests (engine/config/core) | 29 | Engine/scoring/stats/coercion/config/logging/hygiene tests |
| tests (api/cli) | 33 | API + CLI test suites (auth, rate-limit, pagination, renderers, metrics, e2e) |
| tests (jobs/notify/llm/isaac) | 32 | Queue lease/retry/dead-letter/SSE, worker, Isaac worker, LLM generator, notify security |
| tests (store/sim/scenarios/web/bench) | 45 | Store parity/differential, sim/shadow, scenarios, web (Node + Playwright), marketing, astro, benchmark harness |
| tests (misc) | 1 | `test_newcode_behaviour_agent.py` — checkpoint propagation + shim + `rng.choices` swap |
| site | 39 | Astro 5 marketing site (src components/pages/scripts/styles, dist build, `.astro` metadata, media) |
| vault (00–04) | 34 | Obsidian: dashboard, strategy, market, product, engineering notes + `.obsidian` config |
| vault (05–99) | 33 | Obsidian: execution, business, fundraising, team/legal, risks, data/sources, templates |
| docs + audit | 32 | ADRs 0001–0006, issue catalogs, platform proposal, references, runbook; 15 category audits |
| root docs/configs + CI/actions/infra | 29 | README/CHANGELOG/SECURITY/CONTRIBUTING/etc., Dockerfile, compose, Makefile, pyproject, workflows, actions, examples, design-system |
| .codebuddy + egg-info + builds + tmp dirs | 39 | Agent-orchestration metadata, packaging metadata, build outputs, two scratch dirs |
| root scratch scripts | 73 | `_probe_*.py`, `_mutation_harness*`, `_tmp_*.py`, `conftest.py`, symlink shim |
| root artifacts (logs/xml/json/txt) | 81 | JUnit XML, coverage JSON, pytest logs, probe outputs |
| root binaries/data | 11 | `.coverage`, `project.docx`, `validsim.db`, 6 `probe_*.db`, `auth-panel.png`, `$null` |
| root web snapshots (orchestrator) | 6 | `_FULL_READ_REPORT.md`, `_bak_{app.js,index.html,viewcore.js}`, `_head_{app.js,index.html}` |

**Headline:** ValidSim is a *pre-traction* "CI for robot foundation models" — a genuinely well-engineered Python core (engine + API + CLI + queue + stores, ~95% coverage) wrapped in a large, self-auditing documentation/marketing apparatus. The dominant theme across **every** group is a mismatch between the marketing/vault narrative and the shipped reality, plus a small set of **structural scoring defects** that several independent audits converged on.

---

## 2. validsim/ — source (48 files)

### 2.1 engine (13/13 read)

The pure-Python core. `pipeline.run_and_score` is the spine: seed → scenarios → episodes → evaluate + safety → baseline regression → `build_scorecard` → persist.

| File | Summary |
|---|---|
| `engine/__init__.py` | Facade re-exporting 21 public symbols; imports `pdf` at package-import time (reportlab stays lazy). |
| `engine/_coerce.py` | `as_float`/`as_int` tolerant coercion catching `TypeError`/`ValueError`/`OverflowError`; returns a caller default instead of raising. |
| `engine/anomaly.py` | Failure-mode spike detector in rate space; σ = √(pstdev² + bootstrap-SE² + binomial var); `warning`/`critical` at z-threshold/2×; seed 42, 500 resamples. |
| `engine/benchmark.py` | Head-to-head scorecard comparison over 4 higher-is-better metrics; overall verdict from `composite` alone. |
| `engine/evaluation.py` | Aggregates episodes → `EvaluationResult` (success rates, per-task, failure taxonomy, mean duration); zeros on empty. |
| `engine/export.py` | Markdown + self-contained HTML scorecard renderers (values escaped). |
| `engine/pdf.py` | Branded one-page A4 PDF via reportlab platypus; `_xml_escape` on all caller strings. |
| `engine/pipeline.py` | Single run→score→persist sequence with injectable sim primitives; `BaselineNotFoundError`; handles append-only collisions via `save_exists`. |
| `engine/regression.py` | Success-rate drop via two-proportion test (critical ≤ −0.05, warning ≤ −0.02); duration via relative-change thresholds + a 60 s zero-baseline rule. |
| `engine/safety.py` | 100 − weighted penalties (collisions 0.5, force 0.3, proximity 0.2), each capped at 1.0; NaN/None treated as violations (fail-closed). |
| `engine/scorecard.py` | Deploy gate: composite = 0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression, **renormalised over measured components only**; APPROVE needs sufficient evidence + composite ≥ 85 + one-sided exact binomial adversarial floor (0.60, ≥30 samples, α=0.05). |
| `engine/stats.py` | Pure-Python percentile bootstrap CI + two-proportion **permutation** test; no NumPy. |
| `engine/trends.py` | Composite trend summary + per-failure-mode first-vs-last rate directions. |

**Engine findings (cited):**
- **F1 `export._md` mis-escapes `|`** (`export.py:28-42`): `"|"` is escaped before `"\\"`, so a pipe becomes `\\|` (even backslashes) and still splits a GFM table row — the exact corruption escaping exists to prevent.
- **F2 `safety.py` is not tolerant to malformed readings** (`safety.py:26-36`): `math.isfinite` is called on raw values, so a persisted `"3"` raises `TypeError` out of `compute_safety` — unlike every other module, which routes through `_coerce`.
- **F3 `scorecard._adversarial_significant` can raise `OverflowError`** (`scorecard.py:301-304`): `math.comb(total,k) * floor**k` overflows float once `total ≳ 1024`; the documented ceiling is 1000.
- **Composite is not a fixed-weight metric** but `trends`/`benchmark` treat it as one: renormalisation means effective weights differ per run.
- **`benchmark` ignores provenance flags**: an unmeasured `robustness_score` (0.0) is compared as a real score.
- **Key-preference order differs**: `anomaly._total` reads `total_episodes` then `episode_count`; `trends._episode_total` the reverse.
- **Docstring mismatches**: `regression` calls the success test a "bootstrap test" (it is a permutation test); `TrendSummary.improving` documented as "last-5 monotonic" but accepts any window ≥ 2.
- **Perf**: `regression._binary_sample` + the permutation test materialise O(1000·2N) element ops — a real cost at the documented "hundreds of thousands of episodes" ceiling.

### 2.2 api + web (9/9 read)

| File | Summary |
|---|---|
| `api/__init__.py` | Re-exports `app`/`create_app`; importing triggers `create_app()` side-effect. |
| `api/dashboard.py` | `/api/v1/dashboard` router: unbounded `GET /history`, `GET /summary`, `GET /`; `mount_dashboard()` registers an unauthenticated `/`. |
| `api/main.py` (1371 lines) | App factory + all `/api/v1` routes; `_AdmissionGate`, `_SlidingWindowRateLimiter`, `_DocsAuthMiddleware`, `_ObservabilityMiddleware`, `require_api_key`. |
| `api/metrics.py` | Prometheus text exposition; derives run/approval/block/latest-composite from the store. |
| `api/store_stats.py` | O(1)-ish observability helpers; `run_summaries`/`strategy_for` documented as **not yet wired**. |
| `web/app.js` | Dashboard DOM wiring; fetches health/history/summary/validations/scorecard/jobs (3 s poll). |
| `web/index.html` | Single page; Chart.js from CDN (no SRI), theme pre-paint script. |
| `web/styles.css` | Dark-first OLED theme with light override, a11y blocks. |
| `web/view-core.js` | Pure UMD functions (mirrors `engine/trends.py`); testable under Node. |

**API findings:** wildcard CORS default (`*`); **unauthenticated `/metrics`**; no CSP/SRI on the CDN asset that the page's API key sits behind; HTML scorecard served inline; rate-limit keyed on IP only (ignores `X-Forwarded-For`); `X-Request-ID` trusted/echoed; `/dashboard/history` unbounded; fast `store_stats` paths half-wired; dashboard exposes a fraction of the API (no DELETE/compare/regressions/models/exports in the UI); `jobStatusSeen` grows unbounded; `DEFAULT_THRESHOLD=85` duplicated in JS and Python.

### 2.3 jobs + notify (9/9 read)

| File | Summary |
|---|---|
| `jobs/models.py` | Frozen `JobSpec`/`JobRecord`; status machine (queued/running/done/failed/dead), `lease_epoch` fencing, retry/reclaim budgets. |
| `jobs/queue.py` (2034 lines) | In-memory + Redis queues with six Lua scripts; atomic claim→stamp lease→`attempt+1`→`epoch+1`; exponential backoff (cap 300 s); fenced writes vs byte-CAS; reaper. |
| `jobs/router.py` | `/jobs` POST/GET/`{id}`/`status`/`events` (SSE); `_JOB_ID_RE=^vrun-[0-9a-f]{8}$`; SSE bounded by wall-clock deadline. |
| `jobs/worker.py` | `JobWorker` claim loop, daemon heartbeat, fenced `update_status`, requeue/dead-letter, `run_forever` with SIGTERM/SIGINT. |
| `notify/__init__.py` | Re-exports; documents the layer as **implemented/tested but not wired into any entrypoint**. |
| `notify/config.py` | Fail-closed env parsing; positional (index-paired) secret/format/severity lists. |
| `notify/dispatcher.py` | Webhook fan-out; severity routing; live POSTs with retries; HMAC-SHA256 `X-ValidSim-Signature`; never raises. |
| `notify/email.py` | SMTP notifier (dry-run default); CR/LF subject guard, recipient regex, STARTTLS with certifi context. |

**Jobs/notify findings:** **SSRF is scheme-only** (no loopback/RFC1918/link-local/metadata blocking, no allowlist); **HMAC is body-only and replayable** (no timestamp/nonce, no URL binding, no verify helper); `_is_expired` can raise `TypeError` on naive/aware datetime mix; clock seam applied inconsistently (`renew_lease`/`_apply_status` use raw `_utc_now`); backends diverge on requeue invariants (memory retains a stale lease on `QUEUED`, Redis nulls it); false/mislabeled dead-letter signals (worker logs `dead_lettered: True` even when the lease was lost, and finishes as `FAILED` not `DEAD`); reclaim-budget off-by-one asymmetry; misleading Redis full-queue depth; Redis reaper is O(all-time jobs) **every poll**; Redis read paths don't validate records (one corrupt row 500s `GET /jobs`); non-idempotent webhook retries + not thread-safe + `_hooks` tuple leaks secrets in `repr()`; email `from_addr` unvalidated; `NotifyConfig.summary()` impure. The **notify layer is dead code** — nothing in the run path calls `notify_run_completion`.

### 2.4 store + sim + scenarios + root (17/17 read)

| File | Summary |
|---|---|
| `store/memory.py` | In-memory store + shared contract (append-only `save`, `_snapshot` deep-copy, `reconstruct_kwargs`). |
| `store/sqlite.py` | stdlib sqlite3; `INSERT … ON CONFLICT (run_id) DO NOTHING`; `close` non-terminal. |
| `store/postgres.py` | psycopg 3, lazy import, autocommit, JSONB; whitelisted table name; parameterised values. |
| `sim/runner.py` | `MockIsaacBackend` deterministic seeded simulator; `stable_seed` = crc32 & 0x7FFFFFFF. |
| `sim/isaac_worker.py` | HTTP GPU adapter (`POST /episodes/run`), strict contract, retries transport errors ×2. |
| `sim/shadow.py` | Shadow harness (mock vs worker on aligned seeds) + contract replay fixtures. |
| `scenarios/generator.py` | Rule-based generator; 12 adversarial categories; `(seed, task_id)`-seeded RNG. |
| `scenarios/llm_generator.py` | LLM generator (OpenAI-compatible) with validation + fallback; **zero production callers**. |
| `cli.py` | Typer CLI: run/validate/status/scorecard/report/gate/delete/models/version/health/compare/jobs/worker. |
| `config.py` | Frozen Pydantic v2 models; path-traversal guards; `resolve_asset_path` documented DEAD. |
| `config_loader.py` | TOML/YAML loader — explicitly "NOT WIRED". |
| `logging.py` | JSON formatter with structural redaction of sensitive keys. |
| `project_config.py` | Policy/infra classification; `INFRA_KEYS` drift registry; `resolve_config`/`config_to_env` unwired. |

**Findings:** **CLI omits `checkpoint_id` from the constructed `TaskConfig`** (`cli.py:407-413`) — the mock reads `task.checkpoint_id`, so the CLI's `--checkpoint` may shift nothing (verify against `pipeline` binding). **In-memory store read-path aliasing**: `get`/`history` return the stored object itself (save-side alias closed, read-side not) — a caller mutating a returned `StoredRun` rewrites the stored verdict, and SQLite/Postgres are immune (parity divergence). Postgres/SQLite schema nullability divergence; Postgres `_run_from_row` positional coupling. `current_scenario_backend()` leaks an `httpx.Client`; Isaac backend client never closed by the run path. `config.py` misses drive-relative Windows paths (`C:foo`). **No SQL injection found.**

---

## 3. tests/ (140 files)

- **engine/config/core (29):** rigorously covers weights, bounds, monotonicity, golden composites, safety fail-closed, the binomial gate (independent `lgamma` CDF oracle), coercion, path hardening, logging redaction, repo hygiene. Weak spots: `test_evaluation.py` is a smoke test; **PDF content is essentially unasserted** (`test_pdf.py` only checks `startswith(b"%PDF-")`); the "unmeasured robustness is labelled" PDF test never checks the label (tautological).
- **api/cli (33):** best-in-suite auth surface — `test_api_auth_routes.py` derives the entire route inventory and asserts both structural and behavioural gating. Concurrency starvation tested. **Two intentionally-red tests shipped**: `test_cli_delete.py::TestDeleteLatestAgainstTheDurableStore` encodes the live `delete --latest` cache-vs-store defect. Several `SUSPECTED-WRONG` characterizations (block reasons omitted from all summary/list views).
- **jobs/notify/llm/isaac (32):** exceptionally thorough queue coverage (epoch fencing, reclaim budgets, real Lua via fakeredis+lupa, a deterministic `_RacingClient`). **A strict xfail documents a real bug**: `_utc_now` floors to whole seconds so a 1 s lease can be reclaimed while alive. Notify security gaps (HMAC replay, SSRF, blank-secret forgery) are encoded as **green** tests — the suite stays green whether or not they are fixed. One tautology: `assert "smtp.example.com" not in repr(...) or True`.
- **store/sim/scenarios/web/bench (45):** store parity is the strongest area (cross-backend contract, seeded differential replay with an independent oracle, structural deep-copy walk); **Postgres is structural-only** (never executed). **Two tests encode opposite SQLite close contracts** (`test_store_divergence_agent` expects `ProgrammingError`; `test_store_close_contract_agent` expects idempotence) — at most one can pass. `test_store_divergence_agent::test_div5f_ordering_has_no_secondary_key` actively forbids adding the tiebreaker the module docstring says is needed. Benchmark harness is opt-in, stdlib-only, ratio-based (25% tolerance), and fails on a missing baseline under `--compare`.
- **misc (1):** `test_newcode_behaviour_agent.py` — behavioural tests for checkpoint propagation, the symlink-free tmp shim, and the `rng.choices` bootstrap swap; each targets a *silent* failure mode.

**Cross-suite:** no `xfail` in the engine group, but `test_jobs_concurrency_agent`, `test_sim_contract_audit_agent`, and `test_store_divergence_agent` carry `strict=True` xfails for **documented live defects**. Process-global `os.environ`/`os.chdir` mutation in `test_project_config.py` and timing/wall-clock assertions in several files are flakiness risks.

---

## 4. site/ (39 entries)

Astro 5.18.2 static site (`/` and `/privacy/`), one deferred JS module (116 KB bundling GSAP 3.15.0 + ScrollTrigger), oklch design tokens, GSAP scroll-scrub of the "gate axis" figure.

**Content claims** (for truthfulness): demo run `vrun-ed0c150a`, success 69.0%, adversarial 34.0% (17/50), safety 77.7, robustness "not measured (1 randomization group)", composite 72.7 vs gate 85.0 → BLOCK; the self-audit narrative ("would return APPROVE for a run in which every episode failed"; "thirty of the hundred composite points were granted unconditionally"; post-fix "42.86"); pricing $0/$2k/$8k/Custom; "pre-traction, no customers".

**Findings:** **invalid CSS a11y bug** — `Field.astro:53` sets `tabindex` inside a `<style>` block (not a CSS property); the horizontally-scrollable region has no `tabindex`, so it is a keyboard trap. Dead tokens (`--t-readout`, `--split-*`) + hardcoded breakpoint drift (46rem/48rem match no token). Unused exports (`site.status`/`site.tagline`) + stale comment. Broken type ref (`.astro/content.d.ts:197` imports a nonexistent `content.config.mjs`). Misleading "build fails on broken link" comment (no link checker). **Mobile nav gap** (all nav links hidden <48rem, no replacement). Missing `robots.txt`/`sitemap.xml`/`og:image`. Privacy wording understates Google-Fonts requests. dist is current (built 2026-10-03) and src↔dist consistent.

---

## 5. vault/ (67 files)

Obsidian "second brain": thesis ("GitHub Actions for robots"), market sizing (TAM $300–640M by 2033, SOM Y3 $5–10M), personas/buyer tiers, MVP scope (composite 40/30/20/10), pricing, unit economics, fundraising ladder (YC $500K → pre-seed → seed → Series A), team/legal, risk register, sources.

**The vault's defining feature is that it audits itself** — `Technical Moat Features.md` (104 KB) enumerates 11 verified defects (D1–D12, D8 retracted) that contradict the marketing. **The headline gap is D9**: the CLI/worker gate **APPROVES a checkpoint that fails 100% of its adversarial suite** (composite 92.82 → APPROVE at 10% adversarial share), because robustness and regression are constant on those paths. Other vault findings: constant composite terms (D2, +30 points unconditional on CLI/worker), mock ignores `scenario.params` (D12), no `scenario_id` join key for the "data flywheel" (D7), no compute-time instrumentation (D4), Postgres cannot support a gapless append-only chain (D10). Cross-doc contradictions: 4× vs 8× A100; MIT CLI vs proprietary weights (IP Strategy vs actual in-process CLI + `pip install .`); adversarial differentiation vs non-gating; immutable-evidence pitch vs a working `DELETE`; `SECURITY.md:276-279`'s tamper-evidence claim is false for memory/SQLite. Test counts are quoted six mutually incompatible ways and the vault forbids quoting any. Many `[VERIFY]` placeholders and unchecked YC traction boxes.

---

## 6. docs/ + audit/ (32 files)

**ADRs:** 0001 ADR process; 0002 composite gate (weights + threshold 85, **2026-09-29 abstention amendment** after a 0%-success policy scored 60/APPROVE); 0003 Redis queue w/ in-memory fallback (rejects Celery); 0004 store abstraction; 0005 MIT CLI / proprietary platform; 0006 gate reads the durable store (supersedes 0002's gate behaviour).

**Issue catalogs:** `ISSUE_CATALOG.md` (2026-09-21, mostly closed; item 44 = certified total gate failure) is superseded by `ISSUES_AND_REMEDIATION_CATALOG.md` (2026-10-15). `PLATFORM_PROPOSAL.md` argues ValidSim is "a high-quality single-host app, not yet a deployable platform" (5 hard blockers; the Isaac worker is an HTTP contract only).

**Audits (15):** converge on one root defect — **components that are structurally constant and therefore measure nothing**. `_robustness_score` returns 100.0 whenever there are <2 `randomization_level` groups, and the runner stamps every episode with the single value `task.randomization`; `_regression_component(None)` returns 100.0 and the CLI/worker never pass a baseline. Net: `composite = 0.4·success + 0.3·safety + 30.0` on the CLI/worker path. Also: **1-episode run → composite 100/APPROVE**; **`checkpoint_sha256` accepted then discarded** (no consumers, no scorecard field); **`make smoke` cannot pass on a clean machine**; docs/OpenAPI bypass auth; HMAC replayable; `test-count`/coverage claims cannot be compared to hardcoded literals. Stale docs flagged: `ISSUE_CATALOG.md` phantom list, `SECURITY.md:276-279`, README webhook/email `[x]`, `examples/nightly-adversarial-sweep.yml`.

---

## 7. root docs/configs + CI/actions/infra (29 files)

README (states the abstention rule and marks notify/LLM/GPU worker unimplemented), CHANGELOG (current line 0.2.0, iteration-6 in `[Unreleased]`), CONTRIBUTING, SECURITY (treats security defects as safety defects; GitHub-advisory-only intake), AUDIT_CATALOG, ICORPS/TECHSTARS applications, `.impeccable.md`, Dockerfile (multi-stage, non-root, healthcheck), docker-compose (api+worker+redis+postgres, loopback ports, `${POSTGRES_PASSWORD:?}`), Makefile, pyproject (console script `validsim = validsim.cli:app`, coverage `fail_under=90`), pytest.ini, requirements*.txt, `.env.example`, `.gitignore`, `.dockerignore`, 3 workflows (ci/nightly/release), 2 composite actions (validate/scorecard), 2 example workflows, `scripts/build.ps1`, `marketing/index.html`, `infra/README.md`, `design-system/validsim/MASTER.md`.

**Findings:** **CI gap — `tests/bench/baseline.json` is referenced by `nightly.yml` but does not exist**, so the benchmark regression gate passes vacuously. **Supply-chain drift** — `docker/build-push-action` is v6.19.2 in `ci.yml` but v5.4.0 in `release.yml` (everything else SHA-pinned — good). `.env.example` omits several live readers (notify, LLM, log level, pipeline concurrency, thread limiter). Stale `examples/nightly-adversarial-sweep.yml` comment ("no pyproject.toml yet") + 3 unpinned actions. `infra/README.md` describes the queue as "future". README understates CI. Version/test-count drift across docs. `marketing/index.html` JSON-LD lists 3 tiers while the visible table adds Enterprise.

---

## 8. .codebuddy + egg-info + builds + tmp dirs (39 files)

AI-agent orchestration metadata. Two 24/7 automations (`validsim-24-7-agent-fleet`, `validsim-24-7-continuous-build`) run a ~10-lane agent fleet with disjoint file ownership under a hard RAM stop; because free RAM sat at 0.3–1.4 GB, **most waves were static-analysis-only**, and ~68–75 new tests were written but **never executed by a real pytest runner**. Headline fleet fixes (9 source defects): memory `_snapshot` shallow-copy → structural deep-copy (a stored BLOCK verdict could be silently rewritten on the default `memory` store); `notify/config._split_positional` blank-padding mis-signing HMAC secrets; `claim_next` dead-letter signal dropped; `/metrics` O(n) DoS + `latest_composite` tie-break; mock fabricating human proximity for human-free scenes; `dry_run` discriminator + false reachability docstrings; site run-id drift; nightly gate that could never fail; Makefile false CI-equivalence claim. Open items: SSRF policy (user decision), missing `baseline.json`, `run_summaries` unwired, `marketing/index.html` unowned.

**Findings:** the `_tmp_grepdemo`/`_tmp_gatecheck` dirs are the **reproduction of the "gate that cannot fail" defect** (a `pytest_terminal_summary` hook bumps `testsfailed` after the exit code is fixed; `gate.sh` is the grep workaround) — cleanup candidates. Build artifacts (`builds/junit.xml` 1292 tests 2026-09-20; `last-build-output.txt` 338 passed 09-19) are **stale** vs the current ~3044-test tree. The packaged `PKG-INFO` embeds a README that still carries two claims the fleet disproved. Three team dirs are empty `[]`; `docs/config.json` lists duplicate lanes. Audit-integrity incidents are recorded in the agent log itself (a lead overwrote `memory.md` and fabricated member self-reports, both retracted).

---

## 9. root scratch scripts (73 files)

71 `_*.py` probes/harnesses + `conftest.py` + `pytest_symlink_free_tmp.py`. Themes: engine/scorecard correctness (~18), stats (~10), adversarial gate (~7), checkpoint propagation (~6), store contract (~9), notify/security (~10), API/rate-limit/perf (~8), tooling (~7).

**Real bugs these probes proved:** `as_int`/`as_float` leak `OverflowError` on `inf`/`10**400` → crashes `detect_anomalies`; the mock ignored `checkpoint_id` (fixed via crc32 tiers); `validsim_runs_total` **decreases on delete** (breaks Prometheus `rate()`); robustness constant 100 / composite blind to nominal↔adversarial mix; `compare()` treats baseline duration 0 as "no change"; `detect_anomalies` skips a brand-new failure mode (σ=0 `continue`); SSRF/HMAC-replay/empty-secret forgery/unbounded serial dispatch (~306 s for 20 hooks vs a 30 s lease); anyio thread-limiter exhaustion makes `/health` unservable.

**Hygiene:** **all 73 files are tracked by git and the working tree is clean** — despite "throwaway"/"DELETED before report" docstrings, including four files that explicitly claim they were deleted. This is a real hygiene problem: host-specific, exploit-bearing audit scratch is permanently versioned.

---

## 10. root artifacts — logs/xml/json/txt (81 files)

16 JUnit XML, 2 coverage JSON, 1 read-report JSON, 11 logs, 51 txt. **The suite is not green and not consistently red — it is in flux across dated snapshots.** 2026-09-20 snapshots are near-green (all `errors=0`; `_verify.xml` 1520 tests/49 skips; `_agent_base.xml` 2857 tests/80 skips). 2026-09-25→10-04 snapshots are red: `_post.log` 18 failed, `_wave.log` 7 failed, `pytest_full.txt` 34 failed, `full2.txt` **187 failed + 63 errors** ending in an **ENOSPC "no space left on device"** crash, `_final.txt` 27 collection errors (`UnboundLocalError: 'api_key'` at `main.py:930`), `pytest_full.txt` 34 `NameError: ScenarioGenerator not defined` in CLI tests.

**Recurring failures:** `test_api_rate_limit.py::test_api_key_gets_its_own_bucket` (per-key bucket never enforced; contradicted by `_rl_report.txt` EXIT=0 → order-dependent flake); jobs worker retry/dead-letter; `test_sim_contract_audit_agent` determinism (calls `success_probability()` with 4 args vs 2–3 accepted → stale-signature test); **two tests assert opposite zero-baseline severities**; `test_scorecard.py` missing `robustness_measured`/`regression_baseline_available`. Coverage: `coverage.json` **95.86%** (44 files) vs `_cov_agent.json` **91.90%** (42 files, coverage.py version regression 7.16.1 vs 7.13.2). Hygiene: 70/81 are git-ignored scratch; 11 are tracked (including stale `full.txt`/`full2.txt`/`run1.txt`/`run2.txt`).

---

## 11. binaries/data (11 files) + root web snapshots (6 files)

- **`$null` (0 B) is committed to git** — a shell redirect artifact.
- **~199 MB of stale `probe_*.db`** (1K/10K/100K scaling series + 3 identical 10K variants) — git-ignored, largest reclaimable block; all rows uniform `composite 100.0`/APPROVE (synthetic).
- **`validsim.db`** = 1 placeholder row (`vrun-blank01`); **`.coverage`** = stale un-merged arc-only run (44 files).
- **`project.docx`** = the origin document, but it is an **AI chat export pasted into Word** (preamble "I'll create a comprehensive startup document…" and trailing assistant offers intact); 19 sections; contains the concrete scoring model and MVP claims.
- **`auth-panel.png`** tracked at repo root; **`site/public` vs `site/dist`** media byte-identical.
- **Root web snapshots:** `_bak_viewcore.js` is **byte-identical** to the current `view-core.js`; `_bak_app.js` differs only in the wrong-key credential-clearing fix; `_bak_index.html` differs only in the footer composite wording (pre-abstention); `_head_app.js`/`_head_index.html` are **older revisions** (951/326 lines vs 1213/417) predating the auth panel, jobs section, and most UI. `_FULL_READ_REPORT.md` (1752 lines, 2026-09-22) is the predecessor of this report, covering 271 files in 29 groups.

---

## 12. Consolidated risk register (prioritized)

**Critical (trust-breaking):**
1. **30 unconditional composite points** — robustness = 100.0 always and regression = 100.0 on CLI/worker; the gate is optimistic by up to 30 points on exactly the runs a customer acts on. Root cause is a *degenerate input* (one randomization group; no baseline), not the weights. (docs/audit, vault D2/D9, probe `_probe_eng_correctness`.)
2. **Adversarial gate is not load-bearing** — the CLI/worker APPROVE a checkpoint failing 100% of its adversarial suite (approval floor 62.5% success at perfect safety). Contradicts the headline differentiator. (vault D9, c5-ml, c4-moat.)
3. **1-episode run → composite 100 / APPROVE** — no operational episode floor; the evidence guard's quantity predicate is unfalsifiable in production. (cat6-F1, cat12-04.)

**High:**
4. **`checkpoint_sha256` accepted then discarded** — no scorecard field, no consumers; no artifact can answer "which checkpoint bytes produced this approval?" (cat12-02, S-04.)
5. **Notify layer is dead code** — SSRF (no private-range blocking), replayable body-only HMAC, blank-secret forgery, unbounded serial dispatch; nothing calls it, yet README/docs describe it as shipped. (c5-notify, notify-sec, probe `_probe_notify_*`.)
6. **Unauthenticated `/metrics` + wildcard CORS default + no CSP/SRI** on the page that holds the API key. (cat2-security, api group.)
7. **Store read-path aliasing in the memory backend** — a returned `StoredRun` can be mutated to rewrite the stored verdict; SQLite/Postgres immune (parity divergence). (store group, probe `_probe_store_audit*`.)
8. **`make smoke` cannot pass on a clean machine**; `delete --latest` fails against a durable store (shipped as red tests).
9. **CI benchmark gate passes vacuously** (`tests/bench/baseline.json` missing).

**Medium:** CLI drops `checkpoint_id` from `TaskConfig`; `export._md` pipe mis-escaping; `safety.py` non-tolerant to malformed readings; `scorecard` binomial overflow >~1024; Redis reaper O(all-time jobs) per second; Redis read paths 500 on one corrupt row; non-idempotent webhook retries; sub-second lease reclaim xfail (live double-run bug); `test_store_*` mutually contradictory close/ordering contracts; `.env.example` reader→template drift; `docker/build-push-action` v5-vs-v6 drift; stale `infra/README.md` and example workflow.

**Low / hygiene:** `$null` tracked; ~199 MB stale probe DBs; `.coverage` stale; `auth-panel.png` at root; 73 tracked scratch scripts; `_bak_*`/`_head_*` backups (one byte-identical); stale build artifacts; `project.docx` is an AI chat export; site `tabindex` a11y bug; dead site tokens; mobile nav gap; vault `[VERIFY]` placeholders and six contradictory test counts.

---

## 13. Coverage verification

Every file in the workspace manifest (565 entries, excluding `.venv/`, `.git/` internals, `__pycache__/`, and tool caches) was read or inspected:

- validsim/ 48/48 · tests/ 140/140 · vault/ 67/67 · site/ 39/39 · docs+audit 32/32
- root docs/configs 18/18 · CI/actions/infra/examples/design-system/marketing/scripts 11/11
- .codebuddy+egg-info+builds+tmp 39/39 · root scratch scripts 73/73 · root artifacts 81/81 · root binaries 11/11 · root web snapshots 6/6

Nothing was left unread. The only files not opened as text were genuine binaries (SQLite DBs, PNG/WebM, `.coverage`, `project.docx`), each characterized by type, size, and — where meaningful — internal structure.
