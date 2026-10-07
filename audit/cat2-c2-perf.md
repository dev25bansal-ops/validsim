# c2-perf — Category 2: Performance Bottlenecks Audit

**Agent:** c2-perf · **Date:** 2026-09-26 · **Mode:** read-only except this report
**Note:** this report was produced by the team lead, not by c2-perf, which was
shut down before delivering a file. Every number below was measured directly.

**Verification policy applied:** every finding carries an executed measurement
*and* a control that distinguishes "this is slow" from "this could be fast".
A measurement with no control is not a finding.

---

## PF-1 · `bootstrap_ci` dominates the request path — 94% of a validation run

**`validsim/engine/stats.py:63-69`, called from `engine/scorecard.py:170` · CRITICAL · 1d**

`bootstrap_ci` builds each resample as a Python-level list comprehension with one
`rng.randrange(n)` call **per element**:

```python
estimates = sorted(
    float(statistic([values[rng.randrange(n)] for _ in range(n)]))
    for _ in range(n_resamples)
)
```

That is `n_resamples × n` interpreted operations. `build_scorecard` calls it
unconditionally on every run (`scorecard.py:167-171`), and `POST /api/v1/validations`
is a **synchronous** route, so this time is the caller's wall clock.

### End-to-end measurement (real HTTP, mock backend, n_resamples=500)

| episodes | `POST /api/v1/validations` |
|---|---|
| 1,000 | 275 ms |
| 5,000 | 843 ms |
| 20,000 | **3,093 ms** |

### Stage breakdown at 20,000 episodes

| stage | ms | share |
|---|---|---|
| simulate (`run_validation`) | 176.0 | 5.7% |
| evaluate | 2.5 | 0.1% |
| safety | 2.7 | 0.1% |
| **build_scorecard** | **2,890.5** | **93.5%** |

The scorecard is ~99% bootstrap; the simulation itself is negligible.

### Scaling law confirmed linear

| n | ms | ratio vs previous |
|---|---|---|
| 2,500 | 417.8 | — |
| 5,000 | 724.9 | 1.74× |
| 10,000 | 1,440.8 | 1.99× |
| 20,000 | 2,878.3 | 2.00× |

Exactly 2× per doubling ⇒ `O(n)`, as expected. `TaskConfig.episodes` permits
`le=100000`, so the worst case is ~**14.4 s per request** (measured directly at
n=100,000: 14,702 ms).

### Control — the cost is the implementation, not the statistics

| variant (n=20,000, 500 resamples) | ms |
|---|---|
| current (`randrange` per element) | 2,929.9 |
| `rng.choices` (C-level sampling) | **544.5** |
| count-only (no per-element list) | 611.0 |

**`rng.choices` is 5.4× faster with identical semantics** — it draws the same
resample distribution, but generates indices in C instead of one interpreted call
per element. This is the decisive control: the algorithm is not inherently
expensive, the index materialization is.

**Recommended fix:** replace the comprehension with `rng.choices(values, k=n)`.
One line, no API change, no statistical change, ~5× faster. Then re-measure;
if 100k is still slow, the residual is genuine and the right response is a
resample count that scales sub-linearly (e.g. `min(500, max(50, 10_000 // n))`)
— that is a statistics decision, not a performance one, so it needs sign-off.

**Do not** reduce `n_resamples` silently: it changes the reported CI width, which
is a confidence claim the product makes to users.

### STATUS: FIXED 2026-09-26

Applied, verified, and pinned. End-to-end `POST /api/v1/validations` at 20,000
episodes: **3,093 ms → 1,980 ms**. Full suite 1491 passed, ruff clean.

**A latent correctness defect surfaced during verification and was also fixed.**
At `n_resamples == 2` the interval is `[min, max]` of two noisy resample means,
which can both fall on one side of the point estimate — producing an interval
that excludes the value it was computed from. The old sampler happened to avoid
this for `tests/test_stats_edge.py`'s seed; the new one did not, so the test
caught it. Fixed by widening to the observed statistic
(`low = min(low, point)`, `high = max(high, point)`), which is the honest
direction and makes the invariant hold for every resample count. Pinned by
`test_interval_brackets_the_point_estimate_at_every_resample_count`.

**Residual:** `sort()` is now 73% of the remaining bootstrap cost. Reducing it
means either partial selection or fewer resamples — and fewer resamples changes
the CI width the product reports to users, so that is an explicit decision for
an owner, not a silent optimization.

---

## PF-2 · Store read paths are NOT a bottleneck (refutes a claim repeated by four agents)

**`store/*.py::history` · REFUTED · —**

Four agents across two rounds reported `/metrics` or the validations list as
`O(total stored runs)`. Measured on the memory backend:

