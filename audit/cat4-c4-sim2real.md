# c4-sim2real — Simulation Layer Audit

**Agent:** `c4-sim2real` · **Date:** 2026-09-26 · **Repo:** `d:\SIM-TO-REAL`
**Scope:** `validsim/sim/*`, `validsim/engine/{scorecard,pipeline}.py`, `validsim/scenarios/*`, `validsim/jobs/*`, `docs/isaac-worker.md`
**Anchor commit:** `ff15533` (`chore: commit untracked modules, tests and CI fixes as a baseline`)
**No production file was edited.** Probes were written to `%TEMP%` and run with `PYTHONPATH` pointed at the repo.

---

## Findings table

| ID | Title | file:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| **S-1** | Scorecard cannot rank policies: the backend never receives the checkpoint | `validsim/sim/runner.py:105-112` | **Critical** | VERIFIED | 8-16h | `%TEMP%\c4_probe.py` F4 + `%TEMP%\c4_reach.py` |
| **S-2** | `robustness_score` is a constant 100.0 — a fixed +20 composite points for every checkpoint | `validsim/engine/scorecard.py:53-66` | **High** | VERIFIED | 2-4h | `%TEMP%\c4_probe.py` F1 (with control) |
| **S-3** | Adversarial `params` are transmitted on the wire and consumed by nothing | `validsim/scenarios/generator.py:192-251` | **High** | VERIFIED | 4-6d | `%TEMP%\c4_probe.py` F2/F3 (with controls) |
| **S-4** | Isaac wire contract has no checkpoint field — a conforming worker cannot know what policy to run | `validsim/sim/isaac_worker.py:519-527` | **High** | VERIFIED | 4-6h | `%TEMP%\c4_probe.py` F6 + grep of `docs/isaac-worker.md` |
| **S-5** | Episodes carry no provenance: no backend, no category, no randomization record | `validsim/sim/runner.py:42-63` | **Medium** | VERIFIED | 2-3d | `%TEMP%\c4_reach.py` episode-payload probe |
| **S-6** | `JobSpec` cannot express a non-validation job; result namespace collides with validation verdicts | `validsim/jobs/models.py:27-46` | **Medium** | VERIFIED | 0.5d | Read + call-graph; see §S-6 |
| **S-7** | `create_scenario_generator` is never called; the LLM scenario path is dead code | `validsim/engine/pipeline.py:131` | **Medium** | VERIFIED | 1h | grep of `validsim/` |

**Single most important finding: S-1.** Everything else in this report is a consequence of it, and it is the one finding that makes the product's central promise untrue in its default configuration.

---

## S-1 — Scorecard cannot rank policies (Critical, VERIFIED)

### Description

`MockIsaacBackend.run_episode` has **no checkpoint parameter**, so no caller can make the backend behave differently for a different policy. The only path from a checkpoint to the score is `checkpoint_id → stable_seed(...) → seed → backend`. Two checkpoints with completely different real-world quality therefore produce scorecards that differ only by sampling noise.

The same defect exists on the GPU path: the Isaac payload carries no checkpoint either (S-4).

### Repro

```
cd d:/SIM-TO-REAL
$env:PYTHONPATH="d:/SIM-TO-REAL"; python "$env:TEMP/c4_probe.py"
```

```
run_episode signature: (self, task, seed, randomization_level, scenario=None) -> EpisodeResult
'checkpoint' in params: False
six checkpoints, 320 eps each:
  ckpt-a: success=0.600 safety=72.31
  ckpt-b: success=0.616 safety=77.34
  ckpt-c: success=0.572 safety=73.31
  ckpt-d: success=0.609 safety=79.69
  ckpt-e: success=0.616 safety=76.22
  ckpt-f: success=0.575 safety=74.97
stdev across CHECKPOINTS: success=0.0181
CONTROL six arbitrary TASK NAMES, one checkpoint: stdev=0.0217
```

### Control

**The control is the second line, and it is what makes this finding unarguable.** Six *task names* are semantically meaningless strings; the checkpoint is the thing the product claims to measure. The meaningless-string arm produces a **larger** spread (0.0217) than the checkpoint arm (0.0181). Neither arm is a signal — both are `stable_seed` perturbation.

A second control confirms determinism holds, so the cross-checkpoint delta is not measurement instability:

```
CONTROL: same checkpoint twice, identical request
  run1 composite=76.73  run2 composite=76.73  identical=True
```

### Expected vs actual

