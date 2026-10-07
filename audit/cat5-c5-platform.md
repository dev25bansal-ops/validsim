# Audit Report — c5-platform (Platform / Infrastructure / Scoring Integrity)

**Agent:** `c5-platform` · **Date:** 2026-09-26 · **Target:** `d:\SIM-TO-REAL` @ `validsim 0.2.0`
**Mode:** read-only. No production file was modified. No tests were written.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| P1 | `robustness` is a constant 100.0 — 20 composite points carry no information | `engine/scorecard.py:63-64` | **Critical** | VERIFIED | 6h marker / 3d sweep | `$env:TEMP\c5probe_verify.py` |
| P2 | SQLite `save()` silently overwrites a verdict; Postgres refuses | `store/sqlite.py:203` | **Critical** | VERIFIED | 4h | `$env:TEMP\c5probe_v4.py` |
| P3 | `duration_s` / `mean_duration_s` are seeded fictions; cost model ~8x overstated | `sim/runner.py:154` | **High** | VERIFIED | 32h | `$env:TEMP\c5probe_verify.py` |
| P4 | Row-count metrics typed `# TYPE counter` decrease on delete | `api/metrics.py:154-171` | **High** | VERIFIED | 8h | `$env:TEMP\c5probe_v3.py` |
| P5 | Retry policy inverted: errors terminal, crashes retried forever | `jobs/worker.py:137-148` | **High** | VERIFIED | 20h | `$env:TEMP\c5probe_v3.py` |
| P6 | Public `/health` discloses the full security posture unauthenticated | `api/main.py:93, 592-624` | **Medium** | VERIFIED | 2h doc | `$env:TEMP\c5probe_v5.py` |

**Subject classification.** P1–P5 are **shipped code** — reachable from the CLI (`validsim run`),
the API (`POST /api/v1/validations`, `DELETE /api/v1/validations/{id}`), and the worker
(`python -m validsim.cli worker`). P6 is **shipped code**, deliberate, and documented. No finding
in this report concerns proposal-only or dead code.

---

## P1 — `robustness` is a constant 100.0 (Critical, VERIFIED)

### Description
`_robustness_score()` groups episodes by `randomization_level` and **early-returns `100.0` when
fewer than two groups exist** (`scorecard.py:63-64`). But `TaskConfig.randomization` is a single
`Literal["none","partial","full"]` (`config.py:17`) and `run_validation` passes that one level to
every nominal episode (`sim/runner.py:175`, `:182`). So the mock backend **structurally cannot**
produce a second group, and robustness is the maximum on every run.

It carries weight `0.2` (`scorecard.py:34`), so **20 points of every composite score are a fixed
literal** — the scorecard reports a dimension that was never measured.

### Repro
```
$ python $env:TEMP\c5probe_verify.py
levels observed           = {'full'}  (count=1)
robustness_score          = 100.0
constant composite points = 20.0 of composite 82.59
robustness across 25 seeds (default config) = {100.0}  -> distinct: 1
```

### Control (required)
The same 3000 episodes, re-simulated across two real levels (`none` + `full`) rather than
relabelled:
```
CONTROL: levels in control = ['full', 'none']
robustness_score          = 83.13   (moves -> the metric IS reachable)
composite                 = 82.59 (1 level) vs 85.07 (2 levels)
delta attributable to robustness = -2.48 pts
```
**The metric works; the shipped configuration cannot reach it.** An earlier control attempt that
merely relabelled `EpisodeResult.randomization_level` post-hoc was **invalid** and was discarded —
relabelling does not re-simulate at the new success probability (`success_probability` applies a
per-level penalty, `sim/runner.py:99-100`).

### Expected vs actual
- **Expected:** a scorecard dimension reflects a measured property; a maximum score means
  "measured and excellent."
- **Actual:** `100.0` means "nothing to measure." These are indistinguishable in the artifact.

### Business impact
A customer reading `composite 82.59` reasonably assumes 20 points came from cross-condition
robustness testing. None did. This is a **misrepresentation in the artifact that gates physical
robot deployment**, not a rounding concern. It also means any digest or regression comparison over
stored scorecards is blind to robustness: two runs with genuinely different robustness are
indistinguishable, because a constant contributes nothing to a hash.

### Dependencies
None — self-contained in the engine.

### Recommended fix (report only; not implemented)
1. **Now (6h):** add `robustness_measured: bool`, derived from `len(by_group) >= 2`, to the
   `Scorecard` and its `to_dict()`. A consumer must be able to distinguish "100.0 because measured"
   from "100.0 because nothing to measure."
