---
tags:
  - product
  - ux
  - scorecard
status: complete
created: 2026-09-18
area: "03 - Product"

> [!important] Blueprint status — 2026-09-21
> The shipped scorecard is binary: the engine defines only `APPROVE` / `BLOCK` (see **Interaction states** below). An `INCONCLUSIVE` state, recorded decisions, video playback, and deploy buttons are planned product work, not current CLI/API behavior. The current API exports JSON, Markdown, HTML, and PDF scorecards.
---

# 🖥️ Scorecard UX

The scorecard **is** the product. One screen that answers: *"Is this checkpoint safe to deploy?"* — with numbers a CTO can trust and an insurer can audit ([[Product Principles]] #2, #4).

## 5.3 Dashboard wireframe — scorecard overview

```
┌─────────────────────────────────────────────────────────┐
│  VALIDATION SCORECARD — Run #1847                       │
│  Model: gr00t_v42.pt | Task: bin_picking | Date: Today  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  COMPOSITE SCORE: 87.3 / 100  ✅ DEPLOY APPROVED       │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ Success  │  │ Safety   │  │Robustness│  │Regress.│ │
│  │  94.2%   │  │  91/100  │  │  82/100  │  │ -1.3%  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
│                                                         │
│  Episodes: 5,000 | Adversarial: 100 | Duration: 23 min │
│                                                         │
│  ⚠️ 2 REGRESSIONS DETECTED vs v41:                     │
│  • Deformable object grasp: 89% → 84% (p=0.03)        │
│  • Low-light condition: 91% → 87% (p=0.08)            │
│                                                         │
│  [View Failures] [Watch Episodes] [Download PDF]        │
│  [Compare v41] [Approve Deploy] [Block Deploy]          │
└─────────────────────────────────────────────────────────┘
```

## Anatomy of the scorecard

| Element | Data source | Design rule |
|---|---|---|
| **Composite score** (87.3/100) | Weighted: **40% success + 30% safety + 20% robustness + 10% regression** ([[Module Specs]] M4) | Single number first; weights documented in PDF appendix |
| **Success rate** (94.2%) | Completed episodes / total, per task & scenario | Always show denominator (5,000 episodes) |
| **Safety score** (91/100) | Collision frequency, force limits, proximity-to-human | 0–100 scale, never letter grades |
| **Robustness** (82/100 in this mock) | Std-dev of success across randomization variants | Communicates "how fragile is 94%?" — **but see the warning below: the shipped engine cannot produce a value other than 100.0** |
| **Regression delta** (−1.3%) | Success(N) − Success(N−1) with bootstrap 95% CI | Show **p-values**; p=0.03 flags, p=0.08 hedges ([[Product Principles]] #2) |
| **Run metadata** | Episodes, adversarial count, duration | Proves scale of evidence at a glance |
| **Decision buttons** | Deployment gate API | Approve/Block are actions, not exports ([[Core User Flows]] Flow 2) |

> [!warning] The `82/100` robustness in this mock is not producible today
> The ASCII scorecard above is **design target, not captured output** — and one
> of its numbers cannot occur. `run_validation` applies a single
> `task.randomization` level to every episode, so there is only ever **one**
> randomization group, and `_robustness_score` returns its 100.0 maximum by
> construction. Verified on a 5,000-episode run: `randomization_group_count: 1`,
> `robustness_score: 100.0`, `robustness_measured: false`.
>
> A UI rendering robustness as a bare number would therefore always show "100" —
> which reads as *this model is maximally robust* when it means *no
> cross-condition evidence was gathered*. **Design rule that follows: never
> display robustness without `robustness_measured`; show "not measured" (or
> suppress the tile) when it is `false`.** The same applies to the regression
> term, which scores 100.0 whenever no baseline was supplied
> (`regression_baseline_available: false`).
>
> Also note `Duration: 23 min` in the mock: the only duration the engine records
> is `EpisodeResult.duration_s`, a **seeded RNG draw**, not wall-clock. No
> wall-clock duration field exists, and one must not be inferred from it.

## Interaction states

- **Running:** progress by episode count; estimated finish; early-failure teaser
- **Approved ✅:** green composite + threshold used (e.g. ≥85)
- **Blocked ❌:** red composite + mandatory failure-analysis panel + video clips
- **Insufficient evidence (current behavior):** the engine returns `BLOCK`; a future `INCONCLUSIVE` recommendation remains product design intent

## Downstream formats

| Output | Format | Audience |
|---|---|---|
| Dashboard view | Server-rendered FastAPI + static HTML/JS + Chart.js (React/Next.js is target-state only) | ML Engineer, CTO ([[Tech Stack]]) |
| Scorecard export | **PDF + JSON** | Developer review, management |
| Compliance evidence package | PDF + structured data (ISO 10218/13482-aligned) | Insurers, regulators (post-MVP, [[MVP Non-Goals]]) |
| PR comment | GitHub Actions step output | Reviewers ([[GitHub Actions Integration]]) |
| Audit trail | Planned immutable/hash-chained log; current stores expose validation history and deletion | Forensics, liability ([[Compliance]]) |

> [!warning] The 2-second rule
> Dashboard load time target **< 2 seconds** ([[MVP Success Metrics]]) — the Approve/Block click happens on this screen or it doesn't happen at all.

Links: [[Core User Flows]] · [[Module Specs]] · [[Product Vision]] · [[Compliance]] · [[Home]]
