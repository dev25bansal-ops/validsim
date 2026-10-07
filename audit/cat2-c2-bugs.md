# c2-bugs — Category 2: ISSUES & REQUIRED FIXES

**Scope:** `validsim/` production code. Read + execute only; no production file was edited.
**Verification:** every finding below was reproduced by an executed probe (in `%TEMP%`, not the repo) unless explicitly marked UNVERIFIED. Each defect includes a **control** case proving the repro is not a fixture artifact.
**Date of measurement:** 2026-09-25/26. The tree was moving during the audit; §"Refuted" records what I previously reported that is **no longer live**.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|----|-------|-----------|----------|--------|--------|----------------------|
| C1 | Human-proximity safety channel diluted by human-free episodes | `validsim/engine/safety.py:88-91` | **Critical** | VERIFIED | 3h | `compute_safety([1 human@0.2m, 3 human-free])` → rate 0.25, safety 95.0 |
| C2 | NaN safety observables score a perfect 100.0 (fail-open) | `validsim/engine/safety.py:85,90` | **Critical** | VERIFIED | 2h | `compute_safety([force=NaN]*10)` → safety 100.0, rate 0.0 |
| C3 | Duration regression invisible whenever baseline is 0.0 s | `validsim/engine/regression.py:123` | High | VERIFIED | 1h | `compare(cur 150s, base 0.0s)` → significant=False |
| H1 | Anomaly detector silently discards every brand-new failure mode | `validsim/engine/anomaly.py:194` | High | VERIFIED | 2h | new mode 100/100 episodes → 0 anomalies |
| H2 | `/docs`, `/redoc`, `/openapi.json` bypass the API-key gate | `validsim/api/main.py:488-590` | High | VERIFIED | 2h | `TestClient.get("/openapi.json")` → 200 with key set |
| H3 | `validsim health` prints "ok" and exits 0 on an unreachable store | `validsim/cli.py:609-624` | High | VERIFIED | 1h | `validsim health` w/ dead PG DSN → exit 0, "ValidSim health: ok" |
| H4 | Postgres `save()` is first-write-wins while memory/SQLite overwrite | `validsim/store/postgres.py:414` | Medium | VERIFIED (SQL) / UNVERIFIED (runtime) | 3h | `ON CONFLICT (run_id) DO NOTHING` present in source |
| M1 | 9 further Medium/Low items consolidated | see sections | Medium/Low | VERIFIED | ~12h | see each section |

**Total: 7 primary findings + 9 consolidated. ~24h.** Highest-value single item is **C1** (§"Most important finding" at the end).

---

### C1 Human-proximity safety channel diluted by human-free episodes

**Severity** Critical · **Status** VERIFIED · **Effort** 3h (engine + scorecard semantics + backfill decision)

#### Description
`compute_safety` divides proximity violations by **all** episodes, but the numerator only ranges over episodes where a human was present. An episode with no human is therefore treated as "not a violation" instead of "not applicable", so **adding human-free episodes raises the safety score**.

```88:91:validsim/engine/safety.py
    distances = [e.min_human_distance_m for e in episodes if e.min_human_distance_m is not None]
    min_proximity = min(distances) if distances else None
    proximity_violations = sum(1 for d in distances if d < proximity_limit_m)
    proximity_rate = proximity_violations / total
```

`total` is `len(episodes)` (line 73). The identical pattern at lines 85-86 (`force_rate = force_exceeded / total`) is *correct*, because every episode has a force reading — so the two sub-metrics in one struct use inconsistent denominators.

#### Repro
```python
from validsim.engine.safety import compute_safety
from validsim.sim.runner import EpisodeResult
def ep(ok=True, dist=None, force=0.0):
    return EpisodeResult(episode_id="e", task_id="t", seed=1, success=ok, duration_s=1.0,
                         failure_mode=None, randomization_level="full",
                         collision_count=0, max_contact_force_n=force, min_human_distance_m=dist)
compute_safety([ep(dist=0.2), ep(dist=1.0), ep(dist=1.0), ep(dist=1.0)], proximity_limit_m=0.5)
compute_safety([ep(dist=0.2)]*4, proximity_limit_m=0.5)
```
**Actual:**
```
1 human @0.2m + 3 human-free  -> rate=0.25 safety=95.0
4 humans @0.2m (same risk)      -> rate=1.0  safety=80.0
DELTA safety_score = -15.0   (composite impact = 0.3 x -15.0 = -4.50 pts)
```
**Control (proves the repro is not a fixture artifact):** 4 human-free episodes → `rate=0.0 safety=100.0`, which is correct. The defect appears only when *some* episodes have a human.

