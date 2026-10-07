---
tags:
  - fundraising
  - yc
  - copy
status: draft
created: 2026-09-18
area: "07 - Fundraising"
---

# ✍️ YC Application Answers (Drafted)

Draft answers for the W27 form, in founder voice (**we/our**). YC fields are short — use the Short version only after verification; Expanded answers are interview prep. All numbers trace to [[Key Figures]]; replace every placeholder before submitting.

> [!warning] Verify before external send — 2026-09-21
> The current code implements a deterministic mock plus an optional HTTP Isaac worker client, not the real GPU workload described below. Test counts, discovery/LOI/company names, benchmark timings, incorporation, website, Inception status, published Actions, and all placeholders remain unverified unless a dated artifact is attached. Do not treat the answer blocks as paste-ready without that verification.

> [!warning] Placeholder discipline
> Placeholders are **not** measurements. A number belongs in the application only when [[Build Status]] or a dated external artifact supports it. The Isaac Lab port is planned, not “in flight” unless a tracked, active deployment proves that.

## Q1 · What does your company do?

**Short (draft; verify before submission)**
> We're building **GitHub Actions for robots** — continuous validation, regression testing, and safety scoring for robot foundation models before they touch the real world. The current prototype accepts a checkpoint, runs deterministic mock episodes, and returns a scorecard whose `APPROVE`/`BLOCK` verdict fails CI through `validsim gate`. Real Isaac/GPU execution is the next milestone.

**Expanded**
> Robot foundation model companies ship policy updates weekly, but they validate them with hand-built test scenes and judgment calls. Our current prototype covers the missing layer from checkpoint to scorecard: CLI/API, deterministic mock simulation, regression statistics, a 0–100 composite score, and an APPROVE/BLOCK CI gate. Real Isaac/GPU execution, published hosted Actions, and immutable compliance trails are the next milestones, not current claims.

## Q2 · How did you get an idea for this?

**Short**
> We both come from AI/ML and kept watching robot labs raise enormous rounds — Physical Intelligence at **$5.6B**, Skild AI at **$14–15B** — while the validation step underneath those updates was still spreadsheets and "looks good, ship it." Software had exactly this problem in 2010, before CI/CD. We think robots are at that inflection, and the first major deployment failure will make the layer mandatory.

**Expanded**
> Two converging observations. (1) VLA model updates make validation frequency scale with model cadence, not hardware refresh cycles — a CI/CD problem. (2) Isaac Sim/Lab lowers the substrate barrier, but the productized validation workflow is still incomplete. We wrote and internally scored the blueprint; the 8.7/10 and “calling labs the same week” are internal/historical claims that require their source notes before external use.

## Q3 · What, if anything, have you done so far?

**Short**
> Working code, not slides: the ValidSim scaffold (FastAPI API, Typer CLI, config models, episode runner, scenario/evaluation/safety/regression/scorecard engines) with the latest verified suite status in [[Build Status]]. GitHub Actions runs on push/PR; no hourly build agent is verified. Two **local** composite Actions and a Dockerized control plane exist; the published `validsim/*@v1` actions do not. Real Isaac/GPU execution and every traction/benchmark placeholder still require dated evidence.

**Expanded (be honest about the gap)**
> - **Built:** an end-to-end deterministic mock pipeline — submit → mock simulation/scenarios → evaluate → baseline regression/CI → composite scorecard → stored APPROVE/BLOCK. Real Isaac execution remains the next milestone.
> - **Verified:** Use the latest generated row in [[Build Status]]; do not quote a historical test count from this draft.
> - **Honest limitation:** the simulation backend is currently deterministic mock; an HTTP Isaac worker adapter exists, but no real worker image/deployment is in this repository. Porting to Isaac Sim/Lab on real A100s is planned, not shipped.
> - **Operating system:** an Obsidian vault linking strategy → product → engineering → GTM → fundraising, with a figures table and generated build status. Do not claim the note count without a current count.
> - **Traction:** **[VERIFY]/25 discovery calls**, **[VERIFY]/2–3 LOIs**, demo video **[VERIFY recorded / scheduled]**, and a confirmed submission target ([[YC Countdown]]).

## Q4 · How much revenue / how many hard commitments?

**Short**
> Pre-revenue by design. **Verify before submission:** hard commitments must be listed as **[VERIFY] written LOIs from [NAMED COMPANIES]** with success criteria, **[VERIFY] paid pilot(s) at [AMOUNT]**, **[VERIFY] design partners**, and **[VERIFY] conversations**. If none is evidenced, say “pre-revenue, seeking first design partners”; do not fill these placeholders.

**Expanded**
> We are pre-revenue. **Verify** the company/buyer/success criteria for any LOI or pilot; do not treat a checklist as evidence. Month-6 ARR, ARPU and LTV are planning assumptions from [[KPIs]] / [[Unit Economics]], not achieved results.

