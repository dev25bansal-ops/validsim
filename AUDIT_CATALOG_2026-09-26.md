# ValidSim — Consolidated Audit Catalog

**Date:** 2026-09-26 · **Method:** 15 parallel audit agents across categories 2–6, every finding independently re-verified by execution before acceptance · **Suite at time of writing:** 1485 passed / 49 skipped / 0 failed, ruff clean

Per-agent detail is in `audit/cat*.md` (14 reports). This document is the consolidated, deduplicated view.

---

## Verification policy

Every finding below was **reproduced by an executed command** before being accepted. Three outcomes are recorded honestly:

- **VERIFIED** — reproduced; fix implemented and pinned by a test.
- **REFUTED** — investigated and disproven. Recorded because a wrong finding costs as much as a missed one.
- **OPEN** — verified but not yet fixed, usually a policy decision rather than a mechanical bug.

Two prior-session fixes were independently **re-confirmed** by this audit (PDF escaping, SMTP TLS, non-ASCII key), and one prior claim was **refuted** (human-proximity dilution).

---

## Priority 1 — CRITICAL, fixed this session

### 48 · The gate could certify a total task failure
**`engine/scorecard.py:180-199` · VERIFIED · 2h**

30 of 100 composite points are unconditional. `run_validation` applies one `task.randomization` level to every episode, so all episodes land in a single group and `_robustness_score` returns a structural `100.0`; `_regression_component` returns a structural `100.0` whenever no baseline is supplied (the CLI/worker default). The old evidence rule checked episode *count* only, never *outcome*.

Reproduction: 100 episodes, all failing, safety perfect → `composite = 60.0`, and `APPROVE` at thresholds 0.0, 30.0 and 60.0.

**Fix:** the evidence rule now also requires `evaluation.success_count > 0`.
**Tests:** `test_total_task_failure_can_never_approve`, plus a control sweeping thresholds 0–100 to prove the protection is the evidence rule and not threshold arithmetic. A real run (success 0.71, composite 83.33) still gates exactly as before.

### 49 · NaN safety observables scored a perfect 100.0 (fail-open)
**`engine/safety.py:85-122` · VERIFIED · 2h**

`NaN > limit` is `False` in Python, so a NaN contact force was counted as a *passing* episode. A run reporting only NaN forces scored `safety = 100.0`. Same fail-open on the proximity channel. Also fixed: `min()` over a list containing NaN is order-dependent and could discard a genuinely closer reading.

**Fix:** non-finite readings now count as violations (fail closed). `min_proximity` considers only finite values.
**Tests:** 3 new — NaN force, NaN distance, and a control asserting ordinary clean (100.0) and dirty (70.0) runs are unchanged.
**Result:** NaN force now scores 70.0 with rate 1.0, down from a false 100.0.

### 50 · `/docs`, `/redoc`, `/openapi.json` bypassed the API-key gate
**`api/main.py:495-499, 305-350` · VERIFIED · 2h**

FastAPI mounts these on the application itself, outside the `/api/v1` router carrying the `X-API-Key` dependency. With a key configured, all three answered **200** anonymously while `/api/v1/validations` correctly returned 401 — publishing every route, parameter and response schema of an authenticated API.

**Fix:** `_DocsAuthMiddleware` gates the three schema paths whenever a key is set; byte-comparison like the existing check. Installed only when a key exists, so local dev and the open suite keep interactive docs.
**Tests:** 8 new (anon 401, keyed 200, no-key 200, health stays public, wrong key 401).

---

## Priority 1 — CRITICAL, verified, still OPEN

These are real and reproduced. They are **not** fixed because each changes what a score *means*, so existing verdicts move. They need an explicit decision.

### 51 · The checkpoint under test never reaches any simulation backend
**`sim/runner.py:105-112`, `config.py:133-139` · VERIFIED · 8h · dependency for the whole product premise**

`run_episode(task, seed, randomization_level, scenario)` receives no checkpoint, and `TaskConfig` has no checkpoint field. `checkpoint_id` only feeds `stable_seed()` to select the RNG stream.

Reproduction — three different checkpoints, identical task:
```
ckpt-A: success=0.7500
ckpt-B: success=0.7375
ckpt-C: success=0.7500
```
All within sampling noise of the same base rate (0.9 minus a fixed randomization penalty). The variation is an RNG reshuffle, **not** a property of the checkpoint.

