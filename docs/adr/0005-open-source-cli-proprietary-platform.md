# ADR 0005: Open-source CLI + Actions, proprietary platform (licensing split)

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

ValidSim's growth model is bottom-up: developers adopt the free tool, then teams
and enterprises buy the hosted platform ([[Product Principles]] #6,
[[Go-to-Market]] Phase 2, [[Pricing Tiers]] — Free/Dev $0/mo, 10 runs). That
creates a licensing tension the codebase must resolve explicitly:

- To be a growth engine, the **entry points** (CLI, GitHub Actions plugin) must be
  freely inspectable, installable, and forkable — a 500+ star target "depends on"
  the open-source surface ([[IP Strategy]], [[Go-to-Market]]).
- To be a company, the **engines** (simulation pipeline, adversarial scenario
  generation, failure taxonomy, scorecard calibration) and the **server-side
  platform** (API, dashboard) must stay proprietary and patentable
  ([[Moat]], [[IP Strategy]]).

The founding IP strategy states the allocation rule plainly (§18.2):
**"open the entry points, trade-secret the engines, patent the methods that
define the category. The MIT CLI is marketing spend; the scenario generator is
the company."** This ADR records how that rule maps onto the actual repository
and build today.

## Decision

Adopt a **two-license split along the client/server boundary**:

| Surface | License | Where it lives |
|---|---|---|
| CLI (`validsim/cli.py`) + GitHub Actions plugin (`actions/`) | **MIT** (Phase 2) | open-source |
| API, dashboard, engine/scenario/scorecard servers | **Proprietary / trade secret** | server-side only |

The current repository is a single MVP monorepo shipping the whole `validsim`
package, so it is **published as `Proprietary` today**. `pyproject.toml` carries
this explicitly:

```toml
# Proprietary today. NOTE: the CLI (validsim/cli.py) is slated to be
# open-sourced under the MIT licence in Phase 2.
license = { text = "Proprietary" }
```

The MIT CLI is a **Phase-2 carve-out**, not an unshipped afterthought: the plan is
to extract `validsim/cli.py` (and the thin Actions plugin) into a separate
MIT-licensed repository once the provisional patents are on file. The Actions
plugin is intentionally a **thin client over the proprietary API** — it submits
checkpoints and reads scorecards, so open-sourcing it discloses no engine logic.

Boundary rules that make the split hold:

- **Open source = anything a customer runs locally to *talk to* ValidSim.** The
  CLI, the Actions YAML, and the client code are marketing spend and trust
  signals; releasing them is deliberate and carries no patent gate
  ([[IP Strategy]] publication table: "MIT CLI release — no gate — intentional").
- **Proprietary = anything that *decides* a score.** Scoring weights
  ([[ADR 0002]]), the scenario generator, the failure taxonomy, and calibration
  data stay server-side; customers never receive this code.
- **Publish the format, keep the recipe.** The scorecard *schema* is published
  (so insurers can adopt it as a de-facto standard) while the scoring weights and
  calibration behind it remain secret ([[IP Strategy]] "secret → standard").
- **Sequence: file → publish.** Provisional patents on the pipeline and
  adversarial methodology must be on file before any public benchmark, blog post,
  or conference talk reveals mechanics; the CLI carve-out is the one exception and
  is intentionally ungated ([[IP Strategy]], [[Decision Log]]).
- **Contributions are assigned.** Open-source contributions to ValidSim repos
  require a CLA assigning rights to the corporation; founder IP is assigned via
  PIIAAs at incorporation ([[IP Strategy]] chain of title).

## Consequences

**Positive**

- The open-source CLI/Actions surface is a credible, inspectable growth engine
  and a developer-native on-ramp with no enterprise sales motion
  ([[Product Principles]] #6).
- The moat stays intact: engines, weights, and taxonomy remain trade-secret /
  patent-pending, so cloning the public client yields no competitive capability.
- One license line in `pyproject.toml` plus a documented carve-out keeps the
  current monorepo unambiguously proprietary while the Phase-2 split is pending —
  no accidental MIT grant today.
- Publishing the scorecard *format* (not the scoring) positions ValidSim to set
  the compliance evidence standard insurers adopt ([[Compliance]], [[Moat]]).

**Negative / trade-offs**

- A single MVP monorepo can't yet be MIT-licensed as a whole; the clean split
  requires a Phase-2 repo extraction of the CLI/Actions, which adds packaging and
  CI work and risks the two repos drifting.
- The client/server boundary must be actively policed: any engine logic that leaks
  into the CLI (e.g. computing a composite locally) would undermine both the moat
  and the patent position. The current CLI reads a *stored* scorecard and gates on
  it — it does not re-derive scoring — which is the invariant to preserve.
- "Proprietary today" is a provisional state; the eventual MIT carve-out is a
  commitment that must not be made before provisionals are filed (publication
  forfeits foreign patent rights — [[IP Strategy]] warning).
- CLA/PIIAA overhead is real process for a two-person team, but is cheap insurance
  for the assignment chain that makes the split enforceable.

## References

- `pyproject.toml` (license declaration + Phase-2 note) · `validsim/cli.py` ·
  `actions/` · `examples/robot-validation.yml`.
- [[IP Strategy]] §18.2 · [[Go-to-Market]] Phase 2 · [[Pricing Tiers]] ·
  [[Product Principles]] #6 · [[Moat]] · [[Compliance]].
- [[ADR 0002]] (weights kept server-side) · [[ADR 0004]] (scorecard format vs.
  recipe).
