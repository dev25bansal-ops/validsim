# Audit Report — cat4-moat

**Agent:** `c4-moat` · **Date:** 2026-09-26 · **Scope:** scoring semantics, simulation-backend contract, failure taxonomy, adversarial testing
**Mode:** read-only. No production files edited. One probe script in `%TEMP%` only.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| **F1** | Two of four composite terms are constant on the CLI/worker paths (+30.0 pts) | `validsim/engine/scorecard.py:59-66` | **Critical** | VERIFIED | 1–2 days | `python $env:TEMP/c4moat_report_probe.py` → F1, F2 |
| **F2** | Gate **APPROVES** a checkpoint failing 100% of its adversarial suite | `validsim/engine/scorecard.py:154-163` | **Critical** | VERIFIED | 2–4 days | probe → F3, F4 |
| **F3** | Isaac wire contract has **no checkpoint field** — worker cannot know which policy to run | `validsim/sim/isaac_worker.py:519-527` | **Critical** | VERIFIED | 1–2 days | probe → F5 |
| **F4** | `EpisodeResult` has no `scenario_category` — blocks F1's fix and all per-scenario analytics | `validsim/sim/runner.py:42-63` | **High** | VERIFIED | ~1 day | probe → F7 |
| **F5** | Mock backend ignores `scenario.params`; safety observable is RNG-drawn | `validsim/sim/runner.py:103,126-127` | **High** | VERIFIED | 4–6 days | probe → F6 |
| **F6** | `duration_s` is *simulated* time and is **anti-correlated with quality** | `validsim/sim/runner.py:131-136` | **Medium** | VERIFIED | ~1 day | probe → F10 |
| **F7** | Mock failure labels contradict the observables (7-way ceiling 29–38%) | `validsim/sim/runner.py:111-113` | **Medium** | VERIFIED | blocked on worker | probe → F8 |
| **F8** | Category-grouped robustness re-reads `_BASE_DIFFICULTY` (R²=0.993) | `validsim/scenarios/generator.py:37-50` | **Medium** | VERIFIED | with F1's fix | probe → F9 |

**8 findings.** All VERIFIED with a control case. Reachability proven for all (`validsim run` → `_run_impl` → `run_and_score`; `validsim gate` → stored `composite_score`).

---

## F1 — Two of four composite terms are constant on the CLI/worker paths

**Severity** Critical · **Status** VERIFIED · **Effort** 1–2 days

### Description
`_robustness_score` groups episodes by `EpisodeResult.randomization_level`. But `run_validation`
stamps **every** episode — nominal and adversarial alike — with the single value
`task.randomization`. So there is always exactly one group, and the function returns `100.0` via
its `len(rates) < 2` early exit.

Separately, `_regression_component` returns 100 when `regression is None`, and **only the API
passes `baseline_run_id`** (`cli.py:300-306` and `jobs/worker.py:281-289` both omit it). So on the
CLI and async-worker paths, `robustness` and `regression` are *both* constant:

```
composite = 0.4·success + 0.3·safety + 0.2·100 + 0.1·100 = 0.4·success + 0.3·safety + 30.0
```

### Reproduction
```powershell
$env:PYTHONPATH="d:/SIM-TO-REAL"; python "$env:TEMP\c4moat_report_probe.py"
```
Output:
```
EVIDENCE groups={'full'} n=1001
EVIDENCE robustness=100.0  (expected 100.0)
EVIDENCE control_mixed_robustness=52.86  (expected <100)
EVIDENCE success=0.950 safety=100.0 robustness=100.0 composite=98.0
EVIDENCE reconstructed=98.00 == composite -> constant terms = 30.0 pts
```

**Control (required):** a hand-built *mixed* episode list returns `52.86`, not 100. The function
is capable of discriminating; the runner never gives it a mix. This is not a fixture artefact.

### Expected vs actual
- **Expected:** robustness measures cross-condition consistency; the composite responds to episode count.
- **Actual:** robustness is always exactly 100.0 from the runner; the composite is `0.4·success + 0.3·safety + 30.0`; **the exact approval floor is `(85−30−30)/0.4 = 62.5%` success at perfect safety** — so a checkpoint failing **37.5% of all episodes is APPROVED** at the default threshold if its failures are not safety violations.

