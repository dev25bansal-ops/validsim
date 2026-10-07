# c5-ml — AI/ML & Simulation-Fidelity Audit

**Agent:** c5-ml · **Date:** 2026-09-26 · **Scope:** scenario generation, the LLM path, the mock backend's suitability as a learning oracle, and the scorecard inputs an AI/ML capability would depend on.
**Mode:** read-only. No production file was edited. One probe script written outside the repo (`%TEMP%\c5ml_probe.py`).

**Why this lane exists:** the product's differentiation is adversarial testing, and its marketing describes LLM-generated adversarial scenarios. This audit asks a narrower question than "is the AI good enough" — **can any AI/ML capability be trained, validated, or demoed on this data at all?** Several answers are no, and those are the most important findings here.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| C5-01 | Default config runs zero adversarial episodes | `validsim/config.py:138` | **High** | VERIFIED | 1h | `probe F1` |
| C5-02 | 30 of 100 composite points are constants on the shipped CLI/worker path | `validsim/engine/scorecard.py:59-66,69-74` | **High** | VERIFIED | 2-4h | `probe F2` |
| C5-03 | Composite is *exactly* blind to nominal↔adversarial composition | `validsim/engine/evaluation.py:67,76` | **High** | VERIFIED | 2-4h | `probe F3` + control |
| C5-04 | Regression component grants 10 free points and is invisible | `validsim/engine/scorecard.py:69-74` | **High** | VERIFIED | 2h | `probe F2` |
| C5-05 | Approval floor is 62.5% success; failing 37.5% of episodes APPROVEs | `validsim/engine/scorecard.py:180-185` | **High** | VERIFIED | 1h | `probe F4` bisection |
| C5-06 | Mock backend ignores `params` and `category` entirely | `validsim/sim/runner.py:93-103` | **High** | VERIFIED | 4-6d | `probe F5` |
| C5-07 | `min_human_distance_m` is pure noise — a safety input to the score | `validsim/sim/runner.py:126-127` | **High** | VERIFIED | 1d | `probe F5` |
| C5-08 | Mock `failure_mode` is conditionally independent of all observables | `validsim/sim/runner.py:112` | **Medium** | VERIFIED | 4-6d | `probe F6` |
| C5-09 | API cannot set a threshold; ADR 0002 says it can | `validsim/api/main.py` | **Medium** | VERIFIED | 1h | `grep threshold` → 0 matches |
| C5-10 | LLM scenario generator is unreachable from every entrypoint | `validsim/engine/pipeline.py:131` | **Medium** | VERIFIED | 2h | `grep create_scenario_generator` |

---

## C5-01 — Default config runs zero adversarial episodes

**Severity** High · **Status** VERIFIED · **Effort** 1 hour

### Description
`TaskConfig.adversarial_count` defaults to `0`. The API builds `TaskConfig` directly from the request body, so any client that omits the field runs **no adversarial episodes at all** — while the scorecard reads as though it tested them.

### Repro
```python
from validsim.config import TaskConfig
TaskConfig.model_fields["episodes"].default          # 1000
TaskConfig.model_fields["adversarial_count"].default # 0
```
Output: `adversarial_count default = 0`. Check `[PASS] adversarial_count defaults to 0`.

The CLI disagrees with the API: `validsim/cli.py:299-301` defaults `--adversarial` to `24`, and the worker uses `JobSpec.adversarial` (`validsim/jobs/models.py:45`, also default `0`). So the two front ends do not agree on what a default run means.

### Expected vs actual
- **Expected:** a product whose differentiator is adversarial testing adversarially tests by default.
- **Actual:** default share is `0/(0+1000) = 0.0%`. The composite's sensitivity to adversarial results is **exactly zero** out of the box.

### Business impact
This is the precondition that makes C5-02 and C5-03 severe rather than theoretical. It also means the product's own marketing claim ("LLM-based adversarial scenarios, 50-100 edge cases") is not exercised by a default run.

