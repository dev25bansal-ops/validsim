---
tags:
  - fundraising
  - engineering
  - momentum
  - yc
status: active
created: 2026-09-20
area: "07 - Fundraising"
---

# 🚀 Engineering Momentum Log

> [!quote] The thesis this log proves
> *"Investors don't buy a slide deck — they buy a team that ships on a cadence. This log is the receipt."*

A running record of build momentum for investors. It preserves dated history, but only rows that still match source/verified artifacts may be used externally ([[YC Application Answers]], [[Investor Narrative]]).

> [!warning] Verify before external send — 2026-09-21
> This is a historical snapshot. Test totals, `100%` pass-rate, subagent counts, 4× A100 timing, nightly real-Isaac execution, Inception status, and external-user assertions are not verified by the repository. Use [[Build Status]] for the latest test count, attach logs for benchmarks, and remove unsupported investor claims. HMAC signing protects webhook delivery; it is not an immutable audit trail.

## Why this matters now

**Today is Sep 20, 2026 — 43 days to the YC W27 deadline (Nov 2, 2026, 8pm PT).** Between now and then the application asks *"what have you actually built?"* This log is the standing answer — refreshed every time the platform ships, so velocity is an artifact, not a claim.

## Shipped this iteration (0.2.0)

| Capability | What it proves |
|---|---|
| **Historical test snapshot** | 338 was a dated milestone, not the current source of truth; use [[Build Status]] and do not infer a 100% pass rate |
| **Security hardening** | API key auth (`VALIDSIM_API_KEY`, constant-time compare) + HMAC-signing capability in the webhook dispatcher; **that dispatcher has no production caller** (no run sends a scorecard anywhere), and neither the signing nor public webhook registration is an immutable audit trail, and public webhook registration is not implemented ([[Security Hardening]]) |
| **Async job queue + Redis** | `POST /api/v1/jobs` is fire-and-forget on Redis/memory; synchronous `POST /api/v1/validations` remains available ([[Async Job Queue]]) |
| **Trend analytics** | Regression timeline over successive runs — the *"did it get worse?"* question answered per checkpoint |
| **Pagination + report CLI** | List endpoints paginate; `validsim report` generates run reports from the terminal |
| **Multi-stage Docker** | Slimmer runtime image, faster CI cycles |
| **Nightly adversarial workflow** | Repository workflow exists; real Isaac/4× A100 throughput and customer-facing runs are unverified |

## 📊 Metrics that matter

| Metric | Value | Signal |
|---|---|---|
| **Historical test count growth** | 190 → 250 → 338 → 601 (dated milestones) | Use only with their source logs; [[Build Status]] is current |
| **Current suite** | See latest generated row in [[Build Status]] | The earlier 601/95% values are stale |
| **Pass rate** | **Unverified** — the recorded history includes failures; do not claim 100% |
| **Agent-assisted build** | Historical agent/wave counts are unverified | Do not present as investor evidence without run records |

The historical rows preserve momentum, not current proof. Re-check source, generated build records, and dated run logs before quoting any number.

## What this reads as, to an investor

1. **Velocity is testable.** Use the current generated count, not this snapshot's historical numbers.
2. **Security is partial.** Auth exists and outbound webhook signing is *implemented but never invoked by a run*; immutable audit/compliance retention is future work.
3. **Infrastructure is real but local.** Memory/Redis jobs, Postgres, and Docker Compose ship; Kubernetes/Argo and the GPU worker are not part of the current stack.

## ## Historical snapshot boundary

Everything above reflects the repository state on 2026-09-20, before the later catalog pass. It is retained to preserve the founding team's history, not to certify today's suite or customer evidence.

## Next up

- GPU worker shadow-run — 5,000 episodes under 30 minutes on 4× A100 is an unverified target ([[MVP Success Metrics]])
- Configure and test production rate limiting; the process-local limiter is off by default ([[Security Hardening]])
- YC application draft v2 ([[YC Application Answers]])

Links: [[YC Application Answers]] · [[Investor Narrative]] · [[NVIDIA Inception]] · [[Home]] · [[YC Countdown]] · [[Build Status]]