- **Expected:** two different checkpoints produce scorecards whose ordering reflects policy quality.
- **Actual:** ordering is determined by a CRC32 of the checkpoint *string*. A better policy can score lower.

### Business impact

`Vision & Positioning.md` sells "submit a model checkpoint, receive a defensible safety-and-success scorecard." In the shipped default configuration the scorecard is a deterministic function of the checkpoint's name. A design partner running an A/B on two checkpoints is measuring label noise. This is the root cause of S-2, S-3 and S-5, and it is not fixable by improving the metrics — **the input is missing.**

### Dependencies

Requires deciding what a checkpoint *is* to a backend (weights path? ONNX bundle? remote ref?). That decision gates the GPU worker, the landing pipeline, any second physics engine, and multi-embodiment simultaneously. It is currently unowned.

### Recommended fix

Add a checkpoint/policy argument to `SimulationBackend.run_episode` and to `IsaacWorkerBackend._build_payload`. Make the mock a pure function of policy identity by deriving a per-checkpoint profile from a hash of its id — deterministically different, and transparently synthetic rather than pretending to be physics.

### Test strategy

A differential test is the minimum: for two distinct checkpoint ids at a fixed seed, assert the mock's output distribution differs. Without it, a future refactor can silently restore policy-invariance and every other test still passes.

---

## S-2 — `robustness_score` is a constant 100.0 (High, VERIFIED)

### Description

`_robustness_score` groups episodes by `randomization_level` and returns `100.0` when fewer than two groups exist. `run_validation` passes one `task.randomization` string to every episode, so exactly one group always exists.

### Repro

From `%TEMP%\c4_probe.py` F1:

```
real run   : levels={'full'} -> 100.0
CONTROL 2 groups (n=150 each, real spread) -> 87.33
```

### Control

**The control is essential here and it exonerates the metric.** Hand-building two genuine groups (150 `none` + 150 `full`) yields **87.33**, not 100. So the dispersion calculation is correct; the defect is that `run_validation` never produces more than one group. This also refutes a plausible alternative reading — the metric is not broken, its input is.

### Expected vs actual

- **Expected:** robustness measures cross-condition consistency.
- **Actual:** always `100.0`, contributing a fixed `0.2 × 100 = 20` composite points to every checkpoint, including one that collapses under randomization.

### Business impact

One of four weighted components is a constant that always contributes its maximum. A buyer calibrating a gate threshold is calibrating partly against a number that is not evidence. Confirmed reachable end-to-end:

```
ckpt-alpha: composite=76.73 success=0.6119 robustness=100.0 decision=BLOCK
ckpt-beta:  composite=81.18 success=0.6786 robustness=100.0 decision=BLOCK
```

### Dependencies

None for the fix. **Sequencing constraint:** the fix will move historical composites, so it must land *after* the gate reads `deploy_decision` authoritatively (ADR 0006, already done) and *before* any customer threshold guidance ships.

### Recommended fix

Sample a randomization level per episode in `run_validation` so multiple groups exist. Leave `_robustness_score` alone — the 1-group fallback is defensible in isolation.

**Do not** "fix" it by zero-weighting robustness on mock: `_robustness_score` receives only `episodes` and is never told which backend produced them, so zeroing removes 20 points from every run including CI, which is the mock's default.

### Test strategy

Property test: for a task with ≥2 randomization levels, assert `robustness_score < 100`. A second test asserting the constant is *absent* when `adversarial_count == 0` — absent evidence must score as inconclusive, never as the maximum.

---

## S-3 — Adversarial `params` are transmitted and consumed by nothing (High, VERIFIED)

### Description

`_PARAM_SAMPLERS` emits physically meaningful quantities (`mass_kg`, `friction_coefficient`, `lux`, `latency_ms`, `backlash_rad`). `_scenario_payload` serializes them onto the wire. **Nothing on either side acts on them.** `success_probability` reads only `scenario.difficulty`; `run_episode` has one branch keyed on *category*, never on *params*.

### Repro

`%TEMP%\c4_probe.py` F2:

```
param human_distance_m=0.1   -> observable min_human_distance_m=0.24
param human_distance_m=0.42  -> observable min_human_distance_m=0.24
param human_distance_m=0.9   -> observable min_human_distance_m=0.24
param human_distance_m=1.2   -> observable min_human_distance_m=0.24
```

### Control

```
CONTROL same params, seed 42 vs 43 -> 0.24 vs 0.443
```