### Dependencies
None. Changing the default is one line.

### Recommended fix
Default `adversarial_count` to a non-zero value, and/or make the API reject or warn on an explicit `adversarial_count: 0` when `POST /validations` is the entrypoint.

### Test strategy
Assert that a `TaskConfig` built from a bare `ValidationRequest` yields `adversarial_count > 0`; assert the scorecard reports both `nominal_episode_count` and `adversarial_episode_count` so the ratio is never implicit.

---

## C5-02 — 30 of 100 composite points are constants on the shipped CLI/worker path

**Severity** High · **Status** VERIFIED · **Effort** 2-4 hours

### Description
Two of the scorecard's four components are structurally constant, and both constants are the *maximum* (favourable) value.

- **Robustness (20 pts):** `_robustness_score` (`scorecard.py:59-66`) groups episodes by `randomization_level`, but `run_validation` (`runner.py:175,182`) assigns every episode the single value `task.randomization`. One group → `pstdev` of a single rate is 0 → component is always exactly `100.0`.
- **Regression (10 pts):** `_regression_component(None)` returns `100.0` (`scorecard.py:69-74`). `run_and_score` only receives `baseline_run_id` from the API; `cli.py:263-270` and `jobs/worker.py:281-289` never pass one.

### Repro
```python
run = run_and_score(task, "ckpt-A", ValidationStore(), threshold=85.0)  # no baseline
print(run.scorecard.robustness_score, run.regression)
```
Output:
```
robustness_score = 100.0  -> 0.2 x 100 = 20.0 pts
regression       = None   -> component 100 = 10.0 pts
composite        = 83.27
TOTAL CONSTANT   = 30.0 of 100
distinct randomization levels in a run == 1
```
All four checks PASS.

### Expected vs actual
- **Expected:** `composite = 0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression` (ADR 0002).
- **Actual on CLI/worker:** `composite = 0.4·success + 0.3·safety + 30.0`. **Only 70 of 100 points are earned by measurement.**

### Business impact
The effective approval bar is far weaker than ADR 0002's 85.0 implies (see C5-05). A run whose robot genuinely fails 37.5% of episodes is APPROVED. For an insurer-facing artifact, "the composite is 83.3" reads as a measurement of four things when it is a measurement of two plus two constants.

### Dependencies
The robustness fix requires threading scenario identity into grouping; the regression fix requires a baseline on the CLI/worker paths. **Both are separable from C5-03's mitigation, which ships today.**

### Recommended fix
Land per-channel floors first (C5-03 mitigation), then either group robustness by adversarial category or re-weight; and either thread a baseline through the CLI/worker or expose `regression_evaluated: false` so the 10 points are not silently credited.

### Test strategy
A CI invariant: for a fixed task, assert `robustness_score` is **not** always 100.0 across a set of runs with differing adversarial categories, and assert the count of distinct `randomization_level` values equals the number of groups actually varied.

---

## C5-03 — Composite is exactly blind to nominal↔adversarial composition

**Severity** High · **Status** VERIFIED · **Effort** 2-4 hours (mitigation), 2-4 days (full fix)

### Description
`evaluate()` pools nominal and adversarial episodes into one success rate (`evaluation.py:67,76`), and the composite reads only that pooled rate. A run where the robot is excellent nominally and fails every adversarial test is **indistinguishable** from one where the reverse holds, provided the pooled rate matches.

### Repro
Two runs, same share, wildly different composition:

| run | nominal rate | adversarial rate | pooled | composite |
|---|---|---|---|---|
| A | 0.900 | 0.152 | 0.7504 | 90.02 |
| B | 0.688 | 1.000 | 0.7504 | 90.02 |

```
[PASS] pooled rate is (near) identical
[PASS] composite moves 0.00 despite 21pp+21pp composition swap (delta +0.0000)
```