2. **Product decision required:** the real fix is upstream — vary the randomization level *per
   episode* rather than per run. **This changes the seed→episode mapping and therefore every stored
   scorecard.** Breaking change to all stored evidence; must not ship without an explicit decision
   to re-baseline.
3. Until then, document `robustness` as **not measured under the shipped configuration**, and mark
   the mock backend on every exported scorecard.

### Test strategy (described, not written)
- Assert `robustness_score == 100.0` **and** `robustness_measured is False` for a single-level run.
- Assert `robustness_measured is True` and `robustness_score != 100.0` for a two-level run.
- Assert a composite-score regression test that would catch a silent weight change.

---

## P2 — SQLite `save()` silently overwrites a verdict (Critical, VERIFIED)

### Description
The three stores **disagree on what a re-save means**:

| Backend | Statement | Semantics |
|---|---|---|
| Postgres | `ON CONFLICT (run_id) DO NOTHING` (`store/postgres.py:414`) | **first write wins** |
| SQLite | `INSERT OR REPLACE INTO validations` (`store/sqlite.py:203`) | **last write wins** |
| memory | `self._runs[run.run_id] = run` (`store/memory.py:87`) | **last write wins** |

`store/postgres.py:41-42` documents the Postgres behaviour as *"making the deployed verdict log
append-only"*, and `:402-404` explicitly notes it *"differs from the in-memory/SQLite backends where
re-saving overwrites."* The divergence is **known in a comment and unenforced**.

### Repro — a stored APPROVE was replaced by a BLOCK
```
$ python $env:TEMP\c5probe_v4.py
1st save : composite=95.0 decision=APPROVE
2nd save : composite=20.0 decision=BLOCK
```

### Expected vs actual
- **Expected (documented):** the first verdict for a run id is immutable.
- **Actual (SQLite/memory):** the last verdict wins, silently.

### Reachability — this is the customer-facing path
- `cli.py:73` — `_DURABLE_STORE_BACKENDS = frozenset({"sqlite", "postgres"})`, so **`gate` runs on
  SQLite**.
- `actions/validate/action.yml:100-101` — the shipped validate action sets
  `VALIDSIM_STORE=sqlite` **by default for essentially all users**.
- `jobs/worker.py:281-289` — the worker passes `run_id=spec.run_id` on **every** attempt, so a
  retried job re-saves the *same* run id.

**So the append-only guarantee holds on Postgres and fails on the backend the CI gate actually
uses.** A crash-and-retry can replace an `APPROVE` with a `BLOCK` (or vice versa) with no error, no
log line, and no way for the operator to detect it afterwards.

### Business impact
The product's core promise is a trustworthy APPROVE/BLOCK record. On the default CI path that record
is mutable. Any compliance story built on "the verdict log is append-only" is false for the default
configuration.

### Dependencies
None. Contained in the store layer.

### Recommended fix (report only)
**Make SQLite and memory refuse-or-noop on an existing `run_id`, matching Postgres. Do NOT unify by
making Postgres overwrite** — Postgres's behaviour is the one the runbook documents as the
guarantee; changing it to resolve a divergence would invalidate a published contract. Add a
contract-suite test asserting all backends agree.

### Test strategy (described, not written)
- For every backend: save `(run_id, APPROVE)`, then save `(run_id, BLOCK)`; assert the stored verdict
  is still `APPROVE`.
- A parity test in the shared store contract suite so no backend can regress independently.
---

## P3 — Seeded duration fictions make the cost model ~8x overstated (High, VERIFIED)

### Description
`EpisodeResult.duration_s` (`sim/runner.py:61`) is **not measured** — it is drawn from the *seeded*
RNG at `sim/runner.py:131-136` (success `rng.uniform(4.0, 12.0)`, failure `(6.0, 20.0)`, timeout
`(18.0, 35.0)`). `EvaluationResult.mean_duration_s` is `statistics.fmean` of those same values
(`engine/evaluation.py:83`) and **is persisted inside every scorecard's `evaluation_json`**
(`evaluation.py:43` -> `store/postgres.py:425`).

