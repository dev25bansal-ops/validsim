# c5-integrations — Audit Report

**Agent:** c5-integrations · **Scope:** third-party integrations, external API/platform surfaces, dependency cost, CI/distribution channels, and the scoring-math dependencies those surfaces expose.
**Repo:** `d:\SIM-TO-REAL` @ commit-in-working-tree (production files under active edit by other agents — see *Assumptions*).
**Mode:** read-only. No production file was created, edited, deleted or moved. Probes live in `%TEMP%` only.

---

## Findings

| ID | Title | File:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| C5-01 | 30 of 100 composite points are constants; regression silently maxed | `engine/scorecard.py:69-74`, `sim/runner.py:175,182` | **Critical** | VERIFIED | 2–3 d | `python %TEMP%\c5_probe.py` (Probes 1–2) |
| C5-02 | Gate configurable to be ~blind to adversarial failure (0.40 pts) | `config.py:136,138`, `engine/scorecard.py:34` | **Critical** | VERIFIED | 1 d + re-baseline | `python %TEMP%\c5_probe.py` (Probe 3) |
| C5-03 | `MockIsaacBackend` discards `scenario.params` incl. safety observables | `sim/runner.py:99-103,126-127` | **High** | VERIFIED | 2 d | `python %TEMP%\c5_probe.py` (Probe 4), `c5_probe4b.py` |
| C5-04 | Shadow harness cannot detect C5-03 (0.95σ band, aggregate-only) | `sim/shadow.py:66,71,260` | **High** | VERIFIED | 3 d | `python %TEMP%\c5_probe.py` (Probe 5) |
| C5-05 | Notifier published as shipped, zero callers, unconfigurable | `notify/dispatcher.py:167`, `README.md:154` | **High** | VERIFIED | 1 d | `python %TEMP%\c5_probe.py` (Probe 6) |
| C5-06 | `POST /api/v1/jobs` cannot express a threshold | `jobs/router.py:64-70`, `jobs/models.py:40-45` | **Medium** | VERIFIED | 1 d | `python %TEMP%\c5_probe.py` (Probe 7) |
| C5-07 | Unbounded job reclaim; no attempt counter | `jobs/queue.py:554-579`, `jobs/models.py:99` | **Medium** | VERIFIED | 2 d | `python %TEMP%\c5_probe.py` (Probe 8) |
| C5-08 | Redis unfenced `update_status` is non-atomic (latent) | `jobs/queue.py:940-946` | **Low** | VERIFIED | 3 h | `python %TEMP%\c5_probe.py` (Probe 9) |

---

## C5-01 — Two composite components are constants; 30 of 100 points are fixed

**Subject:** shipped code. **Reachable from:** `validsim run` (CLI), `validsim worker`, `POST /api/v1/validations`.

### Description
`build_scorecard` weights four components (`engine/scorecard.py:34`): success 0.4, safety 0.3, robustness 0.2, regression 0.1. Two of them do not vary:

- **`robustness` is always exactly `100.0`.** `_robustness_score` (`scorecard.py:53-66`) groups episodes by `randomization_level` and early-returns `100.0` when `len(rates) < 2` (`scorecard.py:62-64`). `run_validation` stamps **every** episode — nominal and adversarial — with the single `task.randomization` value (`sim/runner.py:175`, `:182`), so exactly one group ever exists.
- **`regression` is `100` whenever `regression is None`** (`scorecard.py:69-74`). `cli.py:263-270` calls `run_and_score` without `baseline_run_id`, and `jobs/worker.py:281-287` does the same. So it is `None` on every CLI run and every worker run.

### Reproduction (Probe 1)
```
robustness_score   = 100.0   (two runs: 100.0, 100.0)
regression_delta   = None
composite_score    = 80.26

recomputed: 0.4*succ% + 0.3*safety + 0.2*robust + 0.1*100 = 80.26
stored composite                                        = 80.26

CONSTANT portion = 0.2*100.0 + 0.1*100 = 30.0 of 100
```

### Control (Probe 2) — proves the cause is the runner, not the scorer
A subclass that stamps a *different* `randomization_level` per episode yields `robustness_score = 83.61`, not 100.0. So `_robustness_score` is capable of varying; the constant is produced upstream by `runner.py:175,182`.

