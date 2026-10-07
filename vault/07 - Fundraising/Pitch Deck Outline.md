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

> [!warning] Verify before external send — 2026-09-21
> The current product is a deterministic mock with optional HTTP worker integration, static dashboard, reports, jobs, and local Actions. GPU episode counts/timings, public Action publication, traction, LOIs, public benchmarks, entity/website, and accelerator status are targets or unverified. Do not put `[verify]` placeholders or target architecture in an investor deck as shipped evidence.
>
> **Market-claim boundary:** “nobody/none/no productized layer,” category size and competitor-gap statements are hypotheses unless current [[Sources]] and discovery evidence support them.

| # | Slide | Key message (one line) | Content | Figures to show | Visual |
|---|---|---|---|---|---|
| 1 | Title | **GitHub Actions for robots.** | ValidSim · one-liner · 2-founder AI/ML · confidential | — | Robot arm + CI pipeline glyph; logo |
| 2 | Problem | A bad robot deploy is physical — and nobody gates it. | Labs ship VLA updates weekly; validation = hand-built scenes + teleop + "looks good, ship it"; no regression detection, no safety score, no audit trail | $50K dropped component · $10K–$100K/hr downtime · $200K–$500K/lab bespoke tooling | 2010 software vs 2026 robotics split screen |
| 3 | Why Now | Five forces converged in 2026. | Capital explosion · weekly shipping cadence · humanoid deployments live · NVIDIA open-sourced the sim layer · compliance pressure emerging | $27.6B VC 2025 (+237%) · $18.8B H1 2026 · $3.92B/9 deals robot FMs · 10,000+ humanoids/yr by 2027 | Five converging arrows timeline |
| 4 | Solution / Demo | Submit a checkpoint. Get a scorecard. Gate CI on the verdict. | Current demo: CLI/local Action → deterministic mock episodes → regression/CI statistics → composite APPROVE/BLOCK scorecard → static dashboard/PDF. Real Isaac/GPU and published Action are future milestones | Use only a measured score and attach run artifact; no unmeasured 5,000-episode timing | Actual scorecard UI + QR to demo |
| 5 | How it works | One shared engine today; NVIDIA-native cloud execution is the roadmap. | Show implemented mock/HTTP-worker boundary, Redis jobs, Postgres/SQLite stores, scoring, static reporting, then label K8s/Argo/Isaac/object storage/audit as planned | Verified composite weights; verified run count only | Mark target architecture as target |
| 6 | Market | The robot CI/CD market is $0 today — like CI/CD in 2010. | TAM/SAM/SOM by buyer tier; every deployed robot = recurring validation spend | $300M–$640M validation TAM by 2033 · $40.5B humanoid market · SAM $15–80M (2026) → $300M–$1B+ (2030) · SOM Y1 $100K–$500K ARR | Concentric TAM/SAM/SOM + tier ladder |
| 7 | Business model | Recurring by construction: every model update = a validation run. | SaaS tiers + usage pricing + compute pass-through + enterprise compliance | Free/$2K/$8K/custom tiers · $75 per 5K-episode run · 70–80% GM · LTV $72K · LTV/CAC 5–14x · NRR >120% | Pricing table + unit-economics bar |
| 8 | Traction & GTM | Developer-led wedge → enterprise compliance tail. | 3 phases: design partners → open-source CLI + public benchmarks → enterprise/insurer partnerships | [verify]/25 discovery calls · [verify] LOIs · current test count from [[Build Status]] · targets: 500+ GH stars, 10 paying teams (M9) | No historical 97-test claim without a dated log |
| 9 | Competition | The pre-deployment × sim-based quadrant is empty. | 2×2: pre/post-deployment vs sim/real; Formant/Viam/InOrbit = post-deployment ops; MLflow/W&B = no physics; in-house = not productized; UL/TÜV = partners | Threat table from [[Competitive Landscape]] | 2×2 map with "★ US ★" in the empty quadrant |
| 10 | Team & Ask | Two ML founders, with the current prototype split shown honestly. | Founder roles must be verified; raising target/terms are **planned**, not acceptance | Use of funds: Isaac port, measured design partners, first hires | Team photos + 12-month milestone bar |

## Narrative rules

1. Open with the **2010-CI/CD analogy in 10 seconds** ([[YC Application Answers]] Q1); it is the whole pitch's load-bearing frame.
2. Slides 2–3 must make the problem feel *urgent*, not interesting — the first catastrophic field failure is 12–24 months out and will make this mandatory.
3. Slide 4 is the credibility slide: real product, real scorecard, not renders. Record the demo against the checked-in MVP and disclose that simulation is currently the deterministic mock.
4. Every traction number on slide 8 must be backed by a dated artifact (call log, LOI PDF, benchmark post) — reviewers ask.
5. Close on the ask with **one** number and **one** date: "$500K, YC W27, deadline Nov 2."

Links: [[YC Application]] · [[Investor Narrative]] · [[Key Figures]] · [[Competitive Landscape]] · [[Home]]
