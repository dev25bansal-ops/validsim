---
tags: [engineering, ci, github-actions]
status: prototype (W6)
---

# 🤖 ValidSim GitHub Actions — Integration Guide

The ValidSim deploy gate as a copy-paste YAML block. This is the Week-6
sprint prototype of the integration described in the founding blueprint
([[GitHub Actions Integration]] §4.7).

> [!important] Self-hosted MVP mode — read this first
> ValidSim does **not** have a SaaS backend yet. These actions run the
> **local mock validation pipeline** (`validsim.sim.runner.MockIsaacBackend`)
> inside your own job: `validsim run` → local scorecard cache →
> `validsim gate`. No data leaves your runner, and **no API key is required
> today**. `VALIDSIM_API_KEY` is documented below only as the *future*
> credential for the hosted cloud backend (GPU simulation via API); when that
> ships, the actions switch from local execution to submit-and-poll with the
> same inputs/outputs. For the forward-looking shape of that switch see
> **§9** (async `POST /api/v1/jobs` vs synchronous validate) and **§10**
> (self-hosted GPU runner → [docs/isaac-worker.md](isaac-worker.md)).

## 1. Quick start (current monorepo form)

While the actions live in this repository, consumers in the same repo use the
local path form:

```yaml
# .github/workflows/robot-validation.yml
name: Robot Policy Validation
on:
  push:
    paths: ['models/**', 'policies/**']

permissions:
  contents: read
  pull-requests: write   # only needed for the scorecard PR comment

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # REQUIRED: composite actions cannot run setup-python themselves.
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run Sim-to-Real Validation
        id: validate
        uses: ./actions/validate
        with:
          checkpoint: ./models/policy_v42.pt
          task: bin_picking
          robot: franka_panda
          episodes: 1000
          adversarial: 50
          fail-below-score: 85

      - name: Post Scorecard
        if: always()
        uses: ./actions/scorecard
        with:
          run-id: ${{ steps.validate.outputs.run-id }}
          comment-on-pr: true
```

A ready-to-copy version of this file ships at
[`examples/robot-validation.yml`](../examples/robot-validation.yml).

## 2. Future standalone form (`@v1`)

Once the actions are published as their own repos (and `validsim` is on
PyPI), the blueprint's one-liner form becomes available — same inputs, same
outputs:

```yaml
      - name: Run Sim-to-Real Validation
        id: validate
        uses: validsim/validate-action@v1
        with:
          checkpoint: ./models/policy_v42.pt
          task: bin_picking
          robot: franka_panda
          episodes: 5000
          adversarial: 50
          fail-below-score: 80
          validsim-version: "0.1.0"       # pip-installs from PyPI
          # api-key: ${{ secrets.VALIDSIM_API_KEY }}  # accepted today but
          # unused — reserved for the cloud backend (see §6)

      - name: Post Scorecard
        if: always()
        uses: validsim/scorecard-action@v1
        with:
          run-id: ${{ steps.validate.outputs.run-id }}
          comment-on-pr: true
```

Differences from the blueprint snippet in §4.7 of the vault note:
`adversarial-scenarios` was renamed to **`adversarial`** and `api-key` is not
yet wired (MVP runs the local mock pipeline). Set `validsim-version` when
using the standalone form so the action pip-installs the published package
instead of the checkout.

## 3. `validsim/validate` — inputs & outputs

| Input | Required | Default | Description |
|---|---|---|---|
| `checkpoint` | **yes** | — | Checkpoint id/path. In MVP it is passed through as an opaque id to `validsim run --checkpoint`; the mock pipeline does not read the file. |
| `task` | no | `bin_picking` | Task id (`--task`). |
| `robot` | no | `franka_panda` | Robot name (`--robot`). |
| `episodes` | no | `1000` | Nominal episodes (`--episodes`). |
| `adversarial` | no | `50` | Adversarial scenarios (`--adversarial`). |
| `fail-below-score` | no | `85` | Composite score (0–100) below which the job fails. Forwarded to `run --threshold` *and* `gate --threshold`. |
| `validsim-version` | no | `""` | Pin a PyPI release (`pip install validsim==X`). Empty ⇒ install from checkout. |