Same parameters, different seed → the observable **does** move. So the oracle is not frozen; the *parameters* are simply not an input. This rules out "the mock is deterministic therefore nothing varies," which is the obvious objection.

### Expected vs actual

- **Expected:** `human_distance_m=0.1` produces a closer encounter than `human_distance_m=1.2`.
- **Actual:** bit-identical. Worse, the reported `min_human_distance_m=0.24` is *not* the requested value in any of the four cases — the sim is not self-consistent with its own request, and this is the safety observable the contract mandates for `human_proximity`.

`failure_mode` is separately unsteerable (`F3`): `rng.choice(FAILURE_MODES)` over 7 modes, observed shares 0.092–0.179 against a uniform 0.1429, max/min ratio 1.95. A fitness function of the form `P(failure_mode == X)` cannot be optimized against this backend.

### Business impact

`docs/isaac-worker.md` §3 documents `params` as meaningful. A technical buyer reading `generator.py` finds 35 parameters that do nothing. This is a **parity divergence**, not merely a mock limitation: the field is on the wire, so a worker is *expected* to honour it, and the mock's silence is indistinguishable from a bug until someone reads the code.

### Dependencies

None for the mock half — it is pure engine work and unblocks any search-based capability.

### Recommended fix

Make the mock consume `params`. Type it rather than leaving it `dict[str, Any]`, so a second backend cannot silently ignore it — a typed `RandomizationProfile` makes the dependency visible in the signature and the fix self-evidencing.

### Test strategy

Property test, not an example test: *for every axis, there exists a pair of parameter values that changes the outcome distribution.* Assert across all axes so no axis can silently regress to being ignored.

---

## S-4 — Isaac contract has no checkpoint field (High, VERIFIED)

### Description

`IsaacWorkerBackend._build_payload` emits seven keys, none of which identify a policy. `docs/isaac-worker.md` mentions "checkpoint" exactly once — line 151, in the shadow-run prose — and never in the §2 request schema or §3 semantics.

### Repro

`%TEMP%\c4_probe.py` F6:

```
payload keys: ['environment', 'episodes', 'randomization_level', 'robot', 'scenarios', 'seed', 'task_id']
'checkpoint_id' in payload: False
CONTROL scenario params carried: True
```

Corroborating grep of `docs/isaac-worker.md`: single hit at line 151.

### Control

`params` **is** carried, proven in the same run. So the payload builder is not dropping fields indiscriminately — it is specifically missing the checkpoint. That distinguishes this from a serialization bug.

### Expected vs actual

- **Expected:** a worker can determine which policy to load.
- **Actual:** a worker built to the documented contract has no way to know. Every episode would return default behaviour.

### Business impact

Two consequences beyond the immediate one. First, the §5.3 shadow comparison ("same checkpoint, both backends") is **unsatisfiable as written today** — the GPU side cannot honour "same checkpoint." Second, and worse: `reference_contract_cases()` fixtures encode no checkpoint field, so **all three pass against a contract missing the product's core input.** A worker could be fully conformant, pass the §5 step-1 gate, be promoted, and be structurally incapable of validating a checkpoint. Conformance would certify the defect.

### Dependencies

Must land **before** the GPU worker is built. After a worker exists, this becomes a re-freeze against a live implementation. It is also the cheapest item in this report.

### Recommended fix

Add `checkpoint_id` to `_build_payload` and to the §2 schema. Extend `reference_contract_cases()` to assert the checkpoint is present on the request **and** echoed on the response and matching it — reusing the existing seed-echo pattern at `isaac_worker.py:248`.

### Test strategy

A conformance fixture that fails today. Plus a determinism fixture (same seed twice → identical result), which nothing currently enforces — `docs/isaac-worker.md` §3 *claims* seeded determinism and the static fixtures cannot detect its absence.

---

## S-5 — Episodes carry no provenance (Medium, VERIFIED)

### Description

`EpisodeResult` has no field recording which backend produced it, which adversarial category applied, or what randomization values were realized. Reachable consequence: no consumer can distinguish a mock run from a GPU run, or a categorized episode from an uncategorized one.

### Repro

`%TEMP%\c4_reach.py`, against a live `POST /api/v1/validations` + `GET /api/v1/validations/{id}`:

```
detail keys: ['baseline_run_id', 'checkpoint_id', 'composite_score', 'created_at',
              'deploy_decision', 'episode_count', 'run_id', 'task_id']
```

`EpisodeResult` fields confirmed as 4 required / 7 defaulted (`runner.py:53-63`); no provenance field among them.

