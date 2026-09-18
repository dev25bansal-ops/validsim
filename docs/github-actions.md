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
> same inputs/outputs.

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
2. Otherwise `pip install .` from the checkout. **This repo currently has no
   `pyproject.toml`/`setup.py`, so step 2 fails by design** and the action
   falls back to `pip install -r requirements.txt` + `PYTHONPATH=$GITHUB_WORKSPACE`
   (monorepo mode — the normal path today).

### CLI output contract (MVP parsing)

The action parses `validsim run` stdout (see `validsim/cli.py`):

```
  Run ID:        vrun-1a2b3c4d          ← output `run-id` (regex vrun-[0-9a-f]{8})
  Composite:     87.30 (threshold 85.0) ← output `score` (first number on the line)
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
> The MVP CLI's `scorecard` command prints **JSON only** — there is no
> `--format markdown` flag. The action therefore runs
> `python -m validsim.cli scorecard --run-id <id>` (JSON) and renders the
> comment body with a small inline stdlib-only Python script inside
> `actions/scorecard/action.yml`. When the CLI gains a native markdown
> formatter, that step should call it instead.

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

## 7. End-to-end example: validating `models/**` pushes

See [`examples/robot-validation.yml`](../examples/robot-validation.yml) —
triggers on `push`/`pull_request` touching `models/**` or `policies/**`,
runs the gate at 85, always posts the scorecard, and gates a demo `deploy`
job on the result.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No python interpreter found` | Add `actions/setup-python@v5` before the action (composite actions can't install Python themselves). |
| `Could not parse a run id` | CLI summary format drifted from §3 contract — update the parse step. |
| gate exit `2` in scorecard step | Scorecard ran in a different job, or validate crashed before caching (see §4 same-job constraint). |
| PR comment missing on `push` events | Expected — no PR exists. Check the job summary; use `pull_request` triggers for comments. |
| `Resource not accessible by integration` on comment | Job lacks `pull-requests: write` permission. |

Links: [[GitHub Actions Integration]] · [[CLI Design]] · [[Solution Architecture]]