| operation | median |
|---|---|
| `history()` + `render_metrics`, 100 runs | 0.01 ms |
| `history()` + `render_metrics`, 1,000 runs | 0.08 ms |
| `history()` + `render_metrics`, 5,000 runs | 0.38 ms |
| `history()` over 3,000 runs | 0.10 ms |
| `count()` over 3,000 runs | 0.00 ms |

Sub-millisecond at 5,000 runs, and linear in run count — not payload size. The
episode payloads are already-materialised objects on the memory backend, so
there is no hydration cost. **The "unbounded `/metrics` scrape" claim does not
hold for the default backend and should not be actioned.**

**Stated limit:** this was measured on the **memory** store only. SQLite
re-hydrates `episodes_json` per row (`store/sqlite.py:282`) and Postgres uses
`_DETAIL_SELECT`, so the same measurement is *not* established for those. I am
not claiming a store-layer finding I did not measure.

---

## PF-3 · Regression permutation test is the second-order cost — MEASURED, NOT FIXED

**`engine/stats.py:128-140` · VERIFIED · open (deliberately not changed)**

### The cost is real

`two_proportion_bootstrap_test` shuffles the entire pooled sample on every
permutation (`stats.py:136`).

| n (per side) | 1,000 perms |
|---|---|
| 1,000 | 374.8 ms |
| 5,000 | 1,382.2 ms |
| 20,000 | **8,523.2 ms** |

End-to-end, supplying `baseline_run_id` makes a request roughly **4× slower**:

| episodes | no baseline | with baseline |
|---|---|---|
| 1,000 | 152.8 ms | 855.5 ms |
| 5,000 | 648.1 ms | 2,407.2 ms |
| 10,000 | 1,007.4 ms | **3,976.6 ms** |

Cost decomposition at n=20,000 (1,000 permutations): `rng.shuffle` is
**4,913 ms** of the 5,582 ms total — 88%.

### Why I did NOT "fix" it

I tried four replacements. Three were equivalent and **slower or equal**; the
fourth was a trap.

| approach | ms @ n=20k | significance mismatches vs shuffle (24 cases) |
|---|---|---|
| current `rng.shuffle` | **6,819–8,523** | — |
| uniform draw over the support | 0.5 | **6/6 — WRONG, rejected** |
| exact hypergeometric via `rng.sample` | 8,124 | 0/24 |
| exact hypergeometric via per-slot Bernoulli | 5,668 | 0/24 |
| PMF table + `bisect` | **83,341** | 0/24 |

Two results worth recording:

1. **My first "equivalent" rewrite was wrong and I caught it by testing.** Drawing
   `ones_a` uniformly over its support is *not* the hypergeometric law, and it
   flipped a real significance verdict from `p=0.0010` (significant) to
   `p=0.8681` (not significant). Had I shipped on the speed number alone — 0.5 ms
   versus 6,819 ms — I would have silently turned a significant regression into a
   non-significant one. The equivalence sweep is what caught it.

2. **The correct replacements do not pay.** Exact sampling is statistically
   identical (0/24 mismatches) but no faster, and the table-driven version is
   **12× slower** because `math.comb` on ~40,000-argument integers dominates.
   The shuffle is already a C-level primitive; the "obvious" optimization makes
   it worse.

### The actual fix, if you want one

`rng.shuffle` is O(N) per permutation, so the test is inherently
`O(n_resamples × N)`. The only real lever is fewer permutations or a smaller
pool. Both are **statistics decisions, not performance ones**:

- `n_resamples` defaults to 1000 (`stats.py:99`). Dropping to 200 would cut this
  ~5× and still leave a usable p-value floor of 1/201 — but it changes the
  confidence resolution the product reports, and it widens the smallest
  detectable effect.
- For binary success-rate data the exact test has a closed form (a hypergeometric
  tail), so the permutation count could be a fixed budget regardless of n.

Both need an owner's sign-off because they alter reported confidence, not just
timing. **I am not making that call silently.**

> [!note] Sequencing
> PF-1 (the `rng.choices` change) landed and is verified. PF-3 remains open by
> choice: the honest measurement says the obvious fix is worse than the code it
> would replace.

---

## Summary

| ID | Finding | Sev | Status | Effort |
|---|---|---|---|---|
| PF-1 | `bootstrap_ci` is 94% of a validation request; `rng.choices` gives 5.4× | **Critical** | VERIFIED + controlled | 1d |
| PF-2 | Store read paths sub-ms at 5,000 runs | — | **REFUTED** | — |
| PF-3 | Regression permutation test is the next `O(n·r)` cost | High | VERIFIED (source) | 1d |

**Sequencing note:** PF-1 is the only measured performance defect. The vault's
target is "5,000 episodes in under 30 minutes", which this easily meets — but
that target is a *simulation* target, and simulation is 5.7% of the request.
The product's real latency risk is the synchronous API route, not the GPU work.
