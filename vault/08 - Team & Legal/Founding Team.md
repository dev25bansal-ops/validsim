---
tags:
  - team
  - founders
status: complete
created: 2026-09-18
area: "08 - Team & Legal"
---

# 👯 Founding Team

§11.1. Two founders, AI/ML, pre-seed. The division of labor mirrors the product architecture — every sprint week has exactly one owner per workstream ([[8-Week Sprint Plan]]).

> [!important] Blueprint status — 2026-09-21
> These are planned roles and skill targets, not verified founder biographies or evidence of shipped Kubernetes/Next.js/billing experience. The current engineering prototype uses FastAPI/Typer, static HTML/JS, Docker Compose, Redis/PostgreSQL, and local Actions ([[Tech Stack]]).

## Roles

| Role | Focus | Skills Required |
|---|---|---|
| **Founder 1 — ML/Robotics Lead** | Simulation engine, evaluation pipeline, model integration, adversarial scenarios | PyTorch, Isaac Sim/Lab, RL/IL, VLA models, domain randomization |
| **Founder 2 — Platform/Infra Lead** | API, queue/orchestration, dashboard, CI/CD integration, DevOps, future billing | FastAPI/Typer, static HTML/JS, GitHub Actions, PostgreSQL, Docker Compose; Kubernetes/Next.js/cloud-billing are target skills |

## Ownership map (from the sprint plan)

| Layer ([[Solution Architecture]]) | Owner |
|---|---|
| M2 Simulation Engine, M3 Adversarial Generator, M4 Scoring | Founder 1 |
| M1 Ingestion/Orchestration, API/CLI, M5 Dashboard, CI plugin | Founder 2 |
| Benchmark task definition, demo video, blog post, incorporation | Both |

## Why 2 founders is the right size (feasibility 8/10)

> [!important]
> - **Pure software** — no hardware, manufacturing, or supply chain to staff ([[Strategic Advantages]] #4)
> - **Open-source substrate** — Isaac Sim/Lab free; the stack table is a shopping list, not an R&D program ([[Tech Stack]])
> - **Both ML skills apply directly** — the product *is* the founders' combined skill set
> - Speed claims made to investors are calibrated to this team: **8-week MVP, weekly demo, 10 calls/week** ([[Investor Narrative]] team story)

## Founder allocation outside the build

| Duty | Who | Cadence |
|---|---|---|
| Discovery calls (10/wk → 5/wk) | Both | [[Go-to-Market]] Phase 1 |
| LOI negotiation with design partners | Founder 1 (technical commitments) | W7–W9 |
| Inception + incorporation paperwork | Founder 2 | W1–W2 |
| YC application + interview prep | Both, 2×/week rehearsals | W10–W12 |

## Equity & vesting (see [[Corporate Structure]])

- **50/50** split (or negotiated) with **4-year vesting, 1-year cliff**
- All IP assigned to the corporation at signing ([[IP Strategy]])
- YC standard terms if accepted: $125K for 7% + $375K uncapped MFN

## Named gaps the team admits to investors

| Gap | Bridge | When |
|---|---|---|
| No dedicated frontend/UX | Founder 2 ships the current static dashboard; a Next.js scaffold remains a W7 target | Hire #2, month 5–7 |
| No enterprise sales experience | Developer-led motion first ([[Go-to-Market]]) | Hire #5, month 9–12 |
| No formal robotics academic credibility | Advisory board: CMU/MIT/Stanford professor | Post-funding ([[Hiring Plan]]) |

Links: [[Hiring Plan]] · [[Corporate Structure]] · [[8-Week Sprint Plan]] · [[Investor Narrative]] · [[Home]]
