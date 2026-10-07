---
tags:
  - business
  - moat
  - strategy
  - engineering
status: active
created: 2026-09-25
area: "06 - Business"
---

# 🔧 Technical Moat Features — Converting Claimed Advantages into Defensible Code

Analysis date **2026-09-25**. Operational deadline **YC W27, 2026-11-02 8pm PT (≈5.4 weeks)**.
Companion to [[Moat]], [[Strategic Advantages]], [[IP Strategy]].

> [!warning] Status of this note
> This is an engineering-strategy proposal grounded in a read of the shipped code as of
> 2026-09-25. It distinguishes **what is claimed in the vault** from **what is implemented**,
> and several of the gaps below are load-bearing for the fundraise narrative. Effort estimates
> are founder-weeks for the existing 2-founder team.

---

## 0. Executive verdict

> [!danger] ELEVEN VERIFIED DEFECTS THAT UNDERCUT THE MOAT NARRATIVE
> All four were **verified by execution or by reading the shipped source on 2026-09-25**,
> not inferred. They are ordered by severity. Each is cheaper to fix than to explain, and
> each is a place where a sophisticated buyer, competitor, or insurer finds the gap themselves.
>
> **D1 — The MIT open-source plan would publish the moat.** `IP Strategy` §18.2 claims the CLI
> is "a thin client over the proprietary API" and that "the scenario generator is the company."
> **Neither is true of the current code.** `validsim/cli.py::_run_impl` calls `run_and_score(...)`
> **in-process**, and `actions/validate/action.yml` does `pip install .` — so the Action ships
> the entire monorepo. MIT publication would irreversibly disclose the composite weights
> (`_W_SUCCESS=0.4`, `_W_SAFETY=0.3`, `_W_ROBUSTNESS=0.2`, `_W_REGRESSION=0.1`), the safety
> weights, the 7-mode taxonomy, and `scenarios/generator.py` itself. The hosted API does not
> exist yet (the Action's own description: "the hosted SaaS backend is coming"). See §10.
>
> **D2 — RESOLVED 2026-09-29 (scoring). The robustness metric is still a constant, but it no
> longer buys score.** `runner.py` still stamps every episode — nominal *and* adversarial — with
> the single value `task.randomization`, so `scorecard.py` always sees exactly one group.
> **Verified by execution:** a 250-episode run (200 nominal + 50 adversarial) produced
> `groups: {'full'}`. Previously `_robustness_score` took a `len(rates) < 2 → 100.0` early exit,
> so the effective shipped formula was `0.4·success + 0.3·safety + **20.0** + 0.1·regression` and
> the term bought nothing.
>
> **The scoring fix:** an unmeasured component now *abstains* — its weight leaves the composite
> denominator rather than contributing a fabricated 100 (`docs/adr/0002`). A run with no
> cross-condition evidence is now scored on what was actually measured, and a run where **every
> episode failed** no longer clears the gate (it was 60.0 → APPROVE at threshold 60; now 42.86 →
> BLOCK). The scorecard reports `robustness_measured: false` either way.
>
> **Still open — the metric itself.** Robustness remains unmeasurable on a normal run, so the
> axis is still inert: `Pricing Tiers` sells "episodes scale with trust," and more episodes still
> cannot move robustness, because the fix removes the *credit* for that silence without
> introducing the *variation* that would make the metric informative. Closing it requires varying
> `randomization_level` across episodes in `sim/runner.py` (or shipping a real cross-condition
> sweep) so more episodes buy genuine cross-condition evidence. **This is the highest-value
> remaining scoring work and it is a product decision, not a bug** — see §0.1.
>
> The D3 / CLI-worker variant of this finding (regression also inert when no baseline is passed,
> which `cli.py` and `jobs/worker.py` never do) is fixed by the same abstention rule: the
> `0.1·regression` term now leaves the denominator instead of adding 10.0.
>
> **D3 — 2 of 7 failure modes have no detector behind them.** `EpisodeResult` carries no
> trajectory, no contact-state trace, no commanded-vs-achieved pose. So `perception_error` and
> `grasp_failure` are **labels with no observable signal**. Worse, `MockIsaacBackend` samples
> `failure_mode = rng.choice(FAILURE_MODES)` — uniform random, unrelated to the scenario
> injected. Mining "weaknesses" from mock data is mining noise, and a benchmark leaderboard
> built on it would be a permanent, dated, public record of meaningless numbers. See §2.
>
> **D4 — No compute-time instrumentation exists, so no cost claim is measurable.** Timing
> instrumentation exists only in HTTP middleware (`api/main.py` `time.perf_counter`) and an SSE
> deadline (`jobs/router.py`). `EpisodeResult.duration_s` is **simulated episode time** — how
> long the robot's motion took — not wall-clock compute. Conflating the two in a published
> benchmark is instant, total credibility loss. See §8.
>
> **Also verified (credit `c4-statistics`):** the product ships **zero sim-to-real
> instrumentation** — no `EpisodeResult` field, no store column, no scorecard field carries
> real-world outcome. We sell sim-to-real transfer while measuring no sim-to-real gap. This is
> the largest defensibility hole in the product and it gates the one item `c4-statistics`
> identifies as the single true structural moat (conformal prediction). See §14.

> [!danger] D9 — THE GATE APPROVES CHECKPOINTS THAT FAIL **100% OF THE ADVERSARIAL SUITE**
> The most serious finding in this document, and it lands squarely on the company's headline
> differentiator. Because D2 makes two of four composite terms constant, the gate decides on
> success and safety alone. **Verified by execution** on the CLI/worker path (100% nominal success,
> **0% adversarial success**): composite **92.82 → APPROVE** at 10% adversarial share, **88.52 →
> APPROVE** at 17%, **81.77 → BLOCK** only at 30%. The gate does not flip until ~17–30% of the run
> is adversarial — and the CLI/worker paths are exactly the paths the `validsim run` demo and the
> GitHub Action use.
> **The approval floor is exactly 62.5% success at perfect safety** — derivable in one step from the
> CLI/worker formula `0.4·s + 0.3·safety + 30.0`: `s ≥ (85 − 30.0 − 30.0)/0.4 = 55/0.4 = 62.5`.
> (An earlier 62.45% figure was a finite-sample bisection artefact and is withdrawn.) So a checkpoint
> failing **37.5%** of all episodes is APPROVED at threshold 85 if the failures are not safety violations.
> **So the product sells adversarial testing without gating on it.** The fix is not more episodes —
> it is **per-segment scoring** (`adversarial_success_rate` as its own component, or an adversarial
> floor). Detail in §0.1. **This outranks the benchmark and every other moat item pre-Nov 2.**
>
> **The mechanism, isolated (verified) — and the structural form is far stronger than a ratio.**
> The 2.17× figure below is a *ratio at one configuration*, and it understates the defect badly:
>
> ```
> episodes     adv   share   cost of TOTAL adversarial failure
>      500     24    4.58%   1.83 pts
>     1000    100    9.09%   3.64 pts
>    10000   1000    9.09%   3.64 pts
>   100000   1000    0.99%   0.40 pts
>
> for reference, degrading 10% of nominal in a 524-episode run: 3.97 pts (2.17x the 24-ep case)
> ```
>
> **The accurate claim is not "adversarial is somewhat underweighted." It is that adversarial
> testing has NO GUARANTEED INFLUENCE on the score at all.** `adversarial_count` maxes at 1,000 while
> `episodes` maxes at 100,000 (`config.py:136,138`), so the adversarial share is
> `(adv / (nominal + adv))` — **whatever fraction of the budget the operator happened to allocate,
> on a config whose caps make that fraction arbitrarily small.** At the maximum legal episode count,
> **every adversarial episode can fail and the composite moves 0.40 points** against a threshold of
> 85. A run would have to be within 0.4 points of the line for the gate to notice.
> **A policy can fail every adversarial test it ran and still clear the gate.**
>
> And success is the *only* moving component: robustness and regression are constants (D2), so the
> adversarial share is the *sole* lever an adversarial result has on the verdict — and that lever
> is operator-controlled.
>
> **Direct consequence for the differentiation claim:** any assertion that "ValidSim catches
> adversarial failure a plain CI misses" is **undermined by our own gate.** That belongs in the risk
> section, not buried — and fixing it is the *strongest* possible demonstration of the moat, because
> it means our scorecard can certify something the customer's own CI structurally cannot.
>
> **⚠️ Dependency, flagged by `c5-ml` — D9 is NOT one piece of work, it is two, and the cheap one
> should ship first regardless of D9's rank.** The *real* fix (adversarial-subset scoring) is
> blocked on the `scenario_category` join key (D7), which needs a worker-contract change. But a
> **cheap mitigation is available immediately and closes most of the hole:**
>
> | | work | blocked on | effort |
> |---|---|---|---|
> | **Mitigation** | Per-channel floors, computed from the nominal/adversarial boundary that **already exists** at `pipeline.py:133` | **nothing** | **~1 day** |
> | **Real fix** | `adversarial_success_rate` as a first-class scored component with its own weight and CI | D7 (`scenario_category` on `EpisodeResult`) + wire change | 2–4 days |
>
> **Do not read D9's rank as "one thing."** Someone picking it up should ship the mitigation on day
> one and the real fix when D7 lands. `c4-compliance` is escalating D9 as **currently unowned and
> blocking** — it needs an owner who touches `build_scorecard` / `_robustness_score` / the
> `regression` weighting, because **every compliance feature on their side is downstream of it.**
>
> **The fix is a FLOOR, not a weight change** (`c5-integrations`, and this is the important
> distinction). Not "raise `_W_ROBUSTNESS`" or "add a safety weight" — those redistribute a fixed
> 100 points and change nothing about the defect. The defect is that **adversarial episodes have no
> guaranteed representation**, so the structural fix is a **minimum adversarial share** (or
> expressing the budget as a ratio rather than two independent integers). The reasoning: **a
> safety-critical test suite should not be able to configure itself out of the score.**
> **Frame this to the founders as a scoring-semantics decision with a re-baseline cost — the same
> shape as the `caveats[]` question — not as an engineering tweak, because it changes what every
> stored scorecard means.** Historical scores become incomparable, so the threshold and any
> published benchmark need re-baselining when it lands.

> [!danger] D12 — THE MOCK **IGNORES `scenario.params` ENTIRELY** (zero-information objective)
> Found by `c4-sim2real`; verified by me and it is worse than "uninformative." Holding the seed
> fixed and varying the requested parameters produces a **bit-identical `EpisodeResult`**:
>
> ```
> human_distance_m = 0.1 / 0.42 / 0.9 / 1.2  ->  min_human_distance_m = 0.24   (all four)
> mass_kg         = 0.02 / 1.294 / 5.0      ->  success=False coll=3 force=76.63 dur=15.474 (all three)
> ```
> **Distinct `EpisodeResult` tuples: 1 in both sweeps.** Only `difficulty` — and `category`, for
> `min_human_distance_m` — affect the output. And the one *safety* observable the contract mandates
> for `human_proximity` (`runner.py:127` returns `rng.uniform(0.05, 0.9)`) **ignores the requested
> distance entirely**: asking for 0.1 m returns 0.24 m. The mock is not even self-consistent with
> its own request.
> `failure_mode` is likewise **unsteerable**: `P(failure_mode == collision) = 0.1450` against a
> uniform 0.1429 — so **a `P(failure_mode == X)` fitness term is provably unsteerable against the mock.**
>
> **Why this is worse than a parity nit.** The wire contract **does transmit `params`** — verified:
> `_scenario_payload` emits `{"id", "category", "params", "difficulty"}` — so the real Isaac worker is
> *expected* to honour them. A backend that accepts a field and silently ignores it is a **silent
> parity divergence**, the same class of defect as D10.
>
> **Consequence for §4 (the synthesis loop):** an evolutionary search over the mock converges to
> `difficulty ≈ 0.95` and reports a "worst case" that is an artifact of `_RANDOMIZATION_PENALTY` plus
> a uniform draw. **Do not ship that as a discovery capability** — it is the easiest thing in this
> audit for a technical buyer to break, and the moat argument collapses on one read of `runner.py`.
> **The "no LLM needed" advantage survives; the claim must be "we own the search infrastructure,"
> not "we find worst-case failures," until a real simulator exists.**
>
> **The fix is cheap, CPU-only, and unblocks the whole loop: make the mock read `params`**
> (`mass_kg` / `friction` / `lux` / distance, not just `difficulty`). **4–6 days, no GPU.** This is
> the prerequisite for a *calibrated* mock later (re-fit `base_success_rate=0.9` and
> `_RANDOMIZATION_PENALTY` against measured reality) and it is what makes the closed loop
> **demonstrable end-to-end on CPU today.** See §4 for the revised phasing.

> [!danger] D5 — MOCK FAILURE LABELS ARE **CONTRADICTORY**, NOT MERELY UNINFORMATIVE
> `runner.py:111-113` draws `failure_mode = rng.choice(FAILURE_MODES)` independently of every
> observable, then derives `collision_count` *from* the mode for only 3 of 7 modes, with a 5%
> random-collision branch injecting collisions into the other 4. **Verified over 4,000 episodes:**
> all 7 modes appear when `collision_count > 0`; 4 of 7 appear when it is 0. So the labels are
> mutually contradictory with the observables — a `collision_count == 0` episode labelled
> `collision` is a labelled contradiction, not noise.
> **Therefore no failure-classification or weakness-mining claim is publishable until the real Isaac
> worker lands**, and any accuracy number produced on mock data is fabricated. Hard guardrail, not
> a caveat.
>
> **Reconciled ceiling (joint measurement by `c4-statistics`, `c5-ml`, and me, 2026-09-25;
> corrected twice).** The figure is **decision-rule dependent**, so the *rule* must be stated with
> the number — a bare percentage is reproducible-differently by a reviewer, which is the exact
> problem we are trying to avoid. Measured independently by all three of us:
>
> | decision rule | ceiling | what it is |
> |---|---|---|
> | uniform random (1/7) | **14.3%** | baseline |
> | most-common-label | **14.5%** | constant predictor |
> | 7-way, `collision_count>0` | **28.4 / 30.0 / 29.2%** | the real classifier ceiling |
> | 7-way, `+ duration>15s` | **37.2 / 39.6 / 39.0%** | the real classifier ceiling |
> | 2-way, `P(not collided)` | **53.8 / 53.8 / 57.0%** | **constant** predictor — *not* the classifier ceiling |
>
> **State it as: "7-way failure-mode ceiling ~30–40% depending on feature set, vs ~95% required, vs
> 14.3% uniform."** The ~53–57% cluster is a **different decision rule** (a constant
> collided / not-collided majority-class guess, inflated further by `c4-statistics`'s original
> 53.4%, which was itself a 2-way rule). **Do not merge the two ranges** — writing "30–55%" implies a
> 7-way classifier could reach 55%, which it provably cannot. The underlying property survives
> intact: **all 7 mock labels appear in both observable branches, so the labels are mutually
> contradictory with the observables, and the mock cannot teach failure classification.**

