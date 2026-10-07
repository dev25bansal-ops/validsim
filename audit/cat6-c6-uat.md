# cat6 — UAT / Acceptance-Criteria Audit (ValidSim)

**Agent:** c6-uat · **Date:** 2026-09-26 · **Tree:** `d:\SIM-TO-REAL` · **Version:** `validsim 0.2.0`
**Scope:** user-facing acceptance criteria, documented-vs-shipped claims, release-quality gates.
**Mode:** read-only. No production file was edited. All findings executed against this tree.

---

## Findings

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| **F1** | `TaskConfig.episodes` has no floor; a 1-episode run returns composite 100.0 / **APPROVE** | `validsim/config.py:136` | **High** | VERIFIED | 2–4h | `POST /api/v1/validations` with `episodes:1` (probe below) |
| **F2** | 30 of 100 composite points are unconditional on the CLI/worker path; composite is systematically optimistic | `validsim/engine/scorecard.py:59-66`, `validsim/cli.py:300-307`, `validsim/jobs/worker.py:280-288` | **High** | VERIFIED | 1–2d | Probe: 5 API runs, robustness ∈ {100.0} only |
| **F3** | `make smoke` cannot pass on a clean machine — documented target is broken | `Makefile:49` | **High** | VERIFIED | 15m | `python -m validsim.cli run … && validsim gate --latest` → exit 2 |
| **F4** | `randomization` unreachable from the CLI and hardcoded in the dashboard; UI cannot reach APPROVE at 85 | `validsim/cli.py:295-307`, `validsim/web/app.js:706` | **Medium** | VERIFIED | 2–3h | `validsim run --randomization partial` → *No such option* |
| **F5** | `config_loader` is dead code with zero production callers while `.env.example` declares its env var | `validsim/config_loader.py`, `.env.example:150` | **Medium** | VERIFIED | 1h | `git grep config_loader -- validsim/` → 0 hits |
| **F6** | `create_scenario_generator` is exported + documented but never called; the LLM scenario path cannot execute | `validsim/engine/pipeline.py:131` | **Medium** | VERIFIED | 30m | `git grep create_scenario_generator -- validsim/` → only façade + defn |
| **F7** | Documented-but-absent endpoints/commands (5 phantoms the issue catalog missed) | `vault/03 - Product/Core User Flows.md:53`, `vault/00 - Dashboard/Build Status.md`, `vault/04 - Engineering/Security Hardening.md`, `CLI Design.md`, `Technical Moat Features.md` | **Medium** | VERIFIED | 4–6h | Claims-audit probe vs live OpenAPI + Typer app |
| **F8** | `examples/nightly-adversarial-sweep.yml` is stale and its actions are unpinned; the pin test cannot see `examples/` | `examples/nightly-adversarial-sweep.yml:18-20,46,52,167` | **Low** | VERIFIED | 30m | YAML parse + SHA-pin probe |

**Single most important finding: F2.** A third of the composite is a constant on the interface the product leads with, and both constants *flatter* the score — so the gate is optimistic by up to 30 points on exactly the runs a customer would act on.

---

## F1 — No minimum episode count; a 1-episode run returns APPROVE

**Severity** High · **Status** VERIFIED · **Effort** 2–4h

### Description
`TaskConfig.episodes` is bounded below by `ge=1` and above by `le=100000`, with **no operational minimum**. Both the API and the CLI accept the value from the caller. Every scorecard component short-circuits on a single sample, so a 1-episode run in which the single episode succeeds produces `success_rate=1.0`, `safety=100.0`, `robustness=100.0` and therefore `composite=100.0` — and `deploy_decision=APPROVE`.

The evidence guard at `scorecard.py:195-198` is **correct and does fire** on under-delivery (`total_episodes >= task.episodes`); I verified it and am explicitly not claiming otherwise. The defect is that the *input* is unconstrained, so a client can request a statistically meaningless run and receive a perfect scorecard.