### Business impact
`Pricing Tiers` design rule #2 sells *"episodes scale with trust"* and gates the Team→Pro upgrade on
episode count. **Two of the four axes a customer pays for are provably constant**, so the upsell
does not move the number on those axes. This is a pricing-integrity problem, not merely a
documentation gap.

### Reachability
`validsim run` → `cli.py:300` `run_and_score(...)` with no `baseline_run_id` → `scorecard.py:154-163`.
Same for the async job worker. This is the path the GitHub Action and the demo video use.

### Recommended fix
Change the grouping key from `randomization_level` to **`scenario_category`** (12 values, present on
adversarial episodes, `None` for nominal). Requires F4 first. Must be **gated off on mock-backend
output** — see F8.

### Test strategy
Assert `robustness < 100.0` for a run containing adversarial episodes across ≥2 categories. That
test fails today. Add a second asserting the composite differs between two episode counts on the
CLI path.

---

## F2 — Gate APPROVES a checkpoint failing 100% of its adversarial suite

**Severity** Critical · **Status** VERIFIED · **Effort** 2–4 days

### Description
`build_scorecard` scores **one flat `episodes` list**. `run_validation` appends nominal *and*
adversarial episodes into that same list, and `evaluate()` counts them all with no split. The
composite therefore cannot distinguish "passed 1,000 nominal, failed 500 adversarial" from
"passed 1,500 nominal."

Combined with F1, the gate decides on success and safety alone.

### Reproduction
```powershell
python "$env:TEMP\c4moat_report_probe.py"   # section F3
```
Output (100% nominal success, **0% adversarial success**):
```
EVIDENCE adv_share=   1% success=0.990 composite= 99.22 -> APPROVE
EVIDENCE adv_share=   5% success=0.952 composite= 96.24 -> APPROVE
EVIDENCE adv_share=  10% success=0.909 composite= 92.82 -> APPROVE
EVIDENCE adv_share=  17% success=0.855 composite= 88.52 -> APPROVE
EVIDENCE adv_share=  30% success=0.769 composite= 81.77 -> BLOCK
EVIDENCE control_all_fail composite=56.45 -> BLOCK
```

**Control (required):** when *nominal* also fails, the gate correctly BLOCKs at 56.45. So the gate
is not simply permissive — it is blind to the adversarial segment specifically.

### Expected vs actual
- **Expected:** a checkpoint that fails every adversarial test is blocked.
- **Actual:** APPROVE up to ~17–30% adversarial share.

### The structural form (stronger than the ratio)
`adversarial_count` maxes at 1,000 while `episodes` maxes at 100,000 (`config.py:136,138`), so the
adversarial weight is `(adv / (nominal + adv))` — **whatever fraction the operator allocated, on a
config whose caps make that fraction arbitrarily small:**
```
EVIDENCE episodes= 100000 adv=1000 share=0.99% cost_of_total_adv_failure=0.40 pts
```
At the maximum legal episode count, **every adversarial episode can fail and the composite moves
0.40 points** against a threshold of 85. And because of F1, success is the only moving component —
so the adversarial share is the *sole* lever an adversarial result has, and it is operator-controlled.

**This is not a tuning miss. A safety-critical test suite can configure itself out of the score.**

### Business impact
The product's headline differentiator is adversarial testing. **The CI gate does not currently
gate on adversarial results.** Leading a fundraising narrative with adversarial-testing
differentiation while this holds is the largest credibility exposure found in this audit.

### Reachability
`validsim gate` → `cli.py:483-486` reads `card.composite_score` against the threshold. `run`/`validate`
print the same decision at `cli.py:327`. The GitHub Action fails the job on that exit code.

### Dependencies
F4 (needs `scenario_category`) for the real fix; **a mitigation exists that needs nothing.**

### Recommended fix — two tracks
| | work | blocked on | effort |
|---|---|---|---|
| **Mitigation** | Floor on success rate computed over **adversarial episodes only** — the nominal/adversarial boundary already exists at `pipeline.py:133`, since `run_validation` runs nominal episodes then one per scenario. No schema change, no wire change. | **nothing** | **~1 day** |
| **Real fix** | `adversarial_success_rate` as a first-class scored component with its own weight and CI | F4 | 2–4 days |

