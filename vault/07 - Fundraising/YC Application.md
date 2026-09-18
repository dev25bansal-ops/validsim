---
tags:
  - fundraising
  - yc
status: active
created: 2026-09-18
area: "07 - Fundraising"
---

# 🎓 Y Combinator W27 Application

§13.1 + §14.2. Deal terms: **$500K = $125K for 7% + $375K uncapped MFN**. Acceptance **~1–2%** from 30,000+ applications per batch; **~50% of accepted companies applied more than once**.

## Program facts

| Field | Detail |
|---|---|
| Deadline | **November 2, 2026, 8pm PT** |
| Decision by | **December 11, 2026** |
| Batch starts | Early January 2027 |
| Investment | $500K ($125K for 7% + $375K uncapped MFN) |
| Acceptance rate | ~1–2% (30,000+ applications per batch) |
| Key stat | ~50% of accepted companies applied more than once |

## The 10-second pitch (verbatim)

> "We're building **GitHub Actions for robots**. Robot foundation model companies ship policy updates weekly but validate them with hand-built test scenes and judgment calls. We built a CI/CD pipeline that runs thousands of parallel simulation episodes, catches regressions, and generates a safety scorecard — so teams can deploy with statistical confidence instead of prayer."

## The unique insight (verbatim)

> "Robot validation is fundamentally different from software testing because **the cost of a bad deploy is physical** — a dropped part, a damaged product line, an injured coworker. This means validation must be **massively parallel** (you can't test 10,000 scenarios on one physical robot), **physics-accurate** (a game engine isn't enough), and produce **audit-ready evidence** (insurers and regulators will demand it). Nobody has productized this layer."

Backed by: [[Problem Statement]], [[Why Now (2026)]].

## Application strategy

- **Lead with the problem:** "Robots ship code updates but have no CI/CD"
- **Show the MVP demo video** — 1 minute, both founders on camera
- **Traction:** 25+ discovery conversations, 2–3 LOIs, working demo
- **Narrative:** "GitHub Actions for robots" — instantly understandable
- **Submit in Week 10 (NOT deadline day)** — earlier applications get earlier interview slots

> [!warning] Week-10 reality check
> From a Sep 18 start, "Week 10" lands after Nov 2. Practical plan: submit **week of Oct 26** with the best demo that exists then, per [[YC Countdown]] timeline-tension analysis. "Submitting at 7:59pm on Nov 2 with unedited video" is the named failure mode in [[90-Day Roadmap]].

## Traction targets before applying (all four, per [[YC Countdown]])

- ✅ **25 discovery conversations** with named companies (Tier-1 labs, [[Buyer Tiers]])
- ✅ **2–3 written LOIs or paid pilots** (success criteria attached, [[Go-to-Market]] Phase 1)
- ✅ **Working demo video** of the MVP (script = the three flows in [[Core User Flows]])
- ✅ **One metric measured and repeatable** (e.g. 5,000 episodes < 30 min on 4× A100, [[MVP Success Metrics]])

## Demo video storyboard (60 seconds)

| Sec | Shot | Proof |
|---|---|---|
| 0–8 | Founder 1: problem + buyer monologue ("3 test scenes and a prayer") | [[Problem Statement]] |
| 8–15 | `git push` → GitHub Action fires | [[GitHub Actions Integration]] |
| 15–30 | Time-lapse: 5,000 episodes, 100 adversarial scenarios | [[Data Flow]] |
| 30–45 | Scorecard: 87.3, 2 regressions, one episode replay of the failure | [[Scorecard UX]] |
| 45–55 | Gate: BLOCK on regressed checkpoint → build goes red | [[Product Principles]] #4 |
| 55–60 | Founders + one-liner + traction numbers | [[Unit Economics]] |

## Interview prep (Weeks 10–12)

- Rehearse **2×/week** (roadmap Phase 4); alternate who answers market vs. technical
- Known hard questions: NVIDIA platform risk ([[Risk Register]] #2), in-house build objection ([[Competitive Landscape]]), humanoid timing risk (#3)
- YC fit self-assessment: **9/10** — "devtools for the defining technology wave of the decade" ([[Strategic Advantages]])

Links: [[YC Countdown]] · [[Investor Narrative]] · [[NVIDIA Inception]] · [[Funding Plan]] · [[Home]]