### Control (required — proves the repro is not a fixture artifact)
Same share, but a genuine 10pp nominal drop:
```
CONTROL nom=0.800 (pooled 0.6704) comp=86.82
[PASS] CONTROL: a 10pp nominal drop DOES move composite (delta -3.20)
```
**The composite responds to real performance change and is blind only to composition.** This is the control that distinguishes "the formula ignores composition" from "my construction was degenerate."

### Expected vs actual
- **Expected:** a product selling adversarial testing weights adversarial results meaningfully.
- **Actual:** adversarial results carry weight only through their *share* of the pool, which is an operator-set config flag. For equal-sized failures in each segment the sensitivity ratio is `(1-s)/s` where `s` is adversarial share — **parity at exactly 50%, reachable range 100.0x to 0.001x** given `episodes` 1..100000 and `adversarial_count` 0..1000. An operator can swing the gate's sensitivity to adversarial results across five orders of magnitude without touching model code.

### Business impact
This is the single most consequential finding in this lane. It means a checkpoint that trades nominal capability for adversarial robustness is **not detectably different** to the gate, and a checkpoint that fails every adversarial test is APPROVED whenever the adversarial share is small. Combined with C5-01 (default share 0.0%), the default product posture is a nominal-only gate wearing an adversarial label.

### Dependencies
Full fix requires the `scenario_id → outcome` join key (not mine; co-owned by c3-engine / c4-statistics / c4-sim2real). **The mitigation below does not.**

### Recommended fix
**Mitigation available today, no schema change:** floor `success_rate` computed over **adversarial episodes only**. `run_validation` (`runner.py:172-185`) already runs nominal episodes first, then one per scenario, so the boundary exists at `pipeline.py:133` and the per-segment rate is computable in-process today. Full fix: report per-segment success and apply per-segment floors, with no change to the formula or the weights.

### Test strategy
Property test: for any two configurations with equal pooled success and different nominal/adversarial composition, assert the scorecards are **not** equal once per-segment floors exist. Control: assert equal-composition runs with different pooled rates **do** produce different composites.

---

## C5-04 — Regression component grants 10 free points and is invisible

**Severity** High · **Status** VERIFIED · **Effort** 2 hours

### Description
A run with `regression=None` receives the maximum 10 composite points. The persisted scorecard contains `regression_delta` but **no** `regression_evaluated`, **no** `block_reasons`, and **no** `baseline_run_id`:

```
scorecard keys: [checkpoint_id, composite_score, confidence_interval, created_at,
                 deploy_decision, episode_count, failure_taxonomy, regression_delta,
                 robustness_score, run_id, safety_score, success_rate, task_id, threshold]
  regression_evaluated? False    block_reasons? False    baseline_run_id? False
JobSpec fields: ['run_id','checkpoint_id','task_id','episodes','adversarial']
  has 'baseline_run_id'? False
```

### Repro
`run_and_score(...)` with no baseline → `run.regression is None` (probe F2). `JobSpec` field list from `jobs/models.py:41-45`. Scorecard keys from `scorecard.py:86-111`.

### Expected vs actual
- **Expected:** either the regression test ran and found nothing, or the scorecard says it did not run.
- **Actual:** `regression_delta = None` is the only signal, and it reads as **"no change detected."** This is a value *shaped like a measurement* that is not one — the same signature as a counter field carrying a constant.

Compounding it: `JobSpec` has no `baseline_run_id` field, so **the worker path structurally cannot gate on regression at all.** A user deploying via `validsim worker` believes they are getting regression gating and are not.

### Business impact
For a compliance artifact, "regression: no change" and "regression: never evaluated" must be distinguishable. Today they are not, and the second case silently pays the maximum.

### Dependencies
`regression_evaluated` must be **defaulted** — `notify/email.py:70-80` rebuilds `Scorecard(**kwargs)`, and `store/sqlite.py` / `store/postgres.py` rebuild `EpisodeResult(**e)`, so a required field breaks historical reads.