**The strongest control** (from `c4-sim2real` / `c2-arch-debt`): six arbitrary *task names* — semantically meaningless strings — produce a **larger** composite spread (stdev 2.90) than six different **checkpoints** (stdev 1.97). Both arms reach the engine only via `stable_seed(checkpoint_id, task_id)`, so both are seed perturbation. The meaningless input produces *more* of the same noise, which disposes of "it's just sampling variance" without anyone having to argue about noise.

A sim-to-validity product whose scorecard cannot distinguish a good checkpoint from a bad one **cannot rank policies** — the core promise is currently unfalsifiable.

**Note:** expected while the backend is the deterministic mock; the Isaac wire contract has the same gap (`sim/isaac_worker.py:519-527`), and `reference_contract_cases()` passes against a contract with no checkpoint field, so conformance would *certify* the defect. Cheap to fix now, before a real worker exists.

### 51-fix · The checkpoint now reaches the backend (implemented 2026-09-26)
**`config.py` · `sim/runner.py` · `sim/isaac_worker.py` · `sim/shadow.py` · FIXED**

Threaded through four layers, because a backend only ever sees a `TaskConfig`:

1. `TaskConfig.checkpoint_id` — optional, so existing callers are unchanged.
2. `run_and_score` binds the `checkpoint_id` argument onto the task via
   `model_copy`, since a backend receives the task, not the run.
3. `MockIsaacBackend` *uses* it: `_checkpoint_offset` derives a deterministic
   per-checkpoint capability shift, quantised to five tiers.
4. The Isaac wire payload and the conformance fixtures now carry it.

**The control that proves the fix, run through the real API:**

| input | success | composite | verdict |
|---|---|---|---|
| ckpt-alpha | 0.577 | 75.20 | BLOCK |
| ckpt-beta | 0.723 | 83.43 | — |
| ckpt-gamma | 0.797 | 87.79 | APPROVE |

**Checkpoint spread 0.2300 vs task-name spread 0.0425** — a 5.4× separation in
the correct direction. Before the fix that inequality was *reversed*: meaningless
task labels moved the score more than the artifact under test. That inversion is
now a permanent regression test (`test_arbitrary_task_names_must_not_outspread_checkpoints`),
because it is the property that actually distinguishes the fix from noise.

Determinism is preserved exactly (same checkpoint twice → identical composite),
and regression detection between two checkpoints now yields a real delta
(−0.1767) rather than seed reshuffling.

**Caveat worth stating plainly:** the mock's per-checkpoint shift is a
*stand-in* for loading weights, not a simulation of it. It makes the mock
respond to the artifact under test, which is what unblocks testing every metric
computed from it — but it is not evidence that any real policy was loaded. Only
a GPU worker plus a behavioural check (two checkpoints, same seeds, different
results) can establish that, and the determinism fixture that would make such a
check meaningful now has a field to hang on.

### 51b · The mock backend is policy-invariant and physics-free
**`sim/runner.py:99-157` · VERIFIED · 1-2d · blocks all ML capability**

Five agents converged here from different angles. `MockIsaacBackend` ignores `scenario.params` and `scenario.category` entirely — four different param values yield **bit-identical** episodes. `min_human_distance_m` is a bare `rng.uniform` feeding 20 composite points of *noise* into a safety channel. `failure_mode` is drawn uniformly and independently of every observable.

**Why this is the highest-leverage item in the catalog:** until the mock varies with its inputs, **no AI/ML capability in this product is testable** — not scenario search, not learned failure classification, not scenario retrieval. Every ML feature would be validated against an oracle that returns the same answer regardless of input.

c6-uat named the general shape: the mock is *total*, so it can neither vary randomization nor under-deliver, leaving guards and metrics green but unexercised. That is a fault-injection gap.

### 51d · A stored verdict can be silently overwritten (SQLite)
**`store/sqlite.py:203` vs `store/postgres.py:414` · VERIFIED by me, independently · 4h**

`sqlite` uses `INSERT OR REPLACE`; `postgres` uses `ON CONFLICT (run_id) DO NOTHING`. The Postgres module docstring calls its own behaviour "append-only" and explicitly notes the SQLite divergence — **known in a comment, unenforced in code.**

Independently reproduced against the shipped default:
```
1st save : composite=95.0  decision=APPROVE
2nd save : composite=20.0  decision=BLOCK     # same run_id
```

**Why this is rated above the constant-`robustness` finding:** a mis-scored run misrepresents a *number*; this makes the *evidence record itself mutable*. The product's entire premise is that a recorded verdict is trustworthy, and on the default store a crash-and-retry can change it with no error and no log line.

