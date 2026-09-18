---
tags:
  - engineering
  - ci
  - github-actions
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# 🤖 GitHub Actions Integration

The literal "GitHub Actions for robots" surface — where the one-liner ([[Company Identity]]) becomes a YAML file a developer copies in 5 minutes. Prototype ships sprint W6; MIT-licensed like the CLI ([[IP Strategy]]).

## 4.7 Reference workflow

```yaml
# .github/workflows/robot-validation.yml
name: Robot Policy Validation
on:
  push:
    paths:
      - 'models/**'
      - 'policies/**'
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Sim-to-Real Validation
        uses: validsim/validate-action@v1
        with:
          checkpoint: ./models/policy_v42.pt
          task: bin_picking
          robot: franka_panda
          episodes: 5000
          adversarial-scenarios: 100
          fail-below-score: 80
          api-key: ${{ secrets.VALIDSIM_API_KEY }}

      - name: Post Scorecard
        if: always()
        uses: validsim/scorecard-action@v1
        with:
          run-id: ${{ steps.validate.outputs.run-id }}
          comment-on-pr: true
```

## How it works

| Step | Mechanism | Backend |
|---|---|---|
| **Trigger** | `push` to `models/**` or `policies/**` — checkpoint pushes are the "commits" of robot CI | — |
| **validate-action@v1** | Submits run, polls until terminal state, fails build if composite score < `fail-below-score` | `POST /api/v1/validations` ([[API Design]]) |
| **scorecard-action@v1** | `if: always()` → posts scorecard summary as PR comment, even on failure | `GET /validations/{id}/scorecard` |
| **Secret** | `VALIDSIM_API_KEY` — same key as CLI env var | Auth ([[Tech Stack]]) |

> [!important] The PR comment is the growth loop
> Every failed robot-policy PR with a ValidSim comment advertises the product to the next engineer on the team. This is the developer-led motion in [[Go-to-Market]] Phase 2 — the Action is both feature and funnel ([[Product Principles]] #6).

## Semantics of `fail-below-score: 80`

- Composite score weights: 40% success + 30% safety + 20% robustness + 10% regression ([[Module Specs]] M4)
- Build fails **non-zero exit** → GitHub branch protection blocks merge → deployment gate enforced in CI before anything touches a fleet ([[Data Flow]] step 6)
- Threshold is per-repo; Enterprise tier gets fleet-level thresholds + approval workflows ([[Pricing Tiers]])

## Variants on the roadmap

| CI system | Status |
|---|---|
| GitHub Actions | MVP (W6 prototype) |
| GitLab CI | Year 1 — same API, `.gitlab-ci.yml` job |
| Jenkins | Year 1–2 — plugin; Tier-3 factories skew Jenkins ([[Buyer Tiers]]) |

## Demo checklist for the 1-minute video ([[YC Countdown]])

- [ ] `git push` a checkpoint → Action auto-triggers
- [ ] Cut to Slack ping: *"Score: 87.3. 2 regressions detected."* ([[Core User Flows]] Flow 1)
- [ ] PR comment with scorecard renders
- [ ] A deliberately regressed checkpoint turns the build ❌ — the gate visibly protecting a fleet

Links: [[CLI Design]] · [[API Design]] · [[Solution Architecture]] · [[Go-to-Market]] · [[Home]]
