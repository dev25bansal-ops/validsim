---
tags:
  - execution
  - sprint
  - mvp
status: active
created: 2026-09-18
area: "05 - Execution"
---

# 🏃 8-Week Sprint Plan — MVP Build

Owners: **Founder 1** = ML/Robotics Lead, **Founder 2** = Platform/Infra Lead ([[Founding Team]]). Scope: [[MVP Scope]]; exclusions: [[MVP Non-Goals]]; acceptance bar: [[MVP Success Metrics]]. Overlay with sales cadence in [[90-Day Roadmap]] and [[YC Countdown]].

## Week 1 — Foundation & Environment Setup

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Install Isaac Sim 4.x + Isaac Lab on DGX Cloud | Both | Working Isaac Sim environment |
| 2–3 | Select open VLA model (GR00T N1 or pi0) | Founder 1 | Model downloaded, inference working |
| 3–4 | Define benchmark task: tabletop bin picking | Both | Task specification document |
| 4–5 | Set up Isaac Lab environment for the task | Founder 1 | Runnable simulation scene |
| 5 | Set up project repo, Docker, CI/CD | Founder 2 | GitHub repo, Dockerfile |
| 5 | Incorporate Delaware C-Corp, register domain | Both | Legal entity + website ([[Corporate Structure]]) |

**Exit Criteria:** Isaac Sim running with one robot + one task. Model inference working. Repo initialized.

## Week 2 — Episode Runner & Domain Randomization

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Build parallel episode runner (1,000 episodes) | Founder 1 | Script: run N episodes, collect results |
| 2–3 | Implement domain randomization (lighting, texture, physics) | Founder 1 | Randomization module (6+ axes) |
| 3–4 | Episode recording (video, joint states, forces) | Founder 1 | HDF5 + MP4 recording pipeline |
| 4–5 | Build basic API endpoint: POST /validate | Founder 2 | FastAPI endpoint |
| 5 | Test: 1,000 episodes in <30 min on 4× A100 | Both | Performance benchmark |

**Exit Criteria:** 1,000 episodes run in parallel with domain randomization. Results recorded.

## Week 3 — Evaluation Engine

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Success rate calculator (per task, per scenario) | Founder 1 | Evaluation module |
| 2–3 | Failure taxonomy classifier (rule-based) | Founder 1 | Failure classification module |
| 3–4 | Safety metrics: collision frequency, force limits, proximity | Founder 1 | Safety score module (0–100) |
| 4–5 | Statistical significance testing (bootstrap, 95% CI) | Founder 1 | Confidence interval calculator |
| 5 | Integration test: full episode → evaluation pipeline | Both | End-to-end test passing |

**Exit Criteria:** System produces success rate, failure taxonomy, safety score with confidence intervals.

## Week 4 — LLM Adversarial Scenario Generator

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Design adversarial scenario taxonomy (12 categories) | Both | Taxonomy document ([[Module Specs]] M3) |
| 2–3 | Build LLM prompt pipeline: task → scenario JSON | Founder 1 | Scenario generator (GPT-4o) |
| 3–4 | Convert scenario JSON → Isaac Sim scene modifications | Founder 1 | Automated scene editing |
| 4–5 | Generate 100 adversarial scenarios for bin picking | Both | 100 scenario configs |
| 5 | Run adversarial episodes + evaluate | Both | Adversarial results integrated |

**Exit Criteria:** 100 LLM-generated adversarial scenarios execute and produce evaluation results.

## Week 5 — Regression Detection

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Build regression delta engine (compare N vs N-1) | Founder 1 | Regression detection module |
| 2–3 | Store validation results in PostgreSQL + TimescaleDB | Founder 2 | Database schema, migrations |
| 3–4 | Regression timeline visualization (backend) | Founder 2 | API endpoint: GET /regressions |
| 4–5 | Test: submit v41 and v42, detect regression | Both | Regression detection working |
| 5 | Build comparison endpoint: POST /compare | Founder 2 | Comparison API |

**Exit Criteria:** System correctly detects regressions between two checkpoints with statistical significance.