### Repro
```python
# probe: degenerate-input acceptance
from fastapi.testclient import TestClient
from validsim.api.main import create_app
c = TestClient(create_app())
for eps in (1, 2, 5):
    d = c.post("/api/v1/validations", json={
        "checkpoint_id": f"f1-{eps}",
        "task": {"task_id": "pick-place", "robot": {"name": "franka_panda"},
                 "environment": {"name": "kitchen"},
                 "episodes": eps, "randomization": "full", "adversarial_count": 0},
    }).json()
    print(eps, d["success_rate"], d["composite_score"], d["deploy_decision"])
```

### Expected vs actual
| episodes | success | composite | decision | expectation |
|---|---|---|---|---|
| **1** | **1.0** | **100.00** | **APPROVE** | should not certify a deploy |
| 2 | 0.5 | 80.00 | BLOCK | — |
| 5 | 0.8 | 84.20 | BLOCK | — |

### Business impact
A customer or CI job can post `{"episodes": 1}` and receive a **100.0 / APPROVE** scorecard that is indistinguishable in shape from a 5,000-episode one. This is the product's core promise — a trustworthy verdict — failing on a permissive input. It is also the cheapest possible thing for a competitor or a bad actor to demonstrate.

### Control (rule 6)
The guard was tested directly and **fires correctly**: `asked=50, delivered=1` → `composite=100.0` → **BLOCK**. An all-fail 100-episode run at `threshold=0` also BLOCKs (the `success_count > 0` clause). So this is **not** a gate-correctness defect and **not** a vacuous guard — it is an input-validation gap. Stating that distinction precisely matters: an earlier framing of mine ("the guard is unreachable/vacuous") was **REFUTED** and is recorded in *False leads*.

### Dependencies
None. Independent of F2.

### Recommended fix
Reject or explicitly mark degenerate runs at the boundary: a `VALIDSIM_MIN_EPISODES` floor (default well below 1,000 but far above 1) enforced in `TaskConfig`, or an explicit `evidence_sufficient: false` flag surfaced in the scorecard so a caller can see *why* a 1-episode run is not a certification. The floor is a product decision; I am not proposing the number.

### Test strategy
`test_degenerate_episode_count_is_not_certifiable` — `episodes=1` must not return APPROVE. `test_minimum_episode_floor_is_enforced` — `episodes=0` → 422. Both belong in the `acceptance` job, not the unit suite.

---

## F2 — 30 of 100 composite points are unconditional (CLI/worker)

**Severity** High · **Status** VERIFIED · **Effort** 1–2 days

### Description
The published formula is `composite = 0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression` (`README.md:131`, `docs/runbook.md:102-104`). On the CLI and async-worker paths **two of those four terms are constants**:

- **`robustness` = 100.0 always.** `_robustness_score` (`scorecard.py:53-66`) groups episodes by `randomization_level`; `runner.py:173-185` assigns `task.randomization` to *every* episode, so a run has exactly one group and `len(rates) < 2` returns the maximum. Unreachable, not merely rare.
- **`regression` = 100 on CLI/worker.** `baseline_run_id` is supplied only by the API; `cli.py:300-307` and `jobs/worker.py:280-288` both omit it, and `_regression_component(None)` returns 100.0.

So on the CLI — the offline-first path the product leads with, and the one every GitHub Action uses — **`composite = 0.4·success + 0.3·safety + 30.0`**. A user tuning `--episodes` moves two of four terms.

### Repro
```python
# probe: robustness invariance
c = TestClient(create_app())
for eps, adv, rnd in [(1,0,"full"),(10,5,"full"),(60,10,"full"),(200,20,"partial"),(500,50,"none")]:
    d = c.post("/api/v1/validations", json={"checkpoint_id": f"c{eps}{rnd}",
        "task":{"task_id":"pick-place","robot":{"name":"franka_panda"},
                "environment":{"name":"kitchen"},"episodes":eps,
                "randomization":rnd,"adversarial_count":adv}}).json()
    print(eps, adv, rnd, d["robustness_score"])
```