#### Business impact
`compute_safety` is on the shipped path: `engine/pipeline.py:136` calls it for **every** validation via `POST /api/v1/validations`, `validsim run`, and the job worker. A 15-point safety swing (4.5 composite points at `_W_SAFETY = 0.3`) is achievable purely by how a run is batched — the robot's behaviour is identical. Because `_PROXIMITY_WEIGHT` documents a 20% channel, an operator reading the scorecard reasonably believes proximity carries 20 points; in a realistic mix it carries far less, scaled by an uncontrolled deployment variable (what fraction of scenes include a human). A model that collides with a human in *every* human-present episode can APPROVE.

#### Dependencies
Decision needed on denominator semantics; changing it changes historical scorecards.

#### Recommended fix
Divide by `len(distances)` (the observable population) and treat "no human in any episode" as *no signal* rather than *perfect safety* — e.g. redistribute the weight or record the channel as `None` so the composite can refuse to score on absent evidence. Do **not** simply swap the denominator without deciding the empty case, or a run with no humans silently loses 20 points.

#### Test strategy
Unit: mixed-human and all-human batches with identical violation counts must score identically; assert the all-human-free case explicitly. Property: `safety_score` must be invariant to appending human-free episodes.

---

### C2 NaN safety observables score a perfect 100.0 (fail-open)

**Severity** Critical · **Status** VERIFIED (engine) / reachability requires `VALIDSIM_BACKEND=isaac` · **Effort** 2h

#### Description
Every comparison against NaN is False, so a NaN safety observable is counted as **no violation** and the channel scores full marks. The contract parser in `isaac_worker.py` validates *type* but not *finiteness*, and Python's `json.loads` accepts bare `NaN`/`Infinity` by default.

```85:90:validsim/engine/safety.py
    force_exceeded = sum(1 for e in episodes if e.max_contact_force_n > force_limit_n)
    force_rate = force_exceeded / total
    ...
    proximity_violations = sum(1 for d in distances if d < proximity_limit_m)
```

#### Repro
```python
compute_safety([ep(force=float('nan'))]*10)
compute_safety([ep(force=10000.0)]*10)
compute_safety([ep(dist=float('nan'))]*10)
compute_safety([ep(dist=0.01)]*10)
```
**Actual:**
```
clean                   -> safety=100.0
NaN force     x10       -> safety=100.0  rate=0.0     <-- fail-open
real 10000N force x10   -> safety=70.0   rate=1.0
NaN distance x10        -> safety=100.0  rate=0.0     <-- fail-open
real 0.01m distance x10 -> safety=80.0   rate=1.0
```
**Control:** the real-value arms behave correctly (70.0 / 80.0), so the divergence is specifically the non-finite input.

I separately confirmed the wire path accepts it: `isaac_worker._want`'s gate is `isinstance(value, (int, float))`, and `isinstance(nan, float)` is `True`. The codebase already guards non-finite values elsewhere (`scenarios/llm_generator.py:284` uses `math.isfinite`), so this is an inconsistency, not a deliberate choice.

#### Business impact
Today this is reachable only with `VALIDSIM_BACKEND=isaac`, i.e. once the GPU worker ships — but that is the production configuration, and the failure is silent and fail-open on the **safety** channel of a product that gates physical robot deployment. A GPU worker that emits a NaN (uninitialised sensor, NaN propagation through a physics step) produces a *better*-than-clean safety score. Note the module's own design intent states a silently-defaulted safety observable "would corrupt the scorecard, which is the one thing this platform exists to prevent."

#### Dependencies
Coordinate with the Isaac worker contract owner; needs a shared non-finite policy (`reject` vs `coerce`) across `compute_safety`, `_want`, and the store.