### Recommended fix
Add a defaulted `regression_evaluated: bool` to `Scorecard`, plus a **non-gating** `caveats[]` channel listing what the score did not measure, rendered in the report/PDF/dashboard but never consulted by the gate. **Do not** put it in `block_reasons[]` — that is the explanation of *why a run blocked*, and writing to it would flip every CLI/worker verdict retroactively.

### Test strategy
Assert `regression_evaluated is False` whenever `baseline_run_id is None`; assert the caveats channel is populated in that case and that the gate decision is byte-identical with and without it.

---

## C5-05 — Approval floor is 62.5% success; failing 37.5% of episodes APPROVEs

**Severity** High · **Status** VERIFIED · **Effort** 1 hour

### Description
With the two constants from C5-02, the gate approves a checkpoint that fails more than a third of its episodes, provided those failures are not safety violations.

### Repro
Bisection on `deploy_decision` over 1,000 nominal episodes with perfect safety:
```
lowest APPROVEing nominal successes (adversarial_count=0) = 625.0/1000
[PASS] measured floor matches 62.5%
```
Algebraically, on the shipped formula: `composite = 0.4·success% + 0.3·100 + 30.0 ≥ 85` → `success% ≥ 55/0.4 = 137.5`… expressed as a fraction of the success component the floor is `55/40 = 62.5%` of episodes. The bisection is the authority; the closed form is a cross-check.

### Expected vs actual
- **Expected:** ADR 0002's 85.0 threshold, calibrated on a four-component formula.
- **Actual:** a checkpoint failing **37.5% of all episodes** is APPROVED. ADR 0002's own trade-offs section anticipates this class ("a weighted sum can mask a catastrophic single-axis failure") and says floors may be added "if a real incident demands it."

### Business impact
This is the number a customer would be denied on. It should be stated plainly wherever the threshold is quoted, with the +30 constant's origin attached, or a reader comparing 62.5% to 85.0 will think the arithmetic is wrong.

### Dependencies
Directly downstream of C5-02.

### Recommended fix
Frame as **closing a documented-requirement gap** (ADR 0002 lines 96-100), not a new feature — that framing is much easier to approve. Per-channel floors on `success_score` and `safety_score` are computable today with zero schema work.

### Test strategy
Table-driven: for a grid of success rates and safety scores, assert `deploy_decision` matches the documented formula including floors.

---

## C5-06 — Mock backend ignores `params` and `category` entirely

**Severity** High · **Status** VERIFIED · **Effort** 4-6 days CPU

### Description
`MockIsaacBackend.success_probability` (`runner.py:93-103`) computes `p = base − randomization_penalty − difficulty*0.5`. It never reads `scenario.params` or `scenario.category`. Mutating a scenario's parameters produces **bit-identical** `EpisodeResult`s.

### Repro
Same seed, `difficulty=0.6`, four injected `human_distance_m` values:
```
human_distance_m=0.1   -> min_human_dist=0.112 success=True force=28.04 dur=8.287
human_distance_m=0.42  -> min_human_dist=0.112 success=True force=28.04 dur=8.287
human_distance_m=0.9   -> min_human_dist=0.112 success=True force=28.04 dur=8.287
human_distance_m=1.2   -> min_human_dist=0.112 success=True force=28.04 dur=8.287
[PASS] all 4 param values give bit-identical EpisodeResults (got 1 distinct)
```
And success rate is identical across all 12 categories:
```
distinct success rates across 12 categories = [0.4185]
[PASS] success rate is category-invariant
```

### Expected vs actual
- **Expected:** the mock is a stand-in that reproduces the *observable contract*, including that harder conditions produce different outcomes.
- **Actual:** the mock is a **policy-invariant, physics-free oracle** — it has perfect information about one hand-tuned scalar (`difficulty`) and none about the robot. Eleven of twelve categories are indistinguishable; `human_proximity` is the only one that conditions any observable, and even there the *value* is random (C5-07).

