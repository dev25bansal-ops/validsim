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

## Implemented command surface

The shipped CLI (Typer) adds async-job, reporting, and registry commands on top of the original `run` / `status` / `scorecard` / `gate` surface. Run-targeting commands accept `--run-id <id>` **or** `--latest` (newest cached run) — exactly one, otherwise exit `2`.

```bash
# Enqueue an async validation job (a `validsim worker` runs it later)
$ validsim job enqueue --checkpoint gr00t_v42 --task bin_picking \
    --episodes 5000 --adversarial 100
Enqueued job vrun-1a2b3c4d (status: queued)

# List queued jobs (JOB ID · STATUS · CHECKPOINT · CREATED)
$ validsim jobs

# Run a worker against the queue + store
$ validsim worker --once                 # claim & run one job, then exit
$ validsim worker --watch                # loop, printing "job <id> -> <status>"
$ validsim worker --watch --max-jobs 10  # bounded loop, self-terminating
$ validsim worker                        # loop until Ctrl-C (SIGTERM-safe)

# Render a cached scorecard as a report
$ validsim report --latest --format markdown   # or --format html
$ validsim report --run-id vrun-1a2b3c4d --json # raw cached scorecard JSON

# Gate a deploy; --json emits {run_id, composite_score, threshold, decision}
$ validsim gate --latest --threshold 85 --json

# Delete a stored run (CLI parity for the API-only DELETE)
$ validsim delete --run-id vrun-1a2b3c4d
$ validsim delete --latest

# Checkpoint registry aggregated from the store
$ validsim models

# Regression report for two stored runs
$ validsim compare --candidate vrun-aaaa1111 --baseline vrun-bbbb2222
$ validsim compare --candidate-latest --baseline vrun-bbbb2222

# Version + one-line feature summary (always exits 0)
$ validsim version
```

## Command → behavior (implemented)

| Command | Backing | Notes |
|---|---|---|
| `job enqueue` | `JobQueue.enqueue` (env `VALIDSIM_JOB_QUEUE`: memory/Redis) | mirrors `POST /api/v1/jobs`; prints id + `queued` |
| `jobs` | `JobQueue.list` | reads the same queue the API enqueues to; empty ⇒ friendly notice, exit 0 |
| `worker` | `JobWorker.run_once` / `run_forever` | `--once` takes precedence; `--watch` echoes `job → status`; `--max-jobs N` bounds any loop (0 = unlimited); `--poll-seconds` idle interval |
| `report` | `scorecard_to_markdown` / `scorecard_to_html` | `--format markdown\|html` (default markdown); `--json` bypasses rendering ([[Scorecard UX]]) |
| `gate` | cached scorecard | `--json` for CI consumers; exit `1` on BLOCK either way ([[Product Principles]] #4) |
| `delete` | `ValidationStore.delete` | exit `0` on success, `2` when the run is absent |
| `models` | `store.history` + `store.count` | one row per checkpoint: runs, latest composite, decision, last validated |
| `compare` | `engine.regression.compare` | `--candidate`/`--baseline` (+ `*-latest`); targets **stored** runs; deterministic `stable_seed` |
| `version` | `__version__` | quick environment/CI sanity check |

The `job enqueue` / `jobs` / `worker` trio is the CLI face of [[Async Job Queue]] and [[Job Queue Worker]]; `report` / `delete` / `models` / `compare` mirror the matching endpoints in [[API Design]].

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

Links: [[API Design]] · [[GitHub Actions Integration]] · [[Async Job Queue]] · [[Job Queue Worker]] · [[Scorecard UX]] · [[MVP Scope]] · [[Home]]