Reachable because `actions/validate/action.yml` sets `VALIDSIM_STORE=sqlite` by default, and because the worker reuses `spec.run_id` on every attempt (`jobs/worker.py:281-289`).

**This is a contract decision, not a patch.** Pick one: append-only everywhere (match Postgres — an immutable verdict log, which is the stronger claim for a compliance-adjacent product), or last-write-wins everywhere (match memory/SQLite). The current split means the guarantee a customer gets depends on which backend they configured.

### 51c · The composite is exactly blind to nominal↔adversarial composition
**`config.py:138`, `engine/evaluation.py:67,76` · VERIFIED · 1d**

Two runs with the same adversarial share and the same pooled success (0.7504) score identically:

| run | nominal | adversarial | composite |
|---|---|---|---|
| A | 0.900 | 0.152 | 90.02 |
| B | 0.688 | 1.000 | 90.02 |

A robot excellent nominally and failing *every* adversarial test is indistinguishable to the gate from the reverse. Control: a genuine 10pp nominal drop moves the composite −3.20 points, so the 0.00 result is a property of the formula, not of the construction.

Compounded by `adversarial_count` defaulting to **0** (c5-integrations, c5-ml) and by the pool being operator-configurable up to 1,000 adversarial against 100,000 nominal — so **every adversarial episode can fail and the composite moves 0.40 points.**

**Highest value per hour:** floor `success_rate` over adversarial episodes only. `run_validation` already runs nominal then one-per-scenario, so the boundary exists at `pipeline.py:133` — about one day, no schema change, and it closes the blindness, the zero default and the tunability together.

### 51c-fix · The adversarial gate (implemented 2026-09-26)
**`engine/scorecard.py` · VERIFIED + controlled · FIXED**

The composite is unchanged — re-weighting it would silently move every existing
verdict. Instead the adversarial segment is split out and gated **separately**,
with the reason carried on the scorecard.

```
Run A: nominal 360/400 = 0.900 | adversarial   0/100 = 0.000
Run B: nominal 260/400 = 0.650 | adversarial 100/100 = 1.000
IDENTICAL pooled success : True
IDENTICAL composite      : True      <- the blind spot, verified
        A -> BLOCK   (adversarial floor)
        B -> APPROVE
```

**The control that shaped the design.** A naive hard floor at 60% broke two
existing tests, and the binomial arithmetic showed why it was wrong:

| n | P(X < 0.60n) at a true rate of 0.60 |
|---|---|
| 12 | 0.335 |
| 30 | 0.422 |
| 50 | 0.439 |

A bare `rate < floor` comparison would block **two in five healthy runs** —
training operators to ignore the gate. So the gate is a **one-sided exact
binomial test** (`P(X <= observed) < 0.05`) with a 30-episode minimum, which
holds the false-block rate near 5% and leaves small samples reported-but-not-gated.

Verified in both directions: blocks 0/100, 10/100 and 20/100; approves 70/100,
85/100 and 100/100; ignores 2/4, 7/12 and 12/20 as noise; leaves nominal-only runs
untouched.

**New scorecard fields** (`adversarial_episode_count`,
`adversarial_success_rate`, `block_reasons`) are surfaced in the CLI summary, and
persisted by both stores — including a fix to `sqlite.py`/`postgres.py`, whose
reconstruct functions enumerate fields explicitly and were silently resetting the
new ones to defaults on reload. A gate whose stated reason vanishes on read is
worse than one that never had it. Legacy rows still load via `.get` defaults.

### 52 · Composite is structurally blind to adversarial failure
**`config.py:138`, `engine/scorecard.py:34` · VERIFIED · 4h**

`adversarial_count` defaults to **0**, and the composite folds all episodes into one success rate. With 100 nominal + 100 adversarial episodes all failing, adversarial failure contributes 0.40 of 100 points — the same as if those episodes did not exist. Five agents reported this independently.

### 53 · `robustness_score` is a constant 100.0
**`engine/scorecard.py:53-66` · VERIFIED · 6h**

Single randomization group ⇒ `len(rates) < 2` ⇒ return `100.0`. It is 20 composite points that carry zero information, on every shipped code path. Making it vary requires the runner to actually vary randomization levels.

### 54 · Anomaly detector silently discards every brand-new failure mode
**`engine/anomaly.py:181-187` · VERIFIED · 2h**