### Business impact
**This blocks every data-driven capability in the product's AI/ML lane.** Evolutionary adversarial search, learned failure classification, and embedding-based scenario retrieval are all meaningless on data with no learnable signal. An evolutionary search here converges on `difficulty=0.95` and reports a "worst case" that is an artifact of `_RANDOMIZATION_PENALTY` plus a uniform draw — trivially falsifiable by any reviewer who reads `runner.py`.

### Dependencies
None — pure CPU engine work. Interacts with the checkpoint-representation decision (not mine) for Tier-2+ realism.

### Recommended fix
Make the mock consume `params` via a **typed** profile rather than a raw `dict[str, Any]`, so the dependency is visible in the signature and a second backend physically cannot ignore it. Accurate framing for the report: it has perfect information about `difficulty` and none about the robot — without that clause "no information" is arguably false, since the mock *is* deterministic.

### Test strategy
**Property test, not an example test:** for every axis in the profile, assert there exists a pair of parameter values that changes the outcome distribution. An example test proves one param moved one observable; the property version means the mock can never silently regress to ignoring an axis.

---

## C5-07 — `min_human_distance_m` is pure noise — a safety input to the score

**Severity** High · **Status** VERIFIED · **Effort** 1 day

### Description
For `human_proximity`, `runner.py:126-127` sets `min_human_distance_m = rng.uniform(0.05, 0.9)` — it ignores the injected `human_distance_m` entirely. This field feeds `compute_safety`'s proximity channel (`safety.py:88-91`), which carries weight `0.2`, i.e. **20 of 100 composite points**.

### Repro
From probe F5: `human_distance_m=0.1` (human 10cm away, a severe condition) reports `min_human_distance_m = 0.112`, and `human_distance_m=1.2` reports the **same** `0.112`. The reported observable is not merely uninformative — at the extreme it is not self-consistent with the request.

### Expected vs actual
- **Expected:** the proximity observable reflects the injected distance, so the safety score responds to human-proximity risk.
- **Actual:** the safety score's proximity channel is a random draw. **A safety metric is contributing noise to 20% of the composite.**

### Business impact
This is the more alarming half of C5-06, because it is a *safety* channel rather than a success channel. A run can lose up to 20 composite points to a random number, and the same seed is the only thing making it reproducible.

### Dependencies
Part of the C5-06 fix; the typed profile should make the mapping explicit.

### Recommended fix
Derive `min_human_distance_m` from the injected `human_distance_m` with a documented, deterministic offset model (e.g. approach dynamics), rather than a uniform draw.

### Test strategy
Property test: monotonically increasing `human_distance_m` must not decrease the reported `min_human_distance_m`; and the proximity-violation rate must increase as `human_distance_m` approaches the 0.5 m limit.

---

## C5-08 — Mock `failure_mode` is conditionally independent of all observables

**Severity** Medium · **Status** VERIFIED · **Effort** 4-6 days

### Description
`runner.py:112` draws `failure_mode = rng.choice(FAILURE_MODES)` — uniform over 7 modes, independent of the scenario, the params, and the outcome. So the label carries no information the observables could predict.

### Repro
17,508 failed episodes across all 12 categories:
```
P(collision>0) = 0.4483   P(collision==0) = 0.5517
CONSTANT majority-class predictor (reads NO observable) = 0.5517
7-way Bayes ceiling, collision_count>0 = 0.2920
7-way Bayes ceiling, +force>50N       = 0.2954
7-way Bayes ceiling, +duration>15s    = 0.3908
```

### The key distinction
The widely-quoted **~53–55% "classifier ceiling" is not a ceiling at all** — it is exactly `max(P(collision>0), P(collision==0))`, which is what a **constant** predictor scoring the majority class achieves *while reading no observable whatsoever*. The genuine 7-way failure-mode ceiling is **29–39%**, against the ~95% a usable classifier needs and a 14.3% uniform baseline.

### Expected vs actual
- **Expected:** failure modes correlate with the conditions that caused them.
- **Actual:** a classifier cannot detect a failure mode that has no features. This is a **data-generation limitation, not a modelling gap** — no model, rule-based or learned, can beat the Bayes rate for a label that is conditionally independent of the features.