### Repro
```
$ python $env:TEMP\c5probe_verify.py
seed=   7 raw_sum=  29938.8s ($29.11) mean=  9.980s ($29.11) delta=0.0000%
seed=  42 raw_sum=  29915.9s ($29.08) mean=  9.972s ($29.08) delta=0.0000%
seed= 123 raw_sum=  29865.9s ($29.04) mean=  9.955s ($29.04) delta=0.0000%

5k-episode projection:
  sum(duration_s)    = 50272.5s = 13.96 'GPU-hr' = $48.88/run
  5000*mean_duration = 50272.5s = 13.96 'GPU-hr' = $48.88/run
  vault claim (Unit Economics.md:43) = $5-7 per 5K run
  -> overestimate factor = $48.88 / $6 ~= 8.1x

mean_duration_s across 5 seeds: min=9.955 max=10.128 spread=1.72%
```

### Expected vs actual
- **Expected:** cost derivable from stored data.
- **Actual:** **no wall-clock or GPU time is recorded anywhere.** A package-wide search for
  `perf_counter|monotonic|elapsed|gpu_seconds` on the validation path returns nothing; the only hits
  are the rate limiter, the HTTP access log, and the SSE deadline. The two duration fields are
  **algebraically identical** (summing N values and multiplying the mean by N are the same operation
  — hence the exact `0.0000%` delta), so **banning one is insufficient**: the other yields the
  identical wrong answer while looking like an authoritative summary statistic.

The **1.72% seed-to-seed spread** is what makes the fiction credible — stable, reproducible, and
carrying units and a currency symbol.

### Business impact
The vault's margin thesis rests on "$5-7 per 5K run against a $75 list price" and a 70-80% gross
margin (`vault/06 - Business/Unit Economics.md:43`). The only cost figure derivable from stored data
is **fictional and ~8x too high**. It is high enough to appear to *validate* the thesis while being
invented — the failure mode most likely to survive a finance review.

### Dependencies
Needs `wall_clock_s` + `gpu_seconds` recorded at persist time. Must be **excluded from the hashed
`Scorecard.to_dict()`** (`scorecard.py:113`) or determinism breaks. `gpu_seconds` must be nullable so
"no GPU used" is distinguishable from "GPU used for zero seconds".

### Recommended fix (report only)
1. Record `wall_clock_s` (always) and `gpu_seconds` (`None` when no GPU) at persist time.
2. **Prohibit both duration fields as cost inputs** — docstring *and* a test asserting their absence
   from the cost path, so a future refactor cannot reintroduce the read.
3. Until instrumented, mark the vault figure explicitly unmeasured in any external material.

### Test strategy (described, not written)
- Assert the cost path never reads `EpisodeResult.duration_s` or
  `EvaluationResult.mean_duration_s` (absence assertion, not a docstring).
- Assert `gpu_seconds is None` for the mock backend, and that no `$0.00` is rendered.
- Assert `wall_clock_s` is absent from the digest input.
---

## P4 — Row-count metrics typed as counters decrease on delete (High, VERIFIED)

### Description
`api/metrics.py:154-171` emits `validsim_runs_total`, `validsim_approvals_total` and
`validsim_blocks_total` with `# TYPE ... counter`, but all three are derived at scrape time from
`len(store.history())` (`metrics.py:104-121`). A row count is not monotonic.

### Repro
```
$ python $env:TEMP\c5probe_v3.py
BEFORE delete:
   validsim_runs_total 3
   validsim_approvals_total 2
   validsim_blocks_total 1
AFTER DELETE (HTTP 204 endpoint):
   validsim_runs_total 2
   validsim_approvals_total 1
   validsim_blocks_total 1
```

### Expected vs actual
- **Expected:** a counter only ever increases, so `rate()` / `increase()` are defined.
- **Actual:** all three **decrease** on `DELETE /api/v1/validations/{run_id}` (HTTP 204,
  `api/main.py:680-684`), and reset to current row count on process restart.

### Reachability
`/metrics` is mounted auth-stripped and served publicly. Every dashboard or alert built on
`rate(validsim_blocks_total[5m])` — the obvious expression for "page me when validations start
blocking" — is **silently wrong**. Worse, it fails in the unsafe direction: blocks spike, the alert
fires, an operator deletes a run to clean up, and the counter drops, which reads as **recovery**.

Note also `blocks = total - approvals` (`metrics.py:117-118`), so the pair is **one derived value
counted twice** — a customer writing `approvals + blocks == runs` as a data-loss reconciliation gets a
tautology that always balances.

`validsim_http_requests_total` **is** a genuine monotonic in-process counter and is safe to alert on.

### Business impact
The primary product signal — "are validations starting to block?" — cannot be alerted on correctly
today, and the failure mode is a false all-clear after cleanup.

### Dependencies
`c3-api` owns the counters.