A failure mode never seen before has zero baseline rate, zero spread, zero bootstrap SE, and zero binomial variance ⇒ `sigma <= _EPS` ⇒ `continue`. The most important signal — a completely novel failure — is dropped without a trace. Control: an *existing* mode spiking is still detected.

---

## Priority 2 — HIGH

| ID | Finding | Location | Status | Effort | Depends on |
|---|---|---|---|---|---|
| 55 | SQLite `save()` overwrites a verdict; Postgres refuses (`ON CONFLICT DO NOTHING`) | `store/sqlite.py:203`, `store/postgres.py:414` | VERIFIED | 4h | policy call || 56 | `checkpoint_sha256` accepted, never read, never persisted — no envelope→verdict binding | `config.py:154` | VERIFIED | 2d | 51 |
| 57 | Redis reaper reclaims **live** leases (Lua string-compare on mixed offsets) | `jobs/queue.py:187` | VERIFIED | 2h | — |
| 58 | All 23 `/api/v1` routes are sync `def` — liveness probe starves under load | `api/main.py:593,627` | VERIFIED | 4h | — |

### 51e · `bootstrap_ci` was 94% of every validation request
**`engine/stats.py:63-69` · VERIFIED + controlled · FIXED 2026-09-26 · 1d**

The single largest performance defect in the codebase, and the only one where a
control proved the cost was an implementation artifact rather than inherent.

`build_scorecard` calls `bootstrap_ci` on every run, on a synchronous route, so
its cost *is* the caller's wall clock. Stage breakdown at 20,000 episodes:

| stage | ms | share |
|---|---|---|
| simulate (`run_validation`) | 176.0 | 5.7% |
| evaluate | 2.5 | 0.1% |
| safety | 2.7 | 0.1% |
| **build_scorecard** | **2,890.5** | **93.5%** |

End-to-end `POST /api/v1/validations` was **3,093 ms** at 20k episodes, scaling
exactly linearly (2.00× per doubling) — so ~14.4 s at the `le=100000` the config
permits.

**The control that settles it:** the resample was built as
`[values[rng.randrange(n)] for _ in range(n)]` — one interpreted call *per
element*. Replacing it with `rng.choices(values, k=n)` draws the identical
distribution but generates indices in C:

| variant (n=20,000, 500 resamples) | ms |
|---|---|
| `randrange` per element | 2,929.9 |
| **`rng.choices`** | **544.5** |
| count-only | 611.0 |

**Result:** 3,093 ms → 1,980 ms end-to-end. The residual bottleneck moved to
`sort()` (73% of what remains), which is inherent to the percentile method.

**A correctness fix fell out of it.** The swap surfaced a latent defect: at
`n_resamples == 2` the interval is just `[min, max]` of two noisy resample
means, which can both land on one side of the point estimate — reporting an
interval that *excludes the value it was computed from*. The old sampler
happened to avoid it for the test's seed; the new one did not. Fixed by widening
the interval to the observed statistic, which is the mathematically honest
direction (it can only be less confident than the resample spread suggests) and
makes the property hold for **every** resample count. Pinned by a new test
sweeping 2 → 500.

> [!note] Residual, and why I stopped here
> `sort()` is now 73% of the remaining bootstrap cost. Optimising it means either
> a partial-selection algorithm or reducing `n_resamples` — and the latter
> changes the reported CI width, which is a confidence claim the product makes to
> users. That is a statistics decision, not a performance one, so it needs an
> explicit owner rather than a silent change.

### 51f · The regression permutation test makes baseline comparison 4× slower
**`engine/stats.py:128-140` · VERIFIED · open deliberately — see below**

`two_proportion_bootstrap_test` shuffles the whole pooled sample per permutation.
Supplying `baseline_run_id` — the documented regression-comparison flow — makes
`POST /api/v1/validations` roughly 4× slower (10,000 episodes: **1,007 ms →
3,977 ms**). `rng.shuffle` is 88% of the cost.

**I did not fix it, because the obvious fix is worse than the code.** I tried four
replacements:

| approach | ms @ n=20k | significance mismatches (24 cases) |
|---|---|---|
| current `rng.shuffle` | **6,819–8,523** | — |
| uniform draw over the support | 0.5 | **6/6 — WRONG** |
| exact hypergeometric via `rng.sample` | 8,124 | 0/24 |
| exact hypergeometric, per-slot Bernoulli | 5,668 | 0/24 |
| PMF table + `bisect` | **83,341** | 0/24 |

