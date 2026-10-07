# CAT12 — Compliance & Claims Integrity Audit (c4-compliance)

**Agent:** c4-compliance · **Date:** 2026-09-26 · **Scope:** compliance/security claims, evidence integrity, deploy-gate semantics
**Target:** ValidSim `d:\SIM-TO-REAL` @ `c2f7ff0`-era working tree (see Assumption A1)
**Mode:** read-only. No production file was edited. This report is the only file created.

---

## Findings table

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| CAT12-01 | 30 of 100 composite-score points are unconditional; a reported threshold of 85 overstates the real bar | `validsim/engine/scorecard.py:53-64`, `:69-72`, `:34` | **Critical** | VERIFIED | 2–3 d | `python -c "from validsim.engine.scorecard import _robustness_score,_regression_component; ..."` (§1) |
| CAT12-02 | `checkpoint_sha256` is accepted, never read, never persisted — chain-of-custody gap | `validsim/config.py:154`, `validsim/engine/scorecard.py:96-111` | **High** | VERIFIED | 1 d | `python -c "...dataclasses.fields(Scorecard)..."` (§2) |
| CAT12-03 | `gate`'s second conjunct contradicts its own docstring; it is a *stricter* re-gate, not a tautology | `validsim/cli.py:469-471` vs `:483-485` | **Medium** | VERIFIED | 1–2 h | `python -c "...brute-force over decision grid..."` (§3) |
| CAT12-04 | Evidence-sufficiency guard cannot detect an under-delivered run; `task.episodes=1` is a valid APPROVE | `validsim/engine/scorecard.py:195-198` | **Medium** | VERIFIED | 1–2 h | `python -c "...build_scorecard with episodes=1..."` (§4) |
| CAT12-05 | README marks webhooks/email `[x]` complete; both dispatchers are unreachable from any entrypoint | `README.md:13,154,164` | **Medium** | VERIFIED | 1 d | `Select-String -Path validsim\*.py,validsim\**\*.py -Pattern 'WebhookDispatcher\|EmailNotifier'` (§5) |
| CAT12-06 | `SECURITY.md` asserts tamper-detectability that no code provides | `SECURITY.md:276-279` | **Medium** | VERIFIED | <1 h | `Select-String -Path docs\runbook.md -Pattern 'append-only guarantee'` (§6) |

**Most important finding: CAT12-01.** 30 of every 100 composite points are handed out without measuring anything.

> [!warning] Concurrent edits — re-verified at handover (2026-09-26)
> Other agents are editing this repository **live**. `engine/scorecard.py` and `cli.py` changed under me between my
> first and final verification passes, so line numbers moved (`gate` clause 446→484) and part of CAT12-04 was
> **partially fixed by another agent** (a `success_count > 0` clause was added at `scorecard.py:195-198`).
> **Every finding below was re-verified against the tree immediately before handover.** Line numbers are current as
> of the last command in this report; re-verify if `engine/` changes again.

---

## CAT12-01 — 30 of 100 composite points are unconditional

**Subject: shipped code.** `validsim/engine/scorecard.py` is unmodified (`git status --porcelain` empty).
**Assumption A1:** the working tree changed during this audit — a test file I had relied on was reverted. All findings below were re-verified against the *current* tree; see "False leads."

### Description

`build_scorecard` (`scorecard.py:154-163`) computes:

```
composite = 0.4*success% + 0.3*safety + 0.2*robustness + 0.1*regression_component
```

Two of those four terms are **structural constants, not measurements**:

- **robustness (weight 0.2 → 20 points).** `_robustness_score` (`scorecard.py:53-66`) groups episodes by
  `e.randomization_level` and returns `100.0` when `len(rates) < 2` (`:63-64`). But `run_validation`
  (`sim/runner.py:172-186`) stamps **both** the nominal loop (`:175`) and the adversarial loop (`:182`) with the
  single value `task.randomization`. **The production runner can only ever emit one group.**
- **regression_component (weight 0.1 → 10 points).** `_regression_component(None)` returns `100.0`
  (`scorecard.py:69-74`). `baseline_run_id` is optional and defaults to `None` (`config.py:159-161`), so a run with
  no baseline scores a perfect 10.

