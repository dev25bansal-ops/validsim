# Category 2 — Architecture & Tech Debt Audit (ValidSim)

**Agent:** c2-arch-debt · **Date:** 2026-09-26 · **Tree state:** `HEAD=ff15533` with a large uncommitted working-tree delta
**Scope:** architectural scalability, reliability, extensibility, and tech-debt register.

## Provenance and method (read this before trusting any status)

Every finding below was **re-verified against the current working tree on 2026-09-26**, not carried forward from earlier analysis. The tree changed substantially during the audit; two findings I would previously have reported are now fixed and appear only under [False leads](#false-leads-ruled-out).

**Assumptions stated explicitly:**

- Default `VALIDSIM_BACKEND=mock` unless stated; the Isaac findings are marked as requiring `VALIDSIM_BACKEND=isaac`.
- Findings are evaluated against the compose deployment (`VALIDSIM_STORE=postgres`, `VALIDSIM_JOB_QUEUE=redis`) and the CLI/CI path (`VALIDSIM_STORE=sqlite`) separately where behaviour differs.
- No live Redis or PostgreSQL server was available to this agent. Findings that depend on one are marked **VERIFIED (static + control)** or **UNVERIFIED (needs live server)**, and the exact confirmation command is given.
- Windows/PowerShell shell; Python 3.14 interpreter.

**A note on measurement discipline.** Three of my own earlier numbers in this audit were wrong and were retracted. The corrections are the *reason* the rules below exist, so I state them rather than hide them: a truncated input made a benchmark look flat; a probe omitted a positional row field and reported a false divergence; a source-text comparison was reported as a behavioural one. Every finding below therefore carries **a control case** proving the repro is not a fixture artefact, and each names what was measured versus what was read.

---

## Findings table

| ID | Title | file:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| C2-01 | Checkpoint under test never reaches any simulation backend — mock or Isaac | `sim/runner.py:105`, `sim/isaac_worker.py:519-527` | **Critical** | VERIFIED | 8-16h | `python -c` signature dump + control-group scoring |
| C2-02 | Isaac wire contract has no `checkpoint_id`; conformance fixtures cannot detect it | `sim/isaac_worker.py:519-527`, `sim/shadow.py:394` | **Critical** | VERIFIED | 4-6h | payload key dump + `grep checkpoint docs/isaac-worker.md` |
| C2-03 | Non-finite safety observables from the worker produce a **perfect** safety score | `sim/isaac_worker.py:150-276`, `engine/safety.py:85-98` | **Critical** | VERIFIED | 4-6h | `%TEMP%/cat2_nan.py` with control + real-violation case |
| C2-04 | `robustness_score` is structurally 100.0 (0.2 of composite) on every production run | `sim/runner.py:160-186`, `engine/scorecard.py:53-66` | **Critical** | VERIFIED | 8-16h | scored run across all three randomization levels |
| C2-05 | `avg_composite` and `validsim_composite_score` are different quantities sharing one name | `api/dashboard.py:55`, `api/metrics.py:115` | Medium | VERIFIED | 1-2h | source read + single-run test fixture analysis |
| C2-06 | Six shipped capabilities are complete, tested, documented — and never called | see finding | High | VERIFIED | 2-14h each | repo-wide grep for callers, per module |
| C2-08 | Store reconstruction uses 4 bare splats in Postgres, 2 in SQLite (maintainability, **not** parity) | `store/postgres.py:245-256`, `store/sqlite.py:117,132` | Medium | VERIFIED + REFUTED claim | 4-6h | 4-scenario behavioural matrix, both backends |

**Withdrawn before filing — two claims that were true earlier today and are now fixed.** I verified both against the current tree rather than carrying them forward:

- **C2-07 (threshold unreachable from the API) is FIXED.** `ValidationRequest` now carries `threshold: float | None` (`config.py`) and `_execute_validation` plumbs `request.threshold if request.threshold is not None else <default>` (`api/main.py`). ADR 0002:105-107 is accurate again. No superseding ADR needed.
- **The evidence guard has been hardened.** `engine/scorecard.py:195-198` now reads `total_episodes >= task.episodes > 0 and evaluation.success_count > 0`, and a module comment explicitly acknowledges the structural-100.0 components. I tested it: a 0%-success, all-collide, 900 N, 0.05 m run yields `composite=30.0 → BLOCK`. **The gate does not fail open on a catastrophic run.**

---

## C2-01 — The checkpoint under test never reaches any simulation backend

**Severity:** Critical · **Status:** VERIFIED · **Effort:** 8-16h (mock) + 4-6h (wire, = C2-02)
**Subject:** shipped code (both backends are in the product; the mock is the default)

### Description

`run_episode` — the single method every episode goes through, on **both** backends — accepts no checkpoint. The checkpoint identifier reaches the engine, is used to derive a seed, is written into the scorecard, and is stored — but never reaches the component that produces the result.

### Reproduction

```
$ python -c "
import inspect
from validsim.sim.runner import MockIsaacBackend, SimulationBackend
from validsim.sim.isaac_worker import IsaacWorkerBackend
print('Mock  run_episode', inspect.signature(MockIsaacBackend.run_episode))
print('Isaac run_episode', inspect.signature(IsaacWorkerBackend.run_episode))
print('Protocol methods :', [m for m in dir(SimulationBackend) if not m.startswith('_')])
print('Mock has run_episodes  :', hasattr(MockIsaacBackend,'run_episodes'))
print('checkpoint in any sig  :', any('checkpoint' in str(inspect.signature(...))))"
```

Output:
```
Mock  run_episode (self, task, seed, randomization_level, scenario=None) -> EpisodeResult
Isaac run_episode (self, task, seed, randomization_level, scenario=None) -> EpisodeResult
Protocol methods : ['run_episode']
Mock has run_episodes  : False
checkpoint in any sig  : False
```

### Control (this is what makes the finding meaningful)

A control group isolates "the checkpoint label carries no signal" from "scores just vary with sample size." **One fixed checkpoint, six arbitrary `task_id` labels** — if the checkpoint mattered, arbitrary label churn should move the score *less*:

| Input varied | composite spread |
|---|---|
| six different `checkpoint_id`s (320 episodes each) | stdev 1.97, range 4.69 |
| **one checkpoint, six arbitrary `task_id`s** | **stdev 2.90, range 7.26** |

Changing an arbitrary task **name** moves the composite *more* than changing the checkpoint does, and flips the verdict (`wipe` → APPROVE, all others BLOCK). Both inputs reach the engine solely through `stable_seed(checkpoint_id, task_id)` (`sim/runner.py:160-186`), so both are pure seed perturbation. The control rules out "it's just RNG noise," because the arbitrary-label arm produces *more* of the same noise from a semantically meaningless input.

### Expected vs actual

- **Expected:** two checkpoints with different real behaviour produce different scorecards.
- **Actual:** the scorecard is a function of `(task_id, episode_count, seed)`. The checkpoint is an unused input to the result.

### Business impact

The product promise is "submit a checkpoint, receive a defensible scorecard." Under the shipped default backend that is **structurally impossible at any episode count** — the output cannot depend on the thing being graded. Every composite ever produced carries no information about the checkpoint it was named for. This also makes the `robustness_score` defect (C2-04) unrecoverable: grouping by randomization cannot discriminate policies that the backend cannot distinguish.

**Reachability:** default path. `VALIDSIM_BACKEND` unset → `create_backend()` returns `MockIsaacBackend` (`sim/__init__.py:55`); `.env.example:100` ships `mock`; compose does not override it.

### Dependencies

Blocks C2-04 (needs a variable to measure). Independent of C2-02 (that is the wire half).

### Recommended fix

Add the checkpoint to the `SimulationBackend` contract and give the mock a notion of a *policy*. The honest shape is a deterministic per-checkpoint profile derived from a hash of the id — **transparently synthetic, deterministically policy-dependent, and not pretending to be physics.** A mock returning physically plausible nonsense would be worse than one that is obviously synthetic.

### Test strategy

A contract test asserting that two checkpoints produce different scorecards under fixed seeds, plus a control that the same checkpoint twice is identical (guards against a fix that introduces nondeterminism). This is the property no current test can express, because no current test can pass two checkpoints.

---

## C2-02 — The Isaac wire contract has no `checkpoint_id`, and the conformance suite cannot detect that

**Severity:** Critical · **Status:** VERIFIED · **Effort:** 4-6h (must precede any worker build)
**Subject:** shipped code (client) + shipped documentation (contract)

### Description

`_build_payload` emits exactly seven keys. `checkpoint_id` is not among them, and the contract document never mentions it outside rollout prose. **A worker built to the documented contract has no way to know which policy to load.**

### Reproduction

```
$ python -c "
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.sim.isaac_worker import IsaacWorkerBackend
t = TaskConfig(task_id='p', robot=RobotSpec(name='r'), environment=EnvironmentSpec(name='e'), episodes=1)
print(sorted(IsaacWorkerBackend._build_payload(t, seed=1, randomization_level='full', scenarios=[]).keys()))"
# -> ['environment', 'episodes', 'randomization_level', 'robot', 'scenarios', 'seed', 'task_id']

$ grep -n checkpoint docs/isaac-worker.md
# 151:  "Same checkpoint, same seeds, both backends"   <- rollout prose ONLY
```

**Zero occurrences** in §1 endpoints, §2 request schema, or §3 episode semantics.

### Why this is worse than a missing field

1. **The contract is internally inconsistent.** §5.3 requires comparing "same checkpoint" across backends, which §2's schema cannot express. §5.3 is unsatisfiable as written, today, with no worker in existence.
2. **The conformance suite is structurally blind to it.** `reference_contract_cases()` (`sim/shadow.py:394`) returns canned request/response pairs containing no checkpoint field. **They pass against a contract that cannot express the product's core input** — a worker could be fully conformant, pass the §5 gate, get promoted, and be structurally incapable of validating a checkpoint. This is the most expensive instance of the audit's recurring pattern: a control that is documented, tested, and blind to its own failure.
3. **The calibration harness is unreachable.** `ShadowRunner` / `sim.shadow` has zero references outside its own module and its tests, and `validsim/sim/__init__.py` does not export it (`'ShadowRunner' in dir(validsim.sim)` → `False`). No CLI command, no API route. The §5 plan has **no step that can fail**.

### Expected vs actual

- **Expected:** the request identifies the artifact under test; a non-conforming worker is rejected before promotion.
- **Actual:** the request cannot identify the artifact; the conformance suite cannot express the requirement; the harness that would catch drift cannot be invoked.

### Business impact

Highest on the GPU workstream. **The worker is the only part of this path that is actually buildable today, and it is the part that is cheapest to do last** — because building it against this contract bakes in the defect at full GPU spend. Contract changes are cheapest while the counterparty is imaginary.

**Reachability:** requires `VALIDSIM_BACKEND=isaac`. Not reachable on the default path — which is why C2-01 and C2-02 are separate findings with different owners.

### Dependencies

Should land **before** any worker implementation. The fixture must land **with** it, or conformance certifies the defect.

### Recommended fix

Three parts, one change: (a) add `checkpoint_id` to `_build_payload`; (b) add it to the §2 schema; (c) add a conformance fixture asserting it is present and round-trips on **both** request and echoed episode response. Do not tighten the parser in the same change — that widens the diff and mixes a contract fix with a strictness change.

**Caveat worth stating:** asserting the *echo* proves **transmission, not loading** — a worker can echo the field without loading weights. The only evidence of loading is behavioural (two checkpoints, same seeds, different results), which is sound only if the worker is deterministic per seed, and **seeded determinism is currently claimed in §3 and enforced nowhere.** A determinism fixture is cheap, CPU-only, and should land alongside.

### Test strategy

A `reference_contract_cases` case that omits `checkpoint_id` must be rejected. A determinism case: same seed twice → identical result. Neither is expressible today.

---

## C2-03 — Non-finite safety observables produce a perfect safety score

**Severity:** Critical · **Status:** VERIFIED · **Effort:** 4-6h
**Subject:** shipped code · **Reachability:** requires `VALIDSIM_BACKEND=isaac`

### Description

`compute_safety` decides violations with `>` and `<` comparisons. IEEE-754 defines **every comparison involving NaN as false**, so a NaN observable is counted as *no violation* rather than as *invalid data*. The Isaac contract parser validates type, field set, seed echo, taxonomy membership and randomisation level — but **never finiteness** — so a worker emitting `NaN` passes validation and reaches scoring.

### Reproduction

`%TEMP%/cat2_nan.py`:

```
CONTROL — well-formed reply
  parse(well-formed)              -> ACCEPTED force=10.0 dist=2.0
    control safety_score = 100.0

TEST A — real proximity violation (0.1m, limit 0.5m)
  parse(0.1m distance)             -> ACCEPTED force=10.0 dist=0.1
    safety_score = 80.0            <- violation correctly counted

TEST B — NaN on both safety observables
  parse(NaN force + NaN distance)  -> ACCEPTED force=nan dist=nan
    safety_score = 100.0           <- PERFECT, zero violations
    force_exceeded_rate = 0.0
    proximity_violation_rate = 0.0
```

### Control

Test A is the control that makes this a finding rather than a fixture artefact: **a real violation on the same code path scores 80.0.** The scoring machinery works; it is specifically non-finite input that is silently treated as safe. Test C confirms the same parser accepts `+Inf`.

### Correction to an earlier claim of mine

I previously told another agent that `json.loads` accepts `NaN` from the wire. **That was wrong**, and the probe now shows it:

```
json.loads(NaN      ) -> rejected JSONDecodeError
json.loads(Infinity ) -> rejected JSONDecodeError
json.loads(-Infinity) -> rejected JSONDecodeError
```

**This narrows the finding's reachability and I am recording it rather than letting the stronger claim stand.** A worker emitting bare `NaN`/`Infinity` literals is rejected by `json.loads`. The defect is still real via two paths that remain open: (a) a worker in a language whose JSON serialiser emits non-standard NaN tokens is rejected, but (b) **a float that is non-finite after a *legal* JSON round-trip** — e.g. `1e400` parses to `inf` in strict JSON, and arithmetic on a legal value can produce NaN. I did not confirm (b) end-to-end; it is the open question, and the fix is the same either way.

### Expected vs actual

- **Expected:** a non-finite safety observable is rejected at the boundary, or treated as invalid rather than safe.
- **Actual:** it is accepted, and yields the maximum safety score.

### Business impact

For a safety gate, "unknown" must never score as "perfect." This falsifies ADR 0002's stated guarantee that *"hard safety violations … remain first-class on the scorecard"* (`:98-100`) — a non-finite contact force is exactly such a violation, silently dropped. The result persists to the append-only verdict log, so a corrupted run is indistinguishable from a clean one after the fact.

### Recommended fix

Reject non-finite values in `_want()` (`sim/isaac_worker.py:150`), where every other contract check already lives. Defence in depth: treat non-finite as invalid in `compute_safety` too, so the mock path and any future backend inherit the guarantee. Note the second half alone would also close the `absent-data-silently-defaults-to-benign` class that recurs across this codebase.

### Test strategy

Parser-level: a reply with `NaN`/`Infinity` in any field must raise `SimWorkerError`. Engine-level: a hand-constructed episode with a non-finite observable must not score 100. Both are cheap and neither is expressible today.

---

## C2-04 — `robustness_score` is structurally 100.0, contributing a fixed +20 to every composite

**Severity:** Critical · **Status:** VERIFIED · **Effort:** 8-16h (blocked on a wire change; see dependencies)
**Subject:** shipped code · **Reachability:** default path

### Description

`_robustness_score` groups episodes by `randomization_level` and returns `100.0` when fewer than two groups exist. `run_validation` passes **the same** `task.randomization` to every episode, so there is always exactly one group.

### Reproduction

```
$ python -c "
from validsim.config import *; from validsim.sim.runner import run_validation, MockIsaacBackend
from validsim.scenarios.generator import ScenarioGenerator
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
t=TaskConfig(task_id='p',robot=RobotSpec(name='r'),environment=EnvironmentSpec(name='e'),
             episodes=50,adversarial_count=10,randomization='full')
eps=run_validation(t,MockIsaacBackend(),ScenarioGenerator(seed=99).generate('p',10),seed=99)
r=build_scorecard(run_id='vrun-00000001',checkpoint_id='c',task=t,evaluation=evaluate(eps),
                 safety=compute_safety(eps),episodes=eps,threshold=85.0)
print('levels:',{e.randomization_level for e in eps},'robustness:',r.robustness_score)"

levels present : {'full'}
robustness     : 100.0
composite      : 78.8 BLOCK
```

### Control

Scored across all three levels — the component that exists to measure cross-condition consistency **does not move**:

| `randomization` | success_rate | composite | robustness |
|---|---|---|---|
| `none` | 0.920 | 95.36 | **100.0** |
| `partial` | 0.880 | 92.98 | **100.0** |
| `full` | 0.760 | 84.34 | **100.0** |

A model degrading 92%→76% under randomization loses ~11 composite points, because the component designed to detect that is constant.

### Expected vs actual

- **Expected:** robustness reflects consistency across domain-randomization groups. ADR 0002:73-75 states it is "the sim-to-real hedge."
- **Actual:** constant maximum. `_W_ROBUSTNESS = 0.2` (`engine/scorecard.py:34`) is a fixed +20 for every checkpoint, including a maximally brittle one.

### Business impact

One of four weighted components cannot vary. Combined with C2-01 (the scorecard cannot rank policies) and C2-07 (the threshold cannot be set), **three of four inputs to the deploy verdict are constants or unreachable.** ADR 0002:73-75 and :45-47 describe a component that does not compute.

### Dependencies — this is why the effort estimate is 8-16h, not 4

The obvious fix (sample a randomization level per episode) **breaks the Isaac batch contract.** `isaac_worker.py:270-273` asserts `expected_level` per *batch*, so a batch spanning multiple levels fails validation. The fix therefore needs a wire-contract change — most likely a `scenario_category` field on `EpisodeResult` (`sim/runner.py:42-63`, which has no such field today). **Do not schedule this as an independent task; it is blocked on a contract change, and the contract change is blocked on C2-02.**

### Recommended fix

Group by adversarial scenario category rather than per-episode randomization level, which is compatible with batch-level validation. Add `scenario_category` to `EpisodeResult` **with a default** (see C2-08 — the stores reconstruct with a bare splat and have no tolerance for a new required field). Note the fix will move composite scores retroactively; historical scorecards become non-comparable, which is a communication problem, not just a code change.

### Test strategy

A scored run containing ≥2 categories must produce `robustness_score < 100`. A single-category run may legitimately score 100 — the existing fallback is defensible in isolation; the defect is upstream that nothing ever creates multiple groups.

---

## C2-05 — Two endpoints report different quantities under one name, and a test pins the coincidence

**Severity:** Medium · **Status:** VERIFIED · **Effort:** 1-2h
**Subject:** shipped code

### Description

- `api/dashboard.py:55` returns `avg_composite` — the **mean** across all runs.
- `api/metrics.py:115` derives `latest_composite` → `validsim_composite_score` — the **newest** run.

Same underlying `store.history()` pass, two different aggregations, one implied concept. The help text at `metrics.py:189` correctly says "most recent validation run," so a reader has no way to detect the collision.

### The test pins the coincidence

`tests/test_dashboard.py:71-74`:
```python
assert set(summary) == {"total_runs", "approvals", "blocks", "avg_composite"}
assert summary["total_runs"] == 1                                              # line 72
assert summary["approvals"] + summary["blocks"] == 1
assert summary["avg_composite"] == created["composite_score"]                # line 74
```

**Line 72 asserts the precondition that makes line 74 true.** A mean of one run equals that run. With two runs of differing composites the assertion would fail; with a one-run fixture it stays green indefinitely. This is a test encoding an *artefact* as a contract.

### Expected vs actual

- **Expected:** two differently-aggregated values carry distinguishable names, and the test asserts the semantics rather than a degenerate case.
- **Actual:** shared name; test green only for single-run stores.

### Business impact

A dashboard tile and a Prometheus gauge showing different numbers under one label. Low severity, high demo risk — this is the kind of ambiguity that becomes an incorrect claim in front of a customer.

### Recommended fix

Rename **by aggregation kind**, not prefix: `mean_composite_score` and `latest_composite_score`. Prefix schemes (`composite_avg` / `composite_latest`) do not stay stable if a third aggregate is added. Add a genuine multi-run assertion for each aggregation. Bundle with any dashboard-wiring work so the JS consumer updates once.

### Test strategy

Seed two runs with different composites; assert `mean_composite_score` is their mean and `latest_composite_score` is the second. Both fail today.

---

## C2-06 — Six shipped capabilities are complete, tested, documented, and never called

**Severity:** High · **Status:** VERIFIED · **Effort:** 2-14h each depending on disposition
**Subject:** mix of shipped code and dead code — classified per row below

### Description

A recurring pattern: a module is implemented, documented, has a full test suite, and **has no production call site**. The suite is green, so the capability reads as covered.

| Module | Lines | Subject | Callers outside itself + tests |
|---|---|---|---|
| `validsim/config_loader.py` | ~246 | shipped, advertised | **none** — `VALIDSIM_CONFIG` (`.env.example:154`) changes nothing |
| `validsim/scenarios/llm_generator.py` | ~560 | shipped, advertised | `pipeline.py:131` hardcodes `ScenarioGenerator`, so `VALIDSIM_LLM_API_KEY` has zero effect |
| `validsim/sim/shadow.py` | ~380 | shipped, unreachable | **none**; not exported from `validsim.sim` |
| `validsim/notify/` (3 modules) | ~700 | shipped, unreachable | **none** from pipeline, API, CLI, or worker |
| `validsim/engine/trends.py` | ~230 | shipped, unreachable | **none** — dashboard reimplements the math in JS |
| `validsim/engine/anomaly.py` | `detect_anomalies` | shipped, unreachable | **none** |
| `validsim/config.py:62-78` (`resolve_asset_path`) | ~17 | shipped, unreachable | **none** — the asset-root *containment* step never runs |

### Control

For each, the check is: does a grep for the symbol outside its own module and `tests/` return anything? All seven return nothing. For `trends.py` the corroboration is stronger — `web/app.js:492-518` reimplements `compute_trends` in JavaScript with a comment reading *"Mirrors validsim/engine/trends.py,"* and **already omits two of its outputs** (`improving`, `volatile`), so the drift is present today, not hypothetical.

### Severity assessment per instance

- **`notify/`** — highest. This is the only mechanism by which a completed `BLOCK` reaches a human. A CI gate fails a build; a Slack message does not. ~700 lines with 7 test files is a *larger* test surface than several shipped features.
- **`llm_generator`** — high, and worse than dead code: `.env.example:119-129` documents three env vars, so an integrator sets a key, observes nothing, and concludes the feature is broken rather than unwired. **1-2h to wire** (`create_scenario_generator(seed=seed)` at `pipeline.py:131`) — the highest return-per-hour item in this audit.
- **`config_loader`** — medium. Either wire it or delete it and remove the env var; the current state advertises a capability that does nothing.
- **`resolve_asset_path`** — **medium, not high**, and the distinction matters. `isaac_worker.py:519-527` sends `task.robot.model_dump()`, so the only paths reaching the GPU are `urdf_path`/`scene_usd` — **exactly the two fields the *wired* `_validate_asset_path` validator already covers** (`config.py:37-51`). The orphaned function is defence-in-depth that never runs, not the only barrier. Over-rating this would cost credibility.
- **`shadow.py`** — see C2-02; rated there because its impact is the GPU workstream, not code hygiene.

### Expected vs actual

- **Expected:** either a capability has a call site, or it is documented as not shipped.
- **Actual:** a green suite certifies a guarantee that no production path exercises. For a security control (`resolve_asset_path`) and a safety signal (`notify`), **a passing test that certifies an inactive control is worse than an obvious gap** — it removes the reader's reason to check.

### Business impact

Direct: no BLOCK notification reaches a human; the LLM scenario feature does nothing; operator config files are ignored. Indirect and larger: **the test suite's green status is not evidence of coverage for these modules**, which weakens every other claim the suite supports.

### Recommended fix

**Each needs an explicit disposition — wire it, or document that it is not wired and why. A line-item note is not enough.** Do not defer: the notify layer and the LLM generator are the two with immediate product value, and the LLM fix is a one-line change.

### Test strategy

A single check that asserts each shipped capability has at least one production call site. That one mechanism would have caught all seven, and keeps catching them as the codebase grows — the highest-leverage test in this report.

---

## C2-07 — WITHDRAWN: the threshold gap is closed; what remains is smaller

**Status:** REFUTED (was Critical earlier today) · Re-verified against the current tree, 2026-09-26.

**The claim I made is no longer true.** `ValidationRequest` now carries `threshold: float | None`, and `_execute_validation` (`api/main.py`) plumbs it:

```python
threshold=(
    request.threshold
    if request.threshold is not None
    else <DEFAULT_THRESHOLD>
),
```

ADR 0002:105-107 — *"overridable per run (`--threshold`, the API, and the gate command)"* — **is accurate again. No superseding ADR is needed.** I would have filed a Critical falsification of a shipped ADR on a stale read.

**One residual, verified and much smaller — Medium:** a caller-supplied threshold is now accepted, but `validsim/jobs/worker.py` still builds its `TaskConfig` from **module constants** (`_DEFAULT_ROBOT`, `_DEFAULT_ENVIRONMENT`), and `JobSpec` (`jobs/models.py`) carries **no robot or environment fields** (`'robot' in JobSpec.__dataclass_fields__` → `False`).

So the sync API can set the bar but not the embodiment, while the async path (`POST /api/v1/jobs`) can set neither. `POST /jobs` still validates a different thing than `POST /validations` for the same nominal request — and the async surface is the one the queue architecture exists to serve.

**Test strategy:** a job enqueued for a named robot must be scored as that robot. No current test can express this, because `JobSpec` cannot carry the information.

---

## C2-08 — Store reconstruction: 4 bare splats in Postgres, 2 in SQLite (maintainability, not parity)

**Severity:** Medium · **Status:** VERIFIED (the asymmetry) + **REFUTED** (a parity claim I withdrew) · **Effort:** 4-6h
**Subject:** shipped code

### Description

Both stores reconstruct detail dataclasses from JSON blobs by bare `cls(**data)`:

| Site | SQLite | Postgres |
|---|---|---|
| `EvaluationResult` | `:118` via `_rebuild` (`:102`) | `:245` bare splat |
| `SafetyResult` | `:127` via `_rebuild` | `:250` bare splat |
| `EpisodeResult` | **`:117` bare splat** | **`:254` bare splat** |
| `RegressionItem` | **`:132` bare splat** | **`:256` bare splat** |

`_rebuild` exists in SQLite and not in Postgres. **A dataclass field addition must be threaded to 1 site if routed through `_rebuild`, or 4 sites in Postgres.**

### The claim I withdrew

I previously told the lead a field addition "can pass on SQLite and fail on Postgres." **That is false, and I verified it before conceding.** Four scenarios, both real read paths:

```
A. well-formed full-detail row      sqlite OK         postgres OK         IDENTICAL
B. unknown key in episodes_json     sqlite TypeError  postgres TypeError  IDENTICAL
C. missing defaulted field          sqlite OK         postgres OK         IDENTICAL
D. unknown key in regression_json   sqlite TypeError  postgres TypeError  IDENTICAL
```

**Identical in all four — and the failure mode is the opposite of what I claimed.** Because both backends bare-splat the *same two* types, a maintainer who forgets one produces the **same** failure on both, which is the safer outcome: it fails identically and visibly rather than diverging silently. The real asymmetry is **edit sites, not behaviour.**

*(My first probe run reported a false divergence: `postgres._run_from_row` takes a positional row `(scorecard, baseline_run_id, evaluation, safety, episodes, regression)` (`:236-242`) and I omitted `baseline_run_id`, shifting every field. Caught because the error claimed a `SafetyResult` field arrived at `EvaluationResult` — impossible unless the offset was wrong.)*

### Design constraint the refactor must respect

`_rebuild` returns `None` on `None` input, correct for the two **top-level** sites (already guarded, feeding the documented legacy approximation). But `:254` and `:256` are **list-element** sites with no such guard. Reusing the helper naively converts a loud `TypeError` into a `StoredRun` whose `.episodes` contains `None` — which then persists and round-trips as valid-looking data.

**Reachability caveat:** `_episodes_to_json` is `json.dumps([asdict(e) for e in episodes])`, and `asdict` never yields `None` for a well-formed list — so this is a **refactor-induced regression risk, not a live production bug.** That framing matters: it stops someone escalating it as an incident, and stops someone "fixing" it by making the helper tolerant everywhere, which would reintroduce the corruption where `None` legitimately means "use the approximation."

### Expected vs actual

- **Expected:** one reconstruction policy, one edit site per backend, uniform tolerance.
- **Actual:** two policies by accident of authoring order, six splat sites between them.

### Business impact

Compose defaults to `VALIDSIM_STORE=postgres` — **the production backend is the one with no chokepoint.** Three agents have independently planned new fields on `EpisodeResult`; the first deployment that writes one and only partially threads it fails at read time on the append-only verdict log. Refactor first, fields second.

### Recommended fix

Shared helper taking the **already-decoded** payload — `_rebuild(data, cls) -> _T | None` for guarded top-level columns, and `_rebuild_sequence(data, cls) -> list[_T]` for list elements (`[cls(**item) for item in (data or [])]`). The sequence form prevents the `None`-element bug by construction rather than by discipline, and names the `[]`-for-`None` behaviour explicitly instead of leaving it as an incidental `or []`. Do not tighten types in the same change.

### Test strategy

**Characterise before refactoring** — a test written afterwards cannot distinguish "preserved behaviour" from "changed to match the new code." Six assertions, all green first: well-formed row identical across backends; unknown key raises identically; missing defaulted field reconstructs identically; missing required field raises identically; `None` top-level column falls back to the documented approximation; `None` list element raises.

---

## False leads (ruled out)

Recording these because each was previously reported as a finding and is now either fixed or refuted. **A retracted claim that is not explicitly retracted will be re-reported by the next agent who reads the earlier note.**

1. **Queue permanently wedges at `max_depth` lifetime enqueues — FIXED, memory backend.** The memory `enqueue` now counts *pending* work (queued + running) and its docstring explains why: *"a long-lived queue dead-locked itself after `max_depth` lifetime jobs."* Verified: 10 jobs all driven to `DONE`, `len(queue)` still 10, and **enqueue #11 succeeded** (`JobQueue.enqueue`, `jobs/queue.py`). The Redis enqueue script's capacity check now reads `KEYS[3]` (the ready list) rather than the insertion index, consistent with the same intent. I did not re-verify the Redis path live — see the open item below.
2. **`/metrics` counters decrease on DELETE (counter-typing violation) — FIXED.** `api/metrics.py:166-184` now emits `validsim_runs_total` / `_approvals_total` / `_blocks_total` as **`gauge`**, with help text stating *"deleting a run lowers it. A gauge, not a counter."* Correct fix; the naming (`_total` on a gauge) is now mildly misleading but documented.
3. **Legacy-row reconstruction can manufacture a deploy verdict — REFUTED (my claim, withdrawn).** The stored `deploy_decision` is read straight off the scorecard and never recomputed; read-time reconstruction does not re-run `build_scorecard`. A row stored as `BLOCK` reads back `BLOCK`. The *real* adjacent defect is that `mean_duration_s` defaults to `0.0`, and `regression.py:123` short-circuits `relative = delta / before if before > 0 else 0.0` — so **every duration comparison against a legacy baseline reports no regression, silently, at any magnitude.** That is a false negative in a comparison function, not a verdict-manufacturing bug.
4. **`resolve_asset_path` is the only barrier between a caller and an out-of-root asset read — REFUTED as stated.** The wired `_validate_asset_path` validator (`config.py:37-51`) already covers both fields that reach the GPU. Downgraded to medium; the residual risk is worker-image sandboxing, which no Python-side validation provides.
5. **`validsim_composite_score` is mis-typed as a gauge — REFUTED (a teammate's claim, which I stopped).** A gauge *is* a current instantaneous value; "composite of the latest run" is exactly that, and it correctly is not a counter. The real metric-typing defect was the three `counter`s, now fixed (item 2).
6. **`sqlite._episodes_to_json` and `postgres._episodes_to_json` differ — REFUTED.** They differ only in the parameter annotation (`Sequence[EpisodeResult]` vs `list[EpisodeResult]`); bodies and output are identical. **My probe compared `inspect.getsource()` text and reported a false positive.** The near-duplication is still real and worth consolidating, but there is no parity defect.

## Open items I could not close

- **Redis queue paths need a live server.** Two claims are static-only here: that the reaper's `LRANGE`-plus-per-job-`GET` scan is O(N) inside one atomic script, and that the null-lease case strands a job on Redis while the Python `_is_expired` reclaims it. Both were reproduced live by another agent earlier in this audit, but **I have not re-verified them against the current tree**, which has changed. Confirmation: run the imported `_REDIS_REAP_EXPIRED_SCRIPT` against a seeded null-lease job, and drive `_REDIS_ENQUEUE_SCRIPT` past `max_depth`. Marked **UNVERIFIED (needs live server)**.
- **Test count is not attributable.** `HEAD` is `ff15533` with ~105 modified tracked files and ~50 untracked. **No count from this audit should be quoted without the working-tree state.** I did not re-measure a collected total this session, deliberately: it would not be anchorable.