| Output | Description |
|---|---|
| `run-id` | `vrun-<8 hex>` id of the run; feed to the scorecard action or `validsim status --run-id`. |
| `score` | Composite score (0–100) as printed by the CLI. |

### How it installs ValidSim

1. `validsim-version` set ⇒ `pip install validsim==<version>` (future PyPI path).
2. Otherwise `pip install .` from the checkout. **This is the normal path
   today** — the repo now ships a `pyproject.toml` (setuptools build backend,
   `[project] name = "validsim"`, a `validsim` console script), so the packaged
   install succeeds. Only if that packaged install fails for some unexpected
   reason does the action fall back to `pip install -r requirements.txt` +
   `PYTHONPATH=$GITHUB_WORKSPACE` (monorepo mode — the fallback is kept purely
   as a defensive safety net, not the primary route).

### CLI output contract (MVP parsing)

The action parses `validsim run` stdout (see `validsim/cli.py`):

```
  Run ID:        vrun-1a2b3c4d          ← output `run-id` (regex vrun-[0-9a-f]{8})
  Composite:     87.3 (threshold 85.0)  ← output `score` (first number on the line)
```

If either line cannot be found, the action fails with an explicit
`::error::` rather than guessing. If the CLI's summary format changes, update
`actions/validate/action.yml` ("Parse run id and composite score" step).

## 4. `validsim/scorecard` — inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `run-id` | **yes** | — | Run id from the validate action's output. |
| `comment-on-pr` | no | `true` | Post the scorecard as a PR comment. Skipped silently when the run has no PR (e.g. `push` events); the scorecard always lands in the **job summary**. |
| `github-token` | no | `${{ github.token }}` | Token for `gh pr comment`; job needs `pull-requests: write`. |

> [!note] Markdown generation in MVP
> The CLI's `scorecard` command still prints **JSON only** — it has no
> `--format` flag. The action therefore runs
> `python -m validsim.cli scorecard --run-id <id>` (JSON) and renders the
> comment body with a small inline stdlib-only Python script inside
> `actions/scorecard/action.yml`.
>
> The CLI now also ships a **native** report formatter as a separate command:
> `validsim report --run-id <id> --format markdown` (or `--format html`,
> default `markdown`) renders the full scorecard, and `validsim report --json`
> emits the raw cached scorecard as machine-readable JSON. The scorecard action
> has not yet been switched over, but once it is, its render step should call
> `report --format markdown` instead of the inline script.

> [!warning] Same-job constraint (MVP)
> Results are cached in a JSON file at
> `${{ runner.temp }}/validsim/scorecards.json` (`VALIDSIM_CACHE_FILE`).
> The runner temp dir is per-**job**, so validate + scorecard must run in the
> same job until the cloud store exists. Cross-job/cross-run lookups by
> `run-id` are a backend feature.

## 5. The gate: how a bad checkpoint blocks deploys

`validsim gate --run-id <id> --threshold <t>` is CI-native
(exit codes from `validsim/cli.py`):

| Exit code | Meaning | Effect in Actions |
|---|---|---|
| `0` | `APPROVE` — composite ≥ threshold | step passes, job continues |
| `1` | `BLOCK` — composite < threshold | **step and job fail** |
| `2` | unknown run id / no cache | job fails (misuse) |

> [!tip] Machine-readable gate output (`gate --json`) and `--latest`
> Add `--json` to have the decision printed to **stdout** as
> `{"run_id", "composite_score", "threshold", "decision"}` instead of the plain
> text line — handy when a later step parses the verdict (e.g. to feed a Slack
> badge or a matrix). The `0/1/2` exit-code contract above is **unchanged** by
> `--json`, so the step still fails the job on `BLOCK`. `gate` also accepts
> `--latest` (the newest cached run) as an alternative to `--run-id`; exactly
> one of the two is required. This is how the nightly sweep in §7 gates without
> threading a run id between steps.

A failed `validate` job blocks anything wired behind it:

```yaml
  deploy-to-fleet:
    needs: validate          # skipped unless the gate returned APPROVE
    runs-on: ubuntu-latest
    steps:
      - run: ./scripts/deploy_fleet.sh --checkpoint ./models/policy_v42.pt
```