**The fix is a floor, not a weight change.** Raising `_W_ROBUSTNESS` or adding a safety weight
redistributes a fixed 100 points and changes nothing about the defect. The defect is that
adversarial episodes have *no guaranteed representation*. Frame this as a **scoring-semantics
decision with a re-baseline cost** — historical scorecards become incomparable, so thresholds and
any published benchmark need re-baselining.

### Test strategy
Assert BLOCK when adversarial success rate is 0 regardless of nominal success. Add a parametric
sweep over adversarial share (1%→30%) pinning the flip point, so a future re-weighting cannot
silently move it.

---

## F3 — Isaac wire contract has no checkpoint field

**Severity** Critical · **Status** VERIFIED · **Effort** 1–2 days

### Description
`IsaacWorkerBackend._build_payload` emits exactly seven keys and **none is the checkpoint**.
`SimulationBackend.run_episode` likewise has no checkpoint parameter. `docs/isaac-worker.md`
mentions "checkpoint" **once**, at line 151, in shadow-run prose — nowhere in the §2 request schema,
the episode response, or §3 semantics.

### Reproduction
```powershell
python "$env:TEMP\c4moat_report_probe.py"   # section F5
```
```
EVIDENCE payload_keys=['task_id','robot','environment','seed','episodes','randomization_level','scenarios']
EVIDENCE has_checkpoint=False
EVIDENCE run_episode_sig=(self, task, seed, randomization_level, scenario=None) -> EpisodeResult
EVIDENCE doc_isaac_worker_checkpoint_mentions=[151]
```

### The structural proof (preferred over any statistic)
`checkpoint_id` appears ~63 times across `validsim/`, but in exactly **three** roles:
1. a **seed input** — `pipeline.py:130` `stable_seed(checkpoint_id, task.task_id)`
2. a **display label** — `cli.py:282`, `engine/export.py:96`, `engine/pdf.py:78`
3. a **store column / index** — `store/sqlite.py:39,52`, `store/postgres.py:83,91`

**There is no fourth role. The checkpoint's only causal path to the score is
`checkpoint_id → stable_seed → seed → backend`** — control flow, not statistics.

### Expected vs actual
- **Expected:** the worker receives the policy under test.
- **Actual:** a worker built to the documented contract has no way to know which policy to run. Every episode would return default behaviour.

### Business impact
The product would be sold on a scorecard that **cannot rank policies**, at full GPU spend. This is
upstream of every other GPU-path finding.

### The conformance harness cannot detect it
`reference_contract_cases()` (`sim/shadow.py:394-452`) asserts types, taxonomy, seed echo, and
episode count. **Every fixture passes against a contract with no checkpoint field, because none can
express the omission.** A worker could be fully conformant, pass the §5 step-1 promotion gate, and
be structurally incapable of validating a checkpoint. **Conformance would certify the defect.**

Compounding: `ShadowRunner` compares only an **aggregate** success-rate delta against
`_DEFAULT_TOLERANCE = 0.15` (`sim/shadow.py:71`) with `_DEFAULT_EPISODES = 20`. At n=20 the sampling
noise is already ~0.1, so 0.15 is roughly 1.5σ — **a params or checkpoint defect is invisible at
the aggregate level by construction.**

### Reachability
`VALIDSIM_BACKEND=isaac` → `create_backend()` (`sim/__init__.py:55-57`) → `IsaacWorkerBackend`.
The entire documented GPU path.

### Recommended fix
Add a `checkpoint` field (URI or resolved path) to the §2 request schema; require the worker to
echo it per episode; add a checkpoint round-trip conformance fixture. Superseding ADR required —
this is a wire-contract change.

### Test strategy
1. **Checkpoint round-trip:** worker must echo the checkpoint it was given.
2. **Params round-trip:** scenarios at `human_distance_m=0.1` and `1.2` must produce observably
   different `min_human_distance_m`.