### Expected vs actual
- **Expected:** a composite whose four parts each measure something, with `regression_delta` distinguishing "no baseline supplied" from "no regression found."
- **Actual:** 30 points are fixed. A run cannot score below 90.0 on those two components combined, and `regression_delta = None` reads as *"no change detected."*

### Business impact
A reader seeing composite 80.26 assumes 20 points came from cross-condition robustness testing and 10 from a regression check. **Neither was measured.** Only success (0.4) and safety (0.3) carry information — 70% of the weight. For a product whose pitch is a *defensible* safety scorecard, the largest single risk is that the headline number is substantially a constant, and the most damaging field is the one that looks like a result.

### Dependencies
Fixing the `regression` half requires a per-job baseline — see **C5-06** (`JobSpec` has no `baseline_run_id`). Fixing the `robustness` half is blocked by **C5-03**: the mock cannot produce cross-condition variation at all.

### Recommended fix (report only — no code written)
1. Do **not** change `_regression_component` to return 0 when `regression is None`. That silently drops 10 points on every CLI/worker run and re-gates historical verdicts. Instead add `regression_evaluated: bool` to the scorecard, and surface a non-gating `caveats[]` list.
2. For robustness, first land **C5-03** so episodes actually differ; `scorecard.py` needs no change once real variation exists.

### Test strategy
Assert `robustness_score != 100.0` for a multi-level run; assert `regression_evaluated is False` and that the caveat list is non-empty when no baseline is supplied. **Add a falsification test:** assert the pipeline reports a *different* robustness for two genuinely different condition sets — otherwise a future refactor could restore the constant silently.

---

## C5-02 — The gate can be configured to be nearly blind to adversarial failure

**Subject:** shipped code + configuration. **Reachable from:** `POST /api/v1/validations`, `POST /api/v1/jobs`, `validsim run`.

### Description
Adversarial episodes influence the score **only** through the success rate (there is no separate safety weight). Their weight is therefore exactly their share of the episode budget — which the caller chooses. `config.py:136` allows `episodes` up to **100,000**; `config.py:138` caps `adversarial_count` at **1,000**.

### Reproduction (Probe 3)
```
 episodes adversarial   share    base  all-adv-fail    cost
      500          24  4.58%   93.04         91.21    1.83
     1000         100  9.09%   91.24          87.6    3.64
    10000        1000  9.09%   91.24          87.6    3.64
   100000        1000  0.99%   94.48         94.08    0.40
```

### Expected vs actual
- **Expected:** a safety-critical test suite cannot configure itself out of the score.
- **Actual:** at the maximum legal `episodes`, **every adversarial episode can fail and the composite moves 0.40 points** against a default threshold of 85. A policy can fail its entire adversarial suite and clear the gate.

Note the ratio framing is misleading: 2.17× (nominal vs adversarial at 500+24) invites "somewhat underweighted." The structural fact is that adversarial influence is **entirely operator-controlled**, and that control is the person being scored.

### Business impact
The gate's most safety-relevant failure mode is the one most easily diluted. Combined with **C5-01**, success rate is the only live input to the score, and a caller can shrink the weight of the thing the product exists to test.

### Recommended fix
A **minimum adversarial share** (or expressing the budget as a ratio rather than two independent integers). Do **not** rebalance the four weights — that redistributes 100 fixed points and leaves the defect intact. If adopted, enforce it as a `model_validator` on `ValidationRequest` and `EnqueueJobRequest` (fail at submit with 422) rather than inside the engine.

**This is a scoring-semantics decision with a re-baseline cost** — historical scores become incomparable. It needs a founders' decision, not an engineering task.

### Test strategy
For a legal budget at the cap, assert that failing all adversarial episodes changes the composite by at least a stated minimum. Add a validation test that a below-floor budget is rejected at submit.

---

## C5-03 — `MockIsaacBackend` discards `scenario.params`, including safety observables

**Subject:** shipped code. **Reachable from:** every run using the default backend (`VALIDSIM_BACKEND` unset → mock); also the reference side of the shadow harness.

### Description
`success_probability` (`sim/runner.py:99-103`) computes `p` from `base_success_rate`, the randomization penalty, and **`scenario.difficulty` only**. `scenario.params` is never read. `run_episode` then branches on `scenario.category` (`runner.py:126-127`) to choose a *sampling band*, still ignoring `params`.

`min_human_distance_m` is a **safety observable** — it feeds `prox_violation_rate` and therefore `safety_score` (`engine/safety.py`).

