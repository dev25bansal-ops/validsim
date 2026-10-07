---
tags:
  - product
  - principles
status: complete
created: 2026-09-18
area: "03 - Product"
---

# 📐 Product Principles

Six non-negotiables. Every scope decision in [[8-Week Sprint Plan]] and every trade-off in a [[Decision Log]] is checked against this list.

> [!important] Blueprint status — 2026-09-21
> These remain product principles, not a list of shipped features. Current evidence records use `APPROVE` or `BLOCK`; hash-chained audit, episode recordings, external GitHub Action distribution, and SaaS billing are future-state work ([[Data Flow]], [[Module Specs]], [[Tech Stack]]).

## The six principles

### 1. Zero-config to start
> Submit a checkpoint, get a scorecard. **No YAML hell.**

- Default task library, default robot spec, default randomization profile
- The GitHub Action example is 10 lines ([[GitHub Actions Integration]])
- Tension to manage: power users need `--randomization full --adversarial 100` flags ([[CLI Design]]) — defaults first, dials second

### 2. Statistical rigor
> Every metric has confidence intervals. **No vibes-based validation.**

- Bootstrap resampling, 95% CI on success rates and regression deltas ([[Module Specs]] M4)
- A "94% success" claim without episode count + CI is a bug, not a feature
- This principle is why an insurer can trust the scorecard where a lab spreadsheet cannot ([[Pain Quantified]])

### 3. Actionable failures
> Don't just say "failed." **Show the video, classify the failure, suggest the fix.**

- Planned: every episode records video, joint states, forces and contacts (HDF5/MP4); current runs persist structured episode-result JSON
- Failure taxonomy = rule-based + LLM classification into the 12 adversarial categories
- The blocked-deploy path must end in a reproducible artifact, not a stack trace ([[Core User Flows]] failure paths)

### 4. Deployment gating
> The scorecard isn't a report; **it's a decision.** Approve or block.

- `validsim gate --run-id vrun-1a2b3c4d --threshold 85` exits non-zero on block ([[CLI Design]])
- Fleet Deployment Gate is an architectural layer, not a feature ([[Solution Architecture]] L5)
- Product-market signal: teams that adopt the gate become sticky → NRR >120% target ([[Unit Economics]])

### 5. Compliance-ready
> Every output is formatted for ISO/insurer consumption.

- Scorecard schema designed against ISO 10218 / ISO 13482 from day one, even though ISO report *generation* is post-MVP ([[MVP Non-Goals]], [[Compliance]])
- Planned immutable, timestamped, hash-chained audit trail underneath every run; current history is not immutable ([[Data Flow]])

### 6. Developer-native
> CLI, API, GitHub Actions. **No enterprise sales motion required to start.**

- Free/Developer tier: $0/mo, 10 runs ([[Pricing Tiers]])
- Open-source CLI + Actions plugin (MIT) as growth engine ([[Go-to-Market]] Phase 2, [[IP Strategy]])
- Bottom-up adoption pulls enterprise in: "lead with developer adoption; enterprise follows" ([[Risk Register]] #5 mitigation)

## Principle conflicts & resolutions

| Conflict | Resolution |
|---|---|
| Zero-config vs. statistical rigor (more knobs = better science) | Rigorous defaults (5,000 episodes, full randomization) so the *default* is already sound |
| Deployment gating vs. false confidence | Current engine blocks insufficient evidence as `BLOCK`; a future `INCONCLUSIVE` state remains design intent |
| Developer-native vs. compliance-heavy buyers | Engineer consumes dashboard; compliance consumes PDF export — same data, two surfaces ([[User Personas]]) |

Links: [[Product Vision]] · [[Scorecard UX]] · [[MVP Scope]] · [[Founding Team]] · [[Home]]
