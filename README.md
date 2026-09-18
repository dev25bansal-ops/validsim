# SIM-TO-REAL — ValidSim

**GitHub Actions for robots** — continuous validation, regression testing, and safety scoring for robot foundation models before they touch the real world.

This repository contains the ValidSim MVP codebase, the founding blueprint, and a 24/7 continuous build pipeline.

## Repository Layout

| Path | Purpose |
|---|---|
| `project.docx` | Confidential founding document (19-section startup blueprint) |
| `vault/` | Obsidian vault — the full blueprint as interconnected notes (open this folder in Obsidian) |
| `validsim/` | Python platform MVP: config models, simulation runner (mock Isaac backend), adversarial scenarios, evaluation/safety/regression/scorecard engines, FastAPI API, Typer CLI |
| `tests/` | pytest suite covering every module |
| `scripts/build.ps1` | Continuous build entrypoint: venv bootstrap, deps, tests, publishes status to the vault |
| `.github/workflows/` | CI (on push/PR) + nightly validation pipeline |
| `Dockerfile`, `docker-compose.yml` | Container runtime: API + Redis + PostgreSQL (GPU Isaac worker placeholder) |
| `builds/` | Local build artifacts (log CSV, JUnit XML) — gitignored |

## Quick Start

```powershell
# Continuous build (installs deps into .venv, runs tests, updates vault status)
powershell -ExecutionPolicy Bypass -File scripts/build.ps1

# Run the API
.venv\Scripts\python.exe -m uvicorn validsim.api.main:app --reload

# CLI: validate a checkpoint (mock backend in MVP)
.venv\Scripts\python.exe -m validsim.cli run --task bin_picking --episodes 1000 --adversarial 50

# Containers
docker compose up --build
```

## The 24/7 Continuous Build Process

1. **Local/agent pipeline** — a CodeBuddy automation runs `scripts/build.ps1` **every hour, 24/7**: it bootstraps the environment, installs dependencies, runs the full test suite, and regenerates `vault/00 - Dashboard/Build Status.md` (linked from the vault Home as `[[Build Status]]`). Failures are diagnosed and fixed by the automation agent, then re-verified.
2. **Remote CI** — `.github/workflows/ci.yml` runs lint + tests + Docker build on every push/PR to `main`; `.github/workflows/nightly.yml` runs a deep validation suite at 03:00 UTC daily.
3. **History** — every run is appended to `builds/build-log.csv`; the vault note shows the last 15 runs and pass-rate.

## Product Pipeline (what the code does)

```
submit checkpoint → simulate (parallel episodes + domain randomization)
→ adversarial scenarios (12 categories) → evaluate (success, safety, robustness)
→ regression test vs baseline (bootstrap CI) → scorecard (composite = 0.4·success
+ 0.3·safety + 0.2·robustness + 0.1·regression) → APPROVE / BLOCK deploy
```

## Milestone Status (per 8-Week Sprint Plan)

- [x] Week 1: repo, Docker, CI/CD pipeline
- [x] Weeks 2–5 skeleton: episode runner (mock), randomization, evaluation, safety, regression, scorecard
- [x] Weeks 4/6: scenario generator, API, CLI, **SQLite persistent store, scorecard Markdown/HTML exports, webhook dispatcher**
- [x] Week 6: **GitHub Actions plugin prototype** (`actions/validate`, `actions/scorecard`, `examples/robot-validation.yml`, `docs/github-actions.md`)
- [ ] Isaac Sim/Lab backend replacing `MockIsaacBackend`
- [ ] PostgreSQL/Timescale store, LLM adversarial generator, dashboard

See the vault: `05 - Execution/8-Week Sprint Plan.md`

---
*Status: CONFIDENTIAL — Founding Document + MVP scaffold. Target: YC W27 (Nov 2, 2026) + NVIDIA Inception.*