**Business impact:** every scorecard reports `threshold=85` while the bar that actually has to be cleared sits on
the remaining 70 points. Two of four weights describe no measurement, which makes the remaining weights *look*
stricter than they are. This is the number the product is sold on and the number CI gates on.

### Reproduction (executed; writes nothing to disk)

```
$ python -c "
from validsim.engine.scorecard import _regression_component, _robustness_score, _W_ROBUSTNESS, _W_REGRESSION
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.sim import create_backend
from validsim.sim.runner import run_validation
t = TaskConfig(task_id='t', robot=RobotSpec(name='r'), environment=EnvironmentSpec(name='e'),
               episodes=200, adversarial_count=20, randomization='full')
eps = run_validation(t, create_backend(), [], seed=1)
print('levels emitted by runner:', {e.randomization_level for e in eps})
print('_robustness_score(...) =', _robustness_score(eps))
print('_regression_component(None) =', _regression_component(None))
print('unconditional points =', _W_ROBUSTNESS*100 + _W_REGRESSION*100)
"
levels emitted by runner: {'full'}
_robustness_score(...) = 100.0
_regression_component(None) = 100.0
unconditional points = 30.0
```

### Control (required by directive rule 6)

The formula must be shown *correct* so the repro is not a fixture artifact. Identical success pattern, only the
number of `randomization_level` groups differs:

```
$ python -c "
from validsim.engine.scorecard import _robustness_score
from validsim.sim.runner import EpisodeResult
def ep(s,l): return EpisodeResult(episode_id='e',task_id='t',seed=s,success=s,randomization_level=l)
mix  = [ep(True,'none')]*10 + [ep(True,'partial')]*5 + [ep(False,'partial')]*5   # 15/20, 2 groups
solo = [ep(True,'full')]*15 + [ep(False,'full')]*5                              # 15/20, 1 group
print('2-level mix 15/20 =', _robustness_score(mix))
print('1-level     15/20 =', _robustness_score(solo))
print('empty            =', _robustness_score([]))
"
2-level mix 15/20 = 50.0     <- formula responds correctly
1-level     15/20 = 100.0    <- identical data, 0 groups -> constant
empty            = 100.0
```

**Expected vs actual:** with a real condition mix the formula yields 50.0 as designed. The defect is **not** in
`_robustness_score` — it is that the production runner never produces a mix. The remedy is therefore much smaller
than "redesign a scoring component": emit a real condition mix (e.g. a per-episode randomization ladder, or nominal
at `none` and adversarial at the configured level).

### Dependencies

`adversarial_count` defaults to `0` (`config.py:138`), so the shipped default also runs **zero** adversarial
episodes. Config default is a separate concern from the engine root cause.

### Recommended fix

1. Make the runner emit ≥2 `randomization_level` values (or make robustness group by something that actually varies,
   e.g. segment). **Caveat: per-episode randomization changes simulation semantics — that is the engine owner's call,
   not a mechanical fix.**
2. Make the no-baseline case explicit rather than silently perfect: either report regression as "not measured" or
   state it in the scorecard.
3. Consider publishing a `conditional_composite` (measured components only) so the headline number is one a reader
   can act on. A disclaimer in a downstream report is weaker than a correctly-labelled field.
4. Do **not** recompute historical scorecards; the fix changes inputs, not the formula.

### Test strategy (describe only — not written, per directive)

- A test that drives the **real** production path (`run_validation` + `create_backend`) and asserts
  `len({e.randomization_level for e in eps}) >= 2`. This is the assertion that would have caught it.
- A control pair: identical episodes, 1 group vs 2 groups, asserting the scores differ.
- A regression test asserting `composite` changes when adversarial outcomes change and nominal count is held fixed.

---

## CAT12-02 — `checkpoint_sha256` is never read and never persisted

**Subject: shipped code.** **Severity: High.**

### Description

`ValidationRequest.checkpoint_sha256` (`config.py:154`) is accepted by the API. Nothing reads it, and it cannot
reach a persisted artifact:

- Read-sites across all of `validsim/`: only the docstring (`config.py:146`) and the field declaration
  (`config.py:154`). **Zero consumers.**
- `Scorecard` (`scorecard.py:96-111`) has no such field, so even a correctly-computed digest would be discarded.
- `run_and_score` accepts `baseline_run_id` but has **no** `checkpoint_sha256` parameter (verified by introspection).