### Control

The `/health` and store readback paths both succeed, so this is not a serialization failure — the fields are genuinely absent from the model, not dropped in transport. Verified separately: runs persist and read back cleanly with 620 episodes each.

### Expected vs actual

- **Expected:** a stored episode records enough to reproduce and attribute it.
- **Actual:** two runs from different backends are indistinguishable in the store, and a per-category robustness metric has nothing to group by.

### Business impact

Any future claim of the form "this score is mock-derived and therefore provisional" is unsupportable from stored data. It also blocks the obvious fix for S-2's mock-gating problem, which needs a backend predicate to score against.

### Dependencies

**Persistence constraint, verified:** both stores reconstruct with a bare splat — `store/sqlite.py:117`, `store/postgres.py:254` — so a *required* new field raises `TypeError` on every historical row. `_MIGRATION_COLUMNS` does not help; episodes are a JSON blob, so there is no column to add. Four further `Scorecard(**dict)` sites exist (`sqlite.py:78`, `postgres.py:174`, `cli.py:397`, `notify/email.py:80`) plus the on-disk cache `.validsim/scorecards.json`.

### Recommended fix

Add the fields **with Python defaults**: `backend: str = "unknown"`, `adversarial_category: str | None = None`, `checkpoint_loaded: str | None = None`, and `Scorecard.robustness_valid: bool | None = None`.

Defaults are correct here for two reasons. Wire strictness is unaffected — `_EPISODE_FIELDS` derives from the dataclass and `_episode_from_dict` (`:216-229`) checks membership, not default-awareness, so a worker omitting the field is still rejected. And **defaults repair the past; a read-path filter only protects the future** — a filter at the five call sites still leaves a previously-written `scorecards.json` and an append-only `episodes_json` blob permanently unreadable.

### Test strategy

Round-trip a legacy-shaped blob (no new keys) through both store backends and assert it deserializes with the new fields at their defaults. Plus a wire test asserting the parser still *rejects* a payload missing `backend`.

---

## S-6 — `JobSpec` cannot express a non-validation job (Medium, VERIFIED)

### Description

`JobSpec` is `(run_id, checkpoint_id, task_id, episodes, adversarial)`. There is no job kind and no parameter payload, so an adversarial-search or calibration job is inexpressible — it would arrive as a validation with a large `episodes` count.

### Repro

Read of `validsim/jobs/models.py:27-46` and `validsim/jobs/worker.py:258-289`. `JobWorker._execute` constructs one `TaskConfig` and calls one `run_and_score`; there is no branch on job type.

### Control

`_require_durable_store` (`cli.py:162`) confirms `validsim gate` reads only the durable store and never the cache, so this is an isolation-of-concerns issue rather than a gate-correctness one. The defect is that a search result would be persisted through `run_and_score` and could be read as a validation verdict.

### Expected vs actual

- **Expected:** a search result is distinguishable from a validation verdict.
- **Actual:** both persist as `StoredRun` with a real `deploy_decision`, and `compute_trends` / `detect_anomalies` consume the history unfiltered and positionally.

### Business impact

The gate's output artifact is a deploy decision. A calibration run persisting into the same history would place non-validation numbers into the trend tiles a customer reads. Severity is capped at Medium today only because no search or calibration job exists yet — it is latent, and it becomes real the moment S-3's fix makes such a job buildable.

### Dependencies

None. Independent of S-1 through S-5.

### Recommended fix

Add `JobSpec.kind: Literal["validate", "search", "calibrate"]` plus an optional `params` payload, route on it in `_execute`, and carry `kind` through to `StoredRun.summary()`. Apply a default non-`validate` filter at every history consumer.

### Test strategy

Enqueue a `search` job; assert it does not appear in `history()` results consumed by trends, and that its `StoredRun` carries a distinguishable kind.

---

## S-7 — `create_scenario_generator` is never called (Medium, VERIFIED)

### Description

`pipeline.py:131` hardcodes `ScenarioGenerator(seed=seed)`. `create_scenario_generator` — the env-driven factory that activates the LLM path — is exported from `validsim.scenarios` and never invoked. `docs/runbook.md` §4.5 documents a fallback path that is not wired.

### Repro

```
grep -n "create_scenario_generator" validsim/ -r
  -> validsim/scenarios/__init__.py, validsim/scenarios/llm_generator.py only
grep -n "ScenarioGenerator" validsim/engine/pipeline.py
  -> 42: from validsim.scenarios.generator import ScenarioGenerator
  -> 131: scenarios = ScenarioGenerator(seed=seed).generate(...)
```

