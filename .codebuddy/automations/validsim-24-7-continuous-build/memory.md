# ValidSim 24/7 Continuous Build — Execution Memory

## PIPELINE DIRECTIVE (updated 2026-09-19 by setup agent — read every run)
- The repository is now a git repo (baseline commit `0a63beb`, branch `main`).
- **After a green build**: if the working tree has uncommitted changes, run `git add -A` and commit with message `chore(ci): continuous-build checkpoint YYYY-MM-DD HH:MM`. **Never push** to any remote.
- **If the build is red**: do NOT commit; fix first.
- New since baseline: dashboard v0 served by FastAPI at `/` (design truth: `design-system/validsim/MASTER.md`), LLM scenario generator (`scenarios/llm_generator.py`, env-gated, rule-based fallback), PostgreSQL store (`store/postgres.py`, `VALIDSIM_STORE=postgres`).
- Cycle 4 (2026-09-19) added: `pyproject.toml` packaging + `validsim` console script, CLI `--latest` flag, `engine/pdf.py` branded PDF + `/scorecard.pdf` endpoint, model registry endpoints (`/api/v1/models`, `/models/{id}/history`), `sim/isaac_worker.py` HTTP adapter + `docs/isaac-worker.md` contract (`VALIDSIM_BACKEND=isaac`). Suite: 250 passed, 2 skipped.
- Remaining backlog (pick one per run): Timescale retention policy docs/migrations, PDF polish (episode replay links), dashboard episode-detail view, LLM prompt eval harness, CI matrix (3.11/3.12/3.13), `--format pdf` in CLI scorecard command, mock-backend fidelity notes for shadow-mode comparison.

## 2026-09-18 ~20:55 UTC (run #7)
- Build: PASS first try (133 tests), PASS after change (135 tests, 8.9s).
- Forward-progress task (from run #6 candidate list): fixed API `/compare`
  determinism. `validsim/api/main.py` now derives the permutation-test seed
  via `stable_seed(run_id, baseline_id)` instead of the hardcoded default 42,
  matching the codebase-wide convention (same pair -> reproducible p-values
  across calls/processes). +2 tests in `tests/test_api.py` (reproducibility
  + seed-derivation pin); no existing tests modified.
- Notes for next runs: CLI `status`/`scorecard`/`gate` could gain a `--latest`
  flag (Week 6 CLI polish); dashboard (Week 7) not started; `engine/export.py`
  PDF branding is a Week 8 item; consider persisting `mean_duration_s` into
  scorecard if dashboard needs it without episodes.

## 2026-09-18 ~19:50 UTC (run #6)
- Build: PASS first try (131 tests), PASS after change (133 tests, 13.8s).
- Forward-progress task (from run #5 candidate list): wired the CLI to the
  persistent validation store. `validsim run` now saves the full StoredRun
  via `create_store()` (honors VALIDSIM_STORE/VALIDSIM_SQLITE_PATH, so CLI
  runs become visible to the API/dashboard), while keeping the JSON cache
  for status/scorecard/gate. Added uniform no-op `close()` on the in-memory
  ValidationStore. +2 tests in `tests/test_cli.py` (sqlite persistence +
  memory-default leaves no artifacts); no existing tests modified.

## 2026-09-18 ~18:35 UTC (run #5)
- Build: PASS first try (126 tests), then PASS after change (131 tests, 8.7s).
- Forward-progress task (Sprint W5 "persistent validation store", completed):
  `validsim/store/sqlite.py` now persists and exactly restores episodes,
  evaluation, safety, regression report, and baseline_run_id (previously only
  the scorecard was stored; aggregates were approximated, so API `/failures`
  and `/regressions` were empty and `/compare` lost duration data under the
  sqlite backend). Added in-place column migration (PRAGMA + ALTER) and
  legacy-row fallback reconstruction. +5 round-trip/persistence/migration
  tests in `tests/test_store_sqlite.py`; no existing tests modified.
- Verified end-to-end: TestClient over sqlite-backed app returns full failure
  details and persists baseline_run_id.
