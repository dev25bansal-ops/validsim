---
tags:
  - engineering
  - cli
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# ⌨️ CLI Design

`validsim` — pip-installable CLI (sprint W6: "Build CLI tool: `validsim run`, `validsim status`"). The CLI is the developer-native front door and the MIT-licensed open-source growth engine ([[Go-to-Market]] Phase 2, [[IP Strategy]]).

## 4.6 Command surface

```bash
# Submit a validation run
$ validsim run \
    --checkpoint ./models/gr00t_v42.pt \
    --task bin_picking \
    --robot franka_panda \
    --environment warehouse_a \
    --episodes 5000 \
    --randomization full \
    --adversarial 100

# Check status
$ validsim status --run-id abc123

# View scorecard
$ validsim scorecard --run-id abc123 --format pdf

# Compare with previous version
$ validsim compare --run-id abc123 --baseline abc122

# Deploy gate check
$ validsim gate --run-id abc123 --threshold 85

# GitHub Actions integration
$ validsim ci --github-actions --fail-below 80
```

## Command → API mapping

| Command | Calls | Flow ([[Core User Flows]]) |
|---|---|---|
| `run` | `POST /api/v1/validations` | Flow 1 |
| `status` | `GET /api/v1/validations/{id}` | Flow 1 |
| `scorecard` | `GET /api/v1/validations/{id}/scorecard` | Flows 1, 3 |
| `compare` | `POST /api/v1/validations/{id}/compare` | Flow 1 step 7 |
| `gate` | `POST /api/v1/deployment-gate` | Flow 2 |
| `ci` | wraps Actions YAML generation | [[GitHub Actions Integration]] |

Full endpoint reference: [[API Design]].

## UX contracts

> [!important] Design rules
> 1. **Zero-config default:** `validsim run --checkpoint model.pt` works — task/robot/episodes fall back to project config saved on first full run ([[Product Principles]] #1).
> 2. **Exit codes are the product in CI:** `0` = gate approved, non-zero = blocked, so `--fail-below 80` fails the GitHub Actions build red ([[Product Principles]] #4).
> 3. **Human + machine output:** `--format pdf|json`, default terminal summary with composite score + top regressions + run-id.
> 4. **Async by default:** `run` returns a `run-id` immediately (15–45 min jobs don't hold terminals); `status` polls, webhook pushes ([[Data Flow]]).

## Example: terminal verdict (mirrors [[Scorecard UX]])

```
✔ VALIDATION 87.3/100 — DEPLOY APPROVED (threshold 85)
  success 94.2% | safety 91 | robustness 82 | regression −1.3%
  ⚠ 2 regressions vs abc122: deformable grasp 89→84 (p=0.03),
    low-light 91→87 (p=0.08)
  → validsim scorecard --run-id abc123 --format pdf
```

## Distribution & licensing

- `pip install validsim` · MIT license · public repo doubles as the Week-10 public artifact ([[YC Countdown]])
- Auth via `VALIDSIM_API_KEY` env var — same secret the GitHub Action uses ([[GitHub Actions Integration]])
- Roadmap: `validsim fleet` (gating at scale) and `validsim hil` ship only when those leave [[MVP Non-Goals]]

Links: [[API Design]] · [[GitHub Actions Integration]] · [[MVP Scope]] · [[Home]]
