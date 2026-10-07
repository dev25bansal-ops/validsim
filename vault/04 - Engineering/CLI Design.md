---
tags:
  - engineering
  - cli
status: complete
created: 2026-09-18
area: "04 - Engineering"
---

# ⌨️ CLI Design

`validsim` — pip-installable CLI (sprint W6: "Build CLI tool: `validsim run`, `validsim status`"). The CLI is the developer-native front door; its current package is proprietary, with open-sourcing intended as the Phase-2 growth motion ([[Go-to-Market]], [[IP Strategy]]).

> [!important] Blueprint status — 2026-09-21
> Implemented commands are listed under **Implemented command surface**. The Week-1 command/API map below is a historical blueprint; its conceptual `gate → POST /deployment-gate` mapping and aspirational terminal example do not describe live HTTP calls or output.

## 4.6 Command surface

> [!warning] Week-1 design sketch, not the shipped CLI
> This block records the original blueprint. Several commands and flags in it
> were never implemented (`validsim ci`, `--fail-below`, `--randomization`,
> `scorecard --format pdf`), and run ids are real `vrun-<8 hex>` values, not
> `abc123`. Use **## Implemented command surface** below for anything you run.

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
| `gate` | Reads the durable store's `deploy_decision` and returns an exit code (no deployment-gate API call) | Flow 2 |
| `ci` | wraps Actions YAML generation (**not shipped** — copy the YAML from [[GitHub Actions Integration]]) | [[GitHub Actions Integration]] |

Full endpoint reference: [[API Design]].

## Implemented command surface

The shipped CLI (Typer) adds async-job, reporting, and registry commands on top of the original `run` / `status` / `scorecard` / `gate` surface. Run-targeting commands accept `--run-id <id>` **or** `--latest` — exactly one, otherwise exit `2`. `--latest` resolves against the local JSON cache for `status` / `scorecard` / `report` / `compare`, and against the **store** for `gate`, the one command whose answer gates a deploy.

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

# Delete a stored run (CLI and API parity)
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
| `gate` | **durable store** (`store.get`) | `--json` for CI consumers; exit `1` on BLOCK either way ([[Product Principles]] #4). Exits `2` on `VALIDSIM_STORE=memory` rather than deciding from the forgeable JSON cache; only the exact stored verdict `APPROVE` approves |
| `delete` | `ValidationStore.delete` | exit `0` on success, `2` when the run is absent |
| `models` | `store.history` + `store.count` | one row per checkpoint: runs, latest composite, decision, last validated |
| `compare` | `engine.regression.compare` | `--candidate`/`--baseline` (+ `*-latest`); targets **stored** runs; deterministic `stable_seed` |
| `version` | `__version__` | quick environment/CI sanity check |

The `job enqueue` / `jobs` / `worker` trio is the CLI face of [[Async Job Queue]] and [[Job Queue Worker]]; `report` / `delete` / `models` / `compare` mirror the matching endpoints in [[API Design]].

## UX contracts

> [!important] Design rules
> 1. **Zero-config default:** `validsim run` supplies task, robot, episode and environment defaults; it does not save a project config today ([[Product Principles]] #1).
> 2. **Exit codes are the product in CI:** `gate` returns `0` = approved, `1` = blocked, `2` = misuse (no durable store, unknown/malformed run id, both flags given), so a low `--threshold` verdict turns the GitHub Actions build red. There is no `--fail-below` flag; the option is `--threshold`, and it may only tighten the threshold stored with the run ([[Product Principles]] #4).
> 3. **Human + machine output:** `report --format markdown|html`; `scorecard` prints JSON; PDF is an API export.
> 4. **Async is opt-in:** `run` executes synchronously; use `job enqueue` plus `worker` for the long-running path, then poll or stream job status ([[Data Flow]]).

## Example: terminal verdict (mirrors [[Scorecard UX]])

Aspirational mock from the blueprint, not captured output: `scorecard` prints JSON and has no `--format pdf` (Markdown/HTML rendering is `validsim report`, PDF is the API's `/scorecard.pdf`), and run ids look like `vrun-1a2b3c4d`.

```
✔ VALIDATION 87.3/100 — DEPLOY APPROVED (threshold 85)
  success 94.2% | safety 91 | robustness 82 | regression −1.3%
  ⚠ 2 regressions vs abc122: deformable grasp 89→84 (p=0.03),
    low-light 91→87 (p=0.08)
  → validsim scorecard --run-id abc123 --format pdf
```

> [!warning] `robustness 82` in that mock is not producible today
> The block above is explicitly an aspirational mock (see the line above it),
> but it is worth being precise about which parts are unreachable rather than
> merely unimplemented: `run_validation` applies one `task.randomization` level
> to every episode, so every run has exactly one randomization group and
> `_robustness_score` returns its 100.0 maximum by construction. The shipped
> engine cannot emit `82`. Any mock, screenshot or deck that shows a sub-100
> robustness number is depicting a code path that does not exist.

## Distribution & licensing

- `pip install .` from this checkout; PyPI distribution and the MIT release are planned ([[YC Countdown]])
- The API uses `VALIDSIM_API_KEY`; local composite Actions currently run the CLI in the consumer checkout and do not require that secret ([[GitHub Actions Integration]])
- Roadmap: `validsim fleet` (gating at scale) and `validsim hil` ship only when those leave [[MVP Non-Goals]]

Links: [[API Design]] · [[GitHub Actions Integration]] · [[Async Job Queue]] · [[Job Queue Worker]] · [[Scorecard UX]] · [[MVP Scope]] · [[Home]]
