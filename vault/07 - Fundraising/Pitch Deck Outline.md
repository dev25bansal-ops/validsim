---
tags:
  - fundraising
  - pitch-deck
  - yc
status: draft
created: 2026-09-18
area: "07 - Fundraising"
---

# 🎬 Pitch Deck Outline — 10 Slides

Deck arc: Problem → Why Now → Solution → How → Market → Model → Traction → Competition → Team → Ask. Every figure below is owned by [[Key Figures]] — do not restate numbers in the deck that contradict it. Format: 16:9, one message per slide, ≤40 words of body copy per slide. Demo video lives between slides 3 and 4 (1 min, both founders on camera, per [[YC Countdown]]).

| # | Slide | Key message (one line) | Content | Figures to show | Visual |
|---|---|---|---|---|---|
| 1 | Title | **GitHub Actions for robots.** | ValidSim · one-liner · 2-founder AI/ML · confidential | — | Robot arm + CI pipeline glyph; logo |
| 2 | Problem | A bad robot deploy is physical — and nobody gates it. | Labs ship VLA updates weekly; validation = hand-built scenes + teleop + "looks good, ship it"; no regression detection, no safety score, no audit trail | $50K dropped component · $10K–$100K/hr downtime · $200K–$500K/lab bespoke tooling | 2010 software vs 2026 robotics split screen |
| 3 | Why Now | Five forces converged in 2026. | Capital explosion · weekly shipping cadence · humanoid deployments live · NVIDIA open-sourced the sim layer · compliance pressure emerging | $27.6B VC 2025 (+237%) · $18.8B H1 2026 · $3.92B/9 deals robot FMs · 10,000+ humanoids/yr by 2027 | Five converging arrows timeline |
| 4 | Solution / Demo | Submit a checkpoint. Get a scorecard. Deploy with confidence. | CLI/GitHub Action → 1K–100K parallel sim episodes → 100 adversarial scenarios → regression diff w/ 95% CI → composite score → APPROVE/BLOCK gate | Composite 87.3 example · <30 min for 5,000 episodes | The actual scorecard UI (see [[Scorecard UX]]) + QR to demo |
| 5 | How it works | Physics-accurate, statistically rigorous, NVIDIA-native. | 5-layer architecture: ingestion/orchestration → simulation (Isaac Sim/Lab, PhysX 5, domain randomization) → LLM adversarial generator (12 categories) → evaluation/scoring → reporting/audit | 1,000–100,000 episodes/run · 12 scenario categories · 40/30/20/10 composite weights | Pipeline diagram from [[Solution Architecture]] |
| 6 | Market | The robot CI/CD market is $0 today — like CI/CD in 2010. | TAM/SAM/SOM by buyer tier; every deployed robot = recurring validation spend | $300M–$640M validation TAM by 2033 · $40.5B humanoid market · SAM $15–80M (2026) → $300M–$1B+ (2030) · SOM Y1 $100K–$500K ARR | Concentric TAM/SAM/SOM + tier ladder |
| 7 | Business model | Recurring by construction: every model update = a validation run. | SaaS tiers + usage pricing + compute pass-through + enterprise compliance | Free/$2K/$8K/custom tiers · $75 per 5K-episode run · 70–80% GM · LTV $72K · LTV/CAC 5–14x · NRR >120% | Pricing table + unit-economics bar |
| 8 | Traction & GTM | Developer-led wedge → enterprise compliance tail. | 3 phases: design partners → open-source CLI + public benchmarks → enterprise/insurer partnerships | [X]/25 discovery calls · [X] LOIs · 97-test CI-green platform built in week 1 · targets: 500+ GH stars, 10 paying teams (M9) | Roadmap chevrons with dated milestones |
| 9 | Competition | The pre-deployment × sim-based quadrant is empty. | 2×2: pre/post-deployment vs sim/real; Formant/Viam/InOrbit = post-deployment ops; MLflow/W&B = no physics; in-house = not productized; UL/TÜV = partners | Threat table from [[Competitive Landscape]] | 2×2 map with "★ US ★" in the empty quadrant |
| 10 | Team & Ask | Two ML founders, exactly the two halves of the product. | Founder 1 sim/ML · Founder 2 platform/infra; hiring plan post-funding; **raising $500K (YC W27) / $1–2M pre-seed** | Use of funds: MVP on Isaac, 3–5 design partners, first hires | Team photos + 12-month milestone bar |

## Narrative rules

1. Open with the **2010-CI/CD analogy in 10 seconds** ([[YC Application Answers]] Q1); it is the whole pitch's load-bearing frame.
2. Slides 2–3 must make the problem feel *urgent*, not interesting — the first catastrophic field failure is 12–24 months out and will make this mandatory.
3. Slide 4 is the credibility slide: real product, real scorecard, not renders. Record the demo against the MVP in [[Build Status]].
4. Every traction number on slide 8 must be backed by a dated artifact (call log, LOI PDF, benchmark post) — reviewers ask.
5. Close on the ask with **one** number and **one** date: "$500K, YC W27, deadline Nov 2."

Links: [[YC Application]] · [[Investor Narrative]] · [[Key Figures]] · [[Competitive Landscape]] · [[Home]]