### Recommended fix (report only)
Keep the three published names (renaming is a breaking change) but mark them **`# TYPE gauge`** and
label them display-only. Add monotonic `validsim_runs_created_total`,
`validsim_approvals_created_total`, `validsim_blocks_created_total`, bumped once per `store.save()`.
All three are needed — shipping only the runs counter leaves the block alert unbuildable.

### Test strategy (described, not written)
- Save 3 runs, delete 1, assert the `_created_total` counters are unchanged while the row-count values
  fall.
- Assert no `# TYPE counter` line in `render_metrics` output is derived from `len(history())`.

---

## P5 — Retry policy is inverted (High, VERIFIED)

### Description
`jobs/worker.py:137-148` catches **every** exception and finalises the job as `FAILED` on the first
attempt. There is no retry budget, and `JobRecord` (`jobs/models.py:91-99`) has **no `attempts`
field** — only `lease_epoch`, a fencing counter.

Meanwhile the queue requeues abandoned work: `reap_expired()` (`jobs/queue.py:554-579`) returns an
expired `running` job to `queued`, and `claim_next` (`:501-521`) increments `lease_epoch` on every
claim with **no upper bound**.

### Repro
```
$ python $env:TEMP\c5probe_v3.py
JobRecord has 'attempts'? False
JobStatus values: ['queued', 'running', 'done', 'failed']
claimed   : status=running epoch=1
reaped    : ['vrun-000000aa']
re-claimed: status=running epoch=2 (unbounded)
```

### Expected vs actual
| Failure mode | Actual | Expected |
|---|---|---|
| Worker **raises** (Isaac worker unreachable, bad checkpoint path, transient OOM) | **terminal `failed`, zero retries** | bounded retries |
| Worker **dies** (SIGKILL, OOM-kill) | **requeued forever**, unobservable | bounded retries then DLQ |

**The direction is inverted in the more damaging way:** the *transient* failure gets no retry, while
the *deterministic* failure loops indefinitely.

**Reachability:** `python -m validsim.cli worker` -> `run_forever` -> `run_once` (the shipped worker
entrypoint, `docker-compose.yml:127`).

**Not a hot loop** (checked, since a crash-loop was the obvious worry): `run_forever` reaps once per
iteration and `_DEFAULT_LEASE_SECONDS = 3600` (`jobs/queue.py:82`), so a crash-loop is bounded to
roughly **one requeue per hour per job**. The real problem is that a job can sit `queued`/`running`
indefinitely with **no signal to anyone**.

### Business impact
A transient GPU-worker blip fails a customer's validation with no second attempt, while a
deterministically-failing job silently consumes a slot forever. Both are invisible:
`reap_expired()`'s return value is discarded at `jobs/worker.py:176`, and no queue-depth or attempt
metric exists.

The vault lists the **dead-letter queue as target-state and explicitly unimplemented**
(`vault/04 - Engineering/Async Job Queue.md:15`) — so today there is no designed terminal state to
reach.

### Dependencies
Needs `JobRecord.attempts` + a `max_attempts` bound enforced at claim time. A counter alone is **not
sufficient** — an unbounded counter changes nothing.

### Recommended fix (report only)
1. Add `JobRecord.attempts` (backward-compatible: `from_dict` defaults optional fields).
2. Add `max_attempts` (env-configurable, like `VALIDSIM_JOB_LEASE_SECONDS`), enforced at claim time.
3. The terminal state after exhaustion must be **distinguishable** from a first-attempt failure (the
   DLQ state).
4. Surface `attempts` on the job record so it is **API-queryable**, not log-only.

### Test strategy (described, not written)
- A backend that raises once then succeeds -> assert the job completes, not `failed` on attempt 1.
- A backend that always raises -> assert exactly `max_attempts` executions, then the DLQ state.
- A worker killed mid-job -> assert the requeue increments `attempts` and eventually dead-letters.
- Assert `run_forever` records `reap_expired()`'s result (currently discarded).
---

## P6 — Public `/health` discloses the full security posture (Medium, VERIFIED)

### Description
`/api/v1/health` is deliberately exempt from the API-key gate (`api/main.py:93`,
`PUBLIC_PATHS = frozenset({"/api/v1/health"})`). It returns `auth_enabled`, `cors_wildcard`,
`rate_limit`, `store_backend` and `job_queue_backend` — a complete map of the instance's security
configuration, to any unauthenticated caller.

