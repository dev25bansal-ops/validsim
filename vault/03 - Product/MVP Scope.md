---
tags:
  - product
  - mvp
  - scope
status: complete
created: 2026-09-18
area: "03 - Product"
---

# ✂️ MVP Scope

> [!warning] Verify before external send — 2026-09-21
> This page is the intended MVP acceptance plan, not evidence that each step is shipped. The repository currently provides a deterministic mock backend, optional HTTP worker adapter, static dashboard, reports, jobs, and local Actions; Isaac Lab execution, GPU throughput, media recordings, real webhooks, and external Action publication are not verified. Do not present the unchecked items as completed.

> [!important] The wedge
> Pick **one embodied-AI stack** (e.g., a GR00T-class or pi0-class VLA model) evaluated on **one standard manipulation benchmark** in Isaac Lab. Everything else is deferred ([[MVP Non-Goals]]).

Rationale: two founders, eight weeks, one demo video ([[Founding Team]], [[8-Week Sprint Plan]]). A narrow slice that produces a *real scorecard for ≥1 external user* beats a broad platform that produces slides ([[90-Day Roadmap]] exit criteria).

## What the MVP does — the 8-step pipeline

| # | Capability | Detail | Sprint week |
|---|---|---|---|
| 1 | User submits a model checkpoint + task config via CLI or API | `validsim run --checkpoint ... --task bin_picking` | W6 |
| 2 | System spins up Isaac Lab environment with the specified robot + task | **Planned**; the current default is a deterministic mock backend | W1 |
| 3 | Runs **1,000–5,000 simulation episodes** with domain randomization | Current engine runs episodes serially in the mock; `<30 min on 4× A100` is an unverified target | W2 |
| 4 | Generates **adversarial scenarios** (50–100 edge cases) across 12 categories | **Shipped as a deterministic, rule-based generator** (`scenarios/generator.py`) — reproducible from `(checkpoint_id, task_id)`, no network I/O. An LLM-backed generator exists (`scenarios/llm_generator.py`, `create_scenario_generator()`) and is tested, but **no run uses it**: `engine/pipeline.py:131` hardcodes the rule-based generator, so `VALIDSIM_LLM_ENABLED` / `VALIDSIM_LLM_API_KEY` have no effect. GPT-4o-to-Isaac scene editing is a separate, larger target | W4 |
| 5 | Evaluates success rate, failure taxonomy, safety metrics | Composite evaluation and rule-based failure taxonomy are implemented; calibrated physics/safety realism is a target | W3 |
| 6 | Compares against previous checkpoint (regression detection) | Bootstrap CI and `GET /regressions` are implemented | W5 |
| 7 | Outputs a **scorecard dashboard** + downloadable PDF report | Static dashboard and PDF/JSON/Markdown/HTML reports exist; the React/Next.js screen is target state | W7–W8 |
| 8 | Exposes results via webhook / GitHub Actions integration | HMAC dispatcher code and local Actions exist; public webhook registration and published Actions are planned | W6 |

## Benchmark choice

- **Task:** tabletop bin picking (defined Week 1, day 3–4)
- **Robot:** Franka Panda (used in all CLI/API examples — [[CLI Design]], [[API Design]])
- **Model:** GR00T N1 or pi0 (open VLA, inference working by Week 1 day 2)
- **Environment:** warehouse_a scene config
- Why bin picking: standard manipulation benchmark, rich failure modes (deformable objects, lighting), maps directly to the buyer monologue in [[Problem Statement]]

## MVP definition of done

- [x] End-to-end: submit → simulate → evaluate → dashboard → PDF (mock/static path; real Isaac path remains unverified)
- [x] Branded PDF scorecard generator (API export)
- [ ] 1-minute demo video, both founders on camera (Week 8, day 3) → [[YC Countdown]]
- [ ] Technical blog post published (Week 8, day 5) = public artifact
- [ ] All targets in [[MVP Success Metrics]] green

## Scope guardrails

> [!warning] The only growth axis allowed during the sprint
> More episodes, more adversarial scenarios, better stats — **not** more robots, more tasks, more integrations. Multi-embodiment/multi-task is the first thing an excited design partner will ask for; park it in [[MVP Non-Goals]] and revisit at the YC batch ([[Go-to-Market]] Phase 1 feedback loop).

## What ships after MVP (ordered)

1. Compliance evidence packages (ISO format) → Enterprise tier [[Pricing Tiers]]
2. Fleet deployment gating at scale → Tier-3 buyers [[Buyer Tiers]]
3. Multi-embodiment + ROS 2 HIL → hardware-in-the-loop [[Solution Architecture]]
4. Billing / multi-tenant SaaS → after first paying teams [[Business Model]]

Links: [[MVP Non-Goals]] · [[8-Week Sprint Plan]] · [[MVP Success Metrics]] · [[Product Vision]] · [[Home]]
