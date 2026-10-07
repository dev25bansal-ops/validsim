# Cat 3 — CLI / Config / Logging / Build-CI Audit Report

**Agent:** c3-cli · **Project:** ValidSim @ `d:\SIM-TO-REAL` (v0.2.0, FastAPI + Typer + SQLite/Postgres + Redis + Prometheus)
**Date:** 2026-09-26 · **Mode:** read-only analysis. No production file was edited.

## Scope note (read this first — it bounds every claim below)

This audit ran concurrently with a period during which **the tree changed under it repeatedly**: `scripts/build.ps1` gained a fix mid-audit, `validsim/project_config.py` + `tests/test_project_config.py` were created by another agent, an entire `tests/bench/` suite appeared and was then removed, and several `validsim/` files carry modifications I did not make. **Several findings I had previously reported are therefore REFUTED below**, because the code they described no longer exists in that form.

Every finding was **re-verified against the tree as of 2026-09-26** with the exact command shown. Where I cannot prove reachability, I say so.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| CLI-01 | `make smoke` cannot pass — chains `gate` without a durable store | `Makefile:49` | **Critical** | VERIFIED | 1h | `python -m validsim.cli run --threshold 50` then `python -m validsim.cli gate --latest` → exit 2 |
| CLI-02 | Regression p-value seeded off run id: API and CLI disagree | `validsim/cli.py:651`, `validsim/api/main.py:727` | **Critical** | VERIFIED | 3h | Two `run` of same checkpoints, then `compare` → p=0.709 then 0.711 |
| CLI-03 | Redis reaper reclaims **live** leases (Lua string-compare) | `validsim/jobs/queue.py:187` | **Critical** | VERIFIED | 2h | Lease `09:46-05:00` vs now `13:46+00:00` → string compare `True`, instant compare `False` |
| CLI-04 | Fabricated cost data: `duration_s` and `mean_duration_s` are the **same fiction**, persisted | `validsim/sim/runner.py:131`, `validsim/engine/evaluation.py:83` | **High** | VERIFIED | 1d | `$env:TEMP\cli_audit_cost_probe.py` → `identical=True` at 3 seeds, ~$48.83/run |
| CLI-05 | CI `test` job cannot collect (`requirements-dev.txt` not installed) | `.github/workflows/ci.yml:46` | **High** | VERIFIED | 5m | `ci.yml:46` installs `requirements.txt` only; `tests/test_delivery_pipeline.py:18` imports `yaml` |
| CLI-06 | `config_to_env` redaction is unreachable from the real config path | `validsim/project_config.py:377,383` | **High** | VERIFIED | 1h | `config_to_env(resolve_config(...))` emits no `SECRET_KEYS` entry |
| CLI-07 | `85.0` approval threshold duplicated at 6 code sites | `cli.py:306,324`; `engine/scorecard.py:108,133`; `engine/pipeline.py:55`; `jobs/worker.py:51` | Medium | VERIFIED | 2h | `Select-String -Path validsim -Pattern "85\.0"` |
| CLI-08 | `add_completion` disabled; CI cache key omits dev deps; `build-push-action` v6 vs v5 | `cli.py:54,64`; `ci.yml:41,129`; `release.yml:109` | Low | VERIFIED | 1h | CliRunner probe; `Select-String` on workflows |

---

## CLI-01 — `make smoke` cannot pass (Critical, VERIFIED)

**Subject:** shipped code (`Makefile`).

**Description.** `smoke` is documented as the end-to-end smoke path but invokes `gate`, which refuses to run against an ephemeral store.

**Repro** (executed):
```
$ python -m validsim.cli run --episodes 20 --threshold 50
  Decision:      APPROVE
$ python -m validsim.cli gate --latest
error: gate needs a durable store, but VALIDSIM_STORE='memory'; set it to one of
postgres, sqlite when running `validsim run` so the verdict outlives that process
gate exit=2
```

**Control** (proves the store is the only variable — same command, same run):
```
$ VALIDSIM_STORE=sqlite VALIDSIM_SQLITE_PATH=$TEMP/cli_audit_runs.db \
  python -m validsim.cli run --episodes 20 --threshold 50   # identical output, APPROVE
$ ... gate --latest
gate: vrun-f6d428bb composite=70.25 threshold=50.0 -> APPROVE
gate exit=0
```

**Expected vs actual.** Expected: `make smoke` passes on a default install. Actual: exits non-zero at the `gate` step every time, because `VALIDSIM_STORE` is unset and `Makefile:49` sets nothing.