Validation is length-only: `min_length=64, max_length=64` with no hex pattern, while `docs/api-reference.md:192`
documents the constraint as "exactly 64 chars (hex digest)".

### Reproduction

```
$ Select-String -Path validsim\*.py,validsim\**\*.py -Pattern 'checkpoint_sha256'
config.py:146: checkpoint_sha256: Optional SHA-256 digest of the checkpoint artifact.
config.py:154: checkpoint_sha256: str | None = Field(

$ python -c "
from validsim.engine.scorecard import Scorecard
import dataclasses, inspect
from validsim.engine.pipeline import run_and_score
print('has field:', 'checkpoint_sha256' in [x.name for x in dataclasses.fields(Scorecard)])
print('run_and_score params:', list(inspect.signature(run_and_score).parameters))
"
has field: False
run_and_score params: ['task','checkpoint_id','store','backend','run_id','threshold','baseline_run_id','run_validation','create_backend']
```

**Control:** the same introspection pattern positively confirms the mechanism works — `baseline_run_id` **is** a
parameter of `run_and_score`, so the absence of `checkpoint_sha256` is a real gap and not an introspection failure.

### Expected vs actual

**Expected:** a supplied digest is validated, bound to the run, and persisted so an auditor can prove which
checkpoint bytes were scored. **Actual:** it is accepted, length-checked, and dropped at the boundary.

### Business impact

No artifact ValidSim produces can answer "which checkpoint bytes produced this approval?" This is the
chain-of-custody gap that every downstream compliance feature (signed scorecards, evidence bundle, audit ledger)
depends on. It is also a **false-assurance** risk: a caller reading the API reference concludes their digest was
verified.

### Recommended fix

