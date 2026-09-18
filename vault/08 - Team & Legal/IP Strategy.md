---
tags:
  - legal
  - ip
status: complete
created: 2026-09-18
area: "08 - Team & Legal"
---

# 🛡️ IP Strategy

§18.2. The allocation rule: **open the entry points, trade-secret the engines, patent the methods that define the category.** The MIT CLI is marketing spend; the scenario generator is the company ([[Moat]] flywheel).

## 18.2 Asset-by-asset protection

| Asset | Protection |
|---|---|
| Simulation pipeline architecture | **Trade secret + provisional patent** |
| Adversarial scenario generation methodology | **Trade secret + provisional patent** |
| Failure taxonomy classification system | **Trade secret** |
| Scorecard format / compliance evidence standard | **Trade secret + potential industry standard** |
| CLI tool + GitHub Actions plugin | **Open-source (MIT)** for adoption |
| Dashboard + API | **Proprietary, trade secret** |

## Reasoning per asset

> [!note]
> - **Pipeline architecture & adversarial methodology (patent-provisional):** these are the two inventions a competitor would clone first. Provisionals are cheap (~$K range, fits the $10K–$15K legal budget, [[Corporate Structure]]), establish priority date, and convert to full patents post-seed. File before any public benchmark or blog post reveals mechanics ([[Go-to-Market]] Phase 2 conflict — see below).
> - **Failure taxonomy (trade secret):** value lives in the accumulating dataset, not the idea — disclosure gains nothing, and the flywheel compounds privately ([[Module Specs]] M3–M4).
> - **Scorecard format (secret → standard):** deliberately published *format* (schema) while keeping scoring weights and calibration data proprietary; goal is for insurers to adopt the format, making it the de-facto standard ([[Moat]] compliance row, [[Compliance]]).
> - **CLI + Actions plugin (MIT):** the open-source surface *is* the growth engine — 500+ stars target depends on it ([[Go-to-Market]] Phase 2). The plugin is a thin client over the proprietary API anyway.
> - **Dashboard + API (proprietary):** all server-side; customers never receive code.

## Chain of title

1. All founder IP assigned at incorporation via PIIAAs ([[Corporate Structure]])
2. Every hire from [[Hiring Plan]] signs assignment agreements day one
3. Advisors get equity, not IP rights ([[Funding Plan]] guardrails)
4. Open-source contributions to ValidSim repos require CLA assigning rights to the corporation

## Publication vs. patent tension (calendar this)

| Event | Reveals | Patent gate |
|---|---|---|
| Technical blog post (sprint W8, day 5) | Pipeline shape, regression methodology | **Provisionals filed first** |
| Public benchmarks (GR00T/pi0/OpenVLA) | Scenario categories, scores | Provisionals filed |
| GTC/ICRA/RSS/CoRL talks | Architecture diagrams | Provisionals filed; keep weights off slides |
| MIT CLI release | Client code only | No gate — intentional |

> [!warning] Do not publish the W8 blog before provisionals are on file
> Public disclosure starts clocks and can forfeit foreign patent rights. Order: file → publish. Log the decision date in the [[Decision Log]].

## What we explicitly do NOT own

- Isaac Sim / Isaac Lab / PhysX / Omniverse / Cosmos — NVIDIA open-source and platform IP; we integrate, we don't claim ([[Tech Stack]])
- Customer model checkpoints and episode data — customer property, held under contract; privacy posture in [[Compliance]]
- USD scene assets from public repositories — license-audit before bundling into the task library

Links: [[Corporate Structure]] · [[Compliance]] · [[Moat]] · [[Go-to-Market]] · [[Home]]
