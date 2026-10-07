---
tags:
  - execution
  - metrics
  - kpis
status: active
created: 2026-09-18
area: "05 - Execution"
---

# 📊 KPIs & Success Metrics

Post-launch scorecard (§16). MVP acceptance tests live in [[MVP Success Metrics]]; these are the company's operating dashboard — reviewed weekly alongside the [[90-Day Roadmap]] ritual.

> [!warning] Verify before external use — 2026-09-21
> Product/business values are targets, not achievements. The latest engineering test/coverage row is retained as a dated snapshot, but it was not re-run for this vault pass; use [[Build Status]] and a generated report before quoting it. GPU throughput, uptime, regression accuracy, customers, ARR, LOIs and calls have no verified external evidence in the repository.

## 16.1 Product metrics

| Metric | Target (Month 6) | Target (Month 12) |
|---|---|---|
| Validation runs completed | **500** | **5,000** |
| Episodes per run | 1,000–5,000 | **10,000–100,000** |
| Regression bugs caught | **5** | **50** |
| Adversarial scenarios generated | 100 | **1,000** |
| Dashboard MAU | 20 | 200 |

> [!note] "Regression bugs caught" is the hero metric
> It is the only product number that directly states value delivered: 50 caught bugs × avoided downtime ($10K–$100K/hr, [[Pain Quantified]]) is the sales pitch. Every caught regression gets logged as a case study for [[Go-to-Market]] Phase 2 content ("How we found 12 regression bugs in a VLA model").

## 16.2 Business metrics

| Metric | Target (Month 6) | Target (Month 12) |
|---|---|---|
| Design partners | **3** | **5** |
| Paying customers | **2** | **15** |
| ARR | **$10K** | **$200K** |
| LOIs signed | **3** | **10** |
| Discovery conversations | **50** | **100** |

Cross-check vs. plan: Year-1 ARR $240K–$450K at 8–15 customers ([[Financial Projections]]); SOM Year 1 $100K–$500K ([[Market Sizing (TAM SAM SOM)]]). Month-12 KPI ($200K) sits inside both envelopes — if we trail, the [[Go-to-Market]] Phase 2 funnel (500 GitHub stars, 50 signups, 10 paying teams) is the diagnostic path.

## 16.3 Technical metrics

| Metric | Target |
|---|---|
| Simulation throughput | **1,000 episodes in < 30 min on 8× A100** — *target, unmeasured*; requires GPU/Isaac, and the shipped engine is a serial CPU mock |
| Scorecard generation time | **< 5 min post-simulation** — *target, unmeasured* |
| API uptime | **99.5%** — *target; the platform has no production deployment in this repository* |
| Regression detection accuracy | **> 95%** — *target, unmeasured; no labelled ground truth exists to score against* |
| Engineering velocity (test suite) | **No count is quoted here on purpose.** A hand-copied number goes stale on the next commit, and this repository's docs have carried at least six mutually incompatible figures (190 / 250 / 338 / 601 / 1,230 / 1,274 / 1,328). The only defensible number is the one the tool prints: `python -m pytest tests/` (totals + coverage `TOTAL`) or `python -m pytest tests/ --collect-only -q` (counts only — a collection count is not a pass count). Use the current generated [[Build Status]] row, and re-run it rather than quoting this page |
| Reliability & security posture | **Not 100%**: the recorded build history contains a `FAIL` run ([[Build Status]]). **Security HIGH findings outstanding**: see `docs/ISSUE_CATALOG.md` — items 01/02 closed 2026-09-21, 06/07/08/09/10/17/28 open or in progress. Audit H1–H3/M1/M3 closed 2026-09-20, [[Security Hardening]] |

> [!warning] Throughput target is stated two ways in the founding doc
> MVP benchmark: 1,000 episodes <30 min on **4× A100** ([[8-Week Sprint Plan]] W2). Platform KPI: 1,000 episodes <30 min on **8× A100**. Treat the 4-GPU figure as the demo bar and the 8-GPU figure as the production SLO; resolve wording in the next [[Decision Log]] entry.

## North-star framing

**Validation runs completed** is the north star: it compounds with the [[Why Now (2026)]] shipping cadence (every model update = one run = recurring spend, [[Business Model]]) and feeds the data flywheel ([[Moat]]).

| Funnel stage | Metric | Owner |
|---|---|---|
| Awareness | Discovery conversations (50 → 100) | Both ([[90-Day Roadmap]]) |
| Commitment | LOIs (3 → 10) → paying (2 → 15) | Founder 1 |
| Usage | Runs, episodes/run, scenarios | Founder 2 |
| Retention | ARR $10K → $200K; NRR >120% target ([[Unit Economics]]) | Both |

Links: [[MVP Success Metrics]] · [[Financial Projections]] · [[Go-to-Market]] · [[Build Status]] · [[Home]]