### Control

The LLM module itself is fully implemented and tested; the defect is purely that the pipeline bypasses it. `runbook.md` §4.5's claim that "any provider failure falls back to the deterministic rule-based generator" describes a path that cannot execute.

### Expected vs actual

- **Expected:** configuring `VALIDSIM_LLM_API_KEY` activates LLM scenarios, degrading to rule-based on failure.
- **Actual:** the LLM path is dead code regardless of configuration.

### Business impact

Low technical effort against a documented capability the vault treats as a moat. The cheapest high-value fix in this report — but it is a one-line change with a real risk of being read as "the LLM integration works" when it remains unexercised in the default path.

### Dependencies

None.

### Recommended fix

Call `create_scenario_generator(seed=seed)` in the pipeline. Add a test asserting the factory is consulted, so a future refactor cannot silently re-bypass it.

### Test strategy

Monkeypatch the factory to a sentinel and assert the pipeline calls it; separately assert a provider failure yields rule-based scenarios rather than raising.

---

## False leads — ruled out, with reasons

1. **"The mock is deterministic, therefore the degeneracy claim is overstated."** Ruled out by the F2 control: same params with different seeds produce different observables (0.24 vs 0.443). The oracle varies; the *parameters* are not an input. Precise framing: the mock is a **policy-invariant, physics-free oracle** — complete information about a hand-tuned scalar, none about the robot.

2. **"`robustness_score` is miscalibrated / `_ROBUSTNESS_SCALE=200.0` is wrong."** Ruled out by the F1 control: two genuine groups give 87.33, a sensible value. The constant is an input defect in `run_validation`, not a statistic defect. Re-tuning the scale would be treating the symptom.

3. **"The CLI cache can break `validsim gate`."** Ruled out. `cli.py:443` uses `_require_stored`, and `_require_durable_store` (`:162`, called `:203`) hard-exits 2 unless the store is durable. The gate never reads `.validsim/scorecards.json`. A field addition breaks `validsim report`, not the gate. I had asserted the opposite earlier in this audit; the correction came from `c4-statistics` and I verified it before conceding.

4. **`compute_trends` / `detect_anomalies` contaminating the dashboard today.** Ruled out as a *current* defect. They have no production caller — the dashboard reimplements the trend math in `web/app.js:492` (its comment at `:488` says it "mirrors" `engine/trends.py`). Real, but it is unwired code plus duplicated math, not a live contamination path.

5. **"The single-threaded job worker is a defect."** Downgraded from a finding. `JobWorker.run_once` does call `_execute` inline, but `_DEFAULT_LEASE_SECONDS = 3600` with a heartbeat at 1/3 interval means a long job renews indefinitely and is not reaped. The design handles long jobs correctly; the missing piece is worker *concurrency*, which only matters once multi-hour GPU jobs exist. Folded into S-6 rather than reported separately.

6. **`test_dashboard.py:74` asserting `avg_composite == composite_score`.** Considered as a finding here, then **dropped as out of my lane** — it belongs to whoever owns `validsim/api/`. Noted only so it is not lost: the assertion holds solely because the fixture has `total_runs == 1` (asserted on line 72), and `validsim_runs_total` / `approvals_total` / `blocks_total` are emitted as Prometheus `counter` (`api/metrics.py:155-171`) while being derived from `store.history()` at scrape time, so a `DELETE` (`api/main.py:665-691`) makes all three decrease.

---

## Assumptions

1. **`ff15533` is the audit baseline.** The working tree showed 126 changed entries during this audit; all findings were reproduced against the tree as it stood at that commit. Re-verify before acting if the tree has moved again.
2. **`VALIDSIM_BACKEND` defaults to `mock`**, so the mock is the shipped default for the API, the CLI and CI. Every S-1/S-2/S-3 impact statement assumes that default.
3. **"Better policy" in S-1 is unmeasurable in this configuration** — I am not claiming the mock produces a specific wrong ordering, only that no ordering it produces is evidence about a policy. The control establishes the absence of signal, which is the stronger and defensible claim.
4. **Effort figures exclude review and CI time** and assume one engineer already familiar with the codebase.
5. **S-3's severity assumes `docs/isaac-worker.md` §3 is intended as normative.** If the params are deliberately advisory-only, S-3 drops to Medium — but the document does not say so.