### Expected vs actual
```
 1    0   full     robust=100.0
 10   5   full     robust=100.0
 60   10  full     robust=100.0
 200  20  partial  robust=100.0
 500  50  none     robust=100.0
 distinct robustness values: [100.0]  -> constant
```
Reconstruction confirms it is load-bearing: `0.4×70.94 + 0.3×80.22 + 0.2×100.0 + 0.1×100 = 82.44`, matching the reported composite exactly.

### Business impact
**Both constants inflate the score.** A policy with genuinely poor cross-condition consistency would score *lower* if the metric worked, so today's composite is **systematically optimistic by up to 30 points** — silently, on every CLI run, in the direction of approval. A CTO shown "82.4" is shown a number that contains 30 points no run can influence. This is a correctness problem in the product's central claim, not a coverage gap.

### Control (rule 6)
The *published formula itself* is honest — it reconstructs the composite to within 0.05 across three run sizes. The defect is in one **input**, not the scoring model. That distinction narrows the fix and is worth stating: **do not touch the weights.**

### Dependencies
Interacts with F4 (a `--randomization` flag alone does not fix this — a per-run knob cannot create within-run variation; the runner must emit mixed levels). Interacts with any future composite threshold change: composites are not comparable across pipeline versions while a third of the weight is unmeasurable.