### Reproduction (Probe 4) — fixed seed, params swept
```
  human_distance_m  success   min_human_distance_m
               0.1     True                  0.112
               0.4     True                  0.112
               0.8     True                  0.112
               1.2     True                  0.112
               3.0     True                  0.112

distinct (success, min_human_distance_m) tuples = 1
```

### Measured band (seed varied, 3000 draws) — substantiates "unrepresentable"
```
human_proximity band over 3000 seeds: 0.050 .. 0.900
lighting_change (non-proximity) band: 0.600 .. 2.500

  0.01  -> NOT REPRESENTABLE      0.9   -> representable
  0.1   -> representable          1.2   -> NOT REPRESENTABLE
  0.5   -> representable          3.0   -> NOT REPRESENTABLE
```
Category shifts the band (`0.05–0.9` vs `0.6–2.5`); params never do.

### Expected vs actual
- **Expected:** a scenario asserting "human at 0.1 m" is simulated as a human at 0.1 m.
- **Actual:** the requested distance is discarded and re-drawn from the RNG. **A value outside the category band cannot be represented at all.**

Meanwhile `isaac_worker.py:95` *does* serialize `"params": dict(scenario.params)`, and `scenarios/llm_generator.py:304-310` populates them. So params are **generated → validated → serialized → ignored at execution**.

