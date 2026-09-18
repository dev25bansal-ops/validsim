---
tags:
  - execution
  - metrics
  - mvp
status: complete
created: 2026-09-18
area: "05 - Execution"
---

# 🎯 MVP Success Metrics

The seven numbers that define "the MVP is done" ([[8-Week Sprint Plan]] Week 8). Each is instrumented in the demo video script and quotable in the YC application ([[YC Countdown]]: *"one metric measured and repeatable"*).

## 7.4 Targets

| Metric | Target | Measured how | Sprint week proven |
|---|---|---|---|
| Episodes per validation run | **1,000–5,000** | Episode runner count | W2 (1,000) → W8 (5,000) |
| Validation run time (5,000 episodes, 4× A100) | **< 30 minutes** | Timestamped run logs | W2 day 5 benchmark |
| Adversarial scenarios generated | **100 per run** | Scenario JSON count executed | W4 |
| Scorecard generation time | **< 5 minutes post-simulation** | Report pipeline timing | W8 |
| Regression detection accuracy | **> 95%** (vs. ground-truth) | v41/v42 seeded-regression test | W5 |
| API uptime during demo | **100%** | Demo-window monitoring | W6–W8 |
| Dashboard load time | **< 2 seconds** | Lighthouse/perf trace | W7 |

## Ground-truth protocol for regression accuracy

> [!important] How ">95% vs. ground-truth" is earned
> Week 5 test: submit two checkpoints (v41, v42) where v42 is **deliberately regressed** on known tasks (e.g., deformable-object grasp, low-light). The engine must flag exactly those deltas with statistical significance — e.g., *"89% → 84% (p=0.03)"* — and no false positives above threshold ([[Scorecard UX]] regression panel).

## Latency budget cross-check

| Stage | Budget | Source |
|---|---|---|
| Submit → scorecard in engineer's hands | < 1 hour total (15–45 min sim + <5 min report) | [[User Personas]], [[Data Flow]] |
| Queue + orchestration overhead | seconds | [[Module Specs]] M1 |

## Relationship to post-launch KPIs

MVP metrics are **engineering acceptance tests**; business metrics (runs completed, MAU, ARR) start ticking after design partners arrive — see [[KPIs]] (500 validation runs by month 6 → 5,000 by month 12; throughput target evolves to 1,000 episodes <30 min on 8× A100).

## Demo-day checklist (Week 8, day 1–2 E2E test)

- [ ] `git push` checkpoint → Action fires ([[GitHub Actions Integration]])
- [ ] 5,000 episodes, 100 adversarial scenarios, wall-clock < 30 min on 4× A100
- [ ] Slack notification with composite score + regression list
- [ ] Dashboard: scorecard, failure treemap, regression timeline, episode replay (<2 s load)
- [ ] PDF scorecard downloads, branded
- [ ] `validsim gate --threshold 85` returns APPROVE on good run, non-zero exit on seeded regression ([[CLI Design]])
- [ ] API uptime 100% across the recorded demo

Links: [[8-Week Sprint Plan]] · [[KPIs]] · [[Scorecard UX]] · [[Build Status]] · [[Home]]