3. **Per-scenario comparison, not aggregate:** complement the ±0.15 band with a per-category
   outcome diff, because aggregate comparison cannot see this class of defect.

---

## F4 — `EpisodeResult` has no `scenario_category`

**Severity** High · **Status** VERIFIED · **Effort** ~1 day

### Description
The category lives on `AdversarialScenario.category` and is consumed inside `run_validation`, but
is **never copied onto the episode**. The primary key for per-scenario analytics does not exist on
the record being analysed.

### Reproduction
```
EVIDENCE EpisodeResult_fields=['episode_id','task_id','seed','success','collision_count',
        'max_contact_force_n','min_human_distance_m','failure_mode','duration_s',
        'joint_states_summary','randomization_level']
EVIDENCE has_scenario_category=False
```

### Expected vs actual
- **Expected:** each episode records the condition it ran under.
- **Actual:** only `randomization_level`, which is a per-task constant (F1).

### Business impact
Blocks F1's fix, F2's real fix, and any weakness mining, retrieval index, or per-category
benchmark. **One schema addition unblocks all three.**

### Dependencies / wire-contract note
`isaac_worker.py:63` derives `_EPISODE_FIELDS` from the dataclass, and `_episode_from_dict`
(`:216-229`) rejects **both unknown and missing** fields. So a defaulted field is still a
**mandatory key on the wire** — a breaking change, but a loud one. All new fields must be
**defaulted**: `notify/email.py:70-80`, `store/sqlite.py:117`, and `store/postgres.py:254` all
reconstruct objects from kwargs/JSON.

**Two ADR requirements:** (1) name the *transition* — a one-release grace period accepting both
shapes, then a hard cutover with an explicit `episode_schema_version`; (2) **a worker returning
`null` for every category would silently drop the robustness grouping back to one group,
reproducing F1 invisibly** — the validator should accept null (legitimately null for nominal
episodes) while the engine asserts the adversarial subset has categories present.

### Test strategy
Differential test: identical success rate across all 12 categories must score ≈100 robustness.
Quantitative variant: assert correlation with `_BASE_DIFFICULTY` on mock is **below a threshold**
(R² < 0.3) — a uniform-category check alone passes trivially and would not catch F8.

---

## F5 — Mock backend ignores `scenario.params`; safety observable is RNG-drawn

**Severity** High · **Status** VERIFIED · **Effort** 4–6 days

### Description
`success_probability` (`:99-103`) computes `p = base − penalty − 0.5·difficulty` and **never reads
`params`**. `run_episode` *does* branch on `category` (`:126-127`), changing the band from
`uniform(0.05, 0.9)` to `uniform(0.6, 2.5)` — so category is not inert — but the **specified
`human_distance_m` is discarded and re-drawn from the RNG.**

### Reproduction
```
EVIDENCE human_distance_m=0.1   -> min_human_distance_m=0.24 (requested value NOT reflected)
EVIDENCE human_distance_m=0.42  -> min_human_distance_m=0.24
EVIDENCE human_distance_m=0.9   -> min_human_distance_m=0.24
EVIDENCE human_distance_m=1.2   -> min_human_distance_m=0.24
EVIDENCE distinct_episode_tuples_across_4_param_values=1
EVIDENCE distinct_tuples_across_3_mass_values=1
EVIDENCE wire_sends_params=[{'id':'h','category':'human_proximity',
                            'params':{'human_distance_m':0.1},'difficulty':0.6}]
```

**Control:** the wire contract **does transmit `params`**, so a real worker is expected to honour
them. The mock accepting and discarding a field is a **silent parity divergence**.

### Expected vs actual
- **Expected:** the simulated condition reflects the requested scenario.
- **Actual:** bit-identical output across a 12× parameter sweep. **A specified value outside 0.05–0.9 cannot be represented at all** — a scenario asserting "human at 1.2 m" is silently simulated as something in that band.

### Business impact
`min_human_distance_m` feeds `prox_violation_rate` → `safety_score`. **The safety observable for
the one category whose entire purpose is human proximity is RNG-drawn rather than
scenario-driven.** And because mock-derived scores are a **different measurement** rather than a
conservative approximation of Isaac-derived scores, every mock-produced number is uninformative
about the real system. Combined with F1 and F8, **the mock cannot produce a meaningful robustness
measurement by any route.**

