---
tags:
  - product
  - ux
  - scorecard
status: complete
created: 2026-09-18
area: "03 - Product"
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
| **Robustness** (82/100) | Std-dev of success across randomization variants | Communicates "how fragile is 94%?" |
| **Regression delta** (−1.3%) | Success(N) − Success(N−1) with bootstrap 95% CI | Show **p-values**; p=0.03 flags, p=0.08 hedges ([[Product Principles]] #2) |
| **Run metadata** | Episodes, adversarial count, duration | Proves scale of evidence at a glance |
| **Decision buttons** | Deployment gate API | Approve/Block are actions, not exports ([[Core User Flows]] Flow 2) |

## Interaction states

- **Running:** progress by episode count; estimated finish; early-failure teaser
- **Approved ✅:** green composite + threshold used (e.g. ≥85)
- **Blocked ❌:** red composite + mandatory failure-analysis panel + video clips
- **Inconclusive:** insufficient episodes for 95% CI → recommend re-run, never a fake verdict

## Downstream formats

| Output | Format | Audience |
|---|---|---|
| Dashboard view | React/Next.js live page | ML Engineer, CTO ([[Tech Stack]]) |
| Scorecard export | **PDF + JSON** | Developer review, management |
| Compliance evidence package | PDF + structured data (ISO 10218/13482-aligned) | Insurers, regulators (post-MVP, [[MVP Non-Goals]]) |
| PR comment | GitHub Actions step output | Reviewers ([[GitHub Actions Integration]]) |
| Audit trail | Immutable, hash-chained log | Forensics, liability ([[Compliance]]) |

> [!warning] The 2-second rule
> Dashboard load time target **< 2 seconds** ([[MVP Success Metrics]]) — the Approve/Block click happens on this screen or it doesn't happen at all.

Links: [[Core User Flows]] · [[Module Specs]] · [[Product Vision]] · [[Compliance]] · [[Home]]