### Business impact
Any "AI-powered failure classification" claim built on this data would be fabricated. The number to quote in any document is the **conclusion** ("the mock's failure taxonomy is conditionally independent of its observables"), not a point estimate — a single figure is reproducible-differently by a reviewer depending on the decision rule and feature set.

### Dependencies
Requires C5-06 first: conditioning outcomes on params is a precondition for any label to be predictable at all.

### Recommended fix
Sample `failure_mode` conditionally on the scenario and the observed failure mechanics, not uniformly.

### Test strategy
Assert per-mode mutual information against scenario category exceeds a floor; assert per-mode F1 is reported with a published confusion matrix, never aggregate F1 alone (a 7-class model can look strong on aggregate while being useless on the commercially important modes).

---

## C5-09 — API cannot set a threshold; ADR 0002 says it can

**Severity** Medium · **Status** VERIFIED · **Effort** 1 hour

### Description
ADR 0002 states the threshold is "overridable per run (`--threshold`, the API, and the gate command)." It is overridable on the CLI (`cli.py:306,324`) and in the worker (`jobs/worker.py:76,97`), but:

```powershell
# grep -c threshold validsim/api/main.py
0
```

`ValidationRequest` (`config.py:141-162`) has no threshold field and `_execute_validation` omits the kwarg, so every API run is gated at the `DEFAULT_THRESHOLD = 85.0` of `pipeline.py:55`.

### Expected vs actual
- **Expected:** the documented three-way override.
- **Actual:** two-way. A documented-contract-vs-behaviour gap in the same class as C5-01.

### Business impact
A customer integrating via API cannot set their own risk bar, and the ADR asserts they can. Any threshold-adaptation work is also blocked behind this, since adapting a threshold you cannot set is meaningless.

### Dependencies
None.

### Recommended fix
Add a `threshold` field to `ValidationRequest` and pass it through `_execute_validation`. One field plus one kwarg.

### Test strategy
Assert a `POST /validations` with an explicit threshold persists that threshold on the scorecard, and that the CLI/API/worker defaults agree.

---

## C5-10 — LLM scenario generator is unreachable from every entrypoint

**Severity** Medium · **Status** VERIFIED · **Effort** 2 hours (decision) / 1 day (wiring)

### Description
`create_scenario_generator()` (`llm_generator.py:541`) is the env-driven factory that selects the LLM backend. Grepping the production tree:

```
validsim/scenarios/__init__.py:11,17,24,30   (re-export)
validsim/scenarios/llm_generator.py:5,8,47,48,314,541,543,546,556  (definition)
```

**No production caller.** `pipeline.py:131` hardcodes the rule-based generator:
```python
scenarios = ScenarioGenerator(seed=seed).generate(task.task_id, task.adversarial_count)
```
So setting `VALIDSIM_LLM_API_KEY` changes nothing about an actual validation run. The LLM path is **shipped code that is unreachable** — not a proposal, and not exercised by any entrypoint.

### Expected vs actual
- **Expected:** the "LLM-based adversarial scenarios" claimed in `MVP Scope` W4, `Tech Stack`, `Product Vision` #2 and `Solution Architecture` L2 to be reachable when configured.
- **Actual:** dead code behind an env var. Meanwhile the rule-based fallback's own docstring notes the DI pattern is applied to `run_validation` and `create_backend` but not to the generator — a one-line omission at `pipeline.py:131`.

### Business impact
Two risks. First, the docs assert a capability no run exercises. Second, `llm_generator.py` holds the prompt inline inside the `validsim` package tree, which `pyproject.toml` ships (`packages = {find = {include = ["validsim*"]}}`) and which the CLI is slated to open-source under MIT — so the IP Strategy's designated trade-secret methodology sits one directory from publication. (Related: `docs/adr/0005:108-109` cross-references ADR 0004 for "scorecard format," but ADR 0004 is the store-abstraction ADR; ADR 0002 owns the scorecard format.)

