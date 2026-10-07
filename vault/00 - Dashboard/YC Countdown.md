---
tags:
  - dashboard
  - fundraising
  - yc
status: active
created: 2026-09-18
area: "00 - Dashboard"
---

# ⏳ YC Countdown — W27 Application Clock

> [!warning] Verify before external send — 2026-09-21
> The checklist and progress log are planning/history, not evidence. Traction, demo, public artifact, GPU benchmark, incorporation/website, and Inception status must be confirmed from dated artifacts before use. The repository does not prove any of those outcomes.

> [!important] The only deadline that matters
> **Y Combinator W27 application deadline: November 2, 2026, 8:00pm PT.**
> Decisions by **December 11, 2026** · Batch starts **early January 2027** · Acceptance rate **~1–2%** (30,000+ applications per batch) · Standard deal: **$500K ($125K for 7% + $375K uncapped MFN)**

## 📅 Key Dates

| Milestone | Date | Weeks from today (Sep 18, 2026) |
|---|---|---|
| Vault created / execution starts | Sep 18, 2026 | 0 |
| NVIDIA Inception application out | by Oct 2, 2026 (wk 1–2) | 2 |
| YC submission target (not deadline day!) | week of Oct 26, 2026 | ~5.5 |
| **YC W27 DEADLINE** | **Nov 2, 2026, 8pm PT** | **6.4** |
| Decision by | Dec 11, 2026 | 12 |
| W27 batch starts | Early Jan 2027 | ~15–16 |

> [!warning] Timeline tension (resolve this week)
> The [[8-Week Sprint Plan]] starting Sep 18 ends **Nov 13** — *after* the Nov 2 deadline. The source doc's "submit in Week 10" assumed an early-September start. Options: (a) compress sprints 7–8, (b) submit Oct 26 with a Week-6-state demo and update the application, or (c) target S27. **Do not submit at 7:59pm on Nov 2 with an unedited video.**

## Traction checklist (required before applying)

- [ ] **25 discovery conversations** with named robotics labs — log each with the `Discovery Call` template; target list in [[Buyer Tiers]]
- [ ] **2–3 signed LOIs or paid pilots** (written commitments with company names, success criteria — see [[Go-to-Market]] Phase 1)
- [ ] **Working demo video** — 1 minute, both founders on camera, end-to-end scorecard run ([[Scorecard UX]])
- [ ] **One public artifact** — benchmark results on GR00T/pi0 or open-source CLI ([[Go-to-Market]] Phase 2, [[IP Strategy]])
- [ ] **One metric measured and repeatable** — e.g. 5,000 episodes < 30 min on 4× A100 ([[MVP Success Metrics]])
- [ ] **Delaware C-Corp incorporated**, website live ([[Corporate Structure]])
- [ ] **NVIDIA Inception application submitted** — approval in days-to-weeks ([[NVIDIA Inception]])

## 🗓️ Weekly Countdown & Sprint Overlay

| Week of | Exec. week | Left to Nov 2 | [[8-Week Sprint Plan]] phase | Non-negotiable |
|---|---|---|---|---|
| Sep 18 | W1 | 6.4 | Foundation & env setup | 10 discovery calls; incorporate |
| Sep 25 | W2 | 5.4 | Episode runner & randomization | 10 calls; Inception app out |
| Oct 2 | W3 | 4.4 | Evaluation engine | 10 calls; first design partner demo |
| Oct 9 | W4 | 3.4 | LLM adversarial generator | 5 calls/wk; LOI conversations |
| Oct 16 | W5 | 2.4 | Regression detection | 5 calls/wk; LOIs signed |
| Oct 23 | W6 | 1.4 | API layer & CLI | Record demo video; draft YC app |
| Oct 30 | W7 | 0.4 | Dashboard | **Submit YC app (early!)**; publish artifact |
| Nov 2 | W8 | 0 | E2E demo & polish | **DEADLINE 8pm PT**; interview prep 2×/wk |

## 📝 Application rules of thumb (from [[YC Application]])

1. Lead with the problem: *"Robots ship code updates but have no CI/CD."*
2. Narrative: **"GitHub Actions for robots"** — instantly understandable.
3. Show, don't tell: demo video + LOIs + measured metric.
4. ~50% of accepted companies applied more than once — rejection is a data point, not a verdict ([[Risk Register]] risk #7).

## Operating rules ([[90-Day Roadmap]])

> [!tip]
> 1. **Momentum is the product.** Weekly demo + weekly call quota are non-negotiable.
> 2. **Everything built serves the application narrative.** Demo video, LOIs, public artifact, Inception acceptance = the four evidence pieces a W27 reviewer weighs.

## Historical progress log

### 2026-09-19 — 44 days to deadline (≈6.3 wks)

> [!warning] Verify before external send
> This dated log is not a current test report. The 338 figure is a historical snapshot; use [[Build Status]]. "Signed webhooks" means the dispatcher can HMAC-sign live sends, not that a public webhook route or immutable audit trail ships.

**Shipped:**
- **Historical platform snapshot** — 338 tests at this date; current count is in [[Build Status]].
- **Security hardening** — API auth + webhook HMAC support; no public webhook-registration endpoint.
- **Pagination + report CLI live.**
- **Multi-stage Docker** build in place.

**Next up:**
- GPU worker shadow-run
- Production rate-limit configuration
- YC application draft v2

Links: [[Home]] · [[YC Application]] · [[NVIDIA Inception]] · [[90-Day Roadmap]] · [[Investor Narrative]]