Combine with branch protection ("Require Robot Policy Validation to pass")
and the score becomes a merge gate too — the fleet can't receive a checkpoint
that scores below `fail-below-score`. Composite = 40% success + 30% safety +
20% robustness + 10% regression (blueprint §"Semantics of fail-below-score").

## 6. Secrets setup

**Today (MVP): nothing to configure.** The mock pipeline is fully local.

For the future cloud backend, pre-create the placeholder so workflows don't
need editing when it ships:

1. Repo → Settings → Secrets and variables → Actions → *New repository secret*.
2. Name: `VALIDSIM_API_KEY`, value: the key from the ValidSim dashboard
   (same key as the CLI's env var).
3. Wire it as `api-key: ${{ secrets.VALIDSIM_API_KEY }}` once
   `validate-action@v1` accepts it.

Never commit keys, and never echo secrets into step logs.

## 7. End-to-end examples

Two ready-to-copy workflows ship under `examples/`. Both run the **local mock
pipeline** in MVP mode (no SaaS backend, no API key) and share the same-job
cache constraint from §4.

### 7.1 PR / push gate — `robot-validation.yml`

See [`examples/robot-validation.yml`](../examples/robot-validation.yml) —
triggers on `push`/`pull_request` touching `models/**` or `policies/**`, uses
the `./actions/validate` + `./actions/scorecard` composite actions, runs the
gate at 85, always posts the scorecard, and gates a demo `deploy` job on the
result. Small episode budget (`1000` nominal + `50` adversarial) for fast
feedback.

### 7.2 Nightly deep pass — `nightly-adversarial-sweep.yml`

See [`examples/nightly-adversarial-sweep.yml`](../examples/nightly-adversarial-sweep.yml)
— the companion to §7.1 for the heavier, scheduled sweep:

| Aspect | Value |
|---|---|
| Triggers | `schedule` cron `0 3 * * *` (03:00 UTC nightly) **and** `workflow_dispatch` (on-demand, e.g. before a release cut) |
| Episode budget | `2000` nominal + `200` adversarial (the deep pass) |
| Permissions | `contents: read` only — read-only job, no PR comment, artifact upload needs no extra scopes |
| Timeout | `timeout-minutes: 60` — fail loudly rather than hang on the heavy run |

Unlike §7.1 it calls the CLI **directly** (`python -m validsim.cli`) instead of
the composite actions, because the sweep only needs `run → gate`:

```yaml
      - name: Run Nightly Adversarial Sweep
        run: |
          python -m validsim.cli run --episodes 2000 --adversarial 200 --checkpoint nightly-checkpoint

      - name: Gate on Nightly Composite
        run: |
          python -m validsim.cli gate --latest
```

Key points to mirror if you adapt it:

- **`gate --latest`** (see §5) targets the newest cached run, so no run id is
  threaded between steps. Exactly one of `--run-id` / `--latest` is accepted.
- **pip cache** is keyed on both `requirements.txt` and
  `requirements-dev.txt` (`cache-dependency-path`) since nightly installs run
  often and should be fast.
- **JUnit evidence**: the MVP CLI has no native JUnit formatter, so the
  workflow wraps the newest cached scorecard (`.validsim/scorecards.json`) into
  a single JUnit XML testcase with an inline stdlib-only Python script, then
  uploads it (plus the raw JSON cache) via `actions/upload-artifact@v4`. Both
  the wrap and upload steps are `if: always()`, so evidence is retained even
  when the gate returns `BLOCK`.
- **Same-job rule still applies**: `run → gate → upload` stay in one job
  because the cache lives on the runner (§4).

> [!note] Why the sweep skips `pip install .`
> The sweep runs `python -m validsim.cli` straight from the checkout after
> installing only the third-party requirements, so no PYTHONPATH shim is needed.
> (The composite actions in §7.1 do use `pip install .` — see §3.)

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No python interpreter found` | Add `actions/setup-python@v5` before the action (composite actions can't install Python themselves). |
| `Could not parse a run id` | CLI summary format drifted from §3 contract — update the parse step. |
| gate exit `2` in scorecard step | Scorecard ran in a different job, or validate crashed before caching (see §4 same-job constraint). |
| PR comment missing on `push` events | Expected — no PR exists. Check the job summary; use `pull_request` triggers for comments. |
| `Resource not accessible by integration` on comment | Job lacks `pull-requests: write` permission. |
| `report`/`gate --json` output not parsing | `--json` prints one JSON object on **stdout**; keep `2>&1` off the pipe so step logs don't mix in. The gate's exit code is unaffected by `--json` (§5). |

## 9. Large sweeps: async `POST /api/v1/jobs` vs synchronous validate

The actions and examples above all run the pipeline **synchronously inside the
job** (MVP local mock). When the cloud/API backend is wired (see §6 and §10),
the REST API exposes two paths — pick by how long the run takes:

| | `POST /api/v1/validations` (sync) | `POST /api/v1/jobs` (async) |
|---|---|---|
| Returns | `201` with the full scorecard | `202` with `{job_id, status}` |
| Blocks until | episodes + evaluation + scorecard are done | the job is merely *queued* |
| Best for | PR/CI one-shots, the §7.1 gate | **large sweeps**, batches, GPU work — anything you don't want to hold a connection open for |
| Queue-full behaviour | n/a | `503` `{"error":"queue_full","max_depth":N}` + `Retry-After: 5` |

For a **large adversarial sweep** (the §7.2 budget and beyond), prefer the
async path: enqueue once and poll, rather than letting a single HTTP request
(or a 60-minute job) hang on the run. A job's `job_id` **is** its `run_id`
(`vrun-<8 hex>`), so the same id flows through:

```text
POST /api/v1/jobs          → 202 { "job_id": "vrun-1a2b3c4d", "status": "queued" }
GET  /api/v1/jobs/{job_id} → lifecycle record (queued → running → done/failed)
GET  /api/v1/jobs/{job_id}/status  → compact {job_id, status, updated_at}
GET  /api/v1/jobs/{job_id}/events  → SSE stream until terminal
```

When the worker finishes, it persists the run under that same id, so
`validsim gate --run-id vrun-1a2b3c4d` (§5) and the scorecard endpoints see it
exactly as a synchronous run would. In Actions today, the synchronous CLI
`run → gate` flow (§7) remains the supported path; the async endpoints are the
shape the hosted backend will use.

Full details — request/response schemas, the four-state job lifecycle, the
in-memory vs Redis queue backends, and running the worker under Docker — live
in [docs/async-jobs.md](async-jobs.md).

## 10. Self-hosted GPU runner (the real backend)

Everything above runs `MockIsaacBackend` locally. The **real** simulation
backend is a self-hosted Isaac Sim / Isaac Lab GPU worker: swap
`MockIsaacBackend` → `IsaacWorkerBackend` with a **config change**
(`VALIDSIM_BACKEND=isaac`), not a code change. The worker is a separate
~15 GB image (Isaac Sim headless, PhysX on an A100) reached over a small JSON
HTTP contract (`POST /episodes/run`, `GET /health`, bearer auth via
`VALIDSIM_ISAAC_WORKER_KEY`).

To use it from Actions you run the job on a **self-hosted runner** that can
reach the GPU worker, instead of `ubuntu-latest`:

```yaml
jobs:
  validate:
    runs-on: [self-hosted, gpu]     # a runner registered to your GPU host
    env:
      VALIDSIM_BACKEND: isaac       # point the pipeline at the real worker
      # VALIDSIM_ISAAC_WORKER_KEY: ${{ secrets.VALIDSIM_ISAAC_WORKER_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: ./actions/validate
        with:
          checkpoint: ./models/policy_v42.pt
```

> [!warning] Worker image is contract-only today
> The GPU worker is not built yet (pre-DGX); the client is verified only
> against `httpx.MockTransport` fakes. Treat `VALIDSIM_BACKEND=isaac` as the
> forward-compatible switch, not a currently-runnable backend.

The endpoint schemas, auth, retry policy (connect/timeout retried twice, HTTP
`4xx/5xx` never retried), and message shapes are the source of truth in
[docs/isaac-worker.md](isaac-worker.md).

Links: [[GitHub Actions Integration]] · [[CLI Design]] · [[Solution Architecture]] ·
[docs/async-jobs.md](async-jobs.md) · [docs/isaac-worker.md](isaac-worker.md)