## Week 6 — API Layer & CLI Tool

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Complete REST API: all endpoints | Founder 2 | Full API with OpenAPI docs ([[API Design]]) |
| 2–3 | Build CLI tool: `validsim run`, `validsim status` | Founder 2 | Installable CLI (pip) ([[CLI Design]]) |
| 3–4 | GitHub Actions plugin (prototype) | Founder 2 | YAML workflow integration ([[GitHub Actions Integration]]) |
| 4–5 | Webhook dispatcher (Slack notification) | Founder 2 | Slack integration |
| 5 | API documentation | Both | docs.validsim.com |

**Exit Criteria:** Developer can submit checkpoint via CLI or GitHub Actions, receive Slack notification.

## Week 7 — Dashboard

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | Next.js dashboard scaffold + auth | Founder 2 | Login, project setup |
| 2–3 | Scorecard view: composite score, success, safety | Founder 2 | Scorecard page ([[Scorecard UX]]) |
| 3–4 | Failure taxonomy visualization (treemap + bar chart) | Founder 2 | Failure analysis page |
| 4–5 | Regression timeline chart (Recharts) | Founder 2 | Timeline page |
| 5 | Episode replay viewer (video + metadata) | Founder 2 | Episode detail page |

**Exit Criteria:** Working dashboard showing scorecard, failures, regression timeline, episode replays.

## Week 8 — End-to-End Demo & Polish

| Day | Task | Owner | Deliverable |
|---|---|---|---|
| 1–2 | End-to-end test: submit → simulate → evaluate → dashboard → PDF | Both | Full pipeline working ([[Data Flow]]) |
| 2–3 | PDF scorecard generator (branded, professional) | Founder 2 | PDF export |
| 3 | Demo video recording (1-minute, both founders) | Both | Demo video for YC |
| 4 | Bug fixes, performance optimization | Both | Stable MVP |
| 5 | Write technical blog post | Both | Public artifact |

**Exit Criteria:** Complete end-to-end demo. Video recorded. Blog post published. MVP ready for design partners.

## Parallel workstreams (non-negotiable, from [[90-Day Roadmap]])

| Cadence | Task | Owner |
|---|---|---|
| W1–W2: 10 calls/wk; W3+: 5/wk | Discovery calls → [[Buyer Tiers]] targets ([[Go-to-Market]]) | Both |
| Weekly (Friday) | Internal demo — "momentum is the product" | Both |
| W1–W2 | Incorporate + NVIDIA Inception application | Founder 2 |
| W7–W9 | Convert warmest calls → 2–3 LOIs | Founder 1 |
| W10 (compressed → before Nov 2) | Record video, submit YC app | Both |

> [!warning] Known schedule conflict
> Sep 18 start + 8 weeks = Nov 13 > Nov 2 YC deadline. See resolution options in [[YC Countdown]]. Weekly exit-criteria review against this plan is the [[Product Principles]] operating rule.

## Security hardening — API & deployment

- [x] API auth — token middleware on all REST endpoints ([[API Design]])
- [x] CORS hardening — allow-list origins, no wildcard in prod
- [x] Webhook HMAC signing + retry — signed payloads, exponential backoff
- [x] Compose secret handling — secrets via env-file/secrets mount, none committed
- [x] Multi-stage Docker build — slim production images, build tools excluded

**Exit Criteria:** API surface is authenticated, CORS-restricted, webhook payloads are HMAC-signed with retry, and production images contain no secrets or build tooling.

### Next-week preview

- [ ] Rate limiting on public API endpoints ([[Module Specs]] API Gateway)
- [ ] Per-client API keys (issue, rotate, revoke) [[API Design]]
- [ ] GPU worker shadow-run vs. contract in `docs/isaac-worker.md` — validate job payload, result schema, and error semantics match the documented interface
- [ ] Pagination stress tests on list endpoints (large result sets, deep offsets)

Links: [[MVP Success Metrics]] · [[90-Day Roadmap]] · [[KPIs]] · [[Build Status]] · [[Home]]
