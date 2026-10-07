---
tags:
  - problem
  - market
  - quantification
status: complete
created: 2026-09-18
area: "02 - Problem & Market"
---

# 📉 Pain, Quantified

> [!warning] Verify before external use — 2026-09-21
> Dollar, downtime, insurance and deal-loss figures are sourced/estimated inputs, not ValidSim-observed outcomes. Confirm source dates and customer applicability before quoting.

The [[Problem Statement]] is only real if the pain has a price tag. It does — every line below is a discovery-call talking point and a slide in the pitch deck ([[Key Figures]]).

## 2.3 The pain, quantified

| Pain Point | Current State | Quantified Cost |
|---|---|---|
| No standardized validation | Each lab builds bespoke internal tooling | **2–4 engineers × 6 months = $200K–$500K per lab** |
| No regression detection | Policy updates silently break previously working behaviors | Undetected failures → production downtime at **$10K–$100K/hr** |
| No safety scorecard | Insurers/enterprise customers demand deployment evidence that doesn't exist | **Blocked enterprise deals worth $500K–$5M each** |
| No adversarial testing | Edge cases (lighting changes, object deformation, human proximity) untested | Catastrophic field failures; liability exposure |
| No fleet-scale validation | Testing on 1 robot ≠ testing on 1,000 | Scaling risk grows **linearly with fleet size** |
| Manual teleoperation | **$30–80/hr** per operator, poor generalization | **$500K–$2M/year per lab** in data collection |
| No compliance evidence | ISO 10218/ISO 13482 require safety documentation; no standard format exists | Regulatory delays; **insurance premiums 2–5× higher** |

## Reading the table: three pain classes

> [!note]
> 1. **Wasted engineering** ($200K–$500K/lab bespoke tooling + $500K–$2M/yr teleop) → we sell *cost replacement*; ROI is immediate even at [[Pricing Tiers|Team tier $2,000/mo]].
> 2. **Blocked revenue** ($500K–$5M enterprise deals stalled for lack of evidence) → we sell *deal unblocking*; this is the [[Buyer Tiers|Tier 3/4]] conversation.
> 3. **Catastrophic tail risk** (field failures, liability, 2–5× premiums) → we sell *risk quantification*; this is why the scorecard must be statistically rigorous ([[Product Principles]] #2).

## Willingness-to-pay anchors

- A lab spending **$200K–$500K** to build internal validation would pay **$50K–$500K/year** to never do that again ([[Market Sizing (TAM SAM SOM)|SAM avg. annual spend, FM labs row]]).
- An OEM with a **$5M** enterprise deal blocked on safety evidence would pay **$100K–$1M/year** for a compliance-ready pipeline ([[Compliance]]).
- Downtime at **$10K–$100K/hr** means catching *one* regression per quarter pays for [[Pricing Tiers|Pro tier ($8,000/mo)]] many times over.

## Evidence to gather on discovery calls

- [ ] How many engineers do you spend on validation tooling? (anchor: 2–4 × 6 months)
- [ ] Cost of one hour of line downtime? (anchor: $10K–$100K)
- [ ] Last deal blocked by missing safety evidence? (anchor: $500K–$5M)
- [ ] Current teleop operator rate and annual data spend? (anchor: $30–80/hr; $500K–$2M/yr)
- [ ] Insurance premium impact of robot deployments? (anchor: 2–5×)

Log answers with the `Discovery Call` template → feeds [[Demand Signals]] and [[YC Countdown]] traction.

Links: [[Problem Statement]] · [[Buyer Tiers]] · [[Business Model]] · [[Unit Economics]] · [[Home]]