> [!danger] D6 — THE ROBUSTNESS FIX IS **CORRECT BUT SELF-TAUNTING ON MOCK DATA**
> Grouping robustness by `scenario_category` is the right fix, but `MockIsaacBackend`'s success
> probability is `base - penalty - 0.5·difficulty` by construction, so a category-grouped
> robustness metric on the mock **re-measures ValidSim's own `_BASE_DIFFICULTY` constants**.
> A category-grouped robustness score on mock would look like a meaningful cross-condition metric
> while being very nearly a **lookup of our own hyperparameter table wearing a cross-condition
> label**.
> **The fix must be gated off on mock-backend output**, and the validation test must be
> **quantitative, not just a uniform-category check** (which passes trivially here and would not
> catch this): assert the metric's correlation with `_BASE_DIFFICULTY` on mock is **below a
> threshold** (e.g. R² < 0.3).
>
> **Reconciled R² (2026-09-25).** Three measurements disagreed because each was taken at a
> different `n` per category, and R² rises with n:
>
> | n per category | R² |
> |---|---|
> | 70 | 0.895 |
> | 200 | 0.885 |
> | 500 | 0.948 |
> | 1,000 | 0.972 |
>
> **Honest statement: R² > 0.88 at every sample size tested, approaching 0.97 at 1,000/category.**
> `c4-statistics`' 0.75 was measured at only 70 episodes/category and reported without stating n;
> my 0.993 was at the high end. Both were understating the converged value.

> [!danger] D7 — `EpisodeResult` DOES NOT CARRY `scenario_category` (the blocking schema gap)
> The category lives on `AdversarialScenario.category` (`generator.py:67`) and is consumed inside
> `run_validation` (`runner.py:177-185`) but **never copied onto the episode**. So the primary
> key for the D2/D9 fixes and the §1 flywheel **does not exist on the record being mined**.
> `scenario_category` (plus `difficulty`) must be added to `EpisodeResult` first; one schema
> addition unblocks the robustness fix, per-segment scoring, the flywheel, and the composite-CI
> redesign.
>
> **The wire contract forces the shape** (`c3-engine`, verified): `isaac_worker.py:63` derives
> `_EPISODE_FIELDS` from the dataclass, and `_episode_from_dict` (`:216-229`) rejects **both
> unknown and missing** fields. So a **defaulted** field is still a **mandatory key** on the wire
> (only its *value* may be null). There is no separate-envelope option — it is a breaking wire
> change, and that is good, because it makes the contract change **loud rather than silent.**
>
> **Two things the ADR must name:**
> 1. **The transition.** Isaac-path runs stored before the upgrade hard-fail on replay (their rows
>    lack the key). Recommend **(a) a one-release grace period** accepting both shapes while
>    logging which schema each reply used, **then (b) a hard cutover** with an explicit
>    `episode_schema_version` in the request envelope. The ADR should name the *transition*, not
>    just the final state.
> 2. **The category must be echoed, and a null-everywhere response is a silent reversion.** If a
>    worker returns `null` for every episode, the robustness grouping silently drops the whole
>    Isaac path back to "one group" — **reproducing the exact bug we are fixing, but invisibly.**
>    The validator should treat a null category as valid (it is legitimately null for *nominal*
>    episodes) while the **engine asserts the adversarial subset has categories present.** Getting
>    this wrong trades a loud bug for a quiet one, which is the worst outcome available.
>
> **Also note the read-path constraint** (`c4-statistics`, confirmed): `notify/email.py:70-80` does
> `Scorecard(**kwargs)`, and `store/sqlite.py:117` / `store/postgres.py:254` both do
> `EpisodeResult(**e)` from stored JSON. A *required* field would break three read paths, so
> **`scenario_category: str | None = None` is the only safe shape.**

> [!info] D8 — **RETRACTED. This was not a defect; the claim was wrong and is withdrawn.**
> Originally asserted: "`two_proportion_bootstrap_test` is a mislabelled binary-only permutation
> test, publicly exported." **That is incorrect and is struck from this document.**
> `c5-integrations` retracted the original claim after `c5-ml` refuted it by executable test; I
> re-derived and re-verified independently: `total_ones = sum(pooled)` is **invariant under
> permutation**, so `T - s_a` *is* the complement sample's sum, and
> `s_a/na - (T - s_a)/nb` **is** the difference of means of the permuted split. Measured gap
> against an explicit `fmean(a') - fmean(b')` over **100,000 permutations on continuous
> (non-binary) data: 8.882e-16** — pure floating-point roundoff, and still 2.3e-10 at magnitude
> 1e6. **The arithmetic is correct for any real-valued input; only the variable *names* are
> binary-flavored.**
> **My recommended fix was also wrong, and specifically the one to avoid.** I proposed "an
> explicit binary precondition check." `c5-ml`'s counter-argument is decisive: **a raising
> precondition adds a new failure mode inside the APPROVE/BLOCK gate path for input that is
> correct today.** A guard that can raise in the deploy-gating path is strictly worse than the
> naming confusion it prevents.
> **Residual action, P3-cosmetic only:** rename `total_ones` → `total_sum`, `ones_a` → `sum_a`,
> and correct the docstring/`__all__` to "permutation test on the difference of means." No
> runtime check. **Nothing in the pre-Nov-2 sequence.**

