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

Paste-ready answers for the W27 form, in founder voice (**we/our**). YC fields are short — **use the Short version**; the Expanded version is interview prep and long-form backup. All numbers trace to [[Key Figures]]; replace every `[...]` before submitting.

> [!warning] Placeholder discipline
> Every `[X]` is a **measured** number, not an estimate. If it is not in [[Build Status]], a `Discovery Call` note, or a signed LOI, it does not go in the application. Overclaiming the sim backend is the fastest way to lose a technical partner — we state plainly that the Isaac Lab port is in flight.

## Q1 · What does your company do?

**Short (paste-ready)**
> We're building **GitHub Actions for robots** — continuous validation, regression testing, and safety scoring for robot foundation models before they touch the real world. A team pushes a model checkpoint; we run thousands of parallel physics-accurate simulation episodes and return a safety-and-success scorecard that **approves or blocks the deploy**.

**Expanded**
> Robot foundation model companies ship policy updates weekly, but they validate them with hand-built test scenes, small teleop trials, and a senior engineer's judgment call. We productized the missing layer between "checkpoint" and "fleet": submit via CLI or GitHub Action, get 1,000–100,000 parallel sim episodes, a regression diff against the previous checkpoint with 95% confidence intervals, a 0–100 composite safety score, and a deployment gate that can fail the build. Pure software, NVIDIA-native, no hardware.

## Q2 · How did you get an idea for this?

**Short**
> We both come from AI/ML and kept watching robot labs raise enormous rounds — Physical Intelligence at **$5.6B**, Skild AI at **$14–15B** — while the validation step underneath those updates was still spreadsheets and "looks good, ship it." Software had exactly this problem in 2010, before CI/CD. We think robots are at that inflection, and the first major deployment failure will make the layer mandatory.

**Expanded**
> Two converging observations. (1) The cadence changed: VLA models like pi0 and GR00T N1 are fine-tuned per-customer and per-task, so validation frequency now scales with *model update* frequency, not hardware refresh cycles — that is what makes it a CI/CD problem rather than a certification problem. (2) The infrastructure changed: Isaac Sim is now open-source and Isaac Lab gives GPU-parallel environments, so the substrate is free while the productized layer above it is empty. We wrote the 19-section blueprint, scored seven frontier bets against it (**8.7/10** for this one, highest of seven), then started building and calling labs the same week.

## Q3 · What, if anything, have you done so far?

**Short**
> Working code and a running pipeline, not slides: the ValidSim platform scaffold (FastAPI API, Typer CLI, config models, episode runner, adversarial scenario generator, evaluation/safety/regression/scorecard engines) with a **126-test pytest suite green on continuous CI** — GitHub Actions on every push/PR, a nightly deep-validation suite, and an hourly build agent that republishes status into our operating vault. Two shippable GitHub Actions (deploy gate + scorecard) and a Dockerized control plane exist today. Live: **[X]** discovery conversations logged, **[X]** LOIs, **[X]** episodes/run, **[X]** min wall-clock for 5,000 episodes on 4× A100.

**Expanded (be honest about the gap)**
> - **Built:** end-to-end MVP pipeline — submit checkpoint → simulate → adversarial scenarios → evaluate → regression diff vs. baseline (bootstrap CI) → composite scorecard (40% success / 30% safety / 20% robustness / 10% regression) → APPROVE/BLOCK.
> - **Verified:** 126 tests passing, latest run in [[Build Status]] (auto-generated, not hand-edited).
> - **Honest limitation:** the simulation backend is currently a deterministic **mock** Isaac interface. Porting to Isaac Sim 4.x / Isaac Lab on real A100s is the next milestone and is the reason we want DGX credits ([[NVIDIA Inception]]).
> - **Operating system:** a **55-note** Obsidian vault linking strategy → product → engineering → GTM → fundraising, with a single source-of-truth figures table and CI-published build status. Written in the first week of execution — our answer to "how fast do you move."
> - **Traction:** **[X]/25** discovery calls, **[X]/2–3** LOIs, demo video **[recorded / scheduled week of [date]]**, submission target week of **Oct 26, 2026** ([[YC Countdown]]).

## Q4 · How much revenue / how many hard commitments?

**Short**
> Pre-revenue by design. Hard commitments: **[X]** written LOIs from **[Company A / Company B / Company C]** with named success criteria, **[X]** paid pilot(s) at **$[X]**, and **[X]** design partners on free runs (≤50/month) converting to the **$2,000/mo** Team tier at pilot end. **[X]** conversations logged toward the 25 target.

**Expanded**
> We are deliberately not on paid contracts yet — two founders shipping an 8-week MVP cannot also run procurement. What we are collecting instead is commitment that costs the customer something: written LOIs naming the company, the buyer, and the success criteria that trigger purchase (a free pilot with no success criteria is not a commitment). Pipeline: **[X]** companies at LOI stage, **[X]** at demo stage, **[X]** at first-call stage. First revenue is projected at Month 6 (**2 paying customers, $10K ARR**, [[KPIs]]), with blended ARPU **$3,000/mo** and LTV **$72,000** at 24-month retention ([[Unit Economics]]).

## Q5 · Why will this win? What's the unique insight?

