---
tags:
  - legal
  - corporate
status: complete
created: 2026-09-18
area: "08 - Team & Legal"
---

# 🏛️ Corporate Structure

§18.1. Delaware C-Corp is a prerequisite, not a preference: it gates [[NVIDIA Inception]] eligibility, YC investment paperwork, and standard VC terms.

## 18.1 Structure table

| Item | Detail |
|---|---|
| **Entity** | Delaware C-Corporation |
| **Equity Split** | 50/50 (or negotiated) with **4-year vesting, 1-year cliff** |
| **IP Assignment** | All IP assigned to corporation via standard assignment agreements |
| **Advisors** | **0.25–0.5% equity per advisor**, 2-year vesting |
| **YC Terms** | **$125K for 7% + $375K uncapped MFN** (standard) |

## Why Delaware C-Corp

| Reason | Consequence if wrong |
|---|---|
| YC standard doc assumes DE C-Corp | Post-acceptance re-domestication wastes batch weeks |
| NVIDIA Inception requires an incorporated entity | Week 1–2 Inception application slips ([[90-Day Roadmap]]) |
| US/Global customers (Tier 1–4, [[Buyer Tiers]]) expect US paper | Enterprise contracts stall in procurement |
| Uncapped MFN + 7% structure is C-corp-native | LLC/stock-class gymnastics with every future round |

## Week-1 checklist (sprint W1, day 5 — shared "Both" task)

- [ ] File Delaware incorporation (registered agent + bylaws + stock issuance)
- [ ] Founder stock purchase agreements + **83(b) elections** (30-day clock!)
- [ ] **Proprietary Information & Invention Assignment Agreements (PIIAA)** signed by both founders → assigns Isaac pipeline + scenario-generator IP to the corp ([[IP Strategy]])
- [ ] Register domain (validsim.com or final name) + website live → Inception eligibility ("working website")
- [ ] Business bank account + bookkeeping
- [ ] Section 83(b) copies mailed; all docs stored in `99 - Attachments`

> [!important] IP assignment before first commit
> The GitHub repo is initialized the same day as incorporation ([[8-Week Sprint Plan]] W1). Assignment agreements must cover everything from the first Isaac Sim script onward — investors diligence the chain of title, and unassigned founder code is the classic pre-seed red flag.

## YC standard terms decoded

```
$500K total = $125K for 7% of the company
            + $375K on an uncapped MFN (most-favored-nation) note
```
- 7% fixed; the MFN caps out at whatever the next priced round offers — no valuation set now ([[Funding Plan]] sequencing)
- ~50% of accepted companies applied more than once — terms don't change between attempts ([[YC Application]])

## Legal budget

- **$10K–$15K** "Legal, incorporation, misc" in Year-1 burn ([[Financial Projections]])
- Scope covered: incorporation, founder docs, PIIAA, basic TOS/privacy for the website
- Deferred to pre-seed: SOC 2 engagement, patent provisionals ([[Compliance]], [[IP Strategy]])

Links: [[IP Strategy]] · [[Compliance]] · [[Founding Team]] · [[Funding Plan]] · [[Home]]
