# ValidSim — WORK_PROGRESS.md

## Initial State (recorded 2026-10-17)

**Working repo:** `/Users/abhijeetmiskin/AppData/valid/validsim`
**Branch at start:** `main` at `98cc723` (clean; up to date with `origin/main` = fork abhijeet1267/validsim)
**Continuation branch:** `continuation/merge-prep` created at `025890c` (tip of user's `founder2/platform-mvp`)

**User's work branch (`founder2/platform-mvp` = `025890c`):**
- 23 commits ahead of `main`. Live in git worktree
  `/Users/abhijeetmiskin/.superset/worktrees/validsim/false-account-e7705a6a` (~124 MB = the "148 MB folder").
  **PRESERVED — not modified, not deleted.** It holds the user's uncommitted changes (incl. `validsim/logging.py` deletion, test edits).
- `west-patella-d66a2d4e` worktree also at `98cc723` (= main).

**Companion reports:** `COMPLETION_REPORT.md` (in false-account worktree), `WORKTREE_STATE.md`, `CURRENT_STATUS.md`, `COMMIT_LOG`, `PULL_REQUEST_DESCRIPTION.md`.

**Initial git status on continuation branch:** clean except untracked `.DS_Store` files.

## Key discoveries (audit in progress)

| Area | Finding | Status |
|------|---------|--------|
| Logging/import | `validsim/logging.py` still exists (byte-identical copy of `_log.py`) → stdlib `logging` shadowing NOT fully resolved. Rename incomplete. | PARTIAL — fix needed |
| Notification wiring | `pipeline.run_and_score` DOES call `notify_run_completion` (lines 191,198). But `notify/dispatcher.py` docstring falsely claims it is "not wired" (stale). DUPLICATE notification bug: the retry/duplicate path (line 191) re-notifies. | PARTIAL — fix needed |
| Health endpoint | Returns `notify_config_from_env().summary()` which is secret-free (`hooks: []`, no URLs/secrets). Report's Appendix C example showing exposed URLs is INACCURATE. | VERIFIED secure; report wrong |
| Tests | Claims 3002+ tests / ~95% coverage. Must be verified by running suite. | UNVERIFIED |

## Planned fixes (in priority order)
1. Delete `validsim/logging.py` (complete logging.py→_log.py rename); add regression test that stdlib `logging` is not shadowed.
2. Fix `pipeline.py`: drop `notify_run_completion` call on the duplicate/retry path (avoid duplicate notifications).
3. Update stale `notify/dispatcher.py` docstring.
4. Add regression tests for health endpoint secret-free behavior + duplicate notification.
5. Run full test suite, lint, type checks; fix regressions.
6. Document contribution statuses vs COMPLETION_REPORT.md; prepare merge plan.

## Files changed so far
1. `validsim/logging.py` — DELETED (completed `logging.py` → `_log.py` rename; was byte-identical duplicate, caused stdlib `logging` shadowing).
2. `tests/test_logging_redaction.py` — docstring cross-refs `validsim.logging` → `validsim._log` (lines 5, 22).
3. `validsim/engine/pipeline.py` — removed `notify_run_completion()` call on the duplicate/retry path (was sending duplicate webhooks on retry); notification now only on new-save path.
4. `validsim/notify/dispatcher.py` — updated stale module docstring (claimed layer "not wired"; it IS wired into `pipeline.run_and_score`).
5. `WORK_PROGRESS.md` — created (this record).

## Tests executed
- Baseline full suite (`pytest tests/`, python3.11, venv at `.venv`):
  **3001 passed, 53 skipped, 16 xfailed, 2 xpassed, exit 0, 196s.** No failures.
  Includes 4 new regression tests: 2 x notification duplication (test_pipeline.py),
  2 x logging shadowing resolved (test_logging_redaction.py).
- Coverage: **95.83% total** (required floor 90% reached) — matches report's "~95%" claim.
- Ruff lint: **All checks passed.**
- FINAL clean-env full suite, 2026-10-18 (`unset` all VALIDSIM_*/POSTGRES/REDIS
  vars to clear a polluted shell, then `pytest -q -p no:cacheprovider --tb=no
  --junitxml`: **tests=3072, failures=0, errors=0, skipped=69, passed=3003,
  exit 0, ~9.5 min.** NOTE: an earlier `pytest -q` in a shell exporting
  `VALIDSIM_API_KEY` showed 1 failure in
  `TestHealthAuthBehaviors::test_unauthenticated_health_allowed_by_default` —
  that failure is a test-isolation artifact (global env var changes default
  behavior), NOT a code regression; it passes with a clean environment.
- `docker compose -f docker-compose.prod.yml config --quiet` (dummy required
  secrets): **exit 0** — the new prod file is valid Compose.
- Import/CLI smoke: `validsim --help` works; `import logging` inside the
  package resolves to stdlib (guarded by new regression tests).

## Session changes committed on `continuation/merge-prep`
6. `docker-compose.prod.yml` — NEW (was referenced by
   `docs/production-deployment.md` but missing from the repo). Single-node
   prod stack (postgres/redis/api/worker), GHCR image, required-secret
   fail-fast, healthchecks + `service_healthy` gating; `config --quiet` → 0.
7. `docs/production-deployment.md` — quickstart rewritten to generate secrets
   into untracked `.env`; embedded full-YAML example replaced with a pointer
   to the now-real `docker-compose.prod.yml` (doc no longer shows phantom config).
8. `tests/test_api_health.py`, `tests/test_api_structure_agent.py` — health
   payload contract extended with secret-free `notify` section assertions.

## Next step
Audit simulation backend (mock vs real Isaac — central to project purpose; check whether the 148 MB folder holds sim assets/checkpoints); review dashboard/PDF/comparison; audit deployment docs vs actual artifacts; document contribution statuses vs COMPLETION_REPORT.md; prepare merge plan.

---

## Local completion phase (2026-10-18, NO push/merge per user instruction)

**Starting state verified:** branch `continuation/merge-prep` at `8105191`;
`git rev-list --left-right --count origin/main...HEAD` = `0 27`
(25 inherited from `founder2/platform-mvp` + 2 session commits).
**Count discrepancy resolved:** the "23 commits" figure in the line above was
stale — the true ahead-count at `025890c` was 25, plus the 2 new commits = 27.
`founder2/platform-mvp...continuation/merge-prep` = `0 2`, as expected.
Pre-existing uncommitted edits (notify-wiring xfail removal, pipeline/notify
doc polish) were found in the tree and preserved untouched.

**Phase 2 — env-dependent tests (ROOT CAUSE FOUND + FIXED):**
- Reproduced at scale: `VALIDSIM_API_KEY=poison-key pytest tests/` →
  **120 FAILED**. The suite-wide `_isolate_deployment_env` fixture in
  `tests/conftest.py` cleared only 3 vars (`VALIDSIM_ENV/STORE/SQLITE_PATH`),
  so any exported `VALIDSIM_*` (API key, store/queue selectors, …) silently
  reconfigured every app the suite built. The single failure quoted in the
  prior handover was the tip of this iceberg.
- Fix: `_ISOLATED_ENV` now covers all 44 `VALIDSIM_*`/`POSTGRES_*`/`REDIS_*`
  vars the package reads (deployment config only; `VALIDSIM_TEST_REDIS_URL`
  deliberately excluded — it is test routing, needed by CI live tests).
  `tests/test_api_health.py::TestDeploymentEnvIsolation` pins it: a
  scanner test fails if any `validsim/` env var is missing from the list,
  plus auth-present/absent behavior tests. Verified: auth cohort
  (97 tests incl. health/auth/routes/negative/security) passes with a
  hostile env (`VALIDSIM_API_KEY` + `VALIDSIM_ENV=production` + store/queue
  overrides exported).
- NOTE: `VALIDSIM_STORE=postgres` at *collection* time still breaks
  collection (module-level `create_store()` in metrics/structure tests) —
  pre-existing design, out of scope; CI never sets it globally.

**Phase 3 — Redis CI coverage (ADDED):** new `redis` job in
`.github/workflows/ci.yml` (redis:7-alpine service, health-gated,
`VALIDSIM_TEST_REDIS_URL` on DB 9, cohort =
`test_jobs.py::TestLiveRedisQueue` + `test_jobs_concurrency_agent.py` +
`test_queue_depth.py::TestRedisDepthCap`), with a JUnit-parsed no-vacuous-pass
probe (fails on any skip/failure, asserts non-trivial collection) and an
artifact upload; `docker`/`compose` gates extended to `needs: [test,
postgres, redis]`. Locally validated: YAML parses, gates correct; live
cohort skips cleanly without a server (Docker unavailable on this host, so
the CI job itself could not be executed — reported honestly).

**Phase 4 — compose validation:** `docker compose -f
docker-compose.prod.yml config --quiet` (dummy secrets) → **exit 0**.
Docs reference the real file. No live smoke (no Docker) — NOT claimed.

**Phase 5 — notify/pipeline:** audited, no new code needed. Persist-before-
dispatch ordering, exactly-once-per-run, never-raises dispatcher, bounded
retries, dry-run default, secret-free health — all already pinned by
existing tests (46 wiring + pipeline duplication/failure tests).

**Phase 7 — Isaac:** `isaac_worker.py` + `docs/isaac-worker.md` + 40 mock-
transport tests reviewed; real GPU execution still externally blocked
(DGX Cloud credits). Nothing fabricated; no substitute implementation.


**Phase 6 — verification (THIS SESSION, JUnit-evidenced):**
- Full suite clean-env, 2026-10-18 (`215f1ef` + preserved notify edits in
  tree): **tests=3076, failures=0, errors=0, skipped=68, passed=3008**
  (JUnit XML). Prior "3003/69" was the pre-fix baseline; +4 net = 3 new
  isolation regression tests + the preserved notify test additions.
- Focused cohort (auth/health/security/logging/pipeline/wiring, 8 files):
  **237 passed** clean; auth cohort **97 passed** under hostile env
  (`VALIDSIM_API_KEY=poison-key` + `VALIDSIM_ENV=production` + store/queue/
  CORS/rate-limit/PG/Redis overrides) — the exact scenario that previously
  produced 120 failures.
- Ruff `validsim tests`: **all checks passed**. `git diff --check`: clean.
- CLI smoke (`python -m validsim.cli --help`): OK.
- Prod + dev compose `config --quiet`: both **exit 0**.


