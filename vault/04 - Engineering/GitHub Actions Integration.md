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

The literal "GitHub Actions for robots" surface — where the one-liner ([[Company Identity]]) becomes a YAML file a developer copies in 5 minutes. The repository contains local composite actions for a checkout-based MVP; a marketplace release is a future milestone ([[IP Strategy]]).

> [!important] Blueprint status — 2026-09-21
> The `validsim/validate-action@v1` and `validsim/scorecard-action@v1` references below are target references for a future published action, not available published actions as of this audit. The local action is `actions/validate`; it runs `validsim run` then `validsim gate` against a job-scoped SQLite store, using the deterministic mock backend. Keep action version/action-reference edits in this YAML example aligned with `.github/workflows/ci.yml`; the action owner owns pinning.

## 4.7 Reference workflow — target published-action form

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

For a runnable local MVP today, point a workflow at `./actions/validate` and `./actions/scorecard` after checking out this repository. The local composite action's actual input is `adversarial` (not `adversarial-scenarios`) and takes no `api-key`; the published `validsim/*@v1` addresses and their inputs above are the proposed end-state interface.

## How the target action works

| Step | Mechanism | Backend |
|---|---|---|
| **Trigger** | `push` to `models/**` or `policies/**` — checkpoint pushes are the "commits" of robot CI | — |
| **validate-action@v1** (target) | Proposed hosted submission/poll service and threshold gate | Future `POST /api/v1/validations` / polling API ([[API Design]]) |
| **scorecard-action@v1** (target) | Proposed hosted fetch/render, then PR comment | Future scorecard API |
| **Current local action** | `actions/validate` runs the checkout's mock pipeline; `actions/scorecard` reads its job cache | No hosted API or API key |
| **Secret** | Reserved for the future hosted action; local actions do not consume it | Auth ([[Tech Stack]]) |

> [!important] The PR comment is a target growth loop
> A local Action can render a PR comment from the current job cache. Whether that drives adoption is an unverified GTM hypothesis; the published Action/marketplace funnel is future work ([[Go-to-Market]], [[Product Principles]] #6).

## Semantics of `fail-below-score: 80`

- Composite score weights: 40% success + 30% safety + 20% robustness + 10% regression ([[Module Specs]] M4)
- `gate` fails the Action on a stored `BLOCK`; GitHub branch protection may block merge, but actual fleet deployment is outside this repository.
- Threshold is per-repo; Enterprise tier gets fleet-level thresholds + approval workflows ([[Pricing Tiers]])

## Variants on the roadmap

| CI system | Status |
|---|---|
| GitHub Actions | Local composite-action prototype; hosted publication not shipped |
| GitLab CI | Year 1 — same API, `.gitlab-ci.yml` job |
| Jenkins | Year 1–2 — plugin; Tier-3 factories skew Jenkins ([[Buyer Tiers]]) |

## Demo checklist for the 1-minute video ([[YC Countdown]])

- [ ] `git push` a checkpoint → Action auto-triggers
- [ ] Show the actual PR summary/comment rendered by the local Action
- [ ] A deliberately regressed checkpoint turns the Action red; do not claim this alone deploys to a fleet

Links: [[CLI Design]] · [[API Design]] · [[Solution Architecture]] · [[Go-to-Market]] · [[Home]]