### Dependencies
None technically. The decision is whether to wire it or delete it.

### Recommended fix
Make it an explicit decision rather than an accident of import wiring. If the LLM path stays, inject the generator through the same seam as the other two collaborators and record prompt provenance on the scorecard. If it goes, remove it and correct the four documents that claim it ships. **My recommendation is to keep the LLM only where it is defensible — as a failure-mode *explainer*, structurally barred from touching the score, with a citation gate so it can only cite episodes it was given — and not as the generator.**

### Test strategy
If wired: assert that with `VALIDSIM_LLM_API_KEY` set, `run_and_score` records a non-null `generation_provenance` (prompt id, prompt sha256, model, temperature) on the `Scorecard` — defaulted, for the `Scorecard(**kwargs)` constraint in C5-04. If deleted: assert no module imports it.

---

## False leads — ruled out, with what killed them

1. **"The permutation test is wrong on continuous data."** Refuted by an independent textbook reference: `sum(pooled[:na])/na` *is* the mean, and p-values matched to 4 decimals across tiny (0.002) and huge (1e6) magnitudes. The misleading `total_ones`/`ones_a` variable names are cosmetic; production is binary at the only call site. *(Not mine — ruled out and reported so the claim doesn't resurface.)*

2. **"The job queue's fencing epoch is broken after a reap."** Refuted by execution: a dead worker's late `_finish` and `renew_lease` are both rejected in **both** windows (queued, and re-claimed), because `update_status` requires `status is RUNNING` and the epoch increments monotonically on claim. The "epoch unchanged by reap" property is harmless given monotonicity plus an independent status gate.

3. **"A shared `binomialvariate` fast path would speed up the gate."** Refuted as a *correctness* matter: the permutation null conditions on the pooled total, so it is hypergeometric, not binomial, and no binomial path substitutes for it. The scorecard CI is display-only and never enters the decision, so swapping its RNG stream would change persisted numbers for no gain.

4. **"Success rate varies by adversarial category."** Refuted: identical to six decimal places across all 12 categories (`[0.4185]`). This was my own earlier working assumption and it was wrong.

5. **"The composition-blindness repro might be a fixture artifact."** Killed by the required control in C5-03: a genuine 10pp nominal drop moves the composite by −3.20 points on the same construction, so the 0.00 result for composition swaps is a real property of the formula.

---

## Assumptions

- Severity is judged against the product's stated purpose (CI/CD validation gating for robot foundation models), not generic library quality.
- "Constant composite points" is computed on the CLI/worker path (`no baseline_run_id`), verified end-to-end through the real `run_and_score`.
- The reachable share range (0.99%–99.90%) uses `episodes` 1..100000 and `adversarial_count` 0..1000 as enforced by `config.py:136,138`.
- All probe measurements were re-run on 2026-09-26 after other agents' edits, and all checks passed.

## Recommended sequence

| # | Action | Effort | Unblocks |
|---|---|---|---|
| 1 | **Floor `success_rate` over adversarial episodes only** — computable today at `pipeline.py:133` | 1 day | C5-01, C5-03, C5-05 |
| 2 | Per-channel floors + surface `nominal_episode_count`/`adversarial_episode_count` | 1 day | C5-02, C5-05 |
| 3 | Defaulted `regression_evaluated` + non-gating `caveats[]` | 1 day | C5-04 |
| 4 | `threshold` field on `ValidationRequest` | 1 hour | C5-09 |
| 5 | Decide: wire or delete the LLM generator | 1 day | C5-10 |
| 6 | **Make the mock consume `params` via a typed profile + property test** | 4-6 days | C5-06, C5-07, C5-08, and the whole AI/ML roadmap |
| 7 | Per-segment reporting + per-category floors | 2-4 days | Full C5-03 fix (needs the join key) |

Item 1 is the highest value per hour in this entire lane, and item 6 is the highest value per day: until the mock has signal, no AI/ML capability in this product is testable.
