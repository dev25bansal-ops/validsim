---
tags:
  - risk
  - register
status: active
created: 2026-09-18
area: "09 - Risks"
---

# ⚠️ Risk Register & Mitigations

§17. Eleven tracked market/product risks. Review at every weekly demo ([[90-Day Roadmap]] ritual); re-score impact/probability monthly; log changes in the [[Decision Log]].

> [!warning] Register scope — 2026-09-21
> This register is not a complete engineering-risk log. Track the verified engineering and security defects in `docs/ISSUE_CATALOG.md` separately. The mitigations below describe opt-in controls unless a row says otherwise.

## The register

| # | Risk | Impact | Probability | Mitigation | Deep-dive |
|---|---|---|---|---|---|
| 1 | **Labs build validation in-house** | High | Medium | Offer managed service at **10× lower cost**; target labs without sim expertise | [[Pain Quantified]] ($200K–$500K build math) · [[Competitive Landscape]] |
| 2 | **NVIDIA productizes CI layer** | High | Medium | Move fast; own workflow + customer relationships; position as ecosystem partner | [[Moat]] (12–18 mo first-mover) · [[NVIDIA Inception]] |
| 3 | **Humanoid deployment slips 1–2 years** | Medium | Medium | Expand to AMRs, arms, drones; validation is robot-type-agnostic | [[Buyer Tiers]] (Tier-3 AMR operators) · [[Why Now (2026)]] |
| 4 | **VLA architectures change rapidly** | Medium | High | Abstract evaluation layer from model architecture; support **ONNX/PyTorch/TensorRT** | [[Module Specs]] M1 · [[Tech Stack]] |
| 5 | **Slow enterprise sales** | Medium | Medium | Lead with developer adoption; enterprise follows bottom-up | [[Go-to-Market]] Phase 2 · [[Unit Economics]] (CAC $5K–$15K) |
| 6 | **Compute costs exceed credits** | Medium | Low | Optimize simulation; batch scheduling; pass-through pricing (+20% margin); **cost-amplification guard mitigated 2026-09-20** — queue depth cap + IP-keyed limiter stop a leaked key driving unbounded runs | [[Tech Stack]] (AWS/GCP fallback) · [[Business Model]] · [[Security Hardening]] |
| 7 | **YC rejection** | Low | **High** | Reapply with verified traction; do not assume NVIDIA Inception remains active or funded | [[YC Application]] · [[Funding Plan]] |
| 8 | **Open-source competitor emerges** | Low | Medium | Move fast; **enterprise features + support are the moat** | [[Pricing Tiers]] (HIL/SSO/SLA rows) · [[Moat]] |
| 9 | **API abuse before rate limiting** | Medium | Medium | When `VALIDSIM_API_KEY` is configured, `/api/v1` is key-gated except public health (bare `/metrics` is also public); the IP-keyed process-local limiter is implemented but **off by default** (`VALIDSIM_RATE_LIMIT=0`), so remote deployments must configure it. Open: distributed limiting, safe production defaults, and a dedicated 401-storm alert | [[Security Hardening]] · [[API & CLI]] · [[Module Specs]] API Gateway |
| 10 | **Postgres store parity drift** | Medium | Low | **Shared contract tests** across backends; **schema-version marker** to detect divergence | [[Module Specs]] · [[Tech Stack]] |
| 11 | **Async queue as a new attack/DoS surface** | Medium | Medium | **Depth cap → `503`** (`max_depth` 1000; `QueueFullError` → `503` + `Retry-After: 5` instead of an opaque `500`); **IP-keyed rate limit** on `POST /jobs` (buckets on client IP only — rotating `X-API-Key` can no longer mint a fresh bucket; bounded bucket set); **SSE deadline** (60 s → `event: timeout`, so a stuck job can't pin a worker thread) — **mitigated 2026-09-20**; residual: limiter is process-local, the cap is a soft ceiling under multi-process Redis, and both are opt-in via env | [[Security Hardening]] · [[Async Job Queue]] · `docs/async-jobs.md` §6 (audit H1–H3, M1) |

## Risk heatmap

```
Impact ▲
 High │   ①②
      │
 Med  │        ③⑤⑥  ⑩⑪
      │           ④
 Low  │              ⑦⑧⑨
      └──────────────────────────▶ Probability
          Low     Med      High
```

## Top-3 focus list (impact × probability, founder attention)

> [!warning]
> 1. **Risk 4 (VLA churn, High probability):** the only *High-probability* material risk — the format-agnostic ingestion layer (PyTorch/ONNX/TensorRT) is non-negotiable architecture, not a nice-to-have ([[Solution Architecture]] L1).
> 2. **Risk 2 (NVIDIA):** existential-if-materialized; our counter is velocity — the [[8-Week Sprint Plan]] and LOI density *are* the mitigation.
> 3. **Risk 1 (in-house build):** re-framed as sales collateral — every "we'll build it ourselves" conversation becomes a [[Pain Quantified]] ROI spreadsheet exercise.

**Risk 7 is a plan, not a problem:** the [[YC Countdown]] evidence — verified LOIs, demo, public artifact and any confirmed Inception award — can inform pre-seed independently.

## Standing review questions (monthly)

- Any new funded competitor in "robot CI/CD" or "policy validation"? → update [[Competitive Landscape]] + Risk 8
- Did humanoid pilot announcements accelerate or slip vs. 10,000+ units/2027? → Risk 3, [[Demand Signals]]
- DGX/AWS/Nebius credit burn rate vs. plan? → Risk 6, [[Financial Projections]]
- Design-partner requests clustering on one deferred feature? → re-rank [[MVP Non-Goals]]

Links: [[Strategic Advantages]] · [[Security Hardening]] · [[90-Day Roadmap]] · [[Home]]