**Business impact.** The single documented "does it work end to end" command is broken on a clean machine. Anyone onboarding — including the demo path — hits it. `gate`'s refusal is *correct by design* (`_require_durable_store`, `cli.py:162-180`); the defect is the caller, not the guard.

**Reachability.** `Makefile:48` is a documented entrypoint; `gate` is reachable via the `validsim` console script (`pyproject.toml:38`).

**Dependencies.** None.

**Recommended fix (described, not applied).** One shared temp dir + `VALIDSIM_STORE=sqlite` + `VALIDSIM_SQLITE_PATH` exported for all three chained steps, then cleanup. Note the trap: setting the store per-command with a *fresh* `mktemp -d` each time makes `run` and `gate` use different files, and `gate` then fails on "no stored runs" instead.

**Test strategy.** `make smoke` (or the equivalent three invocations) must exit 0 in a clean environment. The `&&` chain must additionally be proven to distinguish `gate` exit 1 (legitimate BLOCK) from exit 2 (misuse) — see CLI-02 note in "False leads".

---

## CLI-02 — Regression p-value seeded off run id (Critical, VERIFIED)

**Subject:** shipped code. `engine/pipeline.py:141` is **correct**; the two ad-hoc compare paths are not.

**Description.** `compare()`'s permutation test is seeded from the *run id* in the CLI and API, but from the *stable seed* in the pipeline. Same pair of runs ⇒ different p-values depending on which surface you ask.

**Repro** (executed) — identical checkpoints, so evaluations are bit-identical:
```
DEFECT : same 2 checkpoints, 2nd run with NEW run ids -> p = ['0.709'] then ['0.711']
CONTROL: same checkpoint vs itself                  -> p = ['1.000']  (trivially 1.0)
```
Sites: `cli.py:651` `seed=stable_seed(candidate_id, baseline_id)`; `api/main.py:727` `seed=stable_seed(run_id, body.baseline_id)`. Correct reference: `pipeline.py:130` `seed = stable_seed(checkpoint_id, task.task_id)`, used at `:141`.

**Control (required, and it is the test-design lesson).** Same-checkpoint ⇒ `p = 1.000` exactly, because identical samples make every permutation "extreme" (`engine/stats.py:110-126`). **A regression test comparing the same checkpoint passes both before and after the fix and proves nothing.** The test must compare two *different* checkpoints.

**Expected vs actual.** Expected: the same comparison yields the same p-value from the API, the CLI, and the stored scorecard. Actual: 0.709 vs 0.711 from two runs of the same pair.

**Business impact.** "Our own two surfaces disagree" on safety evidence, with both answers looking correct. An auditor cannot tell which is authoritative. Directly undermines the reproducibility claim the product sells.

**Reachability.** `validsim compare` (console script) and `POST /api/v1/validations/{id}/compare`. Both are public surfaces.

**Dependencies.** Fix both sites in one commit — fixing one leaves the surfaces disagreeing, and a *consistently* wrong answer is arguably worse because it looks trustworthy. `stable_seed` currently lives at `sim/runner.py:30`; it is a pure string function with no sim dependency and belongs in a leaf module so `engine/regression.py` does not import the simulation layer to reach it.