> [!danger] D10 — THE POSTGRES STORE **CANNOT SUPPORT A GAPLESS CHAIN** YET (blocks §1 and `c4-compliance`'s ledger)
> Found by `c4-compliance`; verified by me in source. `PostgresValidationStore` opens a
> **`autocommit=True`** connection (`store/postgres.py:368`) guarded only by a **process-local
> `threading.Lock`** (`:327`, and `with self._lock` at `:430,437,450,460,489,504,511,517`).
> A `threading.Lock` **cannot serialise across processes**, and the API and the job worker are
> **separate processes** (ADR 0003). So a `SELECT head → INSERT` sequence for an append-only
> chain **races** between them: two processes can read the same head and both append, forking the
> chain or producing two rows claiming the same predecessor.
> **Consequence for my §1:** `episode_outcomes` **must not ship as append-only** until a
> transactional append path exists. The specified fix (`c4-compliance` + `c3-data`): transactional
> append with **`pg_advisory_xact_lock`** on the tenant/org id, inserts that **fail** rather than
> `ON CONFLICT DO NOTHING`, and a **gapless monotonic `seq` as PK**. **This is not implemented.**
> Shipping `episode_outcomes` append-only before it lands would create exactly the failure mode
> `c4-compliance` named for their ledger: two divergent append-only tables whose inconsistency
> stays invisible until an auditor finds it.
> **So §1 is now explicitly sequenced behind this**, and it is a shared prerequisite with the
> audit ledger — worth solving **once**, properly, for both.

---

## 0.1 D2 in detail: the robustness constant, and why it is a revenue bug

Found by `c4-statistics`, verified by execution. The chain:

1. `config.py:138` — `TaskConfig.randomization` is a single `RandomizationLevel`
   (`"none" | "partial" | "full"`), one value per task.
2. `sim/runner.py:175` (nominal) and `:182` (adversarial) both stamp
   `randomization_level=task.randomization` — **the same value for every episode in a run.**
3. `engine/scorecard.py` — `_robustness_score` groups episodes **by `randomization_level`**.
   With one group there is nothing to measure, so the component is reported unmeasured.
4. Net effect on the **API** path (which passes a baseline): the composite is a weighted mean
   over the measured components only, so robustness contributes nothing to either numerator or
   denominator.

> [!danger] D2 was worse than 20 points — **30 points on the CLI and worker paths** *(historical)*
> `regression` was `None` (→ component 100) whenever no baseline was supplied, and **only the API
> passes one.** `cli.py:263-270` and `jobs/worker.py:281-289` both omit `baseline_run_id`, so on
> the two primary execution paths (the CLI and the async job worker) the regression term was *also*
> constant. **Verified by execution:** a 1,000-episode run with no baseline reconstructed exactly as
> `0.4·success + 0.3·safety + **30.0**`.
> **So on CLI/worker runs, two of four composite components were structurally inert** — and those
> are the paths the GitHub Action and the `validsim run` demo use.
> (Credit: `c5-ml`, found and verified by `c4-statistics`.)
>
> **All 30 of those points are now withdrawn** by the abstention rule: both components abstain
> rather than scoring 100, so a CLI run with no baseline and one randomization group is scored on
> success and safety alone. **The pricing-integrity problem is fixed in the scoring** — a customer
> no longer pays for 4× episodes that move a flat +30.0.
>
> **What remains is the metric, not the score.** Robustness is still unmeasurable, so more
> episodes still cannot *move* it; the abstention removes the credit for that silence without
> creating the variation that would make the axis informative. Closing that needs
> `randomization_level` to actually vary across episodes (`sim/runner.py`), so the upsell buys
> genuine cross-condition evidence. **Still open.**

**Why this mattered beyond a documentation gap.** `Pricing Tiers` design rule #2 is "episodes
scale with trust, not just compute — 1K → 5K → 10K → unlimited mirrors the statistical-confidence
story." Those two terms used to contribute a **flat +30.0 to every CLI/worker scorecard regardless of
episode count**. A customer who upgraded from Team (5K episodes) to Pro (10K episodes) paid 4×
for nothing on those axes. The upsell motion was not connected to the metric it claimed to
move — a pricing-integrity problem, not just an honesty problem. The scoring half is fixed; the
metric half is §0.1's remaining open item.

**The approval floor this creates.** Because two of four terms are constant, the gate approves on
success and safety alone. **The approval floor is exactly 62.5% success at perfect safety**, derivable
in one step: `0.4·s + 0.3·100 + 30.0 ≥ 85` ⇒ `s ≥ 55/0.4 = 62.5`. (An earlier 62.45% figure was a
finite-sample bisection artefact and is withdrawn.) A checkpoint that fails **37.5% of all
episodes** is APPROVED at threshold 85, provided its failures are not safety violations.

> [!danger] And the consequence that should stop the adversarial-testing narrative cold
> **A checkpoint that fails 100% of its adversarial suite is APPROVED.** Verified by execution on
> the CLI/worker path (100% nominal success, 0% adversarial success):
>
> | adversarial share of run | success | safety | composite | decision |
> |---|---|---|---|---|
> | 1% | 0.990 | 98.71 | 99.22 | **APPROVE** |
> | 5% | 0.952 | 93.81 | 96.24 | **APPROVE** |
> | 10% | 0.909 | 88.18 | 92.82 | **APPROVE** |
> | 17% | 0.855 | 81.11 | 88.52 | **APPROVE** |
> | 30% | 0.769 | 70.00 | 81.77 | BLOCK |
>
> The gate does not flip until ~17–30% of the run is adversarial. So **the product's headline
> differentiator — adversarial testing — is not actually gated on the CLI/worker paths**, which
> are precisely the paths the `validsim run` demo and the GitHub Action use.
> (`c5-ml` found this; `c4-statistics` and I both verified it.)
>
> **This should be fixed before Nov 2, ahead of any benchmark or CI work.** Leading a YC deck with
> adversarial-testing differentiation while the gate has this hole is a credibility risk. The fix
> is not more episodes — it is **per-segment scoring**: `adversarial_success_rate` becomes its own
> scored component, or adversarial episodes get their own floor. See D9.

**Fix (one line, high value).** Change the grouping key from `randomization_level` to
**`scenario_category`** (12 values, available on every adversarial episode, `None` for
nominal). Then robustness genuinely measures cross-condition consistency, the term stops
being inert, and the advertised upsell starts meaning something. Add a regression test
asserting `robustness < 100` for a run containing adversarial scenarios across ≥2 categories —
a test that fails today.

**Candid note:** this is a genuine *simplification* of the intended semantics rather than a
pure bugfix, because the vault's `Module Specs` M4 defines robustness as "std deviation of
success rate across **randomization variants**." Grouping by scenario category measures
something adjacent but different (cross-*condition* consistency). This should be an ADR, not
a silent patch, and the vault's M4 wording should be reconciled with whatever is shipped.

## 0.2 The advantages table

The moat stack in [[Moat]] is a strategy document, not a set of engineering assets. Scored
against the code, the seven "strategic advantages" in [[Strategic Advantages]] convert to
durable moat in **four** cases, are **market-positioning-only** in two, and are **currently
negative** in one.

| # | Stated advantage | Converts to durable moat? | Why / why not |
|---|---|---|---|
| 7 | Data flywheel | **Yes — if built** | The only claim that compounds. Nothing implements it. Highest value per engineer-week of anything here. |
| 5 | Recurring revenue per model update | **Yes — indirectly** | Real, but only *sticky* if the accumulated history (§1) or the audit trail (§7) is retained. Run history you can delete is not a moat. |
| 1 | Near-empty competitive field | **Only if benchmark ships** | White space decays 12–18mo per the vault. A *published standard* converts "empty field" from a timing advantage into a positional one. |
| 3 | YC devtools narrative | **No (by itself)** | A fundraising asset. Necessary for the W27 application, worthless as retention. Stop treating it as moat row. |
| 6 | Compliance tailwind | **Yes — terminal, per vault** | Highest ceiling, longest fuse. Requires the audit trail and the published scorecard *format*. |
| 2 | NVIDIA ecosystem fit | **Risky as stated** | Inception credits are unverified (Key Figures marks them unbooked). Concentrating on Isaac converts a hedge into a single point of failure. §9 fixes this. |
| 4 | Pure software | **No** | A feasibility/licensing constraint, not a barrier to entry. A funded competitor is equally pure-software. Do not claim as moat. |

**Net:** the moat is not the seven advantages. It is **one accumulation asset (the episode
event log) plus one published standard (the benchmark)**, feeding a **retention artifact
(the audit trail)**. Build those three. Everything else is supporting cast.

---

## 1. The data flywheel made real — **BUILD FIRST**

**Serves:** advantage 7 (data flywheel), enabling 5 (recurring) and feeding 6 (compliance).
**Why hard to copy:** not the code — the code is a weekend. It is the *proprietary corpus*
of `(scenario params, physics conditions, policy config → failure mode, outcome)` triples
that a competitor cannot synthesize; they must re-earn it with customer runs. Time-to-copy
is proportional to customer-months accumulated, which is why it cannot be fast-followed.

### The gap, precisely

[[Moat]] claims "every run improves the failure taxonomy, adversarial scenarios, and
benchmarks." In the shipped code:

- `validsim/engine/pipeline.py:131` — the shared pipeline hardcodes
  `ScenarioGenerator(seed=seed).generate(...)`. The `LLMScenarioGenerator`
  (`validsim/scenarios/llm_generator.py`) **is not wired in at all** and is dead code in
  the primary path.
- `validsim/scenarios/generator.py` is a **seeded cycle** through 12 categories
  (`category = ADVERSARIAL_CATEGORIES[i % 12]`) with fixed parameter ranges. There is no
  feedback from outcomes into scenario selection. It is reproducible, which is good, and
  completely non-adaptive, which means it cannot be a flywheel.
- `validsim/sim/runner.py:112` — `MockIsaacBackend` samples
  `failure_mode = rng.choice(FAILURE_MODES)`, i.e. the 7 failure modes are **uniform
  random with no relationship to the scenario that was injected**. Mining "weaknesses" from
  mock data would be mining noise. This must be stated plainly in any investor or
  compliance conversation.
- `validsim/engine/evaluation.py:71` — `failure_taxonomy` is a bare
  `Counter[e.failure_mode]`. The scenario→outcome **linkage is never persisted.**

**Concretely: the loop does not close, because the join key is thrown away.** Episodes
carry `failure_mode`, scenarios carry `category`/`params`/`difficulty`, and nothing records
that episode *i* ran under scenario *j*. There is no table, column, or field anywhere in the
store that holds the pair. That single missing edge is the entire flywheel.

### Data model (the accumulation asset)

Add one **append-only** table. Do not extend the existing `validations` row — it stores
`episodes_json` as a blob, and a blob cannot be mined across runs. This is the one place
the moat genuinely requires denormalization.

```sql
CREATE TABLE episode_outcomes (
    episode_uid        TEXT PRIMARY KEY,     -- sha256(run_id, seed, scenario_id)
    observed_at        TIMESTAMPTZ NOT NULL,
    tenant_hash        TEXT NOT NULL,        -- pseudonymous; never the customer name
    task_id            TEXT NOT NULL,
    checkpoint_id      TEXT NOT NULL,
    -- taxonomy version, so mining can be replayed against old data
    taxonomy_version   TEXT NOT NULL,
    scenario_id        TEXT,                 -- NULL for nominal episodes
    scenario_category  TEXT,                 -- one of the 12, NULL for nominal
    scenario_params    JSONB,                -- the physics knobs, kg/lux/m/N
    scenario_difficulty REAL,
    failure_mode       TEXT,                 -- one of the 7, NULL if success
    -- the minimum viable feedback signal
    success            BOOLEAN NOT NULL,
    collision_count    INTEGER,
    max_contact_force_n  REAL,
    min_human_distance_m REAL,
    duration_s         REAL,                 -- SIMULATED time, not compute time
    -- cost/throughput attribution (see §8)
    compute_seconds    REAL,                 -- wall clock, distinct from duration_s
    gpu_seconds        REAL,
    sim_backend        TEXT,
    sim_backend_version TEXT
);
CREATE INDEX ON episode_outcomes (tenant_hash, task_id, observed_at);
CREATE INDEX ON episode_outcomes (scenario_category, failure_mode);
CREATE INDEX ON episode_outcomes (checkpoint_id);
```

`taxonomy_version` is the detail that makes this a *product asset* rather than a log: it
lets you answer "would my v1.3 detector have caught the failures v2.1 caught?" — a question
you will need the moment you claim detection improvement in a pitch.

Append-only, never updated. Retention is a separate policy knob, not a hard delete (see §7).

### Mining approach

Mine in **rate space** (`failures / episodes`), not raw counts, so runs of different sizes
are comparable. `validsim/engine/anomaly.py` is the correct precedent and is worth reading
first — it already builds sigma from three sources (`pstdev` of baseline rates + bootstrap
SE of the mean + binomial variance of the current observation) and reports a z-score. That
is the right estimator.

**But scope the first implementation to 12 cells (category-level), not 84 (mode × category).**
With 84 cells on a 1,000-episode run, each cell gets ~12 episodes — far below the detection
floor. Measured minimum detectable rate per cell (one-sample vs a 5% baseline, 80% power):
`n=10 → 24.3%`, `n=30 → 16.2%`, `n=50 → 13.6%`, `n=100 → 11.1%`, `n=200 → 9.3%`.
**A 5% failure rate is not resolvable at any realistic per-cell n**, so an 84-cell miner
would surface only gross failures. Category-level (12 cells) is a manageable multiplicity
problem and is the honest v1.

Three mining passes, in increasing cost:

1. **Condition × failure cross-tab.** Group by `scenario_category` and rank by lift =
   P(failure | category) / P(failure). Cheap, SQL-only, immediately useful.
2. **Within-category param sensitivity.** For the top-lift categories only, bin the
   numeric scenario params (e.g. `mass_kg` into quantiles) and look for monotonic failure
   gradients. This finds *where the cliff is* — e.g. "grip fails above 0.62 friction
   coefficient", which is a portable, physical, sellable finding.
3. **Checkpoint-family weakness profile.** For each `checkpoint_id`, the vector of
   per-category failure rates is its **weakness fingerprint** (see §5). Cluster
   fingerprints across customers to find *shared* blind spots. This is the cross-customer
   compounding step, and it is the only pass that needs tenant-spanning data.

> [!danger] Statistical landmines — three, and two are worse than "be careful"
> - **Silence is a power failure, not a clean bill of health.** At the per-cell n above, an
>   unflagged weakness means "we could not detect it," not "there is none." This is the same
>   failure shape as D2 (robustness pinned at 100 reads as "robust"). **The UI must never
>   render "no weaknesses found"** — it must render "insufficient evidence at current episode
>   count to resolve a X% failure rate." Absent this, users will read a power failure as a
>   clean bill of health, which is the single most dangerous UX consequence in this document.
> - **Multiple comparisons.** 84 cells at α=0.05 unadjusted gives `1 − 0.95^84 = 98.7%` FWER
>   — nearly every run flags something, and `anomaly.py`'s z-threshold of 2.0 does nothing for
>   it. Use **BH-FDR at q=0.10** for the exploratory view and **publish `m` (cells tested)
>   alongside every result.**
> - **Selection bias is structural.** Mining the worst cells and reporting them means the
>   reported set is **the maximum of 84 draws**, so the top finding is biased upward even
>   when nothing is wrong. **Report the full ranked list of all cells with q-values, never a
>   top-3.** Showing only significant results is the p-hacking presentation failure, and an
>   investor asking "what was the baseline rate in the cells you didn't report?" finds it
>   instantly.
> - **Simulator artifact overfit.** See §4.

### The feedback loop

```
run episodes
   ↓ append episode_outcomes (run, scenario, outcome, failure_mode)
mine rates → FDR-controlled candidate weaknesses
   ↓
promote replicated patterns to scenario corpus entries (versioned, human-reviewed)
   ↓
next run samples from the enriched corpus instead of the static 12-cycle
   ↓
targeted scenarios → higher failure yield per episode → cheaper detection
   ↓
(goto: better detection → more trust → more runs)
```

The economic engine is **failure yield per GPU-second**, not failure count. If targeted
scenarios raise the failure rate from 2% to 20% on the same compute, you get 10× the
detection signal for the same money — which is simultaneously moat (§1), unit-economics
defense (§8), and the answer to "why is your validation trustworthy."

**Gate before this counts as a moat:** the loop only closes once `scenario_id` reaches the
store. Estimate **0.75–1.5 founder-weeks** for schema + dual-store parity + append on
`save()`, mining pass 1, and the corpus-versioning type. Passes 2–3 are 2027.

**Candid risk:** the flywheel is the **only** moat here that requires customers to exist.
Until ~5 design partners are running weekly, mining has almost no data. So the flywheel is
highest *long-run* value and **lowest* pre-YC demonstrable value. Build the *schema and the
join* now (cheap, and it's the thing a competitor can never retrofit once you have history),
but do not expect to demo the closed loop before Nov 2.

---

## 2. The failure-taxonomy IP — **REVISED: keep secret-only, do NOT patent**

**Serves:** advantages 7, 5; the compliance moat.
**Why hard to copy:** the *labels* are copyable in a week. The **published detection
semantics + per-mode detection logic + a corpus demonstrating other tools miss these
failures** is a specification, and specifications are expensive to converge on once
customers depend on them.

[[IP Strategy]] §18.2 currently allocates "failure taxonomy classification system" to
**trade secret only**, reasoning that "value lives in the accumulating dataset, not the
idea."

> [!warning] REVISED 2026-09-25 — my earlier recommendation to patent the detection methodology
> was **wrong, and `c4-compliance` is right to reject it.** I had proposed filing a provisional on
> "per-mode detection logic" and publishing the labels. On reading the code, **there is no detection
> logic to patent.** `failure_mode` is assigned by `rng.choice(FAILURE_MODES)` — a uniform draw over a
> hardcoded 7-tuple, independent of collision count, force, duration, or any observable.
> `compute_safety` and `evaluate` *consume* the already-assigned label; neither infers a mode. So the
> "method" is `random.choice()` over a list, which an examiner would treat as abstract under §101.
> Filing would burn the $10K–$15K legal budget, and at the 18-month publication it would **publicly
> disclose the mode list — destroying the exact trade secret the vault deliberately kept
> secret-only.** The vault's original reasoning at `IP Strategy.md:29` is correct; my proposal inverted
> it. **Recommendation: keep the taxonomy secret-only, and file the two provisionals
> `IP Strategy.md:18-19` already assigns** (pipeline architecture + adversarial scenario generation)
> — those are real methods.

**The reallocation only becomes available once a real detector exists**, which is a *product*
decision, not a legal one. A genuine methodology means inferring mode from observables:
`collision_count > 0 → collision`, `duration_s > limit → timeout`, joint-state deviation →
`joint_limit`. That is real, protectable IP — and it does not exist yet.

**Interim position (what to publish, and when):**

| Asset | Position | Gate |
|---|---|---|
| Mode **labels** (the 7 strings) | **Keep secret** | Publishing them discloses the asset with nothing protecting it. Revisit only when real detectors exist. |
| **Detection logic** | Does not exist yet | Build first; then trade-secret + provisional becomes defensible. |
| **Accumulated corpus** | Trade secret | Unchanged — and it compounds. |
| Scorecard **schema** | Standard (unchanged) | The published-standard play lives here, not in the taxonomy. |

**So the published-standard moat does *not* come from the taxonomy.** It comes from §3's
benchmark plus the scorecard **schema** — the asset the vault already correctly allocates as
"secret → standard." This is a real reduction in the moat surface, and it is the honest
reading: we have a category-standard *format*, not a category-standard *taxonomy*.

**Implementation (shape unchanged, priority changed):**
1. Write normative definitions for the modes **internally first** — one paragraph each, with an
   explicit *disambiguation* section (what separates `timeout` from `grasp_failure` when both
   present). Disambiguation is where the value is; a bare label list is not an asset.
2. Specify the required per-mode detector inputs from `EpisodeResult`:
   collision → `collision_count`; unstable_placement → position RMS from
   `joint_states_summary`; joint_limit → `effort_max` / dof; timeout → `duration_s`;
   emergency_stop → `collision_count` + stop signal; perception_error / grasp_failure →
   currently **unsupported by the available signal** and must be stated as such.
3. Only once (2) exists: publish a **detection benchmark** with precision/recall per mode. That
   is the artifact that makes the taxonomy defensible — gated on the GPU worker.

**⚠️ The honest gap, which must be disclosed rather than papered over:** with the current
`EpisodeResult` signal set, only about 5 of the 7 modes are actually detectable. There is no
trajectory, no contact-state trace, no commanded-vs-achieved pose, and no persistent stream
of observations. `perception_error` and `grasp_failure` in particular are **labels with no
detector behind them** — and per D5, on mock data even the other five are not inferable.

Two options, pick one and be explicit:
- **Scope the v1 taxonomy to the 5 detectable modes** and keep it secret until detectors exist.
  A credible 5 beats a hollow 7. (Recommended for pre-YC.)
- Carry all 7 internally and mark 2 as `preview`/non-normative until the worker lands.

**Effort:** definitions 2–3 days (Founder 1, needs domain judgment not code). Detector
implementation 1–2 weeks once the GPU worker exists — and note this is now a **prerequisite for
the moat**, not a nice-to-have, because without it there is no method to protect.
**Patent:** do **not** file on the taxonomy. File the two provisionals the vault already
specifies, and file them **before** the Nov 2 demo video (a publication event).

**Time-to-moat:** 6–12 months from when detectors exist, and only once a third party (insurer,
cert body) uses it.

**Candid risk:** high, and higher than my first pass implied. The taxonomy was one of the two
"published standard" legs of the moat story, and **that leg is now substantially weaker** — we
have a standard *format* but not a standard *taxonomy*. The compensating move is to put the
weight on §3's benchmark, which is independently credible, rather than on a taxonomy that is
currently `random.choice()`.

---

## 3. Benchmark leadership — **BUILD (minimum viable), the highest-leverage single artifact**

**Serves:** advantage 1 (white space → positional standard), 3 (devtools), 6 (compliance).
**Why hard to copy:** the first credible public benchmark in a category becomes the
reference others must run. MLflow/W&B and ImageNet are the templates: the leaderboard
compounds through *participation*, and participation is a function of being the only
credible option, which is a function of being first. This is the classic devtools land-grab
and it is the one moat here that can be built **before** the product is complete.

**Design — the minimum version, buildable now:**

`validsim submit` → hosted scorecard for a public checkpoint on public tasks, leaderboard
by task, scores published with confidence intervals.

The `Scorecard` dataclass and `benchmark.compare_scorecards` already do head-to-head metric
comparison; what's missing is a *population* to compare against, which is exactly what the
leaderboard creates. So the increment over shipped code is small.

**Anti-gaming — this is where naive versions die.** A raw success-rate leaderboard is
gameable in ways that destroy trust permanently:

| Attack | Countermeasure |
|---|---|
| **Train on the benchmark tasks.** Public scenarios become training data; scores inflate and the leaderboard measures memorization. | **Hold-out task split**: public tasks for practice, a rotating **private** task set for the ranked score. Rotate monthly. |
| **Select on noise.** Submit the luckiest of 50 seeds. | **MDE floor** (below) + report the 95% CI. A score whose CI overlaps the field is shown as a tie, not ranked. |
| **Under-report failures.** Claim success on ambiguous episodes. | Episodes are ValidSim-run, not customer-run — the grader is the platform, so the outcome is not self-reported. This is a structural advantage worth stating explicitly. |
| **Tune weights to the leaderboard.** Adjust the composite to rank yourself first. | Publish the **schema and weights** (they're in `scorecard.py` as `_W_SUCCESS=0.4` etc.). Keep only the *calibration constants* secret. |
| **Duplicate submissions are perfectly correlated.** `stable_seed(checkpoint_id, task_id)` means two submissions with the same checkpoint+task get the **identical episode set** — not independent samples. Leaderboard CIs would be wrong. | **Dedup on `(checkpoint_sha256, task_id, seed)`** + a methodology note stating the seeding policy. Nobody else will catch this for you. |

**The MDE gate — the single most important design decision.** Do not rank on raw success
rate. Require *precision*, not score:

1. Submitter supplies `n_episodes`. Compute `MDE(n)`.
2. **If `n < n_required(MDE = 2pp) = 3,841`, the entry is marked "insufficient evidence" and
   EXCLUDED from ranking** — not shown as a low score, *excluded*, because at n=1000 the entry
   is consistent with a true rate anywhere in an 8pp band and a "rank" would be noise presented
   as signal.
3. Among qualifying entries, rank by point estimate; display the 95% CI as the primary visual.

Measured MDE vs sample size (p₀=0.90, 80% power, α=0.05 two-sided, per arm) — **publish this
table on the leaderboard itself**, because it converts "you need lots of episodes" from a sales
claim into a number a submitter can verify:

| n per arm | MDE |
|---|---|
| 100 | 14.72pp |
| 1,000 | 4.07pp |
| 2,000 | 2.81pp |
| 5,000 | 1.74pp |
| 10,000 | 1.22pp |
| 50,000 | 0.54pp |

**Per-tier MDE, using the episodes that actually enter the comparison.** `c3-engine` traced the
data flow and I verified it: `pipeline.py:133` concatenates nominal + adversarial into one list,
`evaluate()` counts **all** of them with no split, and `regression.py:99-100` feeds the pooled
counts into the two-proportion test. So per-arm n is **`episodes + adversarial_count`**, not
`episodes/2` and not nominal-only:

| tier | nominal | adversarial | per-arm n | MDE | 2pp target |
|---|---|---|---|---|---|
| Free | 100 | 0 | 100 | 14.72pp | ✗ |
| Team | 2,000 | 240 | 2,240 | **2.65pp** | ✗ |
| Pro | 5,000 | 600 | 5,600 | **1.64pp** | ✓ |
| Enterprise | 10,000 | 1,200 | 11,200 | **1.15pp** | ✓ |

**Lead the tier story with this, not with a blanket inadequacy claim:** *"Free and Team cannot
detect the 2pp regression our own severity bands are calibrated around. Pro and Enterprise can."*
That maps exactly onto `Pricing Tiers.md:18-21`, so it **doubles as the upsell argument** — the
Team→Pro step is the first that actually buys statistical resolution. A blanket "our tiers are all
underpowered" claim is falsified by our own Pro numbers, and an investor will check that first.

> [!warning] Two caveats so the deck does not overclaim
> 1. **The homogeneous-p assumption is violated.** The pooled rate mixes nominal and adversarial
>    episodes with *different* true success probabilities, but the two-proportion MDE formula
>    assumes one p per arm. The true MDE is therefore **somewhat worse** than the table. State
>    these as approximate.
> 2. **The stronger point is a correctness argument, not a power argument** (`c3-engine`): because
>    the rate is pooled, a model can **hold its pooled rate steady while trading nominal
>    capability for adversarial capability**, and the regression test reports "no change." That is
>    a *wrong answer*, not a low-power one — and it is the same root cause as D9. **The fix is
>    per-segment regression, not more episodes.**

**Why MDE-gating beats ranking-by-raw-rate:** submitters can pick `n` to their advantage in
*either* direction. Small `n` inflates apparent variance (noise bandits post lucky highs);
large `n` is expensive so only well-resourced labs climb. An MDE floor fixes both by making
**precision the entry criterion rather than the score.**

**Use a frozen reference set, not a live field median.** Every submission changes a live
median, making it an unstable reference; and if the field is bimodal (strong lab / weak lab)
the median sits in *empty space*, so "within 1pp of the median" is meaningless — you want to
know which *tier* someone is in. Pin a reference set at a date (or a designated baseline
checkpoint) and test every submission against it.

**Two more statistical constraints:**
- **If live-field comparison is ever added,** it needs simultaneous inference (Holm or BH-FDR
  across all pairs) — otherwise with 50 submissions you get ~2 spurious "beats the field"
  claims at α=0.05 by chance. Frozen-reference-plus-unadjusted is the pragmatic answer.
- **Scope the leaderboard to success rate only.** MDE formulas here are binomial. Safety score
  and robustness are not proportions — robustness is a *dispersion*, and dispersion MDEs are a
  different, harder problem. Adding them later would make the math unairworthy.

That last row is a real tension to resolve: the vault says publish the scorecard **format**
and keep the **scoring weights** proprietary. But a leaderboard whose weights are secret is
a leaderboard nobody can interpret or trust. Recommend: publish weights, keep the per-task
calibration data secret. Anti-gaming integrity is worth more than the secrecy.

**Why build before a competitor:** the entire value is that it's *already standard* when the
competitor arrives. A benchmark published six months after a competitor's is worth roughly
zero — you become the second one, and the second benchmark in a category rarely wins. This
is the **most time-sensitive** moat item in the entire document, because it requires
neither GPU capacity nor customer base, only a decision to go first.

**Effort:** MVP (public tasks, private hold-out, CI-gated submission) **2–3 weeks** once a
GPU worker exists. Honest caveat: until the Isaac worker lands, every published score is
produced by `MockIsaacBackend`, whose failure modes are uniform random. **A leaderboard
built on mock data is worse than no leaderboard** — it is a public, dated, permanent record
of meaningless numbers, and "our first public benchmark" is the single most auditable claim
a competitor would attack.

**Time-to-moat:** 12–18 months to recognized standard; must launch within ~9 months of a
competitor's entry to matter.

**Candid risk:** high operational risk, moderate upside. Requires continuous maintenance
(private task set, rotation, adjudication) — that's real ongoing labor for 2 founders, and
it is the kind of thing that dies quietly. Also: a leaderboard makes you the *first* target
for "your benchmark is rigged" and you have no independent referee. Build the anti-gaming
machinery *first* or don't launch it at all.

---

## 4. Scenario-generation quality — the sim-feedback synthesis loop

**Serves:** advantage 7 (flywheel), and the 1-in-3 LLM-dependency risk.
**Why hard to copy:** once scenarios are *discovered* rather than *listed*, they encode
knowledge of where real policies actually break. That corpus is the flywheel from §1 and
is not purchasable.

### The honest state

`validsim/scenarios/generator.py` is a seeded cycle with fixed ranges. The
`LLMScenarioGenerator` is a thin `gpt-4o-mini` JSON wrapper with a rule-based fallback,
and `pipeline.py:131` doesn't use it. So the vault's "LLM adversarial scenario generator"
is, in the shipped default path, **a random number generator with twelve buckets**.

### The proposal: close the loop, then delete the LLM dependency

```
1. seed a population of scenarios (existing generator, perturbed)
2. run a small episode batch per scenario on the backend
3. score each scenario by its outcome:
     - keep if failure-rate lands in the "informative band" (e.g. p ∈ [0.05, 0.40])
     - discard if p ≈ 0   (trivial: the policy shrugs it off, no signal, wasted compute)
     - discard if p ≈ 1   (trivial: everything breaks, no discrimination)
4. mutate the survivors (perturb params, interpolate toward the failure boundary)
5. repeat for a bounded budget
6. promote discovered scenarios into the versioned corpus
```

**Why this is a real advantage once the loop closes:** it needs **no external LLM at all**.
That yields determinism (seeded and reproducible), zero marginal generation cost, no
vendor dependency, and — decisively for robotics enterprise — it **works air-gapped**. A
closed-loop generator is also *cheaper*: the only real constraint is GPU-minutes, which
you already pay for the validation itself.

**Feasibility against the current code** (`c4-sim2real` verified, and I agree): the
`SimulationBackend` Protocol's `run_episode → EpisodeResult` **is sufficient for a terminal-objective
v1 loop.** The worker *must* populate every field (the client validates types, taxonomy, and seed
echo), so a fitness function built from `success`, `failure_mode`, `collision_count`,
`max_contact_force_n`, and `P(failure)` / `P(severity)` needs no richer feedback.

**But the signal stops working at exactly the boundary where the flywheel thesis lives.** Searching
by failure *mechanism* — "this fails because the arm overshoots on approach, so bias mutation toward
inertia/friction" — needs per-step trajectories. `joint_states_summary` is **4 aggregate scalars**:
the worker can report `position_rms 0.4` but not "the gripper stalled at t=3.1s with the wrist at
qpos (…)". That needs `/qpos`, `/qvel`, `/effort` per step — i.e. the HDF5 recording.
**So mechanism-level search is hard-blocked on the worker, and recording is hard-blocked on HDF5.**

> [!danger] ⚠️ The mock makes the loop a **ZERO-INFORMATION OBJECTIVE** — see **D12**
> `MockIsaacBackend` **ignores `scenario.params` entirely**: at a fixed seed, varying
> `human_distance_m` (0.1→1.2) or `mass_kg` (0.02→5.0) yields a **bit-identical `EpisodeResult`**
> (1 distinct tuple per sweep). Only `difficulty` affects output, and `failure_mode` is
> **unsteerable** (`P(collision)=0.1450` vs uniform 0.1429). So **any search over the mock converges
> to `difficulty≈0.95` and reports a "worst case" that is an artifact** of `_RANDOMIZATION_PENALTY` +
> a uniform draw. **Do not ship that as a discovery capability** — the moat argument collapses on one
> read of `runner.py`.
> **The honest pre-Nov-2 claim is therefore: "we own the search infrastructure and the budget model" —
> NOT "we find worst-case failures."** The "no LLM needed" advantage survives intact (deterministic,
> reproducible, zero marginal cost, air-gapped — all true); **the advantage is the algorithm, not
> the output, until a real simulator exists.**

**Honest phasing** (`c4-sim2real`):

| phase | build | claim |
|---|---|---|
| **Now, no GPU** | CMA-ES loop, mutation operators, budget accounting, `WorstCaseResult` witness, `VALIDSIM_SEARCH_ENABLED` default **off**, exercised against `MockTransport` | "search infrastructure + budget model" |
| **Now, no GPU — and the highest-value item in this section** | **Make the mock read `params`** (`mass_kg`/`friction`/`lux`/distance). **4–6 days CPU.** | makes the closed loop **demonstrable end-to-end on CPU today** |
| **Post-worker** | the loop as a real capability. **≤200 evals** default (5 min/ep ⇒ ≈17 GPU-hours ≈0.7 day per checkpoint — treat >500 as a paid tier) | real worst-case discovery |
| **Post-HDF5** | mechanism-level operators | failure-*mechanism* attribution |

**The target-band idea is the key design choice.** Naively maximizing "scenarios that break
the policy" degenerates into finding scenarios that break *everything* — useless, because
they don't discriminate between a good and a bad policy. Holding survivors in the
informative middle band is what makes the corpus diagnostic rather than merely destructive.

### ⚠️ Sim2real honesty: the loop can overfit to the simulator

This is the most important caveat in the proposal and it should not be minimized. Scenarios
discovered by hill-climbing against PhysX contact quirks may not correspond to any real
world failure — you can find "gripper fails when contact stiffness exceeds an
implementation-specific threshold," which is a simulator fact, not a robot fact.

Two mitigations, sharpened by `c4-sim2real`:

1. **The test is "settable AND measurable on real hardware," not "physical-looking."** `friction=1.62`
   qualifies (bench-testable with a force gauge). A `solver_substeps` or `texture_correlation` axis
   is physical-*looking* but **not portable to a real test spec.** So the question is: **can a
   technician reproduce this with a caliper and a light meter?** That admits mass/friction/lux/
   distance/force and excludes solver/rendering knobs. **Mark each parameter axis
   `portable: true|false`** in the scenario spec — it becomes part of the corpus asset.
2. **Cross-simulator agreement is the strongest cheap portability filter, and it is a genuine moat.**
   Run the top-K discovered scenarios on a **second independent engine.** Transfers Isaac→MuJoCo ⇒
   far more likely a real physical effect than a PhysX artifact. **Two engines agreeing is much
   better evidence than one engine plus a claim.** This is the concrete payoff that justifies §9's
   multi-vendor backend interface — the abstraction is what makes the validity filter possible.
   **This also concretely justifies MuJoCo as the second engine:** permissively licensed (directly
   relevant because **Isaac Lab is research-use only**) and the natural cross-engine filter.
3. **Label honestly until real data exists.** Until a real-world-equivalent (RWE) test set can check
   it, every discovered scenario is **`worst_case_in_sim` only.** Publishing an in-sim worst case as a
   real failure mode sends an engineer to fix a bug the robot does not have.

Be honest that mitigation 1 is a constraint, not a guarantee. It is a **fig leaf on its
own** — the units are portable, the *failure mechanism* may not be. The genuine defense is
corroboration against real incidents, which is a customer-data problem, not a code problem.

**Effort:** v1 bounded evolutionary loop **2–3 weeks** after a backend exists (mutation
operators per category, fitness function, budget/termination, corpus promotion, tests for
determinism). **Can start before the worker** using `MockIsaacBackend` to prove the
*mechanism* — but the *output* is meaningless on uniform-random failures, so this is an
architecture rehearsal, not a demo.

**Time-to-moat:** 9–15 months, and only once the corpus is large and validated.

**Candid risk:** the highest-risk item here technically. Evolutionary search over sim
parameters is easy to get subtly wrong (non-reproducibility, unbounded compute, degenerate
populations), and the sim2real gap means the output may not survive contact with a real
robot. **Do not lead the YC demo with this.** It is a strong *roadmap* item and a weak
*evidence* item.

---

## 5. Policy fingerprinting / cross-customer regression baselines — **DO NOT BUILD; 2027 AT BEST**

> [!danger] REVISED 2026-09-25 — `c3-engine` argues this should not be built at all, and I now agree.
> I had proposed this as a compounding moat. Three independent reasons defeat it, any one sufficient:

**(a) There is no cross-customer data to fingerprint, and no way to get it without a much larger
project.** The store has **no tenant or user column** — `validations` is
`(run_id, checkpoint_id, task_id, created_at, scorecard, …)` and `StoredRun` has no owner. "A
library of similar public checkpoints" **does not exist in the system today.** Building it means
building multi-tenancy first, which is `c4-compliance`'s project, not a moat feature.

**(b) A derived-features index across tenants is a privacy liability in a safety product.**
Cross-customer behavioural fingerprints are precisely the category of derived data enterprise
security review blocks. For a product whose Tier-4 buyers are insurers and regulators, this is
the feature that turns a security review from a checkbox into an excavation.

**(c) Statistically it is confounded anyway.** Fingerprinting requires comparable conditions. A
score under `lighting_change` at difficulty 0.3 is not comparable to one at 0.9 — and
`difficulty` is per-scenario and **is not recorded on the episode** (same D7 schema gap). You
would be fingerprinting on a confounded key.

**What survives, and what replaces it.** The *per-customer* weakness profile from §1 is still
worth building — it powers regression timelines and the "which scenarios separate you from your
own baseline" report, needs **no** cross-customer data, and carries **no** privacy liability.
That is the shippable version. The **cross-customer** percentile product is the part that dies.

> [!question] Fingerprint granularity — answering `c4-compliance`'s fleet-rollup question
> **A fingerprint is per `(checkpoint_family, task_id)`, NOT per robot-serial.** A 200-unit fleet
> of identical hardware **shares one fingerprint**. Therefore:
> - **Per-robot variance cannot come from the fingerprint.** It must be computed from the run data
>   (per-unit episode outcomes in `episode_outcomes`), not from the reference library.
> - **p5 across fingerprints is therefore not meaningful for a homogeneous fleet** — there is
>   only one fingerprint to take a 5th percentile of. p5 becomes meaningful only across a
>   *heterogeneous* fleet (mixed architectures, mixed task mixes).
> - **So `c4-compliance`'s rollup math should compute p5 WITHIN a fingerprint group**, using
>   per-unit run outcomes, and reserve cross-fingerprint comparison for heterogeneous fleets.
>
> This is a correction to how I originally framed the fingerprint, and it is load-bearing for the
> rollup design — flagging it explicitly so your doc doesn't assume 200 distinct fingerprints.

**If it is ever revived, the minimum defensible design** (from `c4-compliance`, and stricter
than my first pass):
- **Explicit per-organization opt-in, off by default** — not implied by ToS or by use. Separate,
  revocable, org-level consent naming the exact feature.
- **k-anonymity floor k≥10 contributing orgs** before any cross-customer percentile is emitted.
  Below the floor, suppress or return only the customer's own result.
- **A signed DPA is required regardless.** Cross-customer pooling without a lawful basis is
  unsafe at our size.
- **Do not claim differential privacy.** At n≈50 orgs, DP guarantees are nearly vacuous. The
  defensible stack is opt-in + DPA + k-anon floor.
- **A public leaderboard is materially less safe than a private per-org report**, because
  suppression itself is disclosive. Lead with the private report.
- **Never pool `scenario_params`.** A discrete parameter tuple is itself a re-identification
  vector — a 3-field tuple can identify a customer uniquely at small cohort sizes. This was a
  leak in my original proposal.

Note the vault's own honest line: network effects "need density" — 5–15 design partners
won't produce cross-lab flywheel magic until ~30–50 customers, and Year-1 value is
per-customer learning only. **This feature's value is real but is a 2027–2028 moat.** Its
pre-YC value is that it appears on the roadmap.

**Effort:** fingerprint extraction is ~1 week on top of §1. The reference library, cohorts,
k-anonymity, and the customer-facing report are **3–4 weeks** and gated on legal + enough
customers. **Time-to-moat: 18–30 months.**

**Candid risk:** the highest-risk *business* item. Legal (DPA, opt-in, data-sharing
addendum) on a 2-founder team with a $10–15K legal budget is the real constraint, not the
code. And a percentile claim that later proves wrong (wrong cohort, wrong family
definition) is a credibility event in an industry with few players. **Do not demo percentile
claims before you have ≥5 opt-in customers in a cohort.**

---

## 6. NVIDIA ecosystem integration — **DE-PRIORITIZE, deliberately**

**Serves:** advantage 2, which I've argued is the *riskiest* claimed advantage.

Recommended integrations, in honest priority order:

| Integration | Value | Effort | Verdict |
|---|---|---|---|
| **Isaac Lab extension/package** | Highest — real integration, real switching cost, plays to Inception | 2–3 wks | Build **post-YC** |
| **`validsim` model-card exporter** (checkpoint + scorecard + episode evidence → one artifact) | High and **cheap** — fits the "public artifact" requirement, and a model card *carries the scorecard* which makes the scorecard format travel with it | **2–3 days** | **Build now** |
| **Jetson edge-validation runner** | Medium — edge story, but robotics fleets are pre-scale (vault: Tier-3/4 pain arrives 2027–2029) | 2–3 wks | Defer |
| **Omniverse Replicator integration** | Lowest — Replicator is a *synthetic data* tool, not a validation tool. This is a category error in the strategy. | — | **Don't build** |

**Why de-prioritize the flagship:** advantage 2 is simultaneously the vault's moat row 2
**and** its Risk #2. [[Competitive Landscape]] rates NVIDIA "Medium (platform risk)" and
[[Unit Economics]] notes Inception credits are unbooked. Concentrating engineering on
Isaac maximizes exposure to the one competitor that could absorb you whole. The vault is
already honest that "the lock-in that protects us is the same surface a productized NVIDIA
CI layer would occupy."

**The model-card exporter is the exception worth pulling forward**, because it is 2–3 days
and it multiplies the value of the *other* moats: a published model card is a YC public
artifact, a benchmark submission, and a compliance evidence fragment, from one command.
It is also the only NVIDIA-adjacent item that carries the scorecard format outward (which
is the actual moat) rather than deepening Isaac dependence (which is the actual risk).

**Time-to-moat:** Isaac Lab package 12–24mo (dependent on Inception acceptance, unverified).

**Candid risk:** the whole NVIDIA thesis rests on credits and partnership that
`Key Figures` marks as unbooked and the vault marks as "must be applied for, accepted and
verified before use." If Inception doesn't land, this moat row is vapor. **Do not put
"NVIDIA Inception" in a moat claim before an award letter exists.**

---

## 7. Edge / on-prem / air-gapped deployment

**Serves:** advantage 4 (pure software) → converts to an *enterprise* moat;
enables §4's LLM-free generator.
**Why hard to copy:** medium. Not technically hard. But it is a **procurement unlock** that
no cloud-only competitor can satisfy, and procurement blockers are sticky in a way features
are not.

**This is a real and underrated moat, and the vault underweights it.** The stated blocker
is genuine: robotics enterprises — defense, semiconductor, medical, critical infrastructure —
frequently cannot send checkpoints to a third-party cloud. Their own IP policies forbid it.
A cloud-only vendor is **eliminated at the security review**, before price, before features.
If ValidSim ships air-gapped, those accounts become reachable; if not, they are invisible
and you never learn you lost them.

**Implementation (much cheaper than it sounds, because the engine is already pure):**
- Local store (SQLite already exists and is a first-class backend) — **already done**.
- No telemetry, no egress — a build flag + a network-disabled test, not a new architecture.
- **Offline license** — signed, time-bounded, verifiable without a phone-home.
- The §4 closed-loop generator needs **no LLM**, so air-gapped generation works.
- The honest gap: the GPU worker is a **~15 GB container that is not in this repository**.
  Air-gapped means the customer runs it themselves. That is actually fine — it is the
  **BYO-GPU SKU** the vault already identifies in [[Unit Economics]] as a third COGS lever.
  Air-gapped ≈ BYO-GPU ≈ orchestration/seat fee instead of per-GPU-hour. The three ideas
  collapse into one SKU.

**Effort:** the licensing + packaging + "no egress" story is **1–2 weeks** on top of the
worker existing. But **the worker itself is the blocker**, and the worker is the single
largest unbuilt item in the whole plan.

**Time-to-moat:** 6–12 months once the worker exists.

**Candid risk:** low technical risk, real operational cost (supporting on-prem installs at
2-founder scale). And it **caps revenue** if done badly — an air-gapped perpetual license
trades recurring revenue for reach. Recommend air-gapped as a **priced Enterprise SKU with
annual license renewal**, not a free tier. License enforcement is the genuine business
question here, not the code.

---

## 8. Cost / throughput as a moat

**Serves:** advantage 5 (recurring revenue defended against the $75/run price), and §1's
failure-yield-per-GPU-second.
**Why hard to copy:** low on its own (anyone can batch), high cumulatively (requires
deep simulator-internals knowledge — a small lab can't match Isaac-specific throughput
tuning). This is a *supporting* moat, not a primary one.

### ⚠️ Critical measurement gap

**There is no compute-time accounting anywhere in the engine.** Searching for timing
instrumentation returns only HTTP middleware (`api/main.py` `time.perf_counter` for request
duration) and an SSE deadline (`jobs/router.py`). `EpisodeResult.duration_s` is
**simulated episode time** — how long the robot's motion took — *not* wall-clock compute.

**And it is worse than "the wrong number" — `duration_s` is confounded with the outcome.**
`runner.py:131-136` assigns `rng.uniform(18,35)` seconds to a `timeout` failure but only
`rng.uniform(4,12)` to a success. So **cost-per-episode measured on `duration_s` would be
anti-correlated with quality: the worst-performing policies would look the cheapest.** That is
not imprecision, it is an inverted metric, and it is a diligence hazard to publish at all.

So this item starts with instrumentation, not optimization:
- `wall_clock_s` per episode (measured *around* the `run_episode` call), `compute_s` (run
  aggregate), `gpu_s`, plus `compute_device` / `gpu_type` / `driver_version` as run-level
  metadata. All **defaulted** fields — `notify/email.py:69-79` reconstructs `Scorecard(**kwargs)`.
- **Never** report `duration_s` as a cost metric. Rename it if ambiguity is a risk.
- **Mock-path timings are meaningless** (the mock is instant) and must be labelled as such in
  any published number. Timings must be recorded on the Isaac worker path.

**The one real speedup available today** (`c5-integrations` found this; I measured it
here): `random.Random.binomialvariate(n, p̂)` is **stdlib** (Python 3.12+) and is *exactly* the
same distribution as the resample mean of a 0/1 sample, since each of the n draws is iid
Bernoulli(p̂). Measured in this repo at n=100,000 / R=500: **10,579 ms → 1.17 ms (~9,000×)**,
with no numeric dependency added.

> [!warning] But it is **no longer "free"** — and the reason matters for a *moat* document
> `c5-integrations` walked back their own "free win" framing on a constraint I missed, which
> `c5-ml` caught. **The shipped bootstrap loop is bit-reproducible for a given seed** — same
> seed, same input, identical output — and that number is **pinned in the test suite**
> (`docs/testing.md:59-71` fixes `SEED = 42`, and `:380` pins randomized coverage to a
> `MASTER_SEED`). So determinism is a **documented property of the engine**, not an accident.
> A different RNG stream would change **every persisted `confidence_interval` in history**.
>
> For a moat document this matters more than it would in an engineering note: *"we made our
> statistics 9,000× faster without adding a dependency"* is a strong claim, but if it silently
> changes every number a customer or insurer has already archived, **it undermines exactly the
> defensibility the moat argument rests on.** The speedup is real; **the cost is reproducibility.**
>
> **Correct framing: adopt as an opt-in fast path, never on a persisted scorecard CI.** The CI is
> display-only — the gate at `scorecard.py:182-185` never reads it — so the *decision* is
> unaffected, but archived history still moves. And it must **never** touch
> `two_proportion_bootstrap_test`, which feeds `_regression_component` at 2.5 composite points per
> significant regression, i.e. **gate-affecting.**

**Two further constraints:** it needs **Python 3.12+** while `pyproject.toml` declares `>=3.10`,
so it must be a **guarded fast path** with the current loop as fallback; and it applies **only to
the binary case**, because `anomaly.py:112` also calls `bootstrap_ci` on continuous rates.

**Metrics worth publishing** — but note the reframing, which is better than my first pass:
- **"Minimum cost to certify a checkpoint at 2pp precision"** (≈3,841 episodes/arm, priced).
  This is a **Pareto claim**, not a rate claim, so it is immune to the gaming vectors — and it
  doubles as the natural upsell into the `Pricing Tiers` ladder, which already ties episode caps
  to "the statistical-confidence story."
- Cost per episode is **only defensible when published adjacent to MDE, never alone** — a
  vendor running 10× more episodes per GPU-hour has *lower* statistical quality per unit time,
  so the cheapest entry is the least reliable one. And it is comparable only **within a fixed
  configuration**; a 0.3c/episode run at `randomization='none'` is not comparable to one at
  `'full'`.
- Episodes per GPU-hour; and GPU-seconds to detect a **seeded known failure**, which ties cost
  to quality rather than trading them off.

**Engineering to lead:** batching (already partly there — `IsaacWorkerBackend.run_episodes`
does a single batched POST, so the wire contract is ready), early termination (stop an
episode batch when the failure rate is decisively determined — a sequential confidence
sequence, worth ~2× on trivial scenarios), and scenario-result caching
(high-value, deterministic scenario+seed → outcome memoization).

**Effort:** instrumentation **~1 week** (greenfield, per `c3-engine`). Optimization **2–3
weeks** and **only meaningful with a real GPU worker.** Publishing a benchmark **after** you
have measured on real hardware — publishing a projection is a credibility risk.

**Candid risk:** a cost leaderboard is the easiest thing in this document for a competitor to
attack, and "cheapest per validated checkpoint" ages badly as a *cost* claim — it invites "what
did you include in the denominator," and GPU pricing moves. The MDE-conditional version is more
durable because the statistical part is yours and doesn't deflate. **Low priority; do it after
the worker, never before.**

---

## 9. Multi-vendor neutrality — **the most underrated item in this document**

**Serves:** de-risks advantage 2 and Risk #2 (NVIDIA productizes the CI layer).
**Why hard to copy:** the abstraction itself is easy. The **capability matrix it encodes**
— what each backend can and cannot do, and how scores are comparable across them — is
accumulated knowledge. And it structurally prevents the worst-case outcome.

**The strategic argument:** if ValidSim is a scorecard bolted onto Isaac, NVIDIA is your
competitor. If ValidSim is *the scorecard that any backend plugs into*, NVIDIA is your
**distribution partner** and cannot absorb you without becoming the thing you are
independent of. Same code, opposite strategic position. This converts advantage 2 from a
dependency into a hedge.

**Current state — and why `c3-engine` rates this a redesign, not a week of work.** I first
called this "~1 week" because `SimulationBackend` (`validsim/sim/runner.py`) is already a
`runtime_checkable` Protocol and `create_backend` already does env selection. That was
understated, and the evidence is already in the codebase:

- `IsaacWorkerBackend` has a `run_episodes` **batch** method that is **not on the Protocol and
  not on `MockIsaacBackend`**. The real backend's most important performance capability is
  invisible to the type system and to every caller. That is not a design choice — it is a gap,
  and it is exactly the kind of thing a capability interface exists to prevent.
- `EpisodeResult` is currently a **fixed schema every backend must fill**, including fields a
  given backend may be structurally unable to produce. A backend with no human in the scene
  reports `min_human_distance_m = None`, which is **indistinguishable from "not measured."**
  The schema must become **capability-conditional**, not merely gain a `capabilities()` method.

So the honest shape of the work is:
```python
class SimulationBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...
    def describe(self) -> BackendDescription: ...   # provenance: version, assets, physics engine
    def run_episode(..., policy: PolicyProtocol) -> EpisodeResult: ...
```
with `EpisodeResult` fields becoming capability-conditional and a **backend conformance suite**
to prove each implementation honours its declared capabilities. `BackendCapabilities` should
declare supported categories, fidelity, determinism guarantee, batch/parallel support,
`min_vram_gb`, and **which failure modes this backend can produce** — that last field makes
§2's taxonomy honesty *mechanically enforced*: you cannot claim a `perception_error` detection
from a backend that cannot produce the signal.

> [!danger] ⚠️ And a prerequisite I missed entirely: **the Protocol carries no checkpoint**
> (`c4-sim2real`). Verified: `run_episode(self, task, seed, randomization_level, scenario=None)` —
> **the backend never receives the policy at all.** So the mock *cannot* return a different outcome
> for a different checkpoint, and **no amount of interface work fixes that** — it is a missing
> *input*, not a missing abstraction. A second backend inherits exactly the same blindness.
> **So build the abstraction AFTER the Protocol carries the checkpoint** (or a `PolicyProtocol`
> with `reset()` / `act(obs)`). The second engine is the **payoff, not the prerequisite** — which
> inverts the sequencing I originally proposed and makes this section even more clearly 2027 work.
> It also means the mock is currently incapable of supporting §1's cross-checkpoint regression
> detection, because there is nothing checkpoint-dependent to vary.

**The strategic argument is unchanged and still the strongest in this section.** If ValidSim is
a scorecard bolted onto Isaac, NVIDIA is your competitor. If ValidSim is *the scorecard any
backend plugs into*, NVIDIA is your distribution partner and cannot absorb you without becoming
the thing you are independent of. Same code, opposite strategic position.

**But it is 2027 work, and it is NOT claimable in the Nov 2 deck.** The realistic sequencing:
`Capabilities` + `describe()` as a **declared, unimplemented interface** is cheap and honest
to show on a roadmap slide; the `EpisodeResult` schema change and a second real backend are not.

**Effort:** `Capabilities` + `describe()` + conformance suite **2–3 weeks**. Making
`EpisodeResult` capability-conditional **plus a second working backend (minimal MuJoCo running
one task — the point is the seam, not the simulator) 3–5 weeks.** Total **5–8 weeks**, and
competing for the same founder-hours as the GPU worker.
**MuJoCo specifically is the right second engine for two independent reasons** (`c4-sim2real`):
**(a) permissively licensed** — which matters directly because **Isaac Lab is research-use only**,
so anyone shipping Isaac Lab commercially inherits a licensing constraint; and **(b) it is the
natural cross-engine portability filter** for §4's sim2real overfit problem. **The second engine
therefore does double duty: vendor-risk hedge *and* validity filter.**

**Time-to-moat:** 12–24 months, but the *option value* is immediate and is the real payoff.

**Candid risk:** premature-abstraction risk is real, and my original "low effort, high priority"
framing was wrong on both counts. The bigger risk remains the opposite — **not** doing it and
discovering too late. But the honest position is: **declare the interface now, implement it in
2027.** Do not let this compete with the worker for pre-YC hours.

---

## 10. What NOT to build before Nov 2

Given 2 founders and **5.4 weeks** to the W27 deadline, with the GPU worker unbuilt:

| Do not build | Why |
|---|---|
| **Anything requiring the GPU worker to be *finished*** — benchmark leaderboard, synthesis loop, throughput benchmark, cross-customer library, on-prem packaging. | The ~15 GB worker image is not in this repo. Every one of these is blocked on it, not on your code. |
| **Compliance evidence packages / ISO 10218-13482 generation** | Explicitly Year 1–2 in [[Compliance]]. Needs insurer pilots and legal review. The vault's own sequence is "align → pilot → certify" and you're at "align." |
| **HIL / ROS 2 bridge** | Non-goal, explicitly. Needs fleet hardware access. |
| **Fleet-scale deployment gating** | Year 2, needs multi-tenant auth + RBAC. |
| **Multi-embodiment matrix** | Non-goal. Embodiments explode test surface. One robot proves the pipeline. |
| **Cosmos integration** | Year 2, needs Inception early-SDK access that isn't confirmed. |
| **Billing / Stripe / multi-tenant SaaS** | Non-goal. Design partners contract manually. |
| **Next.js dashboard rewrite** | Current static dashboard works. Rewriting it burns 2 weeks and produces zero YC evidence. |
| **SOC 2** | Year 2 per the vault. |
| **Shipping the LLM generator as a differentiator** | It's unwired (`pipeline.py:131`), it's a thin JSON wrapper, and it **imports `httpx` and makes network calls** — which directly conflicts with §7 air-gapped. Do not lead with it. |

### ⚠️ The most important "do not build": do not open-source the current monorepo

**This is the highest-severity finding in this analysis and it is a direct contradiction of
the stated IP strategy.**

`vault/08 - Team & Legal/IP Strategy.md` §18.2 says: *"The MIT CLI is marketing spend; the
scenario generator is the company."* `vault/06 - Business/Go-to-Market.md` Phase 2 says:
*"Open-source CLI tool + GitHub Actions plugin (MIT license)."*

**But the CLI is not a thin client over a proprietary API — it contains the entire scoring
engine.** In `validsim/cli.py::_run_impl`, the CLI calls
`run_and_score(...)` **in-process**, passing `run_validation` and `create_backend`
explicitly. So `validsim run` executes locally, and MIT-licensing the package publishes:

- `validsim/engine/scorecard.py` — the composite formula and **all four weights**
  (`_W_SUCCESS=0.4`, `_W_SAFETY=0.3`, `_W_ROBUSTNESS=0.2`, `_W_REGRESSION=0.1`) plus
  `_ROBUSTNESS_SCALE=200.0` and the regression penalty constant
- `validsim/engine/safety.py` — the safety weights (`_COLLISION_WEIGHT=0.5`,
  `_FORCE_WEIGHT=0.3`, `_PROXIMITY_WEIGHT=0.2`) and the force/proximity limits
- `validsim/engine/evaluation.py` — the evaluation semantics
- `validsim/engine/stats.py` — the CI and significance methodology
- `validsim/scenarios/generator.py` — **the "scenario generator" the IP note claims is the
  company**, including the 12 categories and all parameter ranges
- `validsim/sim/runner.py` — `FAILURE_MODES`, the 7-mode taxonomy, and the `SimulationBackend` contract

Additionally, `actions/validate/action.yml` confirms the Actions plugin
`pip install .` — i.e. **the Action ships the whole package**, not a client. The vault's
claim that "the plugin is a thin client over the proprietary API anyway" is **not true of the
current code**; the hosted API doesn't exist yet (the Action's own description says "the
hosted SaaS backend (API-key based) is coming").

Under MIT terms, publishing that is an **irreversible public disclosure** of the exact
assets §18.2 designates as trade secret — and it would happen on the same day the
provisional patents are supposedly being filed, creating a public-disclosure clock on the
same day as the filing.

**Recommended fix, before any publication:**
1. **Split the package.** `validsim-client` (MIT: CLI surface, `submit`/`status`/`gate`,
   thin HTTP client, the Actions plugin) vs. `validsim-core` (proprietary: engine,
   scenarios, taxonomy, scoring). The Action installs the *client* and calls the API.
2. **Rewrite the Action** to not `pip install .` the monorepo.
3. **Change `pyproject.toml`** from `license = "Proprietary"` for the whole package — today
   it correctly says Proprietary, which is *inconsistent* with the Phase-2 plan to MIT it.
4. **File provisionals first** (per §2 and the vault's own "file → publish" gate) — before
   the demo video, before the blog post, before the benchmark.

Until the client/core split exists, **the Phase-2 open-source plan is not executable without
giving away the moat.** This is a bigger threat to the moat than any competitor.

---

## 11. Prioritization for a 2-founder team, 5.4 weeks to Nov 2

The binding constraint is not ideas or effort — it's that **the GPU worker does not exist**,
and most of the 10 items are downstream of it.

> [!important] REVISED after cross-team review — the sequencing changed materially
> `c3-engine` identified that **`EpisodeResult` does not carry `scenario_category`** (D7), and
> that one schema addition unblocks the D2 robustness fix, the §1 flywheel, and `c4-statistics`'
> composite-CI redesign simultaneously. It is also a **wire-contract change**
> (`isaac_worker.py` derives its expected field set from the dataclass), so it needs a
> superseding ADR. **If the lead is picking one piece of work to sequence first after the IP
> split, that is it.**

### Before Nov 2 (the only window that matters for W27)

| Rank | Item | Effort | Why now |
|---|---|---|---|
| **1** | **Fix the open-source/IP contradiction** (§10) — split client from core | **2–3 days** | Highest-severity *irreversible* risk. Cheap. Once published, the weights and generator are gone. |
| **2** | **Per-segment scoring: gate on adversarial success** (D9) | **2–4 days** | **Most damaging finding.** The gate currently APPROVES a checkpoint failing 100% of its adversarial suite. We sell adversarial testing without gating on it. Fix before any deck leads with that differentiator. |
| **3** | **Add `scenario_category` (+ `difficulty`) to `EpisodeResult`** (D7) | **~1 day** | Unblocks D2's fix, D9's per-segment scoring, §1's flywheel, and composite-CI in one defaulted-field change. Wire-contract change → ADR. |
| **4** | **Fix the robustness constant** (D2) — regroup by `scenario_category`, gated off on mock | **1–2 days** | Two of four terms are constant (+30 on CLI/worker), and the "episodes scale with trust" upsell moves nothing on those axes. Revenue bug. |
| **5** | **Add the `scenario_id` → `episode_outcomes` join + append-only table** (§1) | **1.5–2 wks** (`c3-engine`) | The flywheel's missing edge. Can't retrofit once you have history. **Also a safety-signal-integrity fix** — see below. |
| **6** | **Correct the false `SECURITY.md:276-279` tamper-evidence claim** | **<1 day** | A security reviewer will read it. Doc fix, not a build. |
| **7** | **`validsim` model-card exporter** (§6) — with a **mock-backend guard** on `robustness` | **2–3 days** | Cheap, and it's a YC public artifact from one command. But per D6/D12 it must not publish a constant metric as if measured. |
| **8** | **`validsim power` calculator** | **2–3 days** | Converts a hidden power gap into a credibility asset. Same move as publishing anti-gaming machinery. |
| **9** | **Make the mock read `scenario.params`** (D12) | **4–6 days, CPU** | Makes the §4 closed loop **demonstrable end-to-end on CPU today**, and is the prerequisite for a *calibrated* mock later. Zero GPU dependency. |
| **10** | **Transactional append path** (D10) — `pg_advisory_xact_lock` + gapless `seq` PK | **~1 wk**, shared | **Gates §1 and `c4-compliance`'s ledger.** Solve once, properly, for both append-only tables. |
| **11** | **Add the `scenario_id` → `episode_outcomes` join + table** (§1) | **1.5–2 wks** (`c3-engine`) | The flywheel's missing edge. **Sequenced behind #10** — must not ship append-only before the chain is safe. |
| **12** | **GPU worker** (not detailed here — `c3-engine`'s scope) | **the critical path** | Everything else queues behind this. |
| **13** | File the **two** provisionals the vault already specifies (pipeline architecture + adversarial scenario generation) — **not** on the taxonomy (§2) | 1–2 days + legal | The vault's own gate. The demo video is a publication event. |

> [!important] Why #5 is now justified on **safety**, not just throughput economics
> `c5-ml` and `c3-engine` independently found that the composite and regression signals are diluted
> by a **user-configurable adversarial fraction**: `adversarial_count` maxes at 1,000 while
> `episodes` maxes at 100,000 (`config.py:136,138`), so an operator can set adversarial weight to
> ~1% and **the gate will barely notice a total adversarial wipeout** (verified in D9). The
> normalized `episode_outcomes` table with `scenario_category` on each episode is what makes that
> dilution *visible and configurable-safe* — it is a **safety-signal-integrity argument**, which is
> a far stronger justification to a lead than cost-per-episode economics.

**Not before Nov 2:** benchmark leaderboard, synthesis loop, cross-customer library (§5 now
"do not build"), throughput benchmark, on-prem packaging, Isaac Lab package, HIL, compliance
packages, dashboard rewrite, Cosmos, billing, SOC 2, and the multi-vendor capability
interface (§9 is now 2027 rearchitecture). All are real; none can be evidenced in the
window that determines whether the company gets funded.

### After YC (Dec 11 decision → Jan batch → 2027)

1. **Public benchmark leaderboard** (§3) — must launch before a competitor's, so **early in
   2027**; the MDE gate + private hold-out task set is the gating work.
2. **Sim-feedback synthesis loop** (§4) — 9–15mo to moat, highest technical risk.
3. **Second simulation backend + capability-conditional `EpisodeResult`** (§9) — 5–8 wks of
   rearchitecture, the "we don't only do Isaac" proof.
4. **Air-gapped/BYO-GPU SKU** (§7) — reuse the worker, license it annually.
5. **Immutable hash-chained audit trail + retraction path** (§12) — 1–2 wks, the strongest of
   these five for a safety product; blocked on project-scoped identity.
6. **Sim-to-real telemetry + conformal band** (§14) — schema first; the only structurally
   uncopyable moat, but needs Enterprise-tier customers willing to return deploy outcomes.
7. ~~Cross-customer fingerprint library~~ (§5) — **removed from the roadmap**; requires
   multi-tenancy, carries a privacy liability, and is confounded without `difficulty` on the
   episode. Only the per-customer weakness profile survives.

---

## 12. Cross-cutting: the audit trail is the keystone, and it's the most honest "build this"

The vault is admirably candid that the audit trail does not exist, and that the current
stores are *deletable*: `DELETE /api/v1/validations/{run_id}` and `validsim delete`
permanently remove validation evidence (`Compliance`, `Data Flow` Step 6, `Solution
Architecture`).

**This is the single sharpest contradiction in the strategy.** The terminal moat per
[[Moat]] is "once insurers adopt our scorecard format, it's de facto standard." Insurers
adopt evidence they can trust is **immutable and retained**. A product whose flagship
compliance pitch is evidence packages, while offering `DELETE` on evidence, is
self-refuting — and the contradiction is one line of documentation away from a deal
falling apart in a security review.

**Two coherent options — and `c4-compliance` has resolved which, which corrects my own framing
that they were mutually exclusive:**
- **(a) Build decision:** remove `DELETE` (or restrict it to a retention window) and add
  append-only + hash chaining. Cost: a customer-visible capability removal.
- **(b) Policy decision:** keep operator deletion but require stronger authorization and
  record **every deletion** in a separate durable ledger.

**The resolution: (b) is the product decision, implemented with (a)'s database-enforced
machinery underneath.** The two are not exclusive — (a) is the DB-enforced version of what (b)
describes procedurally. `docs/runbook.md:433-441` lays out exactly these options and selects
none; ADR 0004's title ("PostgreSQL append-only") overstates the guarantee and should be
annotated as superseded on this question. Record the decision in **ADR 0007**.

**What "what breaks if we remove DELETE" actually answers, per `c4-compliance`:**
- **Dev/CLI: nothing.** memory and SQLite keep `delete()`; `validsim delete` stays. Those
  tiers make no compliance claims.
- **Enterprise: DELETE survives as a *retraction*, not a removal.** DELETE becomes a ledger
  event (`validation.retracted` + reason), marks `retracted_at`, and the auditor sees a recorded
  retraction rather than a hole.
- **The decision that matters is what happens to the payload.** ValidSim stores only *derived*
  scores and episode observables, never checkpoints. The safe default is **preserve evidence and
  ledger entry; let a reasoned retraction supersede rather than purge.** A purge should be a
  separate, audited, admin-only, legal-hold-aware endpoint — **not the ordinary DELETE path.**
- **Why this is a build decision, not only a policy one:** today's `delete()` has **no actor, no
  reason, and no record.** Insurance-grade evidence needs actor identity (which does not exist
  today — one shared `VALIDSIM_API_KEY`) and a reason field. Both are required whichever option
  you pick. **Ship the retraction path only after project-scoped identity lands.**

**⚠️ And one doc fix that costs nothing but protects credibility:** `SECURITY.md:276-279` currently
says a persisted record "makes tampering detectable," which is **false** for memory and SQLite.
A customer's security reviewer will read it. Correct the claim, or the build has to land first.

**Effort (per `c3-engine`):** the chain itself is ~1 day; the hard part is **canonicalization** —
you must hash a byte-stable serialization, and `Scorecard.to_dict()`/`to_json()` is not
guaranteed stable across Python versions (dict ordering, float repr). Use canonical JSON (sorted
keys, fixed float formatting) and **pin the schema version into the hash preimage**. Plus:
resolve the memory/SQLite overwrite divergence or scope the chain to Postgres; **verify on read,
not just write on write** (otherwise it is decoration); and keep all new fields defaulted so
`notify/email.py:69-79`'s `Scorecard(**kwargs)` does not break.

**Note this also strengthens §5's surviving per-customer part:** a retained audit trail is a
switching cost, and switching means abandoning your deployment-decision history. That is a
lock-in customers accept *willingly*, because it is also their liability defense — the buyer
motive the vault already names.

---

## 13. One-page summary

**The moat is not the seven advantages. It is:**
1. **An accumulation asset** — `episode_outcomes` with the `scenario_id → outcome` join
   (§1). Time-to-copy ∝ customer-months. Build the schema before you have the data.
2. **A published standard** — the benchmark (§3) and the scorecard **schema**. Note: the
   taxonomy is **no longer** one of these legs (§2) — it has no method to protect.
3. **A retention artifact** — the hash-chained audit trail (§12), which turns "we run
   your validation" into "we hold your deployment-decision history."
4. **A real-world anchor** — sim-to-real transfer telemetry + a conformal band (§14). The
   only capability a sim-only competitor structurally cannot copy.

**And it is currently undercut by ELEVEN verified defects (see §0) — plus one retracted claim
that did not survive testing:**
- **D1** — the open-source plan (§10) would publish the scoring weights and the scenario generator,
  the exact assets the IP note claims is the company. **Fix before anything publishes.**
- **D2** — the robustness score is a constant 100.0, so **two of four composite terms are inert on
  the CLI/worker paths (+30 points)**, and the "episodes scale with trust" upsell moves nothing on them.
- **D9** — **the gate APPROVES a checkpoint failing 100% of its adversarial suite.** We sell
  adversarial testing without gating on it. Approval floor is 62.5% success at perfect safety.
- **D3** — 2 of 7 failure modes have no detector behind them (§2). **Scope to 5 or mark 2 preview.**
- **D4** — no compute-time instrumentation (§8), so no cost or throughput claim is measurable.
- **D5** — mock failure labels are *contradictory*, so any classification accuracy number is fabricated.
- **D6** — the D2 fix is self-taunting on mock data (R² > 0.88 at every n tested, →0.97 at 1,000/cat).
- **D7** — `EpisodeResult` lacks `scenario_category`, blocking the fixes for D2/D9 and the flywheel.
- **D10** — the Postgres store **cannot yet support a gapless append-only chain** (autocommit +
  process-local lock across separate API/worker processes). **Gates §1 and `c4-compliance`'s ledger.**
- ~~**D8**~~ — **RETRACTED.** The permutation-test claim was wrong; the arithmetic is correct for
  continuous input. Only a cosmetic rename remains, and **no runtime guard** — a raising check in
  the gate path would be strictly worse than the naming confusion.
- **D12** — the mock **ignores `scenario.params` entirely** (bit-identical output across param
  sweeps at fixed seed), so the §4 synthesis loop is a **zero-information objective** until the
  mock reads params. **4–6 days CPU, no GPU** — and the prerequisite for a calibrated mock later.

**Sequencing for 5.4 weeks, 2 founders:** fix the IP contradiction → **per-segment adversarial
scoring (D9)** → **add `scenario_category` to `EpisodeResult` (D7)** → fix the robustness constant
(D2) → correct the false `SECURITY.md` claim → model-card exporter → power calculator →
**make the mock read `params` (D12)** → **transactional append path (D10, gates §1)** →
`episode_outcomes` → **GPU worker** → file the two provisionals → then, and only then, the
public benchmark.

> [!danger] The one-line version for the deck
> **We sell adversarial testing, and the CI gate does not currently gate on adversarial results.**
> Everything else in this document is an opportunity; this is a hole in the product's primary
> claim, and it is 2–4 days to close.

> [!note] The pattern worth keeping (`c5-ml`)
> **Every substantive error in this audit was caught because someone re-ran the claim instead of
> accepting it — and the two that survived longest both survived because they were *plausible*.**
> That is also why D9 lived in the codebase this long: it is not absurd, it is quietly wrong in a
> way that only shows up when you trace which episodes enter the score. The mitigation is cheap
> (re-run it, state the decision rule and the n) rather than expensive (being right first time),
> which is the argument for doing exactly this before Nov 2 rather than after a diligence reader
> finds it.

---

## 14. Sim-to-real transfer — the deepest hole, and the one true structural moat

**Added 2026-09-25 after review by `c4-statistics`.** Not in my original analysis, and
arguably more important than several items that were.

### The problem

**ValidSim ships zero sim-to-real instrumentation.** There is no `EpisodeResult` field, no
store column, and no scorecard field carrying a real-world outcome. The company is named
"Sim-to-Real," `Competitive Landscape` claims ownership of the "Sim-based × pre-deployment"
quadrant, and the pitch is that simulation predicts deployment safety. **None of that is
measured.** We sell transfer validity while holding no evidence of transfer validity.

This compounds D3: the taxonomy has no real-world ground truth to calibrate against, and the
composite score has no external anchor. An insurer shown the scorecard is seeing a number with
no out-of-sample validation that it predicts anything.

### Why this is the only true structural moat

`c4-statistics` identified exactly one capability in their package that a **sim-only
competitor structurally cannot copy**: **conformal prediction on sim-vs-real residuals.** Every
other method — composite CI, sequential testing, TOST, Holm/BH, Wasserstein robustness,
change-point detection, risk envelopes — is sophisticated engineering a funded competitor can
replicate in 6–12 months. Conformal bands need **paired `(sim score, real-world outcome)`
telemetry that only accumulates through customer deployments.** That is the data-flywheel moat
(§1) fused with the compliance-standard moat (§12), and it compounds with tenure.

**The same schema also unlocks §4's honesty problem.** The honest answer to "do your adversarial
scenarios transfer to the real world?" is currently "we don't know." Capturing real-world
outcomes lets you validate discovered scenarios against reality — the only *real* defense
against simulator-artifact overfit.

### Implementation

1. **Schema first, and fold it into §1's `episode_outcomes` rather than creating a parallel
   initiative.** Add `real_world_outcome` (`success | failure | unknown`, nullable),
   `real_world_failure_mode` (links to the 7-mode taxonomy, nullable), and
   `real_world_observed_at` to the episode record, plus a lightweight `deployment_link` table
   (`checkpoint_id` → deployed version → incident count) so sim scores pair with reality.
2. **Pairing is a product decision, not just engineering.** The customer must report deploy
   outcomes. Realistically an Enterprise feature: it only serves customers willing to return
   deployment telemetry — a minority, conveniently the tier `Pricing Tiers` already prices at
   $20K–$50K/mo.
3. **Conformal band with a hard `n_calibration < 30 → emit no band` guard.** Calibration must
   be disclosed on every scorecard. Two honesty constraints:
   - It is a **liability surface**: an 80%-coverage band that misses reads as a breach to a
     non-statistician even though it is statistically correct. Disclosure is not optional.
   - Conformal guarantees are conditional on **exchangeability**. If a customer's real-world
     distribution drifts from the sim population, the band silently loses its guarantee. Surface
     *exchangeability status* as part of the output, not bury it.
4. **A `validsim transfer` report** — "your sim score predicted X, reality was Y, here is your
   calibration residual" — is the Enterprise artifact that makes conformance standards credible.