### Business impact
Mock-derived scores are **not a conservative approximation** of Isaac-derived scores — they are a **different measurement**. A real worker honouring `human_distance_m: 0.1` behaves differently from the mock, so the safety score a customer sees today is not a preview of the one they will get on GPU. This is also the third independent reason the mock cannot produce a meaningful `robustness` value (with C5-01 and C5-04's R² finding).

### Recommended fix
Either consume `params` in `MockIsaacBackend` (at minimum `human_distance_m` for the `human_proximity` category), or reject/flag scenarios whose params fall outside the representable band. Add a mock-backend marker to every exported scorecard regardless.

### Test strategy
Per-scenario assertions: `human_distance_m=0.1` and `1.2` must produce different `min_human_distance_m`; a param outside the band must be rejected rather than silently re-drawn. **Add a falsification test asserting the mock *fails* when handed unrepresentable params** — otherwise the silence persists.

---

## C5-04 — The shadow harness structurally cannot detect C5-03

**Subject:** shipped code. **Reachable from:** the worker-promotion decision.

### Description
`ShadowReport` (`sim/shadow.py:84-112`) carries five fields — `worker_ok`, `mock_success_rate`, `worker_success_rate`, `delta`, `contract_violations`, `passed` — and **none records the scenario params or categories exercised**. `_success_rate` (`:128-132`) is `sum(success)/len(results)`, and `passed = worker_ok and abs(delta) <= self._tolerance` (`:260`). The comparison is **aggregate-only**, so a per-scenario defect is invisible *by construction*.

Constants: `_DEFAULT_EPISODES = 20` (`:66`), `_DEFAULT_TOLERANCE = 0.15` (`:71`).

### Reproduction (Probe 5) — arithmetic from those constants
If the worker ignores params exactly as the mock does, the two rates are independent binomials, so `SD(delta) = sqrt(2p(1-p)/N)`:

| true p | SD(delta) | tolerance | **P(false PASS — fully broken worker)** |
|---|---|---|---|
| 0.3 | 0.1449 | 1.04σ | **69.9%** |
| 0.5 | 0.1581 | 0.95σ | **65.7%** |
| 0.7 | 0.1449 | 1.04σ | **69.9%** |
| 0.9 | 0.0949 | 1.58σ | **88.6%** |

**The tolerance is 0.95σ.** A worker wrong in every parameter it handles still passes roughly two-thirds of the time — and ~89% when success rates are high, which is the regime a promotable worker would be in.

### Expected vs actual
- **Expected:** the docstring's "crisp *is this worker ready to promote?* signal" (`:18`) detects a worker that ignores the wire contract.
- **Actual:** it detects only gross divergence (>15 points). A totally params-ignoring worker passes ~66–89% of the time.

### Business impact
This is the instrument used to decide whether to trust Isaac at all. A `passed=True` is **not evidence** the worker honours the contract. That makes the GPU promotion gate unvalidated — a larger risk than the export surface, and the reason **C5-03** cannot be verified by running the harness.

### Recommended fix
The comparison *shape* is the defect, not the tolerance: add a **per-scenario, per-category outcome comparison** as the primary assertion. Tightening the band alone only catches larger divergences.

### Test strategy
1. Per-scenario/per-category comparison, replacing aggregate-only.
2. **Falsification test for the harness itself:** assert a deliberately params-ignoring worker is reported `passed=False`. Without this, any future threshold change is unfalsifiable and the harness can silently lose detection power again.

---

## C5-05 — Notification capability is published as shipped, but has zero callers and no configuration path

**Subject:** shipped code + documentation. **Reachable from:** nothing.**

### Description
`WebhookDispatcher` (`notify/dispatcher.py:167`) and `EmailNotifier` (`notify/email.py:223`) are fully implemented — HMAC signing, severity routing, retry/backoff, Slack Block Kit. Reachability probe returns matches **only inside `validsim/notify/` itself**: no `pipeline`, `api`, `worker`, or `cli` line appears.

Compounding this, `.env.example:131-147` documents `VALIDSIM_WEBHOOKS_LIVE=0` and a full six-variable SMTP block, but **no variable for a webhook URL** — so live delivery is not merely disabled, it is unconfigurable.

Meanwhile the capability is asserted as complete: `README.md:154` `- [x] **Slack webhooks**`, `README.md:164` `- [x] **Email notification channel**`, `README.md:13` lists the dispatchers as platform features, and `vault/00 - Dashboard/Build Status.md:68` lists it under "New Capabilities."

### Reproduction (Probe 6)
```
git grep WebhookDispatcher|EmailNotifier|.dispatch( -- validsim/:
validsim/notify/__init__.py:7,13,22,24
validsim/notify/dispatcher.py:40,167
validsim/notify/email.py:4,6,38,223
(no pipeline / api / worker / cli line)
```

### Expected vs actual
- **Expected:** a `[x]` README item is reachable by a user following the README.
- **Actual:** reachable **only by writing Python that imports the library** — never by configuration, and never by any shipped entrypoint.

### Business impact
This is a *published claim with no path to truth*, in the document a technical buyer reads first and screenshots into a procurement document. A BLOCK produces no alert: the deploy gate is silent. It is the cheapest high-value fix in the report — the code already exists.

### Recommended fix
Land the doc correction **and** the v1 wiring as **one change** (config env vars; construct the dispatcher in `run_and_score` *after* the store write, so a webhook can never announce an APPROVE that was never persisted; report hook count + live flag in `GET /api/v1/health`). Fixing the doc alone leaves the capability absent; fixing the code silently afterwards leaves the false claim standing.

**Zero new dependencies** — Slack is already `format="slack"`; every other target is a JSON POST `httpx` already makes.

### Test strategy
Assert that a BLOCK run produces exactly one delivery per registered hook; that a notify failure does not fail the validation; and that a dry-run dispatcher makes no network call.

---

## C5-06 — `POST /api/v1/jobs` cannot express a threshold

**Subject:** shipped code. **Reachable from:** `POST /api/v1/jobs` → `validsim worker`.

### Description
`EnqueueJobRequest` (`jobs/router.py:64-70`) accepts exactly four fields: `checkpoint_id`, `task_id`, `episodes`, `adversarial`. `JobSpec` (`jobs/models.py:40-45`) has five: `run_id`, `checkpoint_id`, `task_id`, `episodes`, `adversarial`. **Neither carries `threshold`.**

`JobWorker` takes `threshold` as a keyword-only constructor parameter (`jobs/worker.py:70-79`), stores it, and applies it to **every job that worker runs** (`worker.py:287`).

### Reproduction (Probe 7)
```
JobSpec fields = ['run_id', 'checkpoint_id', 'task_id', 'episodes', 'adversarial']
  has 'threshold': False   has 'baseline_run_id': False
  has 'notify': False      has 'attempts': False

EnqueueJobRequest fields: ['adversarial', 'checkpoint_id', 'episodes', 'task_id']
```

### Expected vs actual
- **Expected:** the async path accepts the same threshold configuration as `POST /api/v1/validations`.
- **Actual:** it cannot. The only way to change the bar is to construct a differently-configured worker process.

### Business impact
For a product whose pitch is "a threshold you configure gates the deploy," **the async path — the one that exists for long GPU jobs — cannot receive that configuration.** Two consequences:
1. Raising the worker's threshold instead would **retroactively change verdicts for every other job that worker runs**, including jobs whose callers never asked for a stricter bar.
2. `severity_from_scorecard` (`notify/dispatcher.py:83-90`) reads `payload["threshold"]` to pick `critical` vs `warn`, so **the same checkpoint can page differently depending on which entry point ran it.**

### Dependencies
`threshold` must be a **per-job** `JobSpec` field, bundled with `baseline_run_id` (which **C5-01** needs) and `notify` (which **C5-05** needs). One change, three fields. Note `jobs/models.py` is under concurrent modification in the working tree — coordinate before editing.

### Test strategy
Enqueue with `threshold=90` and assert the stored scorecard records 90; enqueue two jobs with different thresholds on **one** worker and assert each verdict uses its own.

---

## C5-07 — Unbounded job reclaim; no attempt counter

**Subject:** shipped code. **Reachable from:** `validsim worker` (`run_forever` → `reap_expired`).

### Description
`reap_expired` (`jobs/queue.py:554-579`) returns a lease-expired job to `queued`; `claim_next` increments `lease_epoch` on claim. **Nothing bounds the number of reclaims**, and `JobRecord` (`jobs/models.py:99`) has `lease_epoch` but **no `attempts` field.** Meanwhile `worker.py:139-142` makes any exception terminal on the first attempt.

So the two failure modes are inverted: a **worker crash** retries indefinitely and unobserved; a **raised error** gets no retry at all.

### Reproduction (Probe 8)
Five claim/reap cycles with a 1s lease:
```
after claim : running | epoch 1
reaped      : ['vrun-abc12301']  -> queued, epoch 1
  re-claim 1..4: running, epoch 2,3,4,5
final       : queued | epoch 5        (never terminal)
attempts tracked anywhere? False
```
JobRecord keys: `[created_at, error, finished_at, job_id, lease_epoch, lease_expires_at, result, spec, started_at, status]`

### Control — fencing is correct
With the epoch unchanged after a reap, a dead worker's late write is still **rejected**: the guard is a conjunction of `status == RUNNING` **and** epoch match, and the epoch is monotonic. Both the memory path and the Redis Lua scripts (`_REDIS_RENEW_LEASE_SCRIPT`, `_REDIS_FINISH_SCRIPT`) implement it. **No fencing defect.** I hypothesised one and it was falsified by execution.

### Expected vs actual
- **Expected:** a bounded retry budget, and a dead-letter state for exhausted jobs.
- **Actual:** infinite silent retry on crash; zero retries on error; neither counted. `vault/04 - Engineering/Async Job Queue.md:15` already lists the dead-letter queue as **target-state, not implemented**.

### Business impact
The transient failure — a GPU worker briefly unreachable, the *expected* failure mode for a product whose GPU path does not yet exist — is the one that gets no retry. A job can also cycle for hours with nothing but a log line: the 3600s default lease bounds requeues to ~1/hour, so it is not a hot loop, but it is invisible.

### Recommended fix
`JobRecord.attempts` **and** a `max_attempts` (env-configurable, mirroring `VALIDSIM_JOB_LEASE_SECONDS`) enforced at claim time, with a terminal state distinguishable from a first-attempt failure. A counter with no bound achieves nothing. `attempts` should be queryable via the API, not only logged.

### Test strategy
Claim → expire → reap ×N and assert the job reaches a dead-letter state at the bound; assert `attempts` increments and is visible on the record.

---

## C5-08 — Redis unfenced `update_status` is a non-atomic read-modify-write (latent)

**Subject:** shipped code, **no production caller**. **Severity Low** *as shipped* — see below.

### Description
`RedisJobQueue.update_status` branches at `jobs/queue.py:927`:
- `lease_epoch is not None` → `_REDIS_FINISH_SCRIPT`, atomic ✅
- `lease_epoch is None` → **`client.get(key)` … `client.set(key, …)`** (`:940-946`), two round-trips, no Lua, no `WATCH`/`MULTI` ❌

The in-memory `JobQueue` holds `self._lock` across the identical operation, so **the two backends disagree on atomicity** — and Redis is the default in `docker-compose.yml`.

### Reproduction (Probe 9) — reachability, not a race
```
git grep -n "update_status" -- validsim/ (non-test):
  validsim/jobs/worker.py:212   (inside _finish, always passes lease_epoch)
  validsim/jobs/queue.py:581, 874  (definitions)
(validsim/jobs/router.py: no matches)
```
`jobs/router.py` never writes a job status — its only mutation is `enqueue` (`:108-123`). The docstring at `queue.py:602` ("the HTTP router omit it and get the unfenced behaviour") **describes a caller that does not exist.**

### Expected vs actual
- **Expected:** the invariant "`update_status` without a fencing token must still be atomic."
- **Actual:** it is not, on the default backend. No shipped code path reaches it today.

### Business impact
Latent, not live — I initially filed this as P1 and was corrected. The forward-looking risk is that **a dead-letter transition is an unfenced status write by nature**, so this becomes a **prerequisite for the DLQ work in C5-07**, not independent cleanup. Sequence: `_REDIS_UNFENCED_UPDATE_SCRIPT` → `JobSpec` bundle (C5-06) → DLQ.

### Test strategy
Two connections against a live Redis, interleaving a reaper and an unfenced writer; assert the terminal transition is not lost. Plus an invariant test that both backends agree on atomicity.

---

## False leads — ruled out, with evidence

1. **`two_proportion_bootstrap_test` is not a correctness bug.** `total_ones = sum(pooled)` is permutation-invariant, so `T - s_a` is exactly the complement sample's sum and `s_a/na - (T - s_a)/nb` **is** the difference of means. Measured gap vs an explicit `fmean(a')-fmean(b')` reference: **8.882e-16** over 100k permutations. The variable *names* are binary-flavoured; the arithmetic is not. *(Earlier claimed as a bug by me; refuted.)*
2. **NumPy/SciPy are not worth adding for the bootstrap.** `random.binomialvariate` (stdlib, 3.12+) measured **3–30× faster than NumPy** at these sizes and is exact for the binary case. Rejected as a default anyway: the shipped loop is bit-reproducible per seed and `docs/testing.md:59-71` pins `SEED = 42`, so a changed RNG stream would move every persisted CI. Opt-in at most, never on a persisted scorecard.
3. **The job-queue fencing is sound.** I hypothesised a second defect (a reaped job retaining its epoch) and **falsified it by execution** — stale writes are rejected in both windows. Reported rather than quietly dropped.
4. **`block_reasons[]` does not exist anywhere in the repo.** I had described it as "owned by c3-api" — it was *proposed and accepted*, never built. Grep returns zero matches.
5. **The 503 `queue_full` path is implemented and tested.** I initially called it "dead code." `jobs/router.py:115-122` catches `QueueFullError` → 503 + `Retry-After`, and `tests/test_jobs_queue_full.py` asserts 503-not-500, the body shape, and the header. *(My error, retracted.)*
6. **`/metrics` counter-typing is already fixed.** An earlier finding claimed `validsim_runs_total` etc. were row-count values typed `counter`. **Another agent has since corrected this** — `api/metrics.py:165-185` now emits `# TYPE ... gauge` with a comment explaining deletability. **Not re-filed.**
7. **Adversarial categories are validated.** `scenarios/generator.py:82-86` rejects unknown categories in `__post_init__`; `ADVERSARIAL_CATEGORIES` is a fixed 12-tuple. (My first probe used `"collision"`, a failure *mode* rather than a category — my error, corrected in the report.)
8. **Shadow-harness params coverage.** I flagged `sim/shadow.py` as an unexamined file; c4-moat (read-only) confirmed it carries no params/categories and cannot detect C5-03 — which became **C5-04**.

---

## Assumptions

1. **The working tree is moving.** `git status` shows 14 modified files under `validsim/` and one new untracked module (`validsim/project_config.py`). I re-read `runner.py`, `scorecard.py`, `stats.py`, `shadow.py`, `config.py`, and `metrics.py` before probing; line references reflect the tree at probe time. **Findings C5-01/02/03/04 depend on `scorecard.py`, `runner.py`, `shadow.py` and `config.py`, none of which are currently modified** — but re-verify before acting.
2. `VALIDSIM_BACKEND` unset ⇒ `MockIsaacBackend` (`sim/__init__.py`), the default in `docker-compose.yml` and the test suite.
3. Probes imported the installed package from `d:\SIM-TO-REAL`; the suite's own `conftest.py` was not used, so no repo state was mutated.
4. Probe 5 is closed-form arithmetic over source constants, not a Monte-Carlo simulation of the harness. The binomial-independence assumption is stated explicitly and is the *pessimistic* case for the harness (a correlated worker would be caught more reliably).
5. Per the lead's stop order, **no bug was fixed and no test was written.** Test strategies above are descriptions for the lead to implement.
