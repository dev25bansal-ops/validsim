---
tags:
  - business
  - economics
status: complete
created: 2026-09-18
area: "06 - Business"
---

# 🧮 Unit Economics (Target Year 2)

§8.4. The numbers that make ValidSim fundable rather than merely interesting.

> [!warning] Model status — 2026-09-21
> These are planning assumptions, not measured unit economics. GPU throughput/cost, Inception credits, published-Action distribution, customer CAC/retention and compliance lock-in remain unverified. The current product is mock/CPU-first, public actions are not published, and no immutable audit moat exists. Do not present this model as achieved performance.
>
> One specific trap, flagged because it produces a number that looks like a result: the only per-episode duration in the engine (`EpisodeResult.duration_s`) and its aggregate (`EvaluationResult.mean_duration_s`) are **seeded RNG draws, not measurements**, and the two are algebraically identical. Any cost, margin or "time saved" figure computed from them is fiction — see the warning under *Infra cost* below for the measured 8× overstatement.

## Target metrics

| Metric | Value |
|---|---|
| Average revenue per customer (blended) | **$3,000/mo** |
| CAC (developer-led, low-touch) | **$5,000–$15,000** |
| LTV (24-month retention) | **$72,000** |
| LTV/CAC | **5–14×** |
| Gross margin | **70–80%** |
| Payback period | **2–5 months** |
| Net revenue retention (target) | **>120%** |

## The math behind each line