### Recommended fix
Make the runner emit a **declared ladder** of randomization levels within a single run (derive level from the **episode index**, not a run-global RNG stream, or `runner.py:172-180`'s per-episode determinism breaks). Then define robustness over ≥2 groups. Until it computes, either emit `robustness_valid: false` or stop rendering it — c3-web owns the display. Note `runner.py` assigns `seed + i`, so a ladder keyed on `i` preserves reproducibility.

### Test strategy
`test_robustness_is_not_unconditionally_maximal` — robustness must vary across run configurations. `test_robustness_is_invariant_to_sample_size` — under a **fixed-rate** fixture, robustness must not move with `n` (this is the control that would have caught a bad fixture I encountered). `test_composite_is_decomposable` — already passes; keep it as the regression lock on the published formula.

---

## F3 — `make smoke` cannot pass on a clean machine

**Severity** High · **Status** VERIFIED · **Effort** 15m

### Description
`Makefile:49` chains `run --episodes 20 --threshold 50 && gate --latest && report --latest` with **no `VALIDSIM_STORE` set**. `gate` refuses the default in-memory backend (`cli.py:162-180`) and exits `2`. The documented quick-start sequence and the repo's own end-to-end smoke target therefore fail out of the box.

### Repro
```powershell
$env:VALIDSIM_STORE="memory"
python -m validsim.cli run --episodes 20 --threshold 50   # Decision: APPROVE
python -m validsim.cli gate --latest
#   error: gate needs a durable store, but VALIDSIM_STORE='memory' …
#   exit 2
```

### Expected vs actual
Expected: `make smoke` exits 0. Actual: exit 2, no `gate` verdict.

### Business impact
The one command a new user runs to check the product works is broken. Directly undermines the "zero config" claim in `Product Principles` #1.

### Dependencies
None.

### Recommended fix
Set `VALIDSIM_STORE=sqlite` and `VALIDSIM_SQLITE_PATH` for the **whole chain**. **Trap:** two `$(mktemp -d)` calls create two different sqlite files, which swaps "no durable store" for "no stored runs" — a *different* exit-2 that looks like progress. Use one shared temp dir, and pin `VALIDSIM_CACHE_FILE` into it too, because `status`/`scorecard`/`report` read the **JSON cache** while `gate` reads the **store** — two backends, both must be populated.

### Test strategy
`test_acceptance_demo_env.py` — the full `run → scorecard → report → gate` chain with a shared temp dir, asserting exit 0; plus a BLOCK leg at `--threshold 99` asserting exit 1.

---

## F4 — `randomization` unreachable from CLI; hardcoded in the dashboard

**Severity** Medium · **Status** VERIFIED · **Effort** 2–3h

### Description
`randomization` is a first-class `TaskConfig` field (`config.py:137`, default `"full"`), accepted by the API, and validated on the Isaac wire (`isaac_worker.py:264-273`). It is **absent from the CLI** and **hardcoded** at `app.js:706`.

### Repro
```powershell
python -m validsim.cli run --episodes 100 --adversarial 10 --randomization partial
#   Error: No such option: --randomization
```
`app.js:706` → `randomization: "full",`

### Expected vs actual
`validsim run --help` lists `--episodes`, `--threshold`, `--task`, `--robot`, `--environment`, `--adversarial` — and **not** `--randomization`. Five of six modelled fields are reachable; this one is not.

**Impact:** measured at 1000+24 episodes — `full` min composite 80.62 with **0/3 approvals**; `partial` min 86.69 with **3/3**. **The dashboard as shipped cannot produce an APPROVE at the real threshold**, because it hardcodes the only level that cannot clear it.

### Business impact
A documented, API-supported capability is unreachable from the CLI — the primary interface. This is the item-17 class in code: the field exists in the model and one interface, and not the other.

### Recommended fix
Add `--randomization` to `run` (and `validate`, which delegates to `_run_impl`, so it inherits it); add a `<select>` to the dashboard with `full` as default. **Sequence it after F2** — a per-run knob without a within-run ladder still yields a constant robustness, which looks like control and is not.

### Test strategy
`test_every_task_config_field_is_reachable_from_the_cli` — derived from `TaskConfig.model_fields`, asserted through `--help` + `CliRunner`, **not** by walking Typer internals (an introspection of `cmd.params` reports all six fields missing, including ones that exist; a check that produces false gaps is worse than none). Guard the map with `test_flag_map_covers_the_model_exactly` so it cannot drift from the model.

---

## F5 — `config_loader` is dead code with a documented env var

**Severity** Medium · **Status** VERIFIED · **Effort** 1h

### Description
`validsim/config_loader.py` implements TOML + flat-YAML config-file loading and discovery. `cli.py` never imports it and defines no `--config` option.

### Repro
```
git grep config_loader -- validsim/     → 0 hits
```
`.env.example:150` declares `VALIDSIM_CONFIG`.

### Expected vs actual
Expected: a documented env var that does something. Actual: a well-covered module with **zero production callers**, plus an env var that reads as functional and is not.

### Business impact
Low direct impact, high signal: it is the *shape* of item 17 in code — a documented capability that does not exist. An operator who sets `VALIDSIM_CONFIG` gets silence.

### Recommended fix
Either wire a `--config` flag or delete the module and the env var. Do not leave a documented knob that does nothing.

### Test strategy
`test_documented_capabilities_are_reachable` — for each `__all__`-exported and `.env.example`-declared symbol, assert at least one production (non-test) call site. This is a **general** check that would have caught F5 and F6 automatically.

---

## F6 — `create_scenario_generator` never called; LLM scenario path unreachable

**Severity** Medium · **Status** VERIFIED · **Effort** 30m

### Description
`create_scenario_generator` is exported (`scenarios/__init__.py:17`) and documented at `docs/scenario-taxonomy.md:365` as picking the LLM or rule-based backend with no code change. `pipeline.py:131` hardcodes `ScenarioGenerator(seed=seed)`.

### Repro
```
git grep create_scenario_generator -- validsim/
  validsim/scenarios/__init__.py:17   (façade re-export)
  validsim/scenarios/__init__.py:30   (__all__)
  validsim/scenarios/llm_generator.py:8,48  (definition)
# zero call sites
```

### Expected vs actual
Setting `VALIDSIM_LLM_API_KEY` is documented to select the LLM generator. It cannot: no production path calls the factory.

### Business impact
An entire advertised capability — LLM-generated adversarial scenarios — is inert. The deterministic fallback masks it, so behaviour is safe but the documented feature is absent.

### Recommended fix
Call the factory in `pipeline.py:131`. Note this is the same seam where an F2 within-run randomization ladder would live, so batch the two changes.

### Test strategy
`test_llm_scenario_path_is_reachable` — with `VALIDSIM_LLM_API_KEY` set, assert the pipeline constructs `LLMScenarioGenerator` (mock provider). Fails today.

---

## F7 — Five documented-but-absent endpoints/commands

**Severity** Medium · **Status** VERIFIED · **Effort** 4–6h

### Description
A claims-audit probe extracted every `(VERB, /api/v1/...)` and `validsim <subcommand>` reference from `README.md`, `docs/`, and `vault/`, and resolved each against the **live** OpenAPI schema and the **live** Typer command tree. Five claims have no implementation:

| Phantom claim | Source |
|---|---|
| `POST /api/v1/deployment-gate` | `vault/03 - Product/Core User Flows.md:53` |
| `POST /api/v1/webhooks` | `vault/00 - Dashboard/Build Status.md`, `vault/04 - Engineering/Security Hardening.md` |
| `validsim fleet` | `vault/04 - Engineering/CLI Design.md` |
| `validsim hil` | `vault/04 - Engineering/CLI Design.md` |
| `validsim submit` | `vault/06 - Business/Technical Moat Features.md` |

### Repro
```python
# probe: resolve doc claims against live app + CLI
from validsim.api.main import create_app
from typer.main import get_command
from validsim.cli import app
real = {(m.upper(), p) for p, ops in create_app().openapi()["paths"].items() for m in ops}
cli  = set(get_command(app).commands)
# then regex the docs and diff
```

### Expected vs actual
23 real routes / 15 CLI tokens vs. 5 phantom claims. **Note:** `ISSUE_CATALOG.md` item 17's phantom list is now **stale** — `GET /api/v1/audit-log`, `validsim ci`, `--fail-below`, `scorecard --format pdf` and `validsim delete` have all since been correctly annotated. The class is alive, but the list is not current, which is itself the finding: **correction passes are not prevention.**

Also verified: **28 numeric test-count claims** across docs/vault, and coverage claims (95.84% / 95.88%) disagree with a measured 95.57%. The count is **not stable within a session** (1388 → 1444 → 1466 across ~2h), so any audit must compare docs against a **CI-generated artifact**, never a hardcoded literal.

### Business impact
This is the item-17 class: an investor- or customer-facing document asserting a capability that does not exist. It is the finding most likely to cost real money, and it recurs because nothing prevents it.

### Recommended fix
Ship the claims-audit as a test with **explicit, justified suppressions** (a committed registry with reason + date + owner), so the exception list is reviewable in a PR diff rather than accumulating silently. Do **not** hardcode any count.

### Test strategy
`test_documented_endpoints_exist` · `test_documented_cli_commands_exist` · `test_numeric_claims_match_generated_artifact` (CI writes the collect-only tail to the build summary; docs are compared to that artifact) · `test_published_versions_are_annotated` (every `validsim/*@v1` reference must sit inside a block marked future/planned — nothing is published, so an unannotated `@v1` is a false claim).

---

## F8 — `examples/nightly-adversarial-sweep.yml` stale and unpinned

**Severity** Low · **Status** VERIFIED · **Effort** 30m

### Description
Two independent defects in the file the vault names as the founder's real nightly job (Flow 4):
1. **Stale claim** at `:18-20`: *"no pyproject.toml yet, so there is nothing to pip-install from the repo itself."* `pyproject.toml` exists, and `actions/validate/action.yml:113` does `pip install .`.
2. **Unpinned actions** at `:46`, `:52`, `:167` — `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4` — while `tests/test_delivery_pipeline.py::TestExternalActionsArePinned` requires 40-char SHAs. **That test only globs `.github/workflows/` and `actions/*/action.yml`, so `examples/` is an unchecked hole.**

### Repro
```python
import yaml, re, pathlib
doc = yaml.safe_load(pathlib.Path("examples/nightly-adversarial-sweep.yml").read_text())
sha = re.compile(r"^[0-9a-f]{40}$")
for job in doc["jobs"].values():
    for st in job.get("steps", []):
        u = str(st.get("uses",""))
        if u and not u.startswith("./") and not sha.fullmatch(u.rsplit("@",1)[-1]):
            print("UNPINNED:", u)
# UNPINNED: actions/checkout@v4 / actions/setup-python@v5 / actions/upload-artifact@v4
print("stale claim present:", "no pyproject.toml" in pathlib.Path(...).read_text())  # True
```

### Expected vs actual
Expected: an example users copy is current and safe to run. Actual: it tells the reader the opposite of the truth about packaging, and it carries three mutable action references.

### Business impact
Low, but supply-chain relevant (item 32's class) and credibility-relevant: this is the artifact the founder's own proof-of-dogfooding rests on.

### Recommended fix
Pin to the same SHAs `ci.yml` already uses; correct the stale comment; extend the pin test to `examples/*.yml`.

### Test strategy
`test_every_example_action_is_sha_pinned` · `test_examples_are_not_stale` (no reference to files that no longer exist or claims contradicted by the tree).

---

## False leads — ruled out, with evidence

Recording these because the directive exists because prior claims were retracted, and a retraction that is not documented becomes the next agent's false finding.

1. **"The evidence guard is vacuous / can never fire."** — **REFUTED.** I verified `build_scorecard` directly: `asked=50, delivered=1, all succeed` → `composite=100.0` → **BLOCK**. `scorecard.py:195-198` is a real, load-bearing guard. My earlier claim was true only of the *pipeline* and I stated it as a property of the *function*. The pipeline *cannot* under-deliver (the mock returns exactly `task.episodes`; `isaac_worker.py:296-301` raises on a short reply), so the guard has no production coverage — but that is a **test-coverage gap, not a gate defect**, and the two must not be conflated.
2. **"The dashboard's `threshold` control is dead / the API ignores it."** — **REFUTED as of this tree.** I re-tested and it now behaves correctly: `ValidationRequest` has a `threshold` field, `_execute_validation` passes it, and a control sweep shows the **verdict changes** with the submitted threshold (10/50 → APPROVE, 85/99 → BLOCK at composite 78.78). `app.js` reads `els.form.elements.threshold` and sends it. My earlier measurement (always 85.0) was true when taken and is now stale — reporting it would have been a false finding.
3. **"Robustness is a spend-more-compute gaming surface (22-point swing)."** — **REFUTED.** Raised by a teammate, investigated by three parties, retracted by the originator. The mechanism was small-sample estimator bias plus RNG-consumption order in the *fixture*, not the metric. A control with **fixed group rates** and varying `n` is flat. **No such surface exists in shipped code** (the live metric is constant 100.0 at every episode count). The *requirement* that survives is a precision guard — minimum per-group n plus a bootstrap half-width cap — because a point estimate that unstable is unsound to gate on regardless of direction.
4. **"All 1466 tests pass."** — **REFUTED.** `2 failed, 1440 passed, 8 skipped`; both failures are `tests/test_cli_delete.py::TestDeleteLatestAgainstTheDurableStore`. Also note collect-only (1466) and JUnit (1450) disagree by 16 — **never quote either without naming the command.**
5. **"The composite weights are wrong / the formula is misdocumented."** — **REFUTED.** `0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression` reconstructs the published composite to within 0.05 across three run sizes. The weights are honest; the defect is in one *input* (F2). Do not touch the weights.

---

## Assumptions stated (rule 7)

1. `validsim 0.2.0`; findings are against this working tree, which is **actively changing** — F2's refutation above shows a defect fixed within hours.
2. All measurements use the **mock** backend (`VALIDSIM_BACKEND` unset). Isaac/GPU behaviour is inferred from `isaac_worker.py` source and the documented contract, **not executed** — no worker image exists.
3. Postgres and Redis were not exercised; the API probes used an injected in-memory store.
4. Severity assumes the deployment the README describes (API reachable, single-tenant, an operator may expose it). Under a laptop-only trust boundary, F1 and F2 keep their severity (they are wrong-answer defects, not exploitability defects) but F5/F6 drop to Low.
5. Test counts are volatile within a session; I quote the command alongside every number.