**Short**
> **The cost of a bad deploy is physical.** A dropped part is **$50K**, line downtime is **$10K–$100K/hr**, and an injured coworker is a liability event — you cannot roll back a robot. So validation must be **massively parallel** (you cannot run 10,000 scenarios on one physical robot), **physics-accurate** (a game engine isn't enough), and **audit-ready** (insurers and regulators will demand the evidence). Nobody has productized that combination.

**Expanded**
> Three consequences follow, and each is a moat:
> 1. **Parallel → GPU economics.** Fleet-scale testing only exists in simulation, which makes this a compute-orchestration business with a real cost structure and a data flywheel — every run improves our failure taxonomy and scenario quality.
> 2. **Physics → substrate choice.** PhysX 5 contact/force fidelity is why we built NVIDIA-native instead of on a game engine, and why "isn't this just Isaac Sim?" is the wrong objection: Isaac ships the platform, not the scorecard, the gate, or the audit trail.
> 3. **Audit → the terminal moat.** Once insurers (Munich Re, Swiss Re) and certifiers (UL, TÜV) adopt our scorecard format, it is the de-facto standard — "Very High" durability, and the one thing a fast-follower cannot buy ([[Moat]]).
> The wedge is narrow on purpose: one VLA stack, one benchmark task (tabletop bin picking, Franka Panda), 1,000–5,000 episodes, 100 adversarial scenarios from a 12-category taxonomy, scorecard in under 30 minutes. A real scorecard for one external engineer beats a broad platform of slides.

## Q6 · Why are you the right team?

**Short**
> Two AI/ML founders whose skills are exactly the product's two halves: **Founder 1 [name]** on simulation and evaluation (PyTorch, Isaac Sim/Lab, RL/IL, VLA models, domain randomization) and **Founder 2 [name]** on the platform (Kubernetes, FastAPI, Next.js, GitHub Actions, cloud infra). The role split mirrors the five-layer architecture, so every sprint week has exactly one owner per workstream — which is how a two-person team got a 97-test CI-green scaffold and a full blueprint in week one.

**Expanded**
> - **Feasibility 8/10 for two people:** pure software, no hardware/manufacturing/supply chain; the stack is a shopping list of open-source NVIDIA components, not an R&D program.
> - **Speed claims calibrated to this team:** 8-week MVP, weekly demo, **10 discovery calls/week** in Phase 1 ([[Go-to-Market]]).
> - **We ship in public:** open-source CLI + GitHub Actions plugin and published benchmark results on GR00T/pi0/OpenVLA are Phase-2 marketing *and* YC evidence.
> - **Gaps we name:** no dedicated frontend hire (Founder 2 scaffolds the dashboard), no enterprise sales experience (developer-led motion first), no academic robotics credibility (advisory seat targeted at a CMU/MIT/Stanford professor and an ex-NVIDIA Isaac engineer post-funding) ([[Founding Team]], [[Hiring Plan]]).
> - **We are not precious about the answer:** ~50% of accepted companies applied more than once, and ~1–2% get in. If we're rejected we reapply to S27 with more LOIs, Inception still active, pre-seed timing unchanged.

## Q7 · Technical moat & traction plan (long-form / interview)

**Moat stack** ([[Moat]]) — first-mover (**12–18 months**, decaying) buys time to build integration depth and NVIDIA lock-in, which buy run volume for the data flywheel, which produces the benchmark corpus that makes the scorecard the format insurers trust.

| Moat | Our mechanism | Durability |
|---|---|---|
| First-mover | First productized sim-based CI/CD for robots | 12–18 months |
| NVIDIA ecosystem | Native Isaac Sim/Lab, PhysX 5, Omniverse, Cosmos, DGX Cloud | High |
| Data flywheel | Every run labels failures → better taxonomy → better scenarios | Compounding |
| Compliance standard | ISO 10218/13482-aligned scorecard schema from day one | **Very High** |
| Integration depth | GitHub Actions, ROS 2 HIL, Omniverse connector, model registry | Medium-High |
| Network effects | More teams → more failure data → better detection | Compounding |

**Traction plan to submission (week of Oct 26)** — the four evidence pieces a W27 reviewer weighs ([[YC Countdown]]):

| Evidence | Target | Owner | Status |
|---|---|---|---|
| Discovery conversations | **25** named Tier-1 labs | Both | **[X]/25** |
| LOIs / paid pilots | **2–3** written, with success criteria | Founder 1 | **[X]/3** |
| Demo video | 1 min, both founders on camera, end-to-end run | Both | **[X]** |
| Repeatable metric | 5,000 episodes < 30 min on 4× A100 | Founder 2 | **[X]** |
| Public artifact | benchmark results or open-source CLI | Both | **[X]** |
| Entity + website | Delaware C-Corp, validsim.com live | Founder 2 | **[X]** |
| Inception | application submitted (signals technical validation) | Founder 2 | **[X]** |

**The two hard questions we will be asked, with answers ready:** *"NVIDIA will build this"* → they ship the substrate, not the workflow; our Inception relationship and ecosystem positioning are the hedge, and the compliance standard is the part they cannot fast-follow without regulator patience ([[Risk Register]] #2). *"Labs will build in-house"* → priced against the **$200K–$500K** (2–4 engineers × 6 months) they spend today, and cross-lab benchmarks are structurally impossible for any single lab's internal tool ([[Competitive Landscape]]).

Links: [[YC Application]] · [[YC Countdown]] · [[NVIDIA Inception Application]] · [[Pitch Deck Outline]] · [[Investor Narrative]] · [[Home]]
