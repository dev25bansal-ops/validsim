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

> [!important] The wedge
> Pick **one embodied-AI stack** (e.g., a GR00T-class or pi0-class VLA model) evaluated on **one standard manipulation benchmark** in Isaac Lab. Everything else is deferred ([[MVP Non-Goals]]).

Rationale: two founders, eight weeks, one demo video ([[Founding Team]], [[8-Week Sprint Plan]]). A narrow slice that produces a *real scorecard for ≥1 external user* beats a broad platform that produces slides ([[90-Day Roadmap]] exit criteria).

## What the MVP does — the 8-step pipeline

| # | Capability | Detail | Sprint week |
|---|---|---|---|
| 1 | User submits a model checkpoint + task config via CLI or API | `validsim run --checkpoint ... --task bin_picking` | W6 |
| 2 | System spins up Isaac Lab environment with the specified robot + task | Franka Panda, tabletop bin picking | W1 |
| 3 | Runs **1,000–5,000 parallel simulation episodes** with domain randomization | 6+ randomization axes; <30 min on 4× A100 | W2 |
| 4 | Generates **LLM-based adversarial scenarios** (50–100 edge cases) | GPT-4o prompt pipeline → scene JSON | W4 |
| 5 | Evaluates success rate, failure taxonomy, safety metrics | Rule-based classifier; 0–100 safety score | W3 |
| 6 | Compares against previous checkpoint (regression detection) | Bootstrap 95% CI; `GET /regressions` | W5 |
| 7 | Outputs a **scorecard dashboard** + downloadable PDF report | Composite 40/30/20/10 weighting | W7–W8 |
| 8 | Exposes results via webhook / GitHub Actions integration | Slack notification + PR comment | W6 |

## Benchmark choice

- **Task:** tabletop bin picking (defined Week 1, day 3–4)
- **Robot:** Franka Panda (used in all CLI/API examples — [[CLI Design]], [[API Design]])
- **Model:** GR00T N1 or pi0 (open VLA, inference working by Week 1 day 2)
- **Environment:** warehouse_a scene config
- Why bin picking: standard manipulation benchmark, rich failure modes (deformable objects, lighting), maps directly to the buyer monologue in [[Problem Statement]]

## MVP definition of done

- [ ] End-to-end: submit → simulate → evaluate → dashboard → PDF (Week 8, days 1–2)
- [ ] Branded PDF scorecard generator (Week 8, days 2–3)
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