**Recommended fix (described).** A single `derive_comparison_seed(checkpoint_id, task_id)` helper used by `pipeline.py:130` and both compare sites, mirroring `pipeline.py:130` exactly (candidate's checkpoint, not a blend of both runs).

**Test strategy.** Two tests: `test_compare_seed_ignores_run_ids_across_different_checkpoints` (asserts p differs under run-id seed, matches under derived seed) and `test_compare_same_checkpoint_is_trivially_p_value_one` (asserts `1.0` **exactly**, marked as a control so a future reader does not "fix" it by loosening the assertion).

---

## CLI-03 — Redis reaper reclaims live leases (Critical, VERIFIED)

**Subject:** shipped code, Redis queue backend (the compose default).

**Description.** Two implementations of the same predicate disagree. `_is_expired` (`queue.py:288`) compares **parsed instants**; the Lua reaper (`queue.py:187`) compares **strings**.

**Repro** (executed):
```
now  : 2026-09-26T13:46:00+00:00
lease: 2026-09-26T09:46-05:00   (instant is 14:46+00:00 — NOT expired)
DEFECT  Lua string compare (queue.py:187) -> True    <- reaps a live lease
CONTROL instant compare  (queue.py:288) -> False   <- correct
```
`queue.py:187`: `if expires and expires ~= cjson.null and expires <= now then`. `queue.py:288`: `datetime.fromisoformat(lease_expires_at) <= datetime.fromisoformat(now)` — and its docstring at `:282-283` states "Comparison is on parsed instants, not strings, so the two timestamps need not share a textual offset."

**Control.** The Python path is the control: correct, and documented as such. The Lua path was evidently written believing it implemented `_is_expired`.

**Expected vs actual.** Expected: a lease one hour in the future is never reaped. Actual: with any negative UTC offset in the stored stamp, the lexicographic compare reaps it immediately.

**Business impact.** A live job is re-executed on a GPU already in use; the fencing epoch then discards the first attempt's result. Correctness *and* cost incident on the production path. Latent only because every write goes through `_utc_now()` (`:261-263`), which always emits `+00:00` — i.e. **correct by unenforced convention, and the codebase already documents the correct rule the Lua fails to implement.**

**Reachability.** `reap_expired` runs in the Redis queue path; Redis is the default in `docker-compose.yml:56-57`.

**Dependencies.** None. Not a CLI fix.

**Recommended fix (described).** Make the Lua compare instants — normalise both sides to epoch seconds inside the script, or pass `now` as epoch-ms. Do **not** "fix" this by tightening a convention; that leaves two implementations of one predicate.

**Test strategy.** Must **hand-write a negative-offset stamp** and assert the lease is **not** reaped. Every existing test writes via `_utc_now()`, so the suite is structurally blind to this — another instance of the happy-path-only failure class.

---

## CLI-04 — Fabricated cost data (High, VERIFIED)

**Subject:** shipped code. `duration_s` is **simulated episode time**, not wall clock.

**Description.** `sim/runner.py:131-136` draws `duration` from `rng.uniform(...)` per episode; `engine/evaluation.py:83` computes `mean_duration_s` as the mean of those same values. Both are RNG fiction, and they are **algebraically the same number**.

**Repro** (executed, `%TEMP%\cli_audit_cost_probe.py`, 5000 episodes, priced at the vault's $3.50/A100-hr):
```
seed   7: raw_sum=50226.0s  mean*N=50226.0s  identical=True  projected=$48.83/run
seed  42: raw_sum=50272.5s  mean*N=50272.5s  identical=True  projected=$48.88/run
seed 123: raw_sum=50262.3s  mean*N=50262.3s  identical=True  projected=$48.87/run
```
Summing over N and multiplying the mean by N are the same operation, so a guard naming one field is insufficient.

**Persistence (why this is worse than a calculation bug).** `mean_duration_s` is in `EvaluationResult.to_dict()` (`evaluation.py:43`) and is stored in the `evaluation_json` JSONB column (`store/postgres.py:99-105`, written `:407-414`, read `:110-117`). I round-tripped a real `SqliteValidationStore`: `9.895` in memory → `9.895` after save/load, bit-equal. **So the fiction is durable, queryable, and looks authoritative** — it is a summary statistic with units, which is precisely what a reader trusts.

**Control.** The probe fixes the seed and shows `identical=True`; an earlier cross-seed comparison showed spurious 1.33% divergence, which is sampling noise. Any published figure must state the seed.

**Expected vs actual.** Expected: a cost figure derived from simulation telemetry. Actual: ~$48.88/run against a vault claim of "$5–7 per 5K run" — roughly 8× high, deterministic, and wearing the costume of a real measurement.

**Business impact.** A finance surface built on these fields produces a confident, plausible, wrong number. Worse, banning `duration_s` routes an engineer straight to `mean_duration_s`, which yields the *identical* number. The honest interim answer is "wall-clock per run, GPU cost unmeasured" — `None`, never `0.0`/`$0.00`.

**Reachability.** `EvaluationResult` is returned by `/api/v1/validations/{id}` and persisted; `mean_duration_s` is a documented regression metric (`regression.py:119-137`).

**Dependencies.** Requires recording real `wall_clock_s` / `gpu_seconds` at persist time (a `validsim_run_cost` ledger, `gpu_seconds` nullable) before any cost surface is built.

**Recommended fix (described).** Guard comment on **both** `runner.py:131` and `evaluation.py:83` stating simulated time, never a cost/SLO/capacity input; instrument real timings; make `cost report` render *unmeasured*, never zero.

**Test strategy.** Assert the *absence* of a cost read on both fields, so a refactor cannot reintroduce it. A docstring is a comment; a test is a constraint.

---

## CLI-05 — CI `test` job cannot collect (High, VERIFIED)

**Subject:** shipped CI config. **Reachable from:** every push and PR.

**Description.** `.github/workflows/ci.yml:46` installs `requirements.txt`, then `:47` adds inline `ruff`/`pytest-cov`. It never installs `requirements-dev.txt`. `tests/test_delivery_pipeline.py:18` does a module-level `import yaml`, and PyYAML is dev-only (`requirements-dev.txt:7`).

**Repro.** Read the workflow; the two facts are sufficient and mechanical. Locally, `yaml` is present, which is why the suite passes on a developer machine and the defect is invisible pre-CI.

**Control.** `release.yml:47` and `nightly.yml:39` both install `requirements.txt -r requirements-dev.txt` — the correct pattern, two files away in the same repo.

**Expected vs actual.** Expected: the `test` job runs the same suite as `release`/`nightly`. Actual: it installs a different dependency set and cannot import a test module.

**Business impact.** Every PR runs a job that fails at collection — or, if the import is ever made optional, silently skips the delivery-pipeline invariants that exist specifically to catch "green check that did nothing."

**Dependencies.** None.

**Recommended fix (described).** Install `-r requirements.txt -r requirements-dev.txt` and delete the now-redundant inline `:47`.

**Test strategy.** Existing `test_delivery_pipeline.py` should assert that every workflow's install step covers every module imported by the test suite. Related, same file: `cache-dependency-path` lists only `requirements.txt` (`ci.yml:41`, `:129`), so the cache key will not invalidate when dev deps change.

---

## CLI-06 — `config_to_env` redaction unreachable (High, VERIFIED)

**Subject:** **shipped code** created by another agent this session (`validsim/project_config.py`, untracked; 32 tests passing).

**Description.** Two deliberate key namespaces exist: `POLICY_KEYS` (TOML-native, e.g. `threshold`) and `INFRA_KEYS`/`SECRET_KEYS` (env names, e.g. `VALIDSIM_PG_URL`). `resolve_config()` returns a config file, which by design may contain **only policy keys** — infra keys in a file are rejected outright (a good, deliberate rule). But `config_to_env` gates redaction on `if redact and key in SECRET_KEYS` (`:383`), and `SECRET_KEYS` holds env names. **From the real path, no key is ever in `SECRET_KEYS`, so the redaction branch is dead.**

**Repro** (executed):
```
config_to_env({'threshold': 90.0, 'episodes': 1000})      -> {'threshold': '90.0', 'episodes': '1000'}
config_to_env({'VALIDSIM_PG_URL': 'postgresql://u:pw@h/db'}, redact=False) -> {} (dropped, not emitted)
```
Current tests pass only because they hand-feed env-named keys — the one shape the config-file path never produces.

**Control.** `redact_value` itself is correct and tested (`postgresql://***REDACTED***@db:5432/validsim`). The defect is purely the call convention.

**Expected vs actual.** Expected: `config show` / `support-bundle` redact secrets. Actual: the redaction is unreachable for config-file input; secrets live in the *env* half, which `resolve_config` never touches.

**Business impact.** A support bundle that ships a user's DSN or API key to a public issue tracker. This is the "fail open" direction and must be closed before `support-bundle` exists.

**Dependencies.** Blocks `config show` and `validsim support-bundle`.

**Recommended fix (described).** Reframe as `effective_to_env(effective: Mapping[str, Any])` over the **merged** `VALIDSIM_*`-named mapping (what a `Settings` object produces), so redaction is reachable by construction rather than by the caller passing the right shape. Durable redaction should read the secret set from `SecretStr` fields on the model, not a list — measured: `repr=False` survives `repr()`/`str()` but **leaks** through `dataclasses.asdict()` and `__dict__`, and `api/main.py:703` already uses `asdict`.

**Test strategy.** One test calling the serializer with a **merged env-named** mapping containing a secret and asserting the value is masked. The current suite only passes the env shape, which is why this looks covered.

---

## CLI-07 — `85.0` duplicated across 6 sites (Medium, VERIFIED)

**Subject:** shipped code. Correctness is fine today; this is a drift risk.

`cli.py:306` and `:324` (`run` and `validate`), `engine/scorecard.py:108` (field default) and `:133` (`build_scorecard` param), `engine/pipeline.py:55` (`DEFAULT_THRESHOLD`), `jobs/worker.py:51` (`_DEFAULT_THRESHOLD`). Plus `engine/pdf.py:78` and `notify/dispatcher.py:87,89,108` as dict fallbacks.

**Correction to a prior claim of mine:** I earlier told teammates `85.0` appears in `.env.example` and `docker-compose.yml`. **It does not** — `Select-String` returns zero matches for both. Scope is 6 code sites plus `actions/validate/action.yml:52` and documentation only.

**Impact.** No wrong value today. The risk is silent divergence: if the gate constant changes, the CLI default, the worker default, and the Slack severity routing can disagree with no test failing.

**Recommended fix (described).** One `DEFAULT_APPROVAL_THRESHOLD` in a leaf module, re-exported from `engine/pipeline.py` for compatibility; `typer.Option(DEFAULT_APPROVAL_THRESHOLD, ...)`. Source it from config with an env override so the CLI is not the only place it can be set.

**Test strategy.** A drift guard asserting no `85.0` literal exists in `validsim/**/*.py` outside the canonical definition. This is the cheapest high-value guard in the report.

---

## CLI-08 — CLI/CI polish (Low, VERIFIED)

- `cli.py:54` and `:64` both set `add_completion=False`. Verified on typer 0.25.1 / click 8.5.0: a sub-app with `add_completion=False` correctly shows no `--install-completion`, so **only `cli.py:54` needs changing** — flipping both would add a completion flag to `validsim job --help`, which does not belong there.
- `ci.yml:41` and `:129` set `cache-dependency-path: requirements.txt` only; dev deps are missing from the cache key.
- `docker/build-push-action` is **v6.19.2** in `ci.yml:154` but **v5.4.0** in `release.yml:109`. Recommend aligning the release job forward to v6 (CI already runs the newer major); the SHA pin must be updated to the real v6 digest.
- `validsim run` has no `--json`, so `actions/validate/action.yml:158-160` recovers run-id and score by `grep`-ing human stdout. `gate --json` and `report --json` already exist. A `run --json` payload shaped like `gate --json`'s would collapse that grep contract.

---

## False leads (ruled out, with reasons)

1. **`config_loader.py` is dead code — REFUTED as "dead."** It has no importers in `validsim/`, but it is **not** orphaned: `validsim/project_config.py` is now the live, tested implementation, and `config_loader.py` remains only for its own callers. Not an impact claim; superseded by design.

2. **`--config` flag does not exist — still true, but no longer a defect.** `.env.example:154` advertises `VALIDSIM_CONFIG`; the new `project_config.py` honours it via `discover_config_file()`. My earlier "advertised but inert" claim is **REFUTED**.

3. **Three undocumented env vars — PARTIALLY REFUTED.** `VALIDSIM_LOG_LEVEL`, `VALIDSIM_JOB_QUEUE_MAX_DEPTH`, `VALIDSIM_JOB_LEASE_SECONDS` are read in code and still absent from `.env.example`. However a new drift-guard test now classifies all 29 `VALIDSIM_*` keys, so the *class* of problem is guarded. Downgraded from High to informational.

4. **`85.0` in `.env.example` / `docker-compose.yml` — REFUTED.** Zero matches in both files.

5. **`build.ps1` interpreter-fallback bug — REFUTED (fixed).** I originally reported this as P0. A `$tail` guard now exists at `build.ps1:49`; verified the bare `python` candidate returns `tail=[]` and probes successfully. **Residual P1s only:** stale `$LASTEXITCODE` across candidates (latent), an empty `catch {}` making failures non-diagnosable, and `$ResampleBudget` (`:15-18`) which is still never read and whose new comment falsely claims it mirrors `_CI_N_RESAMPLES`.

6. **Nested test package halting collection — REFUTED (resolved by deletion).** `tests/bench/` previously broke the whole suite via a package-boundary mismatch. It now contains **0 source files**; `pytest tests/ --collect-only -q` exits 0. **Both candidate fixes are moot — do not ship them.** Residual risk: 16 stale `.pyc` files remain, and a `.pyc`-only `tests/bench/` would re-trigger the halt if recreated.

7. **Chained consumers cannot distinguish `gate` exit 1 from 2 — VERIFIED, unresolved.** `Makefile:49` uses `&&`, so a legitimate BLOCK and a misuse error break the chain identically. `cli.py:450` collapses both into `decision: "BLOCK"`, and usage errors exit 2 before any JSON prints. A `reason` field plus emitting the payload on failure paths would make this diagnosable without touching the 0/1/2 contract. Not implemented — outside the read-only scope of this report.

---

## Assumptions

- Verified against the tree as of **2026-09-26**; the tree changed materially during the audit. `git status` shows many modified files I did not touch.
- `validsim/project_config.py` and `tests/test_project_config.py` are **untracked** (`??`); CLI-06 may not ship if they are discarded.
- Local Python is 3.14 with typer 0.25.1 / click 8.5.0 / pydantic 2.13.5; CI pins 3.12. Behavioural findings were reproduced locally.
- CLI-03 is latent in practice because all writes go through `_utc_now()`; it is reachable the moment any code path writes a non-`+00:00` stamp (e.g. a Postgres `timestamptz` round-trip or a config-supplied clock).
- I did not run the full suite as a control for CLI-01..04; each finding has a targeted repro plus a paired control instead.
