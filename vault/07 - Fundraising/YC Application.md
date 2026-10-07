---
tags:
  - fundraising
  - yc
status: active
created: 2026-09-18
area: "07 - Fundraising"
---

# 🎓 Y Combinator W27 Application

§13.1 + §14.2. Planning assumptions for terms/timing: **$500K = $125K for 7% + $375K uncapped MFN**. Acceptance **~1–2%** from 30,000+ applications per batch; **~50% of accepted companies applied more than once**.

> [!warning] Verify before external send — 2026-09-21
> This is a planning draft, not evidence of traction or acceptance. No repository artifact verifies the 25+ conversations, LOIs/pilots, demo video, 4× A100 benchmark, incorporation/website, or accelerator/program status claimed here. Keep those items unchecked until a dated artifact exists; the current simulation backend is the deterministic mock described in [[YC Application Answers]].

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

> "We're building **GitHub Actions for robots**. Robot foundation model companies ship policy updates weekly but validate them with hand-built scenes and judgment calls. Our current prototype runs deterministic mock episodes, detects regressions, and produces a CI-gate scorecard; real Isaac/GPU execution is the next build milestone."

## The unique insight (verbatim)

> "Robot validation is different because **the cost of a bad deploy is physical**. The long-term product must run many parallel, physics-accurate scenarios and retain audit-ready evidence. Our current prototype proves the scorecard/regression/gate workflow; parallel Isaac execution and immutable evidence remain milestones."

Backed by: [[Problem Statement]], [[Why Now (2026)]].

## Application strategy

- **Lead with the problem:** "Robots ship code updates but have no CI/CD"
- **Show the MVP demo video** — 1 minute, both founders on camera
- **Traction:** targets are 25+ discovery conversations, 2–3 LOIs and a working demo; all require verification artifacts
- **Narrative:** "GitHub Actions for robots" — an analogy/target positioning, not proof of a new or empty market category
- **Submit in Week 10 (NOT deadline day)** — earlier applications get earlier interview slots

> [!warning] Week-10 reality check
> From a Sep 18 start, "Week 10" lands after Nov 2. Practical plan: submit **week of Oct 26** with the best demo that exists then, per [[YC Countdown]] timeline-tension analysis. "Submitting at 7:59pm on Nov 2 with unedited video" is the named failure mode in [[90-Day Roadmap]].

## Traction targets before applying (all four, per [[YC Countdown]])

- [ ] **25 discovery conversations** with named companies (Tier-1 labs, [[Buyer Tiers]]) — verify with dated call notes
- [ ] **2–3 written LOIs or paid pilots** (success criteria attached, [[Go-to-Market]] Phase 1) — attach agreements
- [ ] **Working demo video** of the MVP (script = the three flows in [[Core User Flows]]) — record against a checked-in build
- [ ] **One metric measured and repeatable** (e.g. 5,000 episodes < 30 min on 4× A100, [[MVP Success Metrics]]) — attach benchmark output

## Demo video storyboard (60 seconds)

| Sec | Shot | Proof |
|---|---|---|
| 0–8 | Founder 1: problem + buyer monologue ("3 test scenes and a prayer") | [[Problem Statement]] |
| 8–15 | `git push` → local composite Action fires | [[GitHub Actions Integration]] |
| 15–30 | Time-lapse of the actual checked-in run (state mock vs. real Isaac clearly) | [[Data Flow]] |
| 30–45 | Actual scorecard and measured regression result; no invented episode replay | [[Scorecard UX]] |
| 45–55 | Gate: BLOCK on regressed checkpoint → build goes red | [[Product Principles]] #4 |
| 55–60 | Founders + one-liner + **verified** traction numbers | [[Unit Economics]] |

## Interview prep (Weeks 10–12)

- Rehearse **2×/week** (roadmap Phase 4); alternate who answers market vs. technical
- Known hard questions: NVIDIA platform risk ([[Risk Register]] #2), in-house build objection ([[Competitive Landscape]]), humanoid timing risk (#3)
- YC fit self-assessment: **9/10** — "devtools for the defining technology wave of the decade" ([[Strategic Advantages]])

Links: [[YC Countdown]] · [[Investor Narrative]] · [[NVIDIA Inception]] · [[Funding Plan]] · [[Home]]