### Recommended fix
Make the mock read `mass_kg` / `friction` / `lux` / distance, not just `difficulty`. **4–6 days,
CPU-only, no GPU.** This is also the prerequisite for a *calibrated* mock later (re-fit
`base_success_rate` and `_RANDOMIZATION_PENALTY` against measured reality).

### Test strategy
Params round-trip (see F3). Any exported or benchmarked metric must record
`sim_backend`/`sim_backend_version` so mock-derived numbers are never presented as measured.

---

## F6 — `duration_s` is simulated time and is anti-correlated with quality

**Severity** Medium · **Status** VERIFIED · **Effort** ~1 day

### Description
`duration_s` is sampled from the outcome branch: `rng.uniform(18,35)` for a timeout failure,
`rng.uniform(4,12)` for a success. It is **not wall-clock compute**, and it is **not independent of
the outcome.**

### Reproduction
```
EVIDENCE timeout_duration_range=(18.1,34.9) n=158
EVIDENCE success_duration_range=(4.0,12.0) n=2856
```

**Control:** the ranges are disjoint, confirming the coupling is by construction rather than
incidental.

### Expected vs actual
- **Expected:** a cost/throughput metric measures compute.
- **Actual:** cost-per-episode computed on `duration_s` is **anti-correlated with quality** — the worst-performing policies look the cheapest. There is also **no compute-time instrumentation anywhere** in the engine (timers exist only in HTTP middleware and an SSE deadline).

### Business impact
A published cost benchmark built on this field would be an *inverted* metric, not merely imprecise.
Instrument `wall_clock_s` / `gpu_s` / `compute_device` as **defaulted** fields before any cost or
throughput claim. Never report `duration_s` as a cost metric.

### Test strategy
Assert a run's `wall_clock_s` is recorded and non-zero on the Isaac path; assert mock-path timings
are labelled mock-derived.

---

## F7 — Mock failure labels contradict the observables

**Severity** Medium · **Status** VERIFIED · **Effort** blocked on the GPU worker

### Description
`failure_mode = rng.choice(FAILURE_MODES)` is drawn independently of every observable, then
`collision_count` is derived *from* the mode for only 3 of 7 modes, with a 5% random-collision
branch injecting collisions into the other 4. So all 7 labels appear in both observable branches.

### Reproduction
```
EVIDENCE failures=1144 modes_when_coll_eq_0=4/7 ['joint_limit','perception_error','timeout','unstable_placement']
EVIDENCE modes_when_coll_gt_0=7/7
EVIDENCE uniform_baseline=0.1429
EVIDENCE ceiling_7way_collision_only=0.2911
EVIDENCE ceiling_7way_plus_duration=0.3776
EVIDENCE constant_predictor_2way_max=0.5385 (reads NO observable)
```

**Control:** the ~54% figure is a **constant majority-class predictor** that reads no observable —
it is not a classifier ceiling. The genuine 7-way ceiling is **29–38%** against a **14.3%** uniform
baseline. (State the decision rule with the number; a bare percentage is reproducible-differently
by a reviewer.)

### Expected vs actual
- **Expected:** failure labels are inferable from observables, so a classifier can learn them.
- **Actual:** labels are mutually contradictory with the observables. **Any accuracy figure produced on mock data is fabricated by accident.**

### Business impact
No failure-classification or weakness-mining claim is publishable until the real worker lands. Note
also that only ~5 of 7 modes have any supporting signal in `EpisodeResult` at all — `perception_error`
and `grasp_failure` have no trajectory, no contact-state trace, and no commanded-vs-achieved pose.

### Test strategy
Per-mode precision/recall against injected known failures, on the real backend only.

---

## F8 — Category-grouped robustness re-reads `_BASE_DIFFICULTY`

**Severity** Medium · **Status** VERIFIED · **Effort** with F1's fix

### Description
`MockIsaacBackend`'s success probability is `base − penalty − 0.5·difficulty` **by construction**,
and `difficulty` is seeded from `_BASE_DIFFICULTY[category]`. So a category-grouped robustness score
on the mock is very nearly a **lookup of our own hyperparameter table wearing a cross-condition label**.

