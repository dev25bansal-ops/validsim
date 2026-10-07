# Category 2 — Code Quality Audit (ValidSim)

**Agent:** c2-quality · **Date:** 2026-09-26 · **Mode:** read-only (no production edits)
**Scope:** violations of the project's own declared standards (`CONTRIBUTING.md`), maintainability, readability, duplication, dead code, test-suite quality.

> **Snapshot caveat.** The working tree changed repeatedly *during* this audit (multiple agents editing concurrently). Every finding below was **re-verified against the tree as of 2026-09-26** and carries an explicit reproduction. Where a claim from an earlier round no longer holds, it is listed under **False leads** rather than quietly dropped.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| **Q-01** | Lint gate cannot fail — `line-length` rule never enforced; 14 violations ship | `pyproject.toml:47-49` | **Critical** | VERIFIED | 2h | `ruff check validsim tests` → "All checks passed!" vs `--select E501` → **14 errors** |
| **Q-02** | Deploy gate **APPROVES** a run where 100% of adversarial episodes fail | `engine/scorecard.py:157-162` | **Critical** | VERIFIED | 3d | probe: 500 nominal @95% + 24 adversarial all-fail → `composite=91.21 decision=APPROVE` |
| **Q-03** | Anomaly detector silently discards **new** failure modes (all 3 variance terms → 0) | `engine/anomaly.py:181-187` | **Critical** | VERIFIED | 2d | probe: zero-baseline mode at 100/500/900/**1000** per 1000 → `0 anomalies`; control 1% baseline → detected |
| **Q-04** | Job queue wedges permanently — `enqueue` gates on a lifetime counter, zero eviction | `jobs/queue.py:478` | **High** | VERIFIED | 2d | probe: `max_depth=3`, 3 enqueues, no eviction method exists, 4th → `QueueFullError` |
| **Q-05** | Robustness component is structurally constant on the real execution path | `engine/scorecard.py:63-64` | **High** | VERIFIED | 3d | `run_validation()` emits exactly one `randomization_level` per run (60/60 `partial`) |
| **Q-06** | Safety metric denominator inconsistent — weight scaled by human-presence frequency | `engine/safety.py:88-91` | **High** | VERIFIED | 1d | probe: human in 1 of 10 episodes, violating → `proximity_violation_rate=0.1`; control all-10 → `1.0` |
| **Q-07** | 5 mutable models — 2 named in the standard; type annotations lie about 4 | `config.py:121`, `sim/runner.py:43` | **High** | VERIFIED | 1d | `TaskConfig.task_id='MUTATED'` and `EpisodeResult.success=False` both succeed |
| **Q-08** | 7 "frozen" models leak mutable state — immutability is cosmetic | `engine/scorecard.py:111` +6 | **Medium** | VERIFIED | 2d | AST scan: 7 classes `frozen=True` with `list`/`dict` fields |
| **Q-09** | 85.0 deploy threshold hardcoded at 11 sites, 2 competing constants | `engine/pipeline.py:55`, `jobs/worker.py:51` | **Medium** | VERIFIED | 1d | grep `85\.0` → 14 hits incl. 2 distinct module constants |
| **Q-10** | Dead code: unused sentinel, dead script parameter, 247-LOC orphan module | `api/main.py:106`, `scripts/build.ps1:18` | **Medium** | VERIFIED | 1d | each symbol has exactly 1 repo-wide hit — its own definition |
| **Q-11** | 618 duplicated test lines (336 recoverable) across 35 groups | `tests/` | **Medium** | VERIFIED | 2d | MD5 of normalized source per top-level def |
| **Q-12** | 8 functions >60 lines; `create_app()` is 383 lines / 129 statements | `api/main.py:480` | **Medium** | VERIFIED | 3d | AST span + statement count |
| **Q-13** | `health` always exits 0 — unreachable Postgres yields "ok" | `cli.py:609-624` | **Medium** | VERIFIED | 3h | source read; broad-except sites enumerated |
| **Q-14** | 4 AST-identical test bodies + 18 wall-clock-dependent test sites | `tests/` | **Medium** | VERIFIED | 2d | AST-normalized body hashing; `time.sleep`/`join(timeout)` grep |

---

## Q-01 — Lint gate cannot fail; `line-length` rule is never enforced

**Description.** `CONTRIBUTING.md:99-104` declares `line-length = 100` and states *"Keep your code passing ruff with zero warnings."* `pyproject.toml:47-49` sets only `line-length` and `target-version` — **no `lint.select`**. Ruff's default rule set is `E4,E7,E9,F` and **excludes `E501`**. The declared standard is therefore not enforced by the tool that is supposed to enforce it.

**Repro.**
```powershell
ruff check validsim tests
# All checks passed!

ruff check validsim tests --select E501
# Found 14 errors.
```

**Expected vs actual.** Expected: the configured `line-length = 100` rejects long lines. Actual: `ruff check` reports success while 14 lines exceed 100 chars.

**Violations (14).** Production: `validsim/engine/export.py:28` (117 chars). Tests: `tests/bench/test_bench_overload.py:59,63,85`, `tests/bench/test_bench_pipeline.py:53`, `tests/test_coerce.py:266`, `tests/test_delivery_pipeline.py:166`, `tests/test_metrics_content.py:406,443,568`, `tests/test_store_sqlite.py:45,46,96,97`.

**Business impact.** CI reports green unconditionally on this axis, so "zero warnings" in the PR checklist is unfalsifiable. The count has also **grown 9 → 14** since my first measurement, and now includes **production code** (`export.py:28`) — the drift is active, not historical. Every other cleanup in this report is unenforceable until this is fixed, because no lint rule is actually running.

**Dependencies.** None. This is a prerequisite for automating the rest.

**Recommended fix.** Add to `pyproject.toml`:
```toml
[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ANN", "D", "RUF"]
ignore = ["D203", "D213"]
```
Then fix the 14 lines. Roll out incrementally (`E,F,W` first) to avoid a 6,000-item dump — `--select ALL` currently yields **6,095** findings, 600 auto-fixable.

**Test strategy.** CI asserts `ruff check validsim tests --exit-non-zero-on-fix` is green **and** that `E501` is in the enabled set, so a future `select` removal cannot silently disable the gate. Add a meta-test asserting the configured `line-length` is present in the enabled rule set.

---

## Q-02 — Deploy gate APPROVES a run where every adversarial episode fails

**Description.** `engine/scorecard.py:157-162` computes the composite by pooling nominal and adversarial episodes into a **single** success rate (`evaluation.success_rate * 100.0`). No component of the gate scores the adversarial subset independently. `adversarial_count` is bounded by `config.py:136,138` (`le=1000` against `episodes le=100000`), so the adversarial share of the pooled rate is **operator-configurable** rather than safety-determined.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.engine.scorecard import build_scorecard
from validsim.sim.runner import EpisodeResult
T=TaskConfig(task_id='p',robot=RobotSpec(name='r'),environment=EnvironmentSpec(name='e'),episodes=500,randomization='full',adversarial_count=24)
def ep(i,s): return EpisodeResult(episode_id=f'e{i}',task_id='p',seed=i,success=s,
    collision_count=0 if s else 3, max_contact_force_n=5.0 if s else 150.0,
    min_human_distance_m=None, failure_mode=None if s else 'collision',
    duration_s=8.0, randomization_level='full')
nom=[ep(i,i%20!=0) for i in range(500)]
for n,label in ((0,'nominal-only CONTROL'),(24,'ALL 24 adversarial FAIL')):
    adv=[ep(1000+j,False) for j in range(n)]
    al=nom+adv; ev,sf=evaluate(al),compute_safety(al)
    c=build_scorecard(run_id='v',checkpoint_id='c',task=T,evaluation=ev,safety=sf,episodes=al,threshold=85.0)
    print(f'{label:26} composite={c.composite_score:6}  decision={c.deploy_decision}')"
```
**Output (control included, per rule 6):**
```
nominal-only CONTROL       composite=  95.3  decision=APPROVE
ALL 24 adversarial FAIL    composite= 91.21  decision=APPROVE
```

**Expected vs actual.** Expected: a run failing 100% of its adversarial suite is blocked. Actual: **APPROVE at composite 91.21.** Each failing adversarial episode carries 3 collisions and 150 N peak force (limit 50 N) — gross safety violations, none of which moves the verdict below the 85.0 threshold.

**Business impact.** The product's core promise is a deploy gate for robot foundation models. A robot that fails every adversarial test ships. Because the adversarial share is configurable, an operator can dilute the signal to ~1% of the run without any warning at validation time. ADR-0002 and `CONTRIBUTING.md` both present the composite as the *explainable deploy gate*; as implemented it cannot see the one signal it exists to catch.

**Dependencies.** Coordinate with `c3-engine` (owns scoring) and `c4-statistics` (owns the invariant tests). Interacts with Q-05 (robustness also constant) — fixing only one still leaves a composite that under-weights failure.

**Recommended fix.** Add per-component floors: (a) score the adversarial subset independently and gate on it; (b) require a minimum adversarial share before APPROVE is possible; (c) warn at validation time when `adversarial_count / episodes` falls below a floor.

**Test strategy.** A known-red test asserting `deploy_decision == "BLOCK"` with `composite == 91.21` pinned as the **expected-to-change** value. **Pin `composite_score` and `deploy_decision` only — do not assert `success_rate == 0.9065`; the exact value is `0.9064885496183206`, so a float-equality assertion would fail for the wrong reason and mask the gate failure.** Verify the test is RED before the fix and GREEN after.

---

## Q-03 — Anomaly detector silently discards new failure modes

**Description.** `engine/anomaly.py:181-187` blends three variance terms, then skips any mode whose `sigma <= _EPS`:
```181:187:validsim/engine/anomaly.py
expected = statistics.fmean(rates)
spread = statistics.pstdev(rates)
se_mean = _bootstrap_se(rates)
binomial_var = expected * (1.0 - expected) / current_total
sigma = math.sqrt(spread * spread + se_mean * se_mean + binomial_var)
if sigma <= _EPS:
    continue
```
For a mode **absent from every baseline run**, `rates = [0,0,0,…]`, so `expected = 0.0`, `pstdev = 0`, `bootstrap SE = 0`, and `binomial_var = 0·(1−0)/n = 0`. **All three terms vanish independently and simultaneously** → `sigma = sqrt(0) = 0.0` → the mode is skipped. The guard is documented at `:57-58` as a *"Numerical floor"*; that misdescribes it — this is not numerical degeneracy, it is total absence of the signal the detector needs.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.engine.anomaly import detect_anomalies
def run(rid,tax,tot=1000): return {'run_id':rid,'total_episodes':tot,'failure_taxonomy':tax}
for cnt in (100,500,900,1000):
    h=[run(f'b{i}',{'newmode':0}) for i in range(1,4)]; h.append(run('cur',{'newmode':cnt}))
    print(f'  new mode {cnt:>4}/1000 -> {len(detect_anomalies(h))} anomalies')
h=[run(f'b{i}',{'collision':10}) for i in range(1,4)]; h.append(run('cur',{'collision':600}))
a=detect_anomalies(h); print('  CONTROL 1% baseline ->',len(a),'anomaly, z=',a[0].z_score)"
```
**Output:**
```
  new mode  100/1000 -> 0 anomalies
  new mode  500/1000 -> 0 anomalies
  new mode  900/1000 -> 0 anomalies
  new mode 1000/1000 -> 0 anomalies
  CONTROL 1% baseline -> 1 anomaly, z= 187.5143
```

**Expected vs actual.** Expected: a mode appearing at 1000/1000 after a zero baseline is the strongest possible spike and is reported `critical`. Actual: **zero anomalies at every rate, including 100%.** The control proves the detector works — an established 1% baseline spiking to 60% yields `z=187.5`. So the failure is specific to the zero-baseline case, not a broken detector.

**Additional reachability evidence.** `detect_anomalies` is a **publicly exported API** — `engine/__init__.py:5` and `:36` — and is advertised in `README.md:172` as a shipped feature: *"`detect_anomalies()` flags statistically unusual spikes (z-score vs a baseline combining run dispersion, bootstrap standard error, and binomial noise)."* The README documents a variance-blended z-test that collapses to exactly zero for the case that matters most. No internal caller exists today, so the bug is **undetected rather than contained**; wiring it later is exactly when it becomes dangerous, and nothing warns against that.

**Business impact.** A library consumer calling `validsim.engine.detect_anomalies` to learn about new failure modes silently receives `[]`. The failure is unbounded — there is no input regime where it behaves acceptably — and it is invisible to `anomaly.py`'s existing tests because they all use a non-zero baseline. `anomaly.py` is also the second-least-covered engine file (88.89%).

**Dependencies.** `c3-engine` owns the fix. `c4-statistics` co-owns the invariant tests.

**Recommended fix.** Treat `expected == 0` as a distinct case: if the baseline rate is zero and the observed count is non-zero, that is a candidate by construction (a one-sided bound or a pseudo-count in `binomial_var`). Never `continue` silently — count or log the discard so "degenerate" is distinguishable from "no signal". Update `:57-58` to describe the real behaviour, and correct `README.md:172`.

**Test strategy.** Regression test asserting `detect_anomalies` returns **non-empty** for a zero-baseline mode at any rate above zero, plus a severity assertion. This pairs with the engine fix (it will be red until then) — unlike Q-02 it does not encode a currently-incorrect *production* value, so it can land green immediately after the fix with no known-red window.

---

## Q-04 — Job queue wedges permanently on both backends

**Description.** `JobQueue.enqueue` gates new work on `len(self._jobs)` (`jobs/queue.py:478`), but **`_jobs` has no eviction path**. All 12 access sites are one insert (`:488`) plus reads/updates; there is no `pop`, no `del`. `claim_next` *filters* on status rather than removing records, so terminal jobs stay resident and stay counted. The Redis backend has the identical shape (`:781`, depth computed in Lua) with no `LREM`/`LTRIM`/`DEL` anywhere in the package.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.jobs.queue import JobQueue, QueueFullError
from validsim.jobs.models import JobSpec
import inspect
q=JobQueue(max_depth=3)
for i in range(3): q.enqueue(JobSpec(run_id=f'vrun-0000000{i}',checkpoint_id='c',task_id='t',episodes=1,adversarial=0))
print('after 3 enqueues: len()=',len(q))
src=inspect.getsource(JobQueue)
print('  contains .pop(:', '.pop(' in src, '| del self._jobs:', 'del self._jobs' in src)
try: q.enqueue(JobSpec(run_id='vrun-00000099',checkpoint_id='c',task_id='t',episodes=1,adversarial=0))
except QueueFullError as e: print('  4th enqueue: QueueFullError:',e)"
```
**Output:**
```
after 3 enqueues: len()= 3
  contains .pop( False | del self._jobs: False
  4th enqueue: QueueFullError: job queue is full (3/3); cannot enqueue more jobs
```

**Expected vs actual.** Expected: `max_depth` bounds *in-flight* work, as documented at `:61-63` — *"Default maximum number of jobs the queue holds before rejecting new work… Bounds the otherwise-unbounded growth."* Actual: `len(self._jobs)` is a **lifetime enqueue count**, so the queue accepts `max_depth` enqueues *ever* and then refuses permanently. At the default `_DEFAULT_MAX_DEPTH = 1000` (`:65`), a long-lived deployment stops accepting work after 1000 jobs — memory recovery requires a process restart, Redis requires manual key surgery.

**Business impact.** `POST /api/v1/jobs` maps this to `503` (per `README.md:171`), so the API degrades from "queue full, retry shortly" into "queue permanently full, forever" with no self-recovery. The `__len__` docstring at `:624-625` reads *"Number of queued jobs"* — the only place the mismatch was ever visible, and the reason it survived.

**Dependencies.** `c2-perf` identified the mechanism; `c2-bugs` holds the docstring angle. Redis parity must be fixed in the Lua path too.

**Recommended fix.** Do **not** raise `max_depth` — that only moves the cliff. Either evict terminal jobs (DONE/FAILED) from `_jobs` and the Redis index, or compute depth as the count of QUEUED+RUNNING only. Keep `__len__` as total-history if needed, and add a separate `depth()` for the gate.

**Test strategy.** Test asserting the queue accepts more than `max_depth` jobs *in total* once earlier ones reach a terminal state, with a control asserting it still rejects while `max_depth` are genuinely in flight. Cover both memory and Redis backends.

---

## Q-05 — Robustness component is structurally constant on the real execution path

**Description.** `_robustness_score` (`engine/scorecard.py:59-64`) groups episodes by `randomization_level` and returns `100.0` when fewer than two groups exist. A **correctness caveat on my own earlier claim:** I previously reported this as unconditionally constant. That is wrong — a hand-built two-level run does produce `robustness=0.0`, so the function is not inherently degenerate. The defect is narrower and more precise: **`run_validation` never produces two levels in one run**, so the constant branch is the only reachable path in production.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.sim.runner import run_validation, MockIsaacBackend
from collections import Counter
T=TaskConfig(task_id='p',robot=RobotSpec(name='r'),environment=EnvironmentSpec(name='e'),episodes=60,randomization='partial',adversarial_count=0)
eps=run_validation(T,MockIsaacBackend(),[],seed=42)
print('  task.randomization =',T.randomization)
print('  levels produced    =',dict(Counter(e.randomization_level for e in eps)))"
```
**Output:** `levels produced = {'partial': 60}` — a single level across all 60 episodes.

**Control (proves the function itself works):** a hand-built run with `full`(100 pass) + `partial`(100 fail) yields `robustness = 0.0`, not 100.0. So the grouping logic is sound; the *input* is degenerate.

**Expected vs actual.** Expected: robustness measures cross-condition consistency. Actual: on every production path, `len(groups) == 1` → `robustness = 100.0` always, so `_W_ROBUSTNESS` (`scorecard.py:161`) multiplies a constant. The component contributes a fixed `+20` to every composite.

**Business impact.** One of the four ADR-0002 components is inert. Combined with Q-02 (adversarial pooled) and the regression gap below, the composite is substantially less expressive than the documentation claims — which directly undercuts the "explain in one sentence to a CTO" explainability goal.

**Dependencies.** `c3-engine`. Interacts with Q-02.

**Recommended fix.** Either have `run_validation` emit multiple randomization levels within a run (the substantive fix), or drop the robustness component from the composite until it measures something. A third option — recording the intended level spread as config — is weaker but honest.

**Test strategy.** Property test asserting a composite built from materially different runs (varying success rate, collisions, episode count) takes **at least N distinct values**, so any future component silently collapsing to a constant is caught without pinning a specific value. Pair with a per-component diagnostic test.

---

## Q-06 — Safety metric denominator is inconsistent with its sibling

**Description.** `engine/safety.py:88-91` computes the proximity-violation numerator over episodes where a human was present, but divides by `total` (all episodes):
```88:91:validsim/engine/safety.py
distances = [e.min_human_distance_m for e in episodes if e.min_human_distance_m is not None]
min_proximity = min(distances) if distances else None
proximity_violations = sum(1 for d in distances if d < proximity_limit_m)
proximity_rate = proximity_violations / total
```
The sibling `force_rate` (`:85-86`) uses the same `total` denominator, so the two sub-metrics in one `SafetyResult` disagree on their population.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.engine.safety import compute_safety
from validsim.sim.runner import EpisodeResult
def ep(i,d): return EpisodeResult(episode_id=f'e{i}',task_id='t',seed=i,success=True,
    collision_count=0,max_contact_force_n=5.0,min_human_distance_m=d,duration_s=1.0,randomization_level='full')
r=compute_safety([ep(i,0.1 if i==0 else None) for i in range(10)])
print('  human in 1 of 10, that 1 violates -> rate =',r.proximity_violation_rate)
print('  CONTROL human in all 10, all violate -> rate =',compute_safety([ep(i,0.1) for i in range(10)]).proximity_violation_rate)"
```
**Output:**
```
  human in 1 of 10, that 1 violates -> rate = 0.1
  CONTROL human in all 10, all violate -> rate = 1.0
```

**Expected vs actual.** Expected: `1 violating / 1 human-present episode = 1.0`. Actual: **0.1** — the sole proximity violation is diluted 10× by nine episodes with no human at all. Consequently the `_PROXIMITY_WEIGHT = 0.2` penalty (`:22`) is scaled by the human-presence fraction: at 10% human presence the proximity term can contribute at most **2 points instead of 20**.

**Business impact.** A safety weight is silently a function of scene composition rather than robot behaviour. Deployments that include few human-in-the-loop episodes receive proportionally weaker proximity enforcement — the opposite of the intent — with nothing in the output indicating it. The `:34-35` docstring says "out of all episodes", which is literally accurate but describes a metric that is not a violation *rate*.

**Key point for the report:** `engine/safety.py` has **100% line and branch coverage** and still contains this defect. This is the cleanest available demonstration that line coverage is the wrong gate.

**Dependencies.** `c2-bugs` already tracks the numerator/denominator mismatch as a functional bug.

**Recommended fix.** Either use `len(distances)` as the denominator, or keep `total` and rename the field to something like `proximity_violation_share_of_run` so it cannot be misread as a rate. Also document the population explicitly in the `Attributes:` block.

**Test strategy.** Test asserting `proximity_violation_rate == 1.0` when every human-present episode violates, independent of how many non-human episodes exist. Property test: scaling the number of human-free episodes must not change the reported rate.

---

## Q-07 — Five mutable models, two named in the standard; annotations lie about four

**Description.** `CONTRIBUTING.md:140-145` states: *"Configuration and result objects must be immutable… Never mutate a `TaskConfig`, `EpisodeResult`, `EvaluationResult`, etc."* `TaskConfig` (`config.py:121`) and `EpisodeResult` (`sim/runner.py:43`) are **named in the standard** and both mutable. Also mutable: `ValidationRequest` (`config.py:141`), `CompareRequest` (`api/main.py:79`), `EnqueueJobRequest` (`jobs/router.py:64`). Compliant: 20 of 25 model classes.

**Repro.**
```powershell
.venv\Scripts\python.exe -c "
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.sim.runner import EpisodeResult
t=TaskConfig(task_id='x',robot=RobotSpec(name='r'),environment=EnvironmentSpec(name='e'))
t.task_id='MUTATED'; print('  TaskConfig.task_id ->',t.task_id)
e=EpisodeResult(episode_id='e',task_id='t',seed=1,success=True)
e.success=False; print('  EpisodeResult.success ->',e.success)"
```
**Output:** `TaskConfig.task_id -> MUTATED`, `EpisodeResult.success -> False` — both succeed.

**Annotation fidelity (secondary).** `EpisodeResult.randomization_level` is declared `str` (`sim/runner.py:63`) but assigned from `task.randomization`, typed `RandomizationLevel = Literal["none","partial","full"]` (`config.py:17`). The narrow type is widened at the boundary, defeating the `Literal` for the field that Q-05 depends on. Separately, `joint_states_summary: dict[str,float]` uses `field(default_factory=dict)`, which is **correct** — I verified the caller's dict is not aliased — so this is a non-issue and I am recording it as ruled out rather than as a finding.

**Business impact.** `EpisodeResult` flows through every engine function and every store round-trip, so it is both the highest-value and the highest-risk object to make immutable. Leaving it mutable means `frozen=True` on downstream results (Q-08) protects nothing, because a caller can mutate the shared input at any time.

**Dependencies.** `c6-unit` should confirm which tests mutate these objects in place before the change lands.

**Recommended fix.** Add `model_config = ConfigDict(frozen=True)` to the three Pydantic models; `@dataclass(frozen=True)` to `EpisodeResult`. Type `randomization_level` as `RandomizationLevel` to restore the `Literal`.

**Test strategy.** Existing suite plus a new assertion that mutation raises (`FrozenInstanceError` / pydantic `ValidationError`). Run the full suite to catch in-place mutation in tests.

---

## Q-08 — Seven "frozen" models leak mutable state

**Description.** `frozen=True` blocks attribute **rebinding**, not mutation of contained objects. Seven classes carry `list`/`dict` fields:

| Class | file:line | Mutable field(s) |
|---|---|---|
| `EvaluationResult` | `engine/evaluation.py:31,32` | `per_task_success: dict[str,float]`, `failure_taxonomy: dict[str,int]` |
| `RegressionReport` | `engine/regression.py:57` | `items: list[RegressionItem]` |
| `Scorecard` | `engine/scorecard.py:111` | `failure_taxonomy: dict[str,int]` |
| `AdversarialScenario` | `scenarios/generator.py:79` | `params: dict[str,Any]` |
| `ShadowReport` | `sim/shadow.py:111` | `contract_violations: list[str]` |
| `ContractCase` | `sim/shadow.py:339,340` | `request`, `response`: `dict[str,Any]` |
| `StoredRun` | `store/memory.py:50` | `episodes: list[EpisodeResult]` |

**Repro.** AST scan of every `frozen=True` dataclass / Pydantic model in `validsim/` for `list`/`dict`/`set`-typed fields → 7 hits, table above. Runtime consequence: `card.failure_taxonomy["new"] = 1` and `report.items.append(x)` both succeed on "frozen" objects.

**Expected vs actual.** Expected: `CONTRIBUTING.md:140` — result objects are immutable. Actual: immutability is one level deep only. `Scorecard` is the **deploy-gate artifact**, so a caller can alter a verdict's inputs in place after construction; `StoredRun.episodes` is what gets persisted.

**Evidence the hazard is real and already partly handled:** `sim/shadow.py:114-118` `to_dict()` defensively copies `contract_violations` — proof the author knew, but the fix was applied ad-hoc in one method rather than at the type.

**Business impact.** The standard's intent (a verdict cannot be mutated after the fact) is not met for exactly the objects where it matters most. Combined with Q-07, a caller can mutate a shared `EpisodeResult` and silently change every derived score.

**Dependencies.** Touches engine output types; coordinate with `c2-bugs` and `c6-integration`.

**Recommended fix.** Convert collection fields to `tuple[...]` (and `Mapping`/`MappingProxyType` where a mapping is required), or add `__post_init__` coercion plus an explicit "do not mutate" contract in the docstring. The `to_dict()` copy at `shadow.py:114` becomes redundant once the field is a tuple.

**Test strategy.** Test asserting mutation of a converted field raises (`TypeError` on tuple, `TypeError` on `MappingProxyType`). Serialization round-trip tests must be updated for tuple types.

---

## Q-09 — 85.0 threshold hardcoded at 11 sites with two competing constants

**Description.** The deploy threshold has no single source of truth. Two *distinct module-level constants* already coexist, plus inline defaults:

| Site | Form |
|---|---|
| `engine/scorecard.py:108` | `threshold: float = 85.0` (Scorecard field) |
| `engine/scorecard.py:133` | `threshold: float = 85.0` (param default) |
| `engine/pipeline.py:55` | `DEFAULT_THRESHOLD = 85.0` |
| `jobs/worker.py:51` | `_DEFAULT_THRESHOLD = 85.0` |
| `cli.py:343` | `typer.Option(85.0, ...)` |
| `cli.py:361` | `typer.Option(85.0, ...)` |
| `notify/dispatcher.py:87,89,108` | `payload.get("threshold", 85.0)` ×3 |
| `engine/pdf.py:85` | `as_float(scorecard.get("threshold"), 85.0)` |
| `config.py:165` | `description="Composite gate; defaults to 85.0."` |

**Repro.** `grep -rn "85\.0" validsim --include=*.py` → 14 hits across 7 files (incl. docstrings). Docs drift too: `docs/adr/0002` says 85.0 while `vault/04 - Engineering/GitHub Actions Integration.md` uses `fail-below-score: 80`.

**Expected vs actual.** Expected: one constant, imported everywhere. Actual: two competing constants (`pipeline.DEFAULT_THRESHOLD` vs `worker._DEFAULT_THRESHOLD`) that can drift independently, plus 9 inline literals.

**Business impact.** Raising or lowering the gate requires editing 11 sites; missing one leaves Slack severity routing, the PDF header, or the CLI default disagreeing with the engine — a *stale approval* path, since `cli.py gate` trusts the stored verdict. `actions/validate/action.yml:52` hardcodes `"85"` separately and cannot import Python at all, so it needs a documented sync mechanism.

**Dependencies.** `c2-arch-debt` owns the docs-drift decision. Note the interaction with Q-02: raising the composite alone does not fix the adversarial floor.

**Recommended fix.** Single `DEFAULT_APPROVAL_THRESHOLD` in `engine/scorecard.py`, imported by all Python sites. For `action.yml`, either read from a generated file or document the sync requirement explicitly in `docs/github-actions.md`.

**Test strategy.** Test asserting every module's effective default equals the single constant (introspection over the constants), which fails automatically if a new copy is added.

---

## Q-10 — Dead code: unused sentinel, dead script parameter, orphan module

**Description.** Three distinct dead-code items, each confirmed by exhaustive grep (every one has exactly **one** repo-wide hit — its own definition):

1. **`RATE_LIMIT_DISABLED = 0`** (`api/main.py:106`), documented at `:105` as *"VALIDSIM_RATE_LIMIT value that disables rate limiting entirely."* The logic instead uses a bare literal: `if rate_limit > 0` at `api/main.py:500` and `:502`. The named, documented sentinel is never referenced — a reader trusts it and it does nothing.
2. **`[int]$ResampleBudget = 500`** (`scripts/build.ps1:18`) — a public script parameter never read anywhere in the script body. `-ResampleBudget 10000` is a silent no-op.
3. **`config_loader.py`** — 247 LOC. `load_config_file` / `load_default_config` / `discover_config_file` are imported **only** by `tests/test_config_loader.py:9`. `cli.py` defines no `--config` flag, yet `.env.example` declares `VALIDSIM_CONFIG` and the module docstring promises operators can tune a deployment *"without editing code."*

**Business impact.** (1) and (2) are small but actively misleading — both look like live configuration surface. (3) is an **unfulfilled feature**: 247 LOC, a documented env var, and a promise of operator-configurable overrides that no entrypoint can reach.

**Dependencies.** (3) is a product decision, not a cleanup: wire it up or delete it. `c2-arch-debt` should arbitrate.

**Recommended fix.** (1) use the constant at `api/main.py:500,502`; (2) delete the parameter; (3) either add `--config` to `cli.py`/`api` or remove the module, the env var, and the tests.

**Test strategy.** No test needed for (1)/(2) — they are deletions. For (3), if wired, a test that `--config` overrides a value; if deleted, assert the symbol is absent from the package exports (`test_package_exports.py` pattern already exists).

---

## Q-11 — 618 duplicated test lines (336 recoverable)

**Description.** 35 groups of **text-exact** duplicated top-level helpers across the suite (MD5 of whitespace-normalized source per top-level `def`/`class`). Total 618 duplicated lines, **336 recoverable**.

Largest clusters:

| Copies × lines | Helper | Sites |
|---|---|---|
| **7 × 5** | `cache_file` | `test_cli.py:22`, `test_cli_delete.py:28`, `test_cli_extra.py:29`, `test_cli_health.py:38`, `test_cli_jobs.py:32`, `test_cli_report.py:18`, `test_cli_worker_watch.py:34` |
| **7 × 2** | `_invoke` | 7 × `test_cli*.py` |
| **6 × 3** | `client` | `test_api.py:38`, `test_api_delete.py:32`, `test_api_pagination.py:27`, `test_api_renderers.py:34`, `test_dashboard.py:32`, `test_observability.py:58` |
| **2 × 22** | `_episode_json` | `test_isaac_worker.py:51`, `test_isaac_batching.py:53` |
| **2 × 22** | `_episode_json` | `test_shadow.py:79`, `test_shadow_email_paths.py:83` |
| **2 × 17** | `_scorecard` (×3 groups) | `test_api_filters.py:24`/`test_store_filters.py:29`; `test_export.py:13`/`test_notify_email.py:34`; `test_notify.py:13`/`test_notify_enhancements.py:22` |
| **2 × 17** | `_backend` | `test_isaac_worker.py:78`, `test_isaac_batching.py:92` |
| **2 × 11** | `_FakeCursor` | `test_store_filters.py:149`, `test_store_postgres.py:155` (byte-identical) |
| **3 × 4** | `_clear_config_env` | `test_api_health.py:47`, `test_metrics_content.py:214`, `test_observability.py:51` |

The psycopg doubles `_FakeCursor` + `_FakeConnection` are copy-pasted across **four** files: `test_store_count.py:107,120`, `test_store_delete.py:165,179`, `test_store_filters.py:149,162`, `test_store_postgres.py:155,168`.

**Business impact.** A fixture bug must be fixed in 7 places, and a partial fix produces *inconsistent* test behaviour across files — which reads as a product bug and costs debugging time. The 4-way psycopg duplication means a schema change to the fake requires 4 synchronized edits with no compiler to catch a miss.

**Dependencies.** `c6-unit` should sequence this; it touches many test files.

**Recommended fix.** Promote to shared fixtures in `tests/conftest.py` (or a `tests/_helpers.py`): `cache_file`, `_invoke`, `client`, `_episode_json`, `_scorecard`, and a single psycopg double set. `conftest.py` already exists — extend it rather than create a parallel mechanism.

**Test strategy.** Meta-test asserting no top-level test helper is text-exact-duplicated across files (cheap, and prevents the count regrowing).

---

## Q-12 — Eight functions exceed 60 lines; `create_app()` is 383 lines

**Description.** AST span + statement counts over `validsim/`:

| Lines | Stmts | Location | Function |
|---|---|---|---|
| **383** | **129** | `api/main.py:480` | **`create_app()`** |
| 157 | 48 | `engine/pdf.py` | `_build_story()` |
| 91 | 16 | `engine/pipeline.py:78` | `run_and_score()` |
| 86 | 48 | `scenarios/llm_generator.py` | `generate()` |
| 84 | 24 | `sim/isaac_worker.py` | `_episode_from_dict()` |
| 77 | 22 | `engine/scorecard.py:125` | `build_scorecard()` |
| 64 | 15 | `engine/export.py:78` | `scorecard_to_markdown()` |
| 62 | 21 | `api/metrics.py:131` | `render_metrics()` |

Largest modules: `jobs/queue.py` 946 · `api/main.py` 867 · `cli.py` 850. Largest classes: `RedisJobQueue` 292 (`jobs/queue.py:637`) · `IsaacWorkerBackend` 269 · `PostgresValidationStore` 237 · `JobWorker` 233.

Parameter pressure (ruff `PLR0913`): `build_scorecard()` **10 params**, `run_and_score()` **9**, `ShadowRunner.__init__` **8**.

**Business impact.** `create_app()` is 8.7% of `main.py` and wires middleware, CORS, auth, and every route inline — the single worst readability item in the codebase and the reason route-level regressions are hard to bisect. `build_scorecard`'s 10 parameters are a direct risk for Q-02/Q-05: positional-argument mix-ups are silent and produce wrong composites.

**Recommended fix.** Extract `create_app()` into `_register_routes`, `_install_auth`, `_install_cors`. Group `build_scorecard`/`run_and_score` parameters into a frozen config dataclass — this also gives Q-09 a natural home for the single threshold constant. Split `RedisJobQueue`'s in-memory and Redis implementations.

**Test strategy.** Existing API tests should cover the extraction unchanged (they construct the app via `create_app`). Add a smoke assertion that the route table is identical before/after.

---

## Q-13 — `health` always exits 0, so an unreachable store still reports "ok"

**Description.** `cli.py:609-611` and `:620-622` swallow **any** exception from `create_store()` / `create_job_queue()`, set the backend label to `"error"`, and print the failure to **stderr**. Then `cli.py:624` prints `"ValidSim health: ok"` unconditionally.

**Repro.** Source read of `cli.py:600-627`; the two broad-except sites are annotated `# noqa: BLE001 - a status read must never fail`. Control: a healthy store takes the same path and also prints "ok" — the output is indistinguishable except for one stderr line.

**Expected vs actual.** Expected: a probe used by CI or an operator reports failure when the store is unreachable. Actual: **exit code 0 and "ValidSim health: ok" with a completely dead Postgres.** The only signal is a stderr line and a substring in a table.

**Business impact.** This is the highest-severity **observability** defect: the component whose job is to report on the system's health is the one component that cannot fail. Meanwhile `docker-compose` runs `python -m validsim.cli worker`, which does *not* swallow — so the process doing the work and the probe judging it have opposite failure semantics.

**Dependencies.** Shares a root cause with Q-04: the queue/store layer fails in ways the probe cannot surface.

**Recommended fix.** Distinguish "checked and healthy" from "could not check." Exit non-zero (or print a distinct `health: degraded`) when a store/queue probe fails, and keep exit 0 only for genuine health. Retain the broad except — swallowing is right for a status read; reporting success on failure is not.

**Test strategy.** Test with a store stub that raises on every call: assert exit code ≠ 0 and that "ok" is absent. Control: healthy store → exit 0 and "ok" present.

---

## Q-14 — Four AST-identical test bodies; 18 wall-clock-dependent test sites

**Description.** Two distinct test-quality defects.

**(a) AST-identical bodies (zero marginal coverage).** Normalized-AST comparison found 4 groups / 8 functions with byte-identical bodies:
- `test_api_auth_negative.py:274` `test_list_without_key_401` ≡ `test_api_jobs_wired.py:104`
- `test_api_auth_negative.py:279` `test_enqueue_without_key_401` ≡ `test_api_jobs_wired.py:99`
- `test_config_fuzz.py:176` `test_sha256_wrong_length` ≡ `test_config_fuzz.py:181` (same file, differ only by parametrize decorator)
- `test_isaac_worker.py:440` `test_explicit_mock` ≡ `test_sim_factory.py:119`

Plus 2 byte-identical classes: `FakeProvider` (`test_llm_coverage.py:22` / `test_llm_generator.py:31`) and `_FakeCursor` (`test_store_filters.py:149` / `test_store_postgres.py:155`).

**(b) Wall-clock dependence (flake risk).** `test_worker_shutdown.py` has **18** timing sites: 8 `thread.join(timeout=…)` (`:131,135,160,201,226,260,279,296`), 4 `deadline = time.time() + 2.0` poll loops (`:154,190,214,217`), bare `time.sleep` (`:70,156,197,220,258,292`), and `assert time.monotonic() - started < 1.0` (`:299`). Plus `test_stats_edge.py:139` `assert elapsed < 20.0` and `test_jobs_router_hardening.py:171-180, 208-210`.

**Business impact.** (a) inflates the test count without adding coverage — the suite reports 1388 tests while 8 of them re-assert the same thing. (b) makes the suite non-deterministic on loaded CI, which trains the team to retry rather than investigate, and erodes trust in the gate.

**Dependencies.** `c6-unit`.

**Recommended fix.** (a) Merge into `@pytest.mark.parametrize`; move the shared `FakeProvider` and `_FakeCursor` into `conftest.py` (overlaps Q-11). (b) Replace sleeps with `threading.Event` / explicit signalling and assert **state or ordering, not duration**. For `test_stats_edge.py`, assert algorithmic properties (resample count, convergence) or mark `@pytest.mark.slow` rather than asserting a 20-second wall-clock budget. Quarantine the timing files in `nightly.yml` via a reruns strip rather than loosening bounds.

**Test strategy.** Meta-test for (a): no top-level test helper text-exact-duplicated across files. For (b): a reruns-strip comparison (same seed, N repeats) in the nightly job to identify true flakes.

---

## False leads — ruled out (recorded so they are not re-investigated)

| Claim | Why it is wrong | How verified |
|---|---|---|
| `engine/anomaly.py` calls bare `int()` instead of `_coerce.as_int` | **REFUTED.** `anomaly.py:110` and `:97` both route through `as_int(v, default=0)`. Full-engine sweep for `int(`/`float(` finds only `_coerce.py:38,61` (the helpers) and `stats.py:25,27,65,67`, which operate on already-numeric values. | AST sweep of `validsim/engine/` |
| `scorecard_to_markdown` performs no escaping | **REFUTED.** `export.py:13` imports `html.escape`; dedicated Markdown escaping exists (`_MD_CELL_ESCAPES:28`, `_md_cell:32-39`, applied at `:92`). The asymmetry with the HTML renderer is **correct by construction** — HTML injection is `<`-delimited, Markdown table cells additionally break on `\|` and `* _ [ ]`. Routing Markdown through `html.escape` would leave `\|` unescaped and break every table. | Source read of `export.py:13,26-49,92` |
| `store/postgres.py:512` `__len__` docstring says "Number of queued jobs" | **REFUTED / misattributed.** It reads *"Number of persisted runs"* — accurate, as are `sqlite.py:304` and `memory.py:150`. The real defect is `jobs/queue.py:624` and is now filed as **Q-04**. | Read all three `__len__` implementations |
| `jobs/queue.py` `_max_depth` validation is inconsistent with behaviour | **REFUTED.** `:338-366` is correct in all four branches (env path, explicit path, `< 1` rejection), with the offending value interpolated into every message. | Source read of `queue.py:338-366` |
| `EpisodeResult.joint_states_summary` aliases the caller's dict | **REFUTED.** `field(default_factory=dict)` is correct — verified the caller's dict is not shared and later caller mutation does not affect the stored value. This is a non-issue, recorded to prevent a false positive. | Runtime identity probe |
| Robustness is *unconditionally* constant | **CORRECTED — my own earlier claim was too strong.** A hand-built two-level run yields `robustness=0.0`, so the function is not inherently degenerate. The accurate claim (Q-05) is narrower: `run_validation` emits exactly one level per run, so the constant branch is the only path reachable in production. | Two-level control run + `run_validation` output |
| 149 cross-module private-symbol references from tests (coupling concern) | **OVERBUILT.** 1,877 private-name references exist, but only **10** are cross-module imports from `validsim.*`, in **4 of 83** test files. The rest are test-local helpers (`_task`, `_scorecard`, `_invoke` defined in the test files themselves) — correct encapsulation, not coupling. | AST scan of `ImportFrom` nodes |
| Import-cycle risk in `sim/__init__` → `isaac_worker` → `runner` → `scenarios.generator` | **REFUTED.** `scenarios/generator.py` imports only `random`, `dataclasses`, `typing` — zero `validsim` imports — so the chain terminates. All 11 probed modules import cleanly standalone. | Subprocess import of 11 modules + import scan |
| `test_cli_version.py:34` `assert "0.2.0" in output` is currently failing | **REFUTED as stated; risk is real but future.** `__version__` **is** currently `0.2.0`, so the test passes today. It will fail on the next version bump, and line 33 (`assert __version__ in output`) is the correct drift-proof check. Worth deleting line 34. | Version comparison + source read |

---

## Assumptions

1. **Working tree is not a stable baseline.** Concurrent edits by multiple agents mean line numbers drift; each finding carries a reproduction so it can be re-checked rather than trusted from the citation alone.
2. **Severity is judged on deployment impact**, assuming ValidSim is used as documented in `README.md` — as a deploy gate for robot foundation models. Where a defect is unreachable from every entrypoint today, that is stated explicitly in the finding.
3. **`engine/anomaly.py` is treated as a public API** because `engine/__init__.py:5,36` exports it and `README.md:172` advertises it, despite having no internal caller. If the team decides it is internal-only, Q-03 drops from Critical to High; the defect itself is unchanged.
4. **The `_EPS`/`sigma` mechanism in Q-03 is the *intended* design** per its comment, so the fix is a design change rather than a typo fix. That is why the finding argues for a distinct `expected == 0` case instead of a lower epsilon.
5. Coverage figures quoted from other agents were not independently re-measured by me; where I cite them (e.g. `safety.py` at 100%) I rely on their measurement and say so.

---

## Summary

**14 findings: 3 Critical, 4 High, 7 Medium.** The most important is **Q-02** — the deploy gate approves a 524-episode run in which every adversarial episode fails, because the composite pools adversarial and nominal episodes into one rate and the adversarial share is operator-configurable.

**The cross-cutting pattern is that all three Criticals and most Highs are individually correct arithmetic feeding a silent discard**, and in each case the surrounding prose describes the intended contract while the code implements something narrower:

- `queue.py:61-63` documents a *memory bound*; `:478` implements a *lifetime cap* (Q-04)
- `anomaly.py:57-58` documents a *numerical floor*; `:186` implements *signal deletion* (Q-03)
- `README.md:172` documents a *variance-blended z-test*; `:185` computes a quantity identically zero (Q-03)

**None of these are detectable by the tooling the project has configured.** Q-01 shows the lint gate cannot fail; `engine/safety.py` has 100% line *and* branch coverage and still contains a semantically wrong metric (Q-06). Every tool in place measures form; every Critical and High finding is semantic. That is the structural observation I would put first: the codebase is exemplary on every dashboard the team has, and the dashboards are measuring the wrong thing.
