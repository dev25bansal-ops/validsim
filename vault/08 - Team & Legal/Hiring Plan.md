---
tags:
  - team
  - hiring
status: complete
created: 2026-09-18
area: "08 - Team & Legal"
---

# 🧑‍💼 Hiring Plan (Post-Funding)

§11.2–11.3. Six hires sequenced against the [[Funding Plan]] ladder — each seat exists because a specific growth phase breaks without it.

> [!info] What the shipped MVP changes (Sep 2026)
> The founding two have shipped a **1,230-test platform via agent-assisted parallel build** — mock/CPU backend, dashboard v0, two shippable GitHub Actions, nightly adversarial sweep, all green on CI ([[Engineering Momentum Log]], [[Build Status]]). The subagent fleet now absorbs scaffold, test, and prose work at roughly a 10-engineer cadence, so the founders' marginal hours and the first paychecks move to where agents *can't* help: **physical GPU infrastructure** and **distribution**. Hence hire #1 is a **GPU/Isaac infra engineer** (the `MockIsaacBackend` → `IsaacWorkerBackend` swap is hardware-blocked, not code-blocked — see `docs/isaac-worker.md` §4–5) and hire #2 is a **founding GTM engineer** — a developer-marketer who turns a shipped product into signups, replacing the standalone Dev-Advocate seat and pushing pure Frontend/UX to #4.

## First hires

| Hire # | Role | Timing | Salary Range |
|---|---|---|---|
| 1 | **GPU / Isaac Sim Infra Engineer** (DGX worker, PhysX/Omniverse) | Month 4–6 | **$160K–$210K** |
| 2 | **Founding GTM Engineer** (developer-marketer) | Month 5–7 | **$140K–$180K** |
| 3 | **ML Engineer** (evaluation, adversarial scale) | Month 7–9 | **$150K–$200K** |
| 4 | **Frontend/UX Engineer** (dashboard) | Month 9–12 | **$130K–$170K** |
| 5 | **Enterprise Sales** | Month 10–13 | **$120K–$150K + commission** |
| 6 | **DevOps / SRE** | Month 11–15 | **$140K–$180K** |

## Why each seat, in order

| Hire | Unblocks | Phase it serves |
|---|---|---|
| 1 · GPU/Isaac Infra | Stand up the DGX worker image + shadow runs (`docs/isaac-worker.md` §4–5) — the one seat agents can't fill: no GPU exists, so the mock→Isaac swap and the first *real* scorecards are hardware-blocked, not code-blocked | Phase 1 → design-partner load; retires a [[MVP Non-Goals]] row; unblocks [[MVP Success Metrics]] 5K-ep < 30 min |
| 2 · Founding GTM Eng | Product is shipped and green, so the binding constraint moves build → distribution: ships integration demos, GR00T/pi0/OpenVLA benchmark posts, Discord community, and the developer-led funnel *with code* | Phase 2: 500+ GitHub stars → signups (absorbs the old Dev-Advocate seat into revenue-facing engineering) |
| 3 · ML Engineer | Scale adversarial taxonomy beyond 12 categories; flywheel tooling ([[Moat]]) — deep eval science is where agent output still needs a human owner | Phase 2→3: 1,000 scenarios/run target ([[KPIs]]) |
| 4 · Frontend/UX | Dashboard beyond Founder 2's W7 scaffold; enterprise-demo polish — **de-scoped** because dashboard v0 already ships and the subagent fleet extends it; needed only once GTM pulls real signups | Phase 2 signups → 50+ |
| 5 · Enterprise Sales | 1–3 month pilot→contract cycles without founder bottleneck | Phase 3: $500K–$2M ARR |
| 6 · DevOps/SRE | 99.5% API uptime SLA as we leave demo mode ([[KPIs]]) | Enterprise SLA commitments ([[Pricing Tiers]]) |

**Payroll check:** hires 1–4 ≈ $580K–$760K loaded before benefits ⇒ pre-seed ($1M–$2M) funds "team to 4–5" only with founder frugality; seed ($3M–$5M) funds 5–6 + sales ([[Funding Plan]] uses row). The GPU-infra premium (hire #1) is deliberate — it buys the one capability the agent fleet can't: real silicon.

## Advisory board (target)

| Advisor | Background | Value |
|---|---|---|
| Robotics professor (CMU/MIT/Stanford) | Academic credibility | Technical validation, paper co-authorship |
| Ex-NVIDIA Isaac team member | Deep Isaac/Omniverse expertise | Technical guidance, NVIDIA relationship |
| YC alum (devtools/infra) | YC application/interview experience | Application coaching, network |
| Robotics industry operator | Fleet deployment experience | Customer insights, enterprise intros |

Advisor terms: **0.25–0.5% equity each, 2-year vesting** ([[Corporate Structure]]).

## Recruiting constraints

> [!warning] Sequence discipline
> 1. **No hires before funding** — the 8-week MVP is founder-hours only ([[Founding Team]] feasibility)
> 2. **Isaac talent is scarce and Tier-1 labs pay FM-company rates** — hire #1 competes with our own customers for candidates; the MIT/Stanford advisor network is the sourcing channel
> 3. Every hire must serve the application narrative first ([[90-Day Roadmap]] operating rule 2) — devtools investors read headcount order as strategy
> 4. **Agent leverage re-ranks the org chart** — seats that duplicate what the subagent fleet already ships (boilerplate frontend, long-form docs, test scaffolding) are deferred; only hardware-bound (GPU/Isaac) and relationship-bound (GTM, sales) work is headcount-justified post-MVP ([[Engineering Momentum Log]])

Links: [[Founding Team]] · [[Funding Plan]] · [[Corporate Structure]] · [[Go-to-Market]] · [[Home]]