## Q5 · Why will this win? What's the unique insight?

**Short**
> **The cost of a bad deploy is physical.** We target parallel, physics-accurate, audit-ready validation, but the current mock does not establish physics fidelity and the audit trail is not implemented. Our shipped differentiator today is the scorecard/regression/gate workflow; the remaining capabilities are roadmap.

**Expanded**
> Three consequences follow, and each is a moat:
> 1. **Parallel → GPU economics.** Fleet-scale testing only exists in simulation, which makes this a compute-orchestration business with a real cost structure and a data flywheel — every run improves our failure taxonomy and scenario quality.
> 2. **Physics → substrate choice.** PhysX-based fidelity is the reason to target Isaac for real GPU runs; the current mock does not establish that fidelity. Isaac is not a deployment gate, and our immutable audit/compliance trail is still roadmap work.
> 3. **Audit → the intended terminal moat.** Insurer/certifier adoption is a long-term hypothesis, not present evidence; the current product has no immutable audit trail.
> The wedge is narrow on purpose: one VLA stack, one benchmark task (tabletop bin picking, Franka Panda), 1,000–5,000 episodes, 100 scenarios from a 12-category taxonomy, and a scorecard in under 30 minutes are targets. No external-engineer scorecard or benchmark is verified in the repository.

## Q6 · Why are you the right team?

**Short**
> Two AI/ML founders whose skills are exactly the product's two halves: **Founder 1 [name]** on simulation and evaluation (PyTorch, Isaac Sim/Lab, RL/IL, VLA models, domain randomization) and **Founder 2 [name]** on the platform (FastAPI/Typer, static dashboard, GitHub Actions, Docker Compose, cloud infra; Kubernetes/Next.js are target skills). The role split mirrors the target architecture; do not use a historical test count as evidence without re-checking [[Build Status]].

**Expanded**
> - **Feasibility 8/10** is an internal planning score, not investor evidence.
> - **Speed claims** (8-week MVP, weekly demos, discovery calls) are targets until dated artifacts exist.
> - **Open-source CLI/Actions and published benchmarks** are Phase-2 plans; the current package is proprietary and the Actions are local.
> - **Founder names, biographies, academic credibility and advisory status must be verified before submission** ([[Founding Team]], [[Hiring Plan]]).
> - If rejected, reapply with evidence; do not state that Inception remains active without confirmation.

## Q7 · Technical moat & traction plan (long-form / interview)

**Target moat stack** ([[Moat]]): the 12–18 month first-mover window, NVIDIA ecosystem, data flywheel, compliance standard and integrations are hypotheses. The current repository proves the scoring/gate workflow only; Isaac depth, published integrations, public benchmarks and insurer adoption remain targets.

| Moat | Our mechanism | Durability |
|---|---|---|
| First-mover | First productized sim-based CI/CD for robots | 12–18 months |
| NVIDIA ecosystem | Planned real Isaac/Lab + PhysX workload; current mock only | Unverified |
| Data flywheel | Structured failure taxonomy exists; multi-customer compounding is unproven | Hypothesis |
| Compliance standard | ISO-aligned schema/evidence target; no insurer adoption verified | Hypothesis |
| Integration depth | Local GitHub Actions ship; hosted action, ROS 2 HIL, Omniverse/registry are planned | Mixed |
| Network effects | No verified multi-team data network | Hypothesis |

**Traction plan to submission (week of Oct 26)** — the four evidence pieces a W27 reviewer weighs ([[YC Countdown]]):

| Evidence | Target | Owner | Status |
|---|---|---|---|
| Discovery conversations | **25** named Tier-1 labs | Both | **[verify]/25** |
| LOIs / paid pilots | **2–3** written, with success criteria | Founder 1 | **[verify]/3** |
| Demo video | 1 min, both founders on camera, end-to-end run | Both | **[verify]** |
| Repeatable metric | 5,000 episodes < 30 min on 4× A100 | Founder 2 | **[verify]** |
| Public artifact | benchmark results or open-source CLI | Both | **[verify]** |
| Entity + website | Delaware C-Corp, validsim.com live | Founder 2 | **[verify]** |
| Inception | application submitted (signals technical validation) | Founder 2 | **[verify]** |

**The two hard questions:** NVIDIA may build parts of the workflow, so differentiation must come from verified integration and evidence. Labs may build internally; the **$200K–$500K** build estimate and cross-lab advantage are planning assumptions, not observed customer validation ([[Risk Register]], [[Competitive Landscape]]).

Links: [[YC Application]] · [[YC Countdown]] · [[NVIDIA Inception Application]] · [[Pitch Deck Outline]] · [[Investor Narrative]] · [[Home]]