### Reproduction
```
EVIDENCE R2_base_difficulty_vs_mock_success=0.9926 n_per_cat=300
```

### Expected vs actual
- **Expected:** the metric measures the policy's cross-condition consistency.
- **Actual:** on mock it restates `scenarios/generator.py:37-50`.

**Control/caveat:** the reported R² is a property of the **mock**, not of the proposed fix. The
fix is correct for a real backend; it is the *mock* that is degenerate. Any exporter or benchmark
touching `robustness` must carry a mock-backend guard.

### Business impact
Without a guard, a category-grouped robustness number derived from mock runs would look like a
meaningful cross-condition measurement while being circular.

### Test strategy
Quantitative guard: assert `R²(robustness, _BASE_DIFFICULTY) < 0.3` on mock output. A
uniform-category test passes trivially and would not catch this.

---

## False leads

Things I examined and ruled out — recorded so they are not re-litigated:

1. **`two_proportion_bootstrap_test` is "binary-only" / mislabelled — REFUTED.** I previously
   reported this. It is **correct for any real-valued input**: `total_ones = sum(pooled)` is
   invariant under permutation, so `T − s_a` *is* the complement sum, and
   `s_a/na − (T−s_a)/nb` **is** the difference of means of the permuted split.
   Measured `max_abs_diff=8.882e-16` vs an explicit `fmean(a')−fmean(b')` over 20,000 permutations on
   continuous non-binary data. Only the *variable names* are binary-flavoured. **A raising
   "binary precondition" guard would be actively harmful** — it adds a failure mode inside the
   APPROVE/BLOCK path for input that is correct today. Residual is cosmetic renaming only.

2. **"The composite-CI is meaningless because two terms are constant" — partially refuted.** The CI
   is computed on success rate alone (`scorecard.py:167-171`), so it is *not* inflated by F1. The
   real problem is that a precise interval around a success rate is being reported while the
   *verdict* ignores the adversarial segment (F2). Different defect, same area.

3. **"Statistical power per tier is misstated" — resolved, not a defect.** Effective per-arm n is
   `episodes + adversarial_count` (pooled; `evaluate()` never splits). MDE at 80% power, p₀=0.90:
   Free 14.72pp, Team 2.65pp, Pro 1.64pp, Enterprise 1.15pp. **Pro and Enterprise clear the 2pp
   target** — so "all paid tiers underpower" is false and must not be claimed. Caveat: the pooled
   rate mixes two true probabilities, so true MDE is somewhat worse.

4. **"A checkpoint-label sensitivity test proves the checkpoint is unused" — inconclusive, dropped.**
   My control group (1 checkpoint × 6 task_ids: stdev 1.15) did not reproduce an earlier reported
   magnitude (2.90) at n=6 single-seed runs. Too noisy to carry a claim. **F3's structural argument
   (only causal path is the seed) replaces it entirely** and needs no sample size.

5. **`LLMScenarioGenerator` is dead code — confirmed but out of scope for this report.**
   `pipeline.py:131` hardcodes `ScenarioGenerator(seed=seed)`, so the LLM path is never invoked.
   Real, but a product-completeness observation rather than a scoring defect.

---

## Assumptions

1. `VALIDSIM_BACKEND` defaults to `mock` (`sim/__init__.py:55`); all F1/F5/F7/F8 measurements are
   on the deterministic mock. **Findings about the real Isaac worker are inferred from the wire
   contract and client validator, not from execution** — no worker image exists in this repository.
2. The default gate threshold is 85.0 (`scorecard.py:135`, `cli.py:343`).
3. `Pricing Tiers` episode counts (Free 1K / Team 5K / Pro 10K / Enterprise unlimited) are read from
   `vault/06 - Business/Pricing Tiers.md:18-21` and used only for the MDE table in False Lead #3.
4. F3's severity assumes the documented §2 schema is the contract a worker would be built against.
   If a private, undocumented payload exists, F3's *wire* claim weakens — but F3's *Protocol* claim
   (no checkpoint parameter on `run_episode`) stands regardless.