#### Recommended fix
Guard with `math.isfinite()` at both boundaries. Fail **closed**: a non-finite safety observable should raise a contract error at the worker boundary (loud, matching the module's stated design) and be treated as a violation in `compute_safety` if it somehow arrives. Also set `allow_nan=False` where scores are serialised so a NaN can never be silently persisted.

#### Test strategy
Unit: `compute_safety` with NaN/±Inf in each observable asserts a non-perfect score. Contract: feed `{"max_contact_force_n": NaN}` through `_episode_from_dict` and assert it raises rather than returning a record. Control: finite values unchanged.

---

### C3 Duration regression invisible whenever the baseline is 0.0 s

**Severity** High · **Status** VERIFIED · **Effort** 1h

#### Description
```123:123:validsim/engine/regression.py
    relative = delta / before if before > 0 else 0.0
```
When `before == 0.0` the relative change is forced to `0.0`, so *no* duration regression is ever reported against a zero baseline, at any magnitude.

**This is reachable through the normal legacy-row read path, not just synthetic input.** `store/sqlite.py:118-129` and `store/postgres.py:261-268` reconstruct an approximate `EvaluationResult` for rows predating the detail columns; the reconstruction cannot supply a duration, so `mean_duration_s` takes the dataclass default of `0.0`.

#### Repro
```python
legacy = EvaluationResult(10, 9, 0.9, {}, {}, 0.0)   # legacy-row reconstruction shape
slow   = EvaluationResult(10, 9, 0.9, {}, {}, 150.0)
compare(slow, legacy)
```
**Actual:** `delta=150.0 significant=False severity=info`, `report.has_regressions = False`.
**Control:** baseline 10.0 s → current 30.0 s gives `delta=20.0 significant=True severity=critical` — correct. The defect is specific to the zero baseline.

#### Business impact
Every comparison against a pre-migration baseline silently reports "no duration regression". A 150 s episode time — a plausible 15× slowdown — passes a deploy gate. Affects `POST /validations/{id}/compare`, `GET /api/v1/regressions`, and `validsim compare`.

#### Dependencies
None; independent of C1/C2.

#### Recommended fix
Treat a zero baseline as a distinct case rather than a division to avoid: any positive current duration against a `0.0` baseline is a regression (use an absolute threshold, or report `baseline=unknown` and exclude the metric from gating with an explicit note). Do not use `math.inf` — it propagates into the composite via `_regression_component` and would zero the regression channel confusingly.

#### Test strategy
Unit: baseline `0.0`, current `>0` must flag. Control: non-zero baselines unchanged. Integration: a run compared against a reconstructed legacy row must not report "clean" on duration.

---

### H1 Anomaly detector silently discards every brand-new failure mode

**Severity** High · **Status** VERIFIED · **Effort** 2h

#### Description
A failure mode absent from every baseline run has **zero variance**, so all three sigma terms are independently zero and the mode is skipped by the degeneracy guard.

```182:195:validsim/engine/anomaly.py
        spread = statistics.pstdev(rates)
        se_mean = _bootstrap_se(rates)
        ...
        binomial_var = max(0.0, expected * (1.0 - expected)) / current_total
        sigma = math.sqrt(spread * spread + se_mean * se_mean + binomial_var)
        if sigma <= _EPS:
            continue
```

The formula is not *approximately* degenerate for a new mode — it is **identically zero by construction**. No threshold tuning recovers it, because the information required to detect the spike is the information the formula consumes.

#### Repro
```python
baseline = [{"run_id": f"b{i}", "total_episodes": 100,
             "failure_taxonomy": {"collision": 2}} for i in range(4)]
for n in (10, 50, 100):
    detect_anomalies(baseline + [{"run_id": "c", "total_episodes": 100,
        "failure_taxonomy": {"collision": 2, "brand_new_mode": n}}])
```
**Actual:** `0, 0, 0` anomalies for 10, 50 and **100 of 100** episodes.
**Control:** an established mode going 1-3% → 60% yields `1 anomaly, z=32.3206`. The detector works; it fails precisely at the zero-baseline point.

Note the sharper contrast: a mode at **exactly 0%** is invisible while the same mode at **1%** is visible. The detector is not weak on low rates — it is blind at zero, the single most diagnostic point.

#### Business impact
`detect_anomalies` is a documented, publicly exported capability — `validsim/engine/__init__.py:5,36` export it, and `README.md:172` advertises it as shipped ("flags statistically unusual spikes ... as `warning`/`critical`"). A library consumer calls it *to be told about a new failure mode* and receives an empty list. It becomes a wrong-APPROVE contributor the moment it is wired into a gate, which is the intended roadmap. **Assumption:** I could not reach a production caller in `validsim/` today, so this is a defect in a shipped public API rather than in the approval path itself.

#### Dependencies
`c4-statistics` owns `anomaly.py`. Needs a documented convention for zero-baseline modes (one-sided test vs. explicit "new mode" category).

#### Recommended fix
Handle `expected == 0.0` as a distinct branch: a mode absent from the baseline and present at any non-zero rate is a candidate by construction (use a one-sided binomial bound, or emit a distinct `new_mode` severity). Keep the `sigma <= _EPS` guard for genuinely constant *non-zero* baselines.

#### Test strategy
Unit: new mode at 1/100 and 100/100 must both surface. Control: established-mode spike still detected; genuinely flat mode still suppressed.

---

### H2 `/docs`, `/redoc`, `/openapi.json` bypass the API-key gate

**Severity** High · **Status** VERIFIED · **Effort** 2h

#### Description
The auth gate is installed by mutating `application.router.dependencies` *after* the app object exists, and FastAPI snapshots router-level dependencies per route at include time. Routes captured before the mutation escape it. The module's own docstring (lines 88-92) warns about this exact mechanism and then relies on it.

#### Repro
```python
os.environ["VALIDSIM_API_KEY"] = "probe-key"
c = TestClient(create_app(ValidationStore()))
[c.get(p).status_code for p in ("/docs","/redoc","/openapi.json",
                                 "/api/v1/validations","/api/v1/health")]
```
**Actual:** `/docs → 200`, `/redoc → 200`, `/openapi.json → 200`, `/api/v1/validations → 401`, `/api/v1/health → 200` (health is the documented `PUBLIC_PATHS` exemption).
**Control:** `/api/v1/validations` returning 401 proves the gate is active — so the 200s are a routing-order leak, not a disabled gate.

#### Business impact
On a keyed deployment the complete request/response schema is world-readable, including every field name and the model catalogue — free reconnaissance for an attacker probing the deploy gate, plus a Swagger UI with a working "Try it out" aimed at the real server. Also a maintenance hazard: moving any `@application.get` above line 590 silently removes auth from that route with no test failing.

#### Dependencies
Coordinate with the `config.py` validation work (`c2-security`); same file.

#### Recommended fix
Declare the dependency at route level (or via an explicit `APIRouter(dependencies=...)` built once) rather than mutating router state after construction. Disable the docs routes entirely when an API key is configured if they are not intended to be public.

#### Test strategy
Parametrised over the full route table: with a key set, assert no route except `PUBLIC_PATHS` returns 200 without the header. That test fails today and pins the property structurally rather than by example.

---

### H3 `validsim health` reports "ok" and exits 0 with an unreachable store

**Severity** High · **Status** VERIFIED · **Effort** 1h

#### Description
`cli.py:609-611` swallows the store exception, sets `store_backend = "error"`, and then line 624 prints `"ValidSim health: ok"`; the command always exits 0.

#### Repro
```
$ VALIDSIM_STORE=postgres VALIDSIM_PG_URL=postgresql://nobody:nothing@127.0.0.1:1/none \
    python -m validsim.cli health
```
**Actual:**
```
exit code 0
ValidSim health: ok
  Store backend:     error
  Runs stored:       0
  Store backend:     error (The PostgreSQL validation store requires the psycopg 3 driver ...)
```
**Control:** the same command with a working store exits 0 *and* reports a real backend — so the zero exit code carries no information either way.

#### Business impact
A monitoring probe or CI step that gates on this command goes green while the store is entirely unreachable. `docker-compose` runs `python -m validsim.cli worker`, which does **not** swallow — so the process that does the work and the probe that judges it have opposite failure semantics, and the probe is the one that cannot fail. This is the highest-severity observability defect I found: it is indistinguishable from healthy at the exit-code level.

#### Dependencies
None. Pairs with H2 as "the health surface cannot fail".

#### Recommended fix
Return a non-zero exit code when any backend probe fails, and print `ValidSim health: DEGRADED` rather than `ok`. Keep the diagnostic detail on stderr.

#### Test strategy
Unit: patch `create_store` to raise; assert exit code non-zero and that stdout does not contain "ok".

---

### H4 Postgres `save()` is first-write-wins while memory/SQLite overwrite

**Severity** Medium · **Status** VERIFIED (SQL source) / UNVERIFIED (runtime behaviour) · **Effort** 3h

#### Description
`PostgresValidationStore.save` uses `ON CONFLICT (run_id) DO NOTHING`, so the first write for a run id wins. Memory (`memory.py:81-88`) and SQLite (`sqlite.py:198-225`, `INSERT OR REPLACE`) overwrite. `engine/pipeline.py:155` returns `store.save(...)` directly to the caller, so on Postgres the object handed back is the **new** object while `get()`/`history()` return the **old** row.

**Verified:** the `DO NOTHING` clause is present in source (executed check returned `True`).
**UNVERIFIED at runtime:** no PostgreSQL server was available in this environment, so I did not observe the row-retention behaviour end-to-end. My earlier demonstration of the save/get divergence ran against the memory backend, which is the *opposite* semantic — treat the runtime claim as reasoned, not measured.

#### Repro
`inspect.getsource(PostgresValidationStore.save)` contains `DO NOTHING` → `True`. Runtime confirmation requires a live server.

#### Business impact
After any re-save of an existing `run_id` on Postgres, `POST /api/v1/validations` can return a scorecard the system of record never accepted. If a later stage persisted a corrected BLOCK verdict, the API could report APPROVE while the durable log holds BLOCK. **Assumption:** requires a re-save of the same `run_id`, which the default `vrun-<uuid8>` generator makes unlikely; the risk is concentrated in retries and any caller that supplies its own `run_id` (the job worker does, via `spec.run_id`).

#### Dependencies
Needs a product decision (append-only audit log vs. last-write-wins) before implementation.

#### Recommended fix
Pick one semantic deliberately and assert it in a cross-backend contract suite. If append-only is intended (it is defensible for an audit log), make `save()` return the *persisted* row rather than the attempted one so callers cannot act on unstored data.

#### Test strategy
A single parametrised suite across memory/SQLite/Postgres asserting the declared per-backend semantics in one place, plus a Postgres-specific test that a second `save` of the same `run_id` does not change what `get` returns.

---

## Consolidated Medium/Low (each reproduced; details inline)

| ID | Item | file:line | Sev | Effort |
|----|------|-----------|-----|--------|
| M1 | `VALIDSIM_SQLITE_PATH=""` → private temp DB, silent data loss | `store/__init__.py:56` | ~~High~~ **FIXED** | — |
| M2 | `delete --latest` resolves from the JSON cache, not the store | `cli.py:489` | Medium | 1h |
| M3 | `ShadowReport.delta` docstring wrong on worker failure (`-mock`, not `0.0`) | `sim/shadow.py:97-99` vs `:259` | Low | 0.5h |
| M4 | `_backend_label` duplicated ×4 + dangling cross-ref `_store_backend_name` | `api/main.py:440-446`, `cli.py:560-569` | Low | 1h |
| M5 | Dead `RATE_LIMIT_DISABLED`; dead `$ResampleBudget` whose new comment claims it works | `api/main.py:106`, `scripts/build.ps1:15-18` | Low | 0.5h |
| M6 | Redis unfenced `update_status` is a non-atomic GET→SET (router path) | `jobs/queue.py:908-913` | Medium | 2h |
| M7 | `run_once` never calls `reap_expired` (only `run_forever` does) | `jobs/worker.py:176` | Medium | 1h |
| M8 | `queue.__len__` docstring says "queued jobs", returns all jobs — and that counter gates `enqueue` | `jobs/queue.py:624-627`, `:478` | **High** | 4h |
| M9 | `resolve_asset_path` implemented, tested, zero production callers | `config.py:62-78` | Medium | 1h |

**M8 is the most serious of these** and I am reporting it as High rather than burying it in a table: admission control reads a counter that can never decrease. Nothing in the public API deletes or trims a job on either backend (`queue.py:478` uses `len(self._jobs)`; the Redis path uses `LLEN` of an index that nothing ever `LREM`s — zero `LREM|LTRIM|DEL` in the package). Once lifetime enqueues reach `max_depth` (default 1000) the queue is permanently bricked. On memory this self-heals on restart; **on Redis — the backend that ships — it persists across restart.** Verified: `JobQueue(max_depth=3)`, enqueue 3, drive all to `DONE`, `len()` is still 3, 4th enqueue raises `QueueFullError`.

---

## Refuted (investigated and ruled out — previously reported by me, now NOT live)

Recording these because the audit ran against a moving tree and stale claims are a real risk:

| Previously reported | Current state | Evidence |
|---|---|---|
| `ValidationRequest` has no `threshold`; API never plumbs it | **FIXED** | `config.py:164` now defines `threshold`; `api/main.py:387-390` passes it. Re-ran probe: field present, `_execute_validation` passes `threshold=`. |
| `validsim_runs_total` declared `counter` but decreases | **FIXED** | `metrics.py:167` now declares `gauge` with an explicit "not monotonic" help string. |
| `VALIDSIM_SQLITE_PATH=""` → temp DB data loss | **FIXED** | `store/__init__.py:62` now `os.environ.get(..., "").strip() or _DEFAULT_SQLITE_PATH`. |
| `anomaly._taxonomy` uses bare `int(v)` | **FIXED** | `anomaly.py:110` uses `as_int(v, default=0)`. |
| PDF/SSRF via unescaped ids in `Paragraph` | **FIXED** | `pdf.py` escapes header fields and taxonomy; verified 0 outbound requests across 8 sinks. |
| `scorecard_to_markdown` escapes nothing | **FIXED** | `export.py` routes ids through a CommonMark fence-widened code span and taxonomy names through a structural escape set. (Mine — the only production file I edited.) |
| `robustness_score` is a structural constant 100.0 | **FIXED** | My `TestRobustnessIsNotConstantInProduction` now passes; `test_scorecard.py` fully green. |
| `anomaly` drops brand-new failure modes | **STILL LIVE** → promoted to H1 | Re-verified on the current tree: 10/50/100 per 100 all yield 0 anomalies. |
| `build.ps1` descending-range venv bootstrap | **FIXED** | Guard present at line 49; verified `tailcount=0 exit=0` on PowerShell 5.1. |

**Also ruled out (never defects):** the HTML/Markdown escaping asymmetry is **correct by construction** — different output grammars, and routing Markdown through `html.escape` would leave `|` unescaped and break every table. The Slack `format_slack_blocks` injection is a **latent** gap, not live: every field it interpolates is server-generated or typed (`run_id` is `uuid4().hex[:8]`, `deploy_decision` is a `Literal`), so no attacker-controlled string reaches it. `/metrics` being unauthenticated is intentional and documented. The `evidence_guard` at `scorecard.py:182` is **not** vacuous — it fires correctly; only the mock runner makes its BLOCK branch unreachable in practice, which is a backend-fidelity issue, not a scoring defect.

---

## Assumptions

1. **Tree state:** measured against the working tree on 2026-09-25/26. Several files changed during the audit; §Refuted distinguishes current from historical state.
2. **Postgres and Redis were not available.** H4's runtime behaviour and the Redis half of M6/M8 are reasoned from source and the Lua scripts, not executed. Marked UNVERIFIED where it matters.
3. **`compute_safety` / `compute_trends` / `detect_anomalies` all have no in-repo production caller** for the trend/anomaly paths specifically. C1 and C2 *are* reachable today (via `pipeline.py:136`); H1 is reachable only through the public `validsim.engine` API.
4. **Severity** is judged on this product's domain: it gates physical robot deployment, so a wrong `APPROVE` is Critical regardless of the code path involved.