Compute the digest in the pipeline from the actual checkpoint bytes (do not trust the caller's value), add it as a
real `Scorecard` field, persist it, and carry it into every export. Separately, either enforce the documented hex
pattern or correct the docs — noting that enforcing it is a **behaviour change to a documented API surface** and
belongs in its own change.

### Test strategy

- Assert a non-hex 64-char string is rejected (or accepted, per whichever decision is taken) — a test that pins the
  current permissive behaviour should be reviewed, since it documents a gap.
- Round-trip: submit with a digest → read back the stored scorecard → digest is present and matches recomputation.

---

## CAT12-03 — `gate`'s second conjunct contradicts its docstring (and is *not* a tautology)

**Subject: shipped code.** `validsim/cli.py` unmodified. **Severity: Medium.**

### Description

The `gate` docstring (`cli.py:469-471`) states:

> "The verdict the engine recorded is authoritative… so this command **never recomputes it from the composite**,
> and only the exact string `APPROVE` approves."

The code (`cli.py:483-485`) does the opposite:

```python
approved = (
    card.deploy_decision == _APPROVE
    and float(card.composite_score) >= effective
)
```

**Correction to an earlier claim of mine:** I previously told the team-lead this was "the surface that decides
shipping" and "doubly inflated." Both were overstatements. A brute-force check over the decision grid shows the
conjunct is **not** a tautology — it diverges in 51 of 2,424 combinations — but the divergence is **only ever in
the blocking direction**, and only when `--threshold` raises the bar above the stored one.

### Reproduction

```
$ python -c "
thr=85.0; disagree=0; checked=0
for succ in range(101):
  for safety in (0,50,100):
    comp=0.4*succ+0.3*safety+30.0
    for sufficient in (True,False):
      eng = sufficient and comp>=thr
      for flag in (-1.0,0.0,90.0,100.0):
        eff = max(thr,flag) if flag>=0 else thr
        gate = eng and (comp>=eff)
        checked+=1
        if eng!=gate: disagree+=1
print('combinations checked:',checked); print('disagreements:',disagree)
"
combinations checked: 2424
disagreements: 51

# all 51 divergences: flag > stored_threshold, sufficient_evidence True
# e.g. composite=85.2, --threshold 90  ->  engine APPROVE, gate BLOCK
```

**Control:** with `flag <= stored_threshold` the disagreements are **zero** — the conjunct is a no-op in the default
configuration. Only `--threshold 90` (or 100) against a stored threshold of 85 produces divergence. So the default
path is unaffected and the defect is a documentation contradiction plus surprising behaviour under a documented flag,
not a live bypass.

### Expected vs actual

**Expected:** the command does one thing — read the authoritative stored verdict. **Actual:** it re-derives approval
from the composite, so a run the engine approved can be blocked at the gate when a caller passes a higher
`--threshold`. The docstring's *reasoning* is the better design; the code should match it.

### Business impact

Low direct risk (fail-closed direction only). Real cost is trust: the deploy-gate command's docstring misdescribes
its own behaviour on the exact path that decides whether hardware ships.

### Recommended fix

Drop the second conjunct and keep `card.deploy_decision == "APPROVE"` (the docstring's stated intent), or correct the
docstring to describe the re-check. **This changes a shipped exit-code contract, so it is the CLI/reliability
owner's call.** If the conjunct is kept, it should be documented as "a second, stricter bar" rather than denied.

### Test strategy

- A test asserting `gate --threshold <above stored>` on an APPROVEing run with a composite below that flag exits 1
  (pins current behaviour) — and a decision on whether that is the intended contract.
- A doc-consistency test asserting the docstring's claims match the code path, to prevent recurrence.

---

## CAT12-04 — Evidence-sufficiency guard cannot detect an under-delivered run

**Subject: shipped code.** `validsim/engine/scorecard.py:195-198`. **Severity: Medium.**
**Note: partially mitigated by a concurrent agent edit** — see below.

### Description

```python
sufficient_evidence = (
    evaluation.total_episodes >= task.episodes > 0
    and evaluation.success_count > 0
)
```

**First clause — dead by construction.** The comment describes protecting against "a worker returning 1 of 50
episodes". But `run_validation` returns exactly `task.episodes + len(scenarios)` episodes
(`sim/runner.py:170,186`) and `evaluate` counts what it receives, so `total_episodes >= task.episodes` is **true by
construction** for every normally-produced run. It can only fire for a hand-constructed or corrupt
`EvaluationResult`.

**Second clause — added concurrently by another agent; it closes only part of the gap.** A `success_count > 0`
clause now blocks a 0%-success run. That is a real improvement and it is acknowledged here. It does **not** address
the evidence-*quantity* problem: a run with `success_count=1` out of 1 requested episode still satisfies both
clauses. **Corroboration:** the new comment at `scorecard.py:186-194` independently reasons about CAT12-01 —
*"That is a flat 30.0 composite points which measures nothing about task success"* — so another agent reached the
same 30.0 figure, which is a useful cross-check on CAT12-01's arithmetic.

### Reproduction

```
$ python -c "
from validsim.engine.scorecard import build_scorecard
from validsim.engine.evaluation import evaluate
from validsim.engine.safety import compute_safety
from validsim.config import TaskConfig, RobotSpec, EnvironmentSpec
from validsim.sim import create_backend
from validsim.sim.runner import run_validation
t = TaskConfig(task_id='t', robot=RobotSpec(name='r'), environment=EnvironmentSpec(name='e'),
               episodes=1, adversarial_count=0, randomization='none')
eps = run_validation(t, create_backend(), [], seed=7)
ev = evaluate(eps)
card = build_scorecard('vrun-1', 'ck', t, ev, compute_safety(eps), eps)
print('delivered:', len(eps), '| success_count:', ev.success_count, '| composite:', card.composite_score, '| decision:', card.deploy_decision)
"
delivered: 1 | success_count: 1 | composite: 100.0 | decision: APPROVE
```

**Control:** the guard is neither absent nor accidentally inert — on a normal multi-episode run both clauses
evaluate true, which is the correct path. The defect is that the **quantity** predicate can never be false in
production, and the concurrently-added clause only guards the separate zero-success case.

### Expected vs actual

**Expected:** the engine verifies it received the evidence volume it asked for, at the point the count is known, and
a scorecard resting on one episode is not certifiable. **Actual:** the volume predicate is unfalsifiable in
production, and a one-episode run is a valid `APPROVE` at composite 100.0.

### Business impact

The one guard a deploy gate has against "scored on almost no evidence" cannot trigger on the normal path, and the
case its own comment describes is reachable by the user setting `episodes=1`. A single-episode APPROVE is a
legitimate-looking artifact that would pass a customer's evidence review unless someone counts the episodes.

### Recommended fix

Verify the delivered count against the requested count in the pipeline/worker boundary (where the count is known) and
fail loudly on a shortfall, rather than re-deriving it from a total the pipeline constructs. Consider a floor on
`episodes` below which a scorecard is marked low-confidence regardless of composite.

### Test strategy

- A worker that returns fewer episodes than requested must produce a BLOCK and an explicit reason, not a high score.
- A run with `task.episodes=1` should be marked insufficient (or require an explicit override) — pin whichever
  policy is chosen.

---

## CAT12-05 — README marks webhooks/email complete; dispatchers are unreachable

**Subject: shipped library code + documentation.** **Severity: Medium (documentation/commercial).**

### Description

`README.md:13,154,164` mark Slack webhooks and an email notification channel as delivered (`[x]`), including
specific behaviours (Block Kit payloads, HMAC signing, exponential-backoff retry, multipart HTML+text bodies).

`WebhookDispatcher` and `EmailNotifier` exist, but **nothing outside `validsim/notify/` constructs them.** There is
no API route, no CLI command, and no config-driven auto-wiring. `.env.example:135-147` supplies a transport toggle
(`VALIDSIM_WEBHOOKS_LIVE`) with no way to register a destination URL, and a six-variable SMTP block.

### Reproduction (reachability proof, per directive rule 5)

```
$ Select-String -Path validsim\*.py,validsim\**\*.py -Pattern 'WebhookDispatcher|EmailNotifier'
validsim\notify\__init__.py:7,13,22,24
validsim\notify\dispatcher.py:4,40,167
validsim\notify\email.py:4,6,38,223
   -> every hit is inside validsim/notify/ itself

$ Select-String -Path validsim\cli.py -Pattern 'notify|slack|webhook|smtp|email' -CaseSensitive:$false
   (no output)

$ Select-String -Path validsim\api\main.py -Pattern 'notify|webhook' -CaseSensitive:$false
   (no output)
```

**Control:** the same search pattern positively finds the classes in their own module, so a zero result from
`cli.py`/`main.py` reflects absence, not a broken search.

**Status: the capability is unreachable from any entrypoint.** The library code is real and tested; the *product
surface* does not exist.

### Expected vs actual

**Expected:** a documented `[x]` means a user can reach the feature. **Actual:** a user who reads the README cannot
enable webhooks or email by any supported means.

### Business impact

A buyer who reads the README and then fails to find the capability concludes the product overstates what it ships.
For an enterprise audit this is credibility damage on the exact axis (transparency) the product is selling.

### Recommended fix

Either wire a registration path (API route or config file) so the `[x]` becomes true, or relabel the README items as
library-only/proposed. **Do not delete the capability** — it is genuinely implemented at the library layer.

### Test strategy

A reachability test that asserts every `[x]`-marked README capability has either an entrypoint path or an explicit
"library-only" label. This generalises: a documentation-drift test is cheaper than discovering the drift in a
security review.

---

## CAT12-06 — `SECURITY.md` asserts tamper-detectability no code provides

**Subject: documentation.** `SECURITY.md:276-279`. **Severity: Medium.**

### Description

Under "Hardening already in place":

> "**Deterministic, auditable scoring** — seeded simulation…, and a persisted run/scorecard record in PostgreSQL
> (JSONB + indexed columns) or SQLite, giving an **audit trail that makes tampering detectable**."

This is false for the current stores. `delete()` physically removes rows on all three backends
(`store/memory.py:95`, `store/sqlite.py:235`, `store/postgres.py:441`). Postgres is append-only *on conflict*
(`ON CONFLICT (run_id) DO NOTHING`, `store/postgres.py:414`) — first-write-wins, which is not tamper-evidence: a
deletion is undetectable, and a restore from backup is indistinguishable from tampering.

The repository's own runbook states this correctly, so the accurate documentation exists and is routed elsewhere.

### Reproduction

```
$ Select-String -Path validsim\store\*.py -Pattern 'def delete'
store/memory.py:95    store/sqlite.py:235    store/postgres.py:441

$ Select-String -Path docs\runbook.md -Pattern 'append-only guarantee|does not implement an audit log'
docs\runbook.md:420-431   # states the guarantee is a write-path property, NOT immutability
```

**Control:** the runbook text is the control — a second document describing the same subsystem reaches the opposite
(accurate) conclusion, which confirms the `SECURITY.md` wording is the outlier rather than a misreading.

### Expected vs actual

**Expected:** hardening documentation lists controls that are on by default and verifiable. **Actual:** it lists a
guarantee that a customer security reviewer can falsify with one API call (`DELETE /api/v1/validations/{run_id}`)
or one `validsim delete`.

### Business impact

This is the single highest-leverage line in the repository for enterprise credibility: it is the first thing a
buyer's security reviewer reads, and it makes a claim that fails on contact.

### Recommended fix

Rewrite the bullet to state what is actually true: deterministic and seeded scoring with a persisted record;
first-write-wins on Postgres; **operator deletion is possible and is not tamper-evident.** If a real immutable
audit trail is built later, update this bullet then.

### Test strategy

A docs-assertion test that greps `SECURITY.md` for integrity claims and requires each to name the enforcing
mechanism and backend — so a future claim cannot be added without a code reference.

---

## False leads (ruled out, with reasons)

1. **"CI is red because of a failing robustness test."** I asserted this to the team-lead twice; it was **false**.
   The RED test file was uncommitted working-tree content (86 insertions, absent from `HEAD`) and has since been
   reverted. `git status --porcelain` on `tests/test_scorecard.py` is now empty and the class
   `TestRobustnessIsNotConstantInProduction` no longer exists. The **defect is real** (re-verified independently,
   §1) but the build is green. Lesson recorded: I stated a consequence I had not tested.

2. **"The `gate` re-check is a tautology."** A teammate's model claimed this and I repeated it into my own report
   before testing it. Brute force over 2,424 combinations shows **51 disagreements**. It is not a tautology — it is a
   *stricter second bar* active only when `--threshold` exceeds the stored threshold. Both my earlier claim
   ("decides shipping") and the tautology claim were wrong in opposite directions. §3 has the corrected version.

3. **"The `gate` is a live bypass / 'doubly inflated'."** Refuted: divergence is one-directional (gate can only
   BLOCK, never upgrade a BLOCK to an APPROVE) and only under `--threshold > 85`. Not a security finding.

4. **"Failure-mode classification is a trade secret worth patenting."** Refuted by reading the code:
   `failure_mode = rng.choice(FAILURE_MODES)` (`sim/runner.py:112`) is a uniform random draw over a hardcoded
   7-tuple. `compute_safety` and `evaluate` consume the already-assigned label; neither infers one. There is no
   detection methodology to protect or patent.

5. **"`checkpoint_sha256` is stored but unverified."** Refuted — it is not stored at all. Strengthened the finding
   rather than weakening it: no schema field exists.

6. **"The `adversarial_count=0` default is a config bug."** Partially true but mis-owned. The engine defect
   (CAT12-01) is sufficient on its own with adversarial episodes present; the `0` default is a compounding,
   *separate* concern about what a first-time user sees.

7. **`_regression_component` / robustness as "dead code."** Refuted — both are live and reachable; they return
   correct values for their inputs. The defect is that the inputs are degenerate in production, which is a different
   claim with a different fix.

---

## Assumptions

- **A1 — moving target.** The working tree changed during this audit (a test file was reverted; `engine/scorecard.py`
  and `validsim/cli.py` are currently unmodified per `git status --porcelain`). Every finding was re-verified
  against the tree as of 2026-09-26. Line numbers may shift if `engine/` is edited again.
- **A2 — no live database.** All verification used in-memory/SQLite-free paths and pure functions. Postgres-specific
  and Redis-specific behaviour was reasoned from source, not executed, and is marked accordingly in each finding.
- **A3 — mock backend only.** The default `VALIDSIM_BACKEND=mock` is deterministic; findings about episode counts
  and success rates are properties of that backend. The Isaac path is HTTP-contract-only in this repository, so
  CAT12-01's magnitude figures were not reproduced under real physics.
- **A4 — "impact" is judged against the documented product**, not against a hypothetical hosted platform. No
  hosted platform exists in this repository.