> [!note] Derivations — sanity-check these in every [[Financial Projections]] update
> - **LTV $72,000** = $3,000/mo × 24 months retention
> - **LTV/CAC 5–14×** = $72,000 ÷ $15,000 (worst) … ÷ $5,000 (best)
> - **Payback 2–5 months** = CAC ÷ monthly gross profit ($3,000 × 70–80% GM ≈ $2,100–$2,400/mo)
> - **CAC $5K–$15K** holds only because the motion is developer-led: free tier → GitHub Actions installs → self-serve upgrade ([[Go-to-Market]] Phase 2, [[Product Principles]] #6)

## Infra cost (COGS) assumptions

The 70–80% gross margin is a *compute-loaded* claim. Three regimes, in build order:

> [!note] COGS is near-zero today, GPU-loaded later
> - **Today — mock/CPU MVP (near-zero compute COGS).** The default backend is `MockIsaacBackend`; no GPU sits in the inference path. CPU API hosting, Postgres and Redis remain real operating costs, so “$0 per run” means **no GPU compute charge**, not zero total cost. Test status is in [[Build Status]]; no test count is quoted here because the historical 1,230 figure is unverified and superseded.
> - **Projected — Isaac worker live.** A 5,000-episode run is **≤ 2 A100-hours** (4× A100 < 30 min, [[MVP Success Metrics]]) at one worker per GPU, ≥10 GB VRAM, A100-40/80 GB on DGX Cloud (`docs/isaac-worker.md` §4). Billed at the $3.50/A100-hr pass-through that is ≤ **$7/run**; underlying DGX cost is lower (the +20% margin line, [[Business Model]]), so **GPU COGS ≈ $5–7 per 5K run against a $75 list price** ([[Pricing Tiers]]) — compute stays **<10% of revenue**, holding GM inside 70–80% even fully GPU-loaded.
>
>   > [!warning] The $5–7/run figure is a *target* built on an unmeasured throughput assumption — and it must never be derived from the engine's own duration fields
>   > The "4× A100 < 30 min" input is a **benchmark target** ([[MVP Success Metrics]]), not a measurement: no GPU run has been executed, and the shipped engine is a serial CPU mock. So $5–7 is arithmetic *conditional on* an unvalidated premise.
>   >
>   > The trap is that the repository appears to contain a per-episode duration that could "confirm" it. It cannot, and computing from it **overstates cost by roughly 8×**:
>   >
>   > - `EpisodeResult.duration_s` is a **seeded RNG draw** (`validsim/sim/runner.py`: `rng.uniform(4.0, 12.0)` on success, `uniform(6.0, 20.0)` on failure, `uniform(18.0, 35.0)` on timeout) — not measured wall-clock and not GPU time.
>   > - `EvaluationResult.mean_duration_s` is `statistics.fmean(e.duration_s ...)` — **algebraically identical** to the sum (mean × N ≡ sum), so it is the same fiction wearing an authoritative-looking aggregate. Measured delta: exactly **0.00%** at every seed tested.
>   > - Reproduced at 5,000 episodes: sum ≈ 50,500 s ≈ **14.0 A100-hours ≈ $49/run** at $3.50/A100-hr, versus the $5–7 claimed here — **~8×** — and stable to ±0.2% across seeds 7/42/123, which is precisely what makes it dangerous: a reproducible number carrying units and a currency symbol that nobody sanity-checks.
>   >
>   > **Rule: a cost figure whose only available source is a seeded RNG must never reach a finance surface.** `StoredRun` carries no `wall_clock_s` and no `gpu_seconds` — those are the genuinely missing inputs. See `docs/PLATFORM_PROPOSAL.md` §7.1.
> - **Self-hosted-runner implication.** The GitHub Action runs on a CPU-only `ubuntu-latest` runner and offloads GPU to ValidSim's worker ([[GitHub Actions Integration]]) — so *ValidSim* carries the GPU COGS above. Offering a **BYO-GPU SKU** (customer runs the ~15 GB worker image on their own A100/DGX, same `VALIDSIM_BACKEND=isaac` swap) drives ValidSim's per-run GPU COGS → **~$0** for those runs; that tier prices as a flat orchestration/seat fee, not per-GPU-hour. It is a third lever against Risk #6 below and a way to sell to labs that already own HPC fleets without pass-through.

## Why CAC stays low (structural, not aspirational)

1. **Low-friction entry** — a checkout install plus a one-push workflow is the planned developer path; published Actions and a PyPI release are not verified ([[CLI Design]])
2. **PR comments advertise the product** once the local Action is runnable from a customer checkout; the published `validsim/*@v1` funnel is target state
3. **Potential NVIDIA Inception intros** only after membership/credits are independently confirmed
4. **Future public benchmarks** may convert researcher traffic into signups; no such published benchmark was verified in the repository

## Why NRR >120% is credible

Four automatic expansion axes in [[Business Model]]: seats (5–20 runs/mo/engineer), episodes (1K→100K), embodiments (1→10→unlimited), compliance reports ($50 each). Robot fleets grow linearly; validation spend grows with them ("scaling risk grows linearly with fleet size" — [[Pain Quantified]]).

## Sensitivity analysis — what breaks the model

| Assumption stress | Impact | Mitigation |
|---|---|---|
| Labs build in-house (Risk #1) | CAC spikes; some segments unbuyable | Price under the $200K–$500K internal build; target labs without sim expertise ([[Risk Register]]) |
| GPU prices rise / credits exhaust (Risk #6) | GM < 70% | Pass-through +20% keeps margin % intact; multi-cloud fallback A100/H100 ([[Tech Stack]]); BYO-GPU self-hosted runner shifts COGS to the customer (see *Infra cost* above) |
| Enterprise cycle stretches past 3 months (Risk #5) | Payback > 5 months | Developer-led bottom-up pull; Team tier revenue while enterprise pilots bake ([[Go-to-Market]]) |
| Retention < 24 months | LTV < $72K, LTV/CAC < 5× | Deployment gating and a future immutable audit trail may create lock-in; neither the customer outcome nor the trail is verified today |

## Investor framing

> [!quote] One-liner for the [[Investor Narrative]]
> "A $3K/mo customer who costs <$15K to acquire, pays back in under 5 months, and expands >20%/year because their robot fleet — and their model-update cadence — never slow down."

Links: [[Pricing Tiers]] · [[Business Model]] · [[Financial Projections]] · [[KPIs]] · [[Home]]
