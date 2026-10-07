# ADR 0002: Composite scorecard as the deploy gate

- **Status:** Accepted — the gate half is **partially superseded by [[ADR 0006]]**
  (how `validsim gate` derives its answer). The weights stand; the **denominator
  rule was amended 2026-09-29** — an unmeasured component now abstains instead of
  contributing a fabricated 100 (see *Unmeasured components abstain* below).
- **Date:** 2026-09-18 (amended 2026-09-29)

## Context

ValidSim's core value proposition is that a validation run ends in a *decision*,
not a report ([[Product Principles]] #4: "The scorecard isn't a report; it's a
decision. Approve or block."). A CI pipeline needs one number it can compare
against one threshold to exit 0 or 1 — not a dashboard a human must interpret.

The engine, however, measures four distinct things from a run:

- **success** — fraction of episodes that completed the task;
- **safety** — collisions, force-limit breaches, human-proximity violations
  ([[Module Specs]] M4);
- **robustness** — consistency of success across domain-randomization groups;
- **regression** — whether the checkpoint degraded versus its baseline.

These can disagree (a model can be safe but regressing; successful but brittle
across conditions). The question is how to collapse them into a single gate that
is defensible to engineers *and* to the insurers/regulators who are Tier-4 buyers
([[Buyer Tiers]], [[Compliance]]).

## Decision

The scorecard computes one **composite score** as a weighted mean of the four
components, each already normalized to 0–100, and gates on it against a
configurable threshold (default **85.0**):

```
composite = Σ(wᵢ·vᵢ) / Σ(wᵢ)     over the components that were MEASURED
decision  = APPROVE if composite >= threshold else BLOCK
```

Implemented in `validsim/engine/scorecard.py` as the constants
`_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1`
(weights must sum to 1.0). Component definitions:

- **success** = `evaluation.success_rate × 100`.
- **safety** = the safety engine's weighted 0–100 score.
- **robustness** = `100 − 200·pstdev(per-group success rates)`, clamped to
  0–100. A 0.5 std-dev spread zeroes the component.
- **regression** = `100 − 25 × (count of significant regressions)`, floored at
  0. "Significant" is decided by the bootstrap-CI regression engine, so noise
  never trips the gate ([[Product Principles]] #2).

### Unmeasured components abstain (2026-09-29 amendment)

**Superseded:** the original formula was a flat sum in which an *unmeasured*
component silently contributed a perfect score.

The denominator is now the summed weight of the components that were actually
measured. A component that could not be measured **abstains** — it contributes
neither fabricated credit nor a fabricated penalty:

| Component | Unmeasured when | Prior (defective) |
|---|---|---|
| robustness | fewer than 2 randomization groups | scored 100 |
| regression | no baseline supplied | scored 100 |

**Why this was a defect, not a tuning choice.** `run_validation` applies a
single `task.randomization` level to every episode, so *every production run*
lands all episodes in one randomization group. Robustness was therefore
structurally 100 on essentially all real runs, and regression was 100 whenever
no baseline was passed — **30 of 100 composite points, unconditional**. The
measured counterexample: a run where **every episode failed** (`success_rate =
0.0`) scored 60.0 and was **APPROVED at threshold 60**. A gate that certifies
"this model never completed the task" is worse than no gate, because it converts
a total failure into a signed-off approval.

Abstention is preferred over zeroing an unmeasured component. Zeroing would
mean a routine single-group run is permanently penalised 0.2 of its weight and
could never reach a high threshold through no fault of the model; excluding the
term from the denominator keeps the score interpretable ("the average of what
we actually measured") while removing the fabricated credit.

**Measured consequences** (safety 100, no baseline, single group):

| Scenario | Before | After | Verdict |
|---|---|---|---|
| 0% success, every episode fails | 60.0 | **42.86** | BLOCK at any threshold |
| 50% success | 71.4 | 71.4 | BLOCK |
| 90% success | 96.0 | 94.29 | APPROVE ≥ 85 |
| 100% success | 100.0 | 100.0 | APPROVE |

The ordering is monotonic and a flawless run still reaches exactly 100.0, so
the weights continue to sum to 1.0 over the measured set.

The scorecard also carries the evidence explicitly — `robustness_measured`,
`regression_baseline_available` and `randomization_group_count` — so a consumer
can always tell an absent measurement from a perfect one, and a separate
evidence-sufficiency gate blocks a run that achieved no task success whatever
the threshold.

The composite, its components, the 95% bootstrap CI on success, and the
`APPROVE`/`BLOCK` verdict are persisted together in the `Scorecard`. The CLI
`validsim gate` re-reads the stored composite/threshold and **exits non-zero on
BLOCK**, making the same artifact the CI gate
([[GitHub Actions Integration]], `actions/scorecard`).
*Superseded by [[ADR 0006]]: the gate re-reads the stored **verdict** from the
durable store, and the composite only as a second condition.*

### Why these weights

The ordering **success > safety > robustness > regression** encodes the product's
risk priorities, and the weights are deliberately simple, round, and sum to 1.0:

- **success (0.4)** leads because task completion is the buyer's primary
  outcome — a model that can't do the job fails regardless of everything else.
- **safety (0.3)** is the second-heaviest because a physical robot that succeeds
  by harming a person or itself is unusable; safety must be able to sink a
  high-success run. It is intentionally *not* weighted above success to keep the
  single scalar interpretable; hard safety violations are surfaced separately in
  the failure taxonomy rather than being allowed to dominate every score.
- **robustness (0.2)** is the sim-to-real hedge: a model that only succeeds under
  one lighting/physics condition will fail in the field, so cross-condition
  consistency earns meaningful but secondary weight.
- **regression (0.1)** is the smallest because it is a *relative* signal against
  a baseline, not an absolute quality measure; it nudges the gate to catch silent
  degradation without letting a single regression veto an otherwise strong
  checkpoint.

## Consequences

**Positive**

- One number, one threshold, one binary verdict — trivially consumable by CI and
  by non-technical approvers, satisfying the deployment-gating principle.
- Every component is still stored alongside the composite, so a `BLOCK` explains
  itself (which lever failed) instead of being an opaque red X.
- The weights are a single named constant set, easy to reason about, test, and
  (if evidence demands) recalibrate.
- Gating on the *composite* rather than any single metric prevents gaming one
  dimension at the expense of another.

**Negative / trade-offs**

- A weighted sum can mask a catastrophic single-axis failure (e.g. a low-but-
  nonzero safety score diluted by high success). Mitigation: hard safety
  violations and the failure taxonomy remain first-class on the scorecard and in
  exports; the **evidence-sufficiency gate** (added 2026-09-29) now also blocks
  outright any run with no positive task success, which is the single-axis
  failure that actually occurred.
- Abstention is not free. A run whose robustness is unmeasured is scored on a
  0.7-weight denominator rather than 1.0, so composite values are **not
  comparable across runs** with different evidence coverage. This is deliberate —
  a comparable number that silently includes fabricated credit is worse than an
  honest one that is scoped — but a consumer must read
  `robustness_measured` / `regression_baseline_available` alongside the score
  rather than treating it as a bare number. Long-term, varying randomization
  levels across episodes would make robustness measurable on every run and
  remove the caveat entirely; that is a change to `sim/runner.py`, not here.
- The weights are a judgment call, not a learned model. They are defensible and
  documented but arguable; changing them changes the meaning of every historical
  score, so any revision must be superseded via a new ADR and versioned in the
  scorecard schema. The same applies to the 2026-09-29 denominator amendment:
  it moves historical composites (e.g. 96.0 → 94.29 for a strong run) and is
  versioned here for that reason.
- A single global default threshold (85.0) won't fit every task's risk appetite;
  it is therefore overridable per run (`--threshold`, the API, and the gate
  command), at the cost of teams needing to agree on their own bar.

## References

- `validsim/engine/scorecard.py` (weights, composite, decision) ·
  `validsim/engine/safety.py` · `validsim/engine/regression.py` ·
  `validsim/cli.py` (`gate`).
- [[Product Principles]] #2 (statistical rigor) · #4 (deployment gating).
- [[Solution Architecture]] L3/L5 · [[Module Specs]] M4.