**The trap worth recording:** my first rewrite drew `ones_a` uniformly over its
support and ran in 0.5 ms — a 13,000× "speedup". Testing it against the real
statistic showed it flipped a significance verdict from `p=0.0010` (significant)
to `p=0.8681` (not significant). It was not the hypergeometric law. Shipping on
the benchmark alone would have silently converted a real regression into a
non-significant one — the exact failure this product exists to prevent.

The correct replacements are statistically identical (0/24) but **no faster**,
and the table-driven one is **12× slower** because `math.comb` on 40,000-argument
integers dominates. `rng.shuffle` is already a C-level primitive.

**The real lever is fewer permutations or a closed-form exact test** — both change
reported confidence, so both need an owner. Not a call to make silently.
| 59 | `JobSpec` has no `threshold`/`baseline_run_id` — async path drops caller intent | `jobs/models.py:27-47` | VERIFIED | 2h | 41 |
| 60 | `validsim health` prints "ok" and exits 0 on an unreachable store | `cli.py:609-624` | VERIFIED | 1h | — |
| 61 | Dashboard unreachable when a key is set (`GET /` returns 401 JSON) | `api/main.py:590,836` | VERIFIED | 1h | — |
| 62 | `make smoke` cannot pass — chains `gate` without a durable store | `Makefile:49` | VERIFIED | 1h | — |
| 63 | Duration regression invisible when baseline is `0.0 s` | `engine/regression.py:123` | VERIFIED | 1h | — |
| 64 | JSON logger serialises any `extra` field verbatim — no redaction layer | `logging.py:98-101` | VERIFIED | 1h | — |
| 65 | `TaskConfig.episodes` has no floor — a 1-episode run can return composite 100.0 | `config.py:136` | VERIFIED | 1h | 44 |
| 66 | Three shipped "controls" implemented and tested but never called | `config.py:62`, `config_loader.py:227`, `llm_generator.py:541` | VERIFIED | 1d | — |
| 67 | Notification layer is 100% library-only; docs mark it `[x]` | `notify/dispatcher.py:167` | VERIFIED | 1d | owner call |

---

## REFUTED — investigated and disproven

Recorded because a wrong finding costs as much as a missed one.

| Claim | Verdict |
|---|---|
| Human-proximity channel diluted by human-free episodes | **REFUTED.** The denominator is total episodes either way. Measured rate identical (0.01) with 1-of-100 vs 100-of-100 episodes containing a human. No dilution exists. |
| Unescaped reportlab markup → SSRF | **REFUTED.** 7/7 sinks show `outbound=0`; already fixed. |
| SMTP STARTTLS without cert verification | **REFUTED.** Already fixed; `verify_mode=CERT_REQUIRED`, `check_hostname=True`. |
| Non-ASCII supplied header → 500 | **REFUTED.** Already fixed; returns 401. |
| `runs/approvals/blocks_total` typed `counter` but decreasing (c5-platform P4) | **STALE.** I fixed the type to `gauge` earlier this session; re-checked and all three now emit `"gauge"`. |

> [!note] Reports were written against a moving tree
> The audit ran while fixes were landing, so a few findings were already stale when reported. Each was re-verified against the current file before entering this catalog; stale ones are listed above rather than silently dropped. **Re-verify any citation before acting on it** — `cli.py` line numbers shifted by ~37 mid-audit.

---

## Effort summary

| Priority | Items | Effort |
|---|---|---|
| P1 Critical — fixed | 3 | ~6h done |
| P1 Critical — open (policy) | 4 | ~3d |
| P2 High | 13 | ~5d |
| Refuted | 4 | 0 |

---

## What still needs your decision

1. **The checkpoint never reaches the backend (51).** The highest-value item in this catalog and a genuine architectural gap, not a bug. Fixing it means threading `checkpoint_id` through `TaskConfig` → the backend contract → the Isaac wire protocol.
2. **What a robustness score means when only one randomization level exists (53).** Options: vary levels per episode so it measures something real, or report "not measured" as 0 rather than 100. Both move the score distribution.
3. **Adversarial weighting (52).** Whether adversarial episodes should be scored separately rather than pooled into one success rate.
4. **Store write semantics (55).** Append-only verdict log (Postgres) vs overwrite (memory/SQLite). Pick one contract.
5. **Notification egress (67).** Wiring dispatch into the run path changes runtime egress behaviour and needs an owner.
