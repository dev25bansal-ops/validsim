# ADR 0002: Composite scorecard as the deploy gate

- **Status:** Accepted
- **Date:** 2026-09-18

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

The scorecard computes one **composite score** as a fixed weighted sum of the
four components, each already normalized to 0–100, and gates on it against a
configurable threshold (default **85.0**):

```
composite = 0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression
decision  = APPROVE if composite >= threshold else BLOCK
```

Implemented in `validsim/engine/scorecard.py` as the constants
`_W_SUCCESS, _W_SAFETY, _W_ROBUSTNESS, _W_REGRESSION = 0.4, 0.3, 0.2, 0.1`
(weights must sum to 1.0). Component definitions:

- **success** = `evaluation.success_rate × 100`.
- **safety** = the safety engine's weighted 0–100 score.
- **robustness** = `100 − 200·pstdev(per-group success rates)`, clamped to
  0–100; a single randomization group scores 100 (no observed cross-condition
  variance). A 0.5 std-dev spread zeroes the component.
- **regression** = `100 − 25 × (count of significant regressions)`, floored at
  0; 100 when no baseline comparison is present. "Significant" is decided by the
  bootstrap-CI regression engine, so noise never trips the gate
  ([[Product Principles]] #2).

The composite, its components, the 95% bootstrap CI on success, and the
`APPROVE`/`BLOCK` verdict are persisted together in the `Scorecard`. The CLI
`validsim gate` re-reads the stored composite/threshold and **exits non-zero on
BLOCK**, making the same artifact the CI gate
([[GitHub Actions Integration]], `actions/scorecard`).

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
  exports; a future ADR may add absolute per-component floors if a real incident
  demands it.
- The weights are a judgment call, not a learned model. They are defensible and
  documented but arguable; changing them changes the meaning of every historical
  score, so any revision must be superseded via a new ADR and versioned in the
  scorecard schema.
- A single global default threshold (85.0) won't fit every task's risk appetite;
  it is therefore overridable per run (`--threshold`, the API, and the gate
  command), at the cost of teams needing to agree on their own bar.

## References

- `validsim/engine/scorecard.py` (weights, composite, decision) ·
  `validsim/engine/safety.py` · `validsim/engine/regression.py` ·
  `validsim/cli.py` (`gate`).
- [[Product Principles]] #2 (statistical rigor) · #4 (deployment gating).
- [[Solution Architecture]] L3/L5 · [[Module Specs]] M4.