### Repro
```
$ python $env:TEMP\c5probe_v5.py
GET /api/v1/health with auth configured, NO key presented -> 200
{"status":"ok","version":"0.2.0","auth_enabled":true,"cors_wildcard":true,
 "rate_limit":{"requests":60,"window_seconds":60.0},
 "store_backend":"sqlite","job_queue_backend":"memory"}
```

### Expected vs actual
- **Actual:** an unauthenticated caller learns that auth is enabled, **that CORS is wildcard**, the
  exact rate-limit budget, and which store/queue backends are in use.
- This is a **deliberate, documented design choice** (the probe must run without a credential, and
  `api/main.py:592-602` states it is config-only and I/O-free), not an oversight.

### Business impact
Low on its own — it is configuration, not data. It matters for two reasons: `cors_wildcard: true` is
precisely the field an operator should be checking, and gating it would remove their ability to check
it. `store_backend` / `job_queue_backend` are **class-name derivations**, not connection state
(`api/main.py:463-469`), so they reveal nothing about whether Postgres is reachable — live
reachability is what belongs behind auth, in a readiness probe.

### Dependencies
None. Note `/metrics` is also auth-stripped and exposes run volumes, approval/block counts and the
latest composite — a deliberate trade, since every Prometheus would otherwise need a ValidSim key.

### Recommended fix (report only)
**Do not gate the config fields.** If dependency state is ever added, put it in an authenticated
`/ready` endpoint rather than widening `/health`. If the exposure is unacceptable in a given
deployment, the mitigation is network-level (loopback bind / NetworkPolicy), not field gating.

### Test strategy (described, not written)
- Assert `/health` remains reachable without a key and returns the documented keys.
- Assert no dependency-reachability field ever appears in the `/health` payload.

---

## False leads — ruled out

Recorded so these are not re-investigated.

1. **"The runbook says the rate limiter fails open."** REFUTED as a *code* defect: the code fails
   **closed** (`api/main.py:217-220`, `:227-228`; `DEFAULT_RATE_LIMIT = 60` at `:110`). This was a
   **documentation** defect in `docs/runbook.md`, already corrected. Not a code finding.

2. **"`robustness` is merely noisy or unmeasured."** REFUTED — stronger than first claimed. It is a
   **constant**, not noise: 25 seeds all produce exactly `100.0` with zero variance.

3. **"Postgres also overwrites, so the overwrite is consistent."** REFUTED —
   `ON CONFLICT (run_id) DO NOTHING` (`store/postgres.py:414`) is first-write-wins, and the module
   docstring states the divergence explicitly (`:402-404`).

4. **"A crash-looping job spins hot."** REFUTED — reaping is once per `run_forever` iteration with a
   3600s default lease, so it is bounded to ~1 requeue/hour/job. The defect is unbounded *retries*
   plus zero *observability*, not a hot loop.

5. **"The two duration fields merely agree closely (~1%)."** REFUTED — they are **algebraically
   identical** (0.0000% delta at a fixed seed). An earlier "1.33%" figure was cross-seed sampling
   noise from comparing different seeds, not a property of the fields.

6. **Invalid control: relabelling `randomization_level` post-hoc.** DISCARDED — relabelling does not
   re-simulate at the new success probability (`sim/runner.py:99-100`). The P1 control re-simulates
   two real runs instead. Recorded because the discarded control produced a *plausible but wrong*
   0.31-point delta.

7. **"3,000-episode raw sum vs 5,000-episode projection showed a 66% divergence."** DISCARDED — my own
   arithmetic error, comparing different episode counts. Re-run like-for-like at equal N, the delta is
   exactly 0.0000%.

---

## Assumptions

1. **`validsim 0.2.0`, Python 3.14.4**, run from the repo root with `PYTHONPATH` set. The package
   declares `requires-python = ">=3.10"`.
2. **Store backends were exercised in-process**, not against a live Postgres server. The Postgres side
   of P2 is established from the SQL text and the module's own docstring
   (`store/postgres.py:41-42`, `:402-404`, `:414`), not from execution — flagged as the one part of
   P2 that is source-verified rather than run-verified.
3. **"Under the shipped configuration"** means `TaskConfig.randomization` left at its default and the
   mock backend, which is what the compose stack, the test suite, and the worker all use.
4. The Isaac GPU worker is **contract-only** in this repo (`docs/isaac-worker.md`); P5's "Isaac worker
   unreachable" row is the transient case the retry policy fails to handle, inferred from the
   documented backend contract rather than a running GPU.
5. The vault's `$3.50/A100-hr` and `$5-7 per 5K run` figures are used **as the stated claim** to
   measure against, not as verified pricing.
