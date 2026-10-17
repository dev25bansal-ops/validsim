# ValidSim — External Research: Improve / Advance / Enhance / Upgrade / Fix / Build

**Date:** 2026-10-16 · **Method:** multi-search-engine research (international + CN engines), 16 query clusters, plus 1 deep fetch. Sources cited inline.

**How to read this:** each section maps **external best practice → ValidSim's specific gap (from the 2026-10-16 full read) → concrete action**. Priority tags: **P0** = blocks the product's core claim; **P1** = high value; **P2** = quality/scale.

---

## 0. Executive summary — the ten moves that matter most

| # | Move | Why (external evidence) | Fixes (internal) |
|---|---|---|---|
| 1 | **Gate on the adversarial suite, not just pooled success** | A green sim run is not a ship signal; the teams that get burned treat it as one (robo2u). Adversarial/OOD robustness must be a first-class gate, not a diluted average. | D9: gate APPROVES a checkpoint failing 100% of adversarial |
| 2 | **Replace "constant" components with measured evidence or abstention** | Determinism ≠ validity ("a reproducible lie is still a lie"). A component that is structurally constant measures nothing. | ~30 unconditional composite points; robustness=100 always |
| 3 | **Use a lower-confidence-bound gate (Wilson), not a point estimate** | Eval gating practice: report per-case results and gate on the Wilson lower bound; never relax the threshold to pass (alphalab 2026). | 1-episode APPROVE; thin-evidence approvals |
| 4 | **Add a validated *offline* validation metric** | CI-MSE: offline metric correlates with rollout performance at Spearman −0.87 vs −0.61 for raw MSE across 27 checkpoints (arXiv 2606.29898). | Mock can't rank policies (c4-sim2real S-1) |
| 5 | **Close the sim-to-real validity gap with conformal prediction + paired real telemetry** | The vault's own §14: only conformal prediction on paired sim/real telemetry closes transfer validity. Conformal/ACI is now standard for robot safety (Berkeley; UIC; CoSMO arXiv 2610.18321). | "we sell transfer validity while holding no evidence of it" |
| 6 | **Wire + harden the notify layer** | Webhook delivery standard: HMAC `t=,v1=` with timestamp, idempotency key, exponential backoff, dead-letter (Razorpay; Stripe-style). | notify is dead code; SSRF scheme-only; replayable HMAC |
| 7 | **Make the audit trail real: hash-chained append-only** | Tamper-evident = SHA-256 prev-hash chain, append-only, cryptographically verifiable (AuditWeave 2026; AgentLens). | `SECURITY.md` tamper claim false for memory/SQLite; `DELETE` removes evidence |
| 8 | **Fix security defaults** | SSRF defense-in-depth: allowlist + block RFC1918/loopback/link-local/metadata + no redirects + DNS re-validation (OWASP cheat sheet). | unauth `/metrics`; wildcard CORS; no CSP/SRI |
| 9 | **Adopt the 2026 Python toolchain** | uv + Ruff (+ ty/mypy strict) is the converged 2026 standard, 10–100× faster, one config (pyinns; rajpoot.dev). | pip/requirements drift; ruff E501s; no lockfile |
| 10 | **Integrate real benchmarks (LIBERO / RoboCasa / ManiSkill) as first-class tasks** | 2026 eval ecosystem: LIBERO, LIBERO-Plus/Pro, RoboCasa, ManiSkill, VLA-Arena, RoboDojo, RoboMemArena, FlagEval-Robo (sim+real dual-track). | single bespoke bin-picking task; nothing externally comparable |

---

## 1. FIX — correctness, security, and the trust-breaking defects (P0/P1)

### 1.1 The scoring/gating core (the product's whole claim)

**External:** the 2026 robotics-CI consensus is a **staged validation ladder** (unit → fast deterministic sim regression → full-stack sim → HIL → hardware) where **each tier has quantitative pass/fail thresholds with units** and runs on every merge. Critically: *"Sim tells you the logic is right and the gross dynamics are plausible. Hardware tells you the truth."* And the **determinism trap** — *"A sim can be perfectly deterministic and perfectly wrong... A reproducible lie is still a lie."*

**ValidSim's gap:** the composite is `0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression`, but on the CLI/worker path **robustness is always 100.0** (one randomization group) and **regression is always 100.0** (no baseline) → `composite = 0.4·success + 0.3·safety + 30.0`. The gate then APPROVES a checkpoint failing 100% of its adversarial suite. This is the "green sim = ship signal" failure mode, shipped.

**Actions:**
- **Emit a declared randomization ladder.** The runner must stamp a *ladder* of `randomization_level` values keyed on episode index (e.g. `none/low/mid/high`), not the single `task.randomization`. This makes `_robustness_score` measurable and removes ~20 of the 30 free points. *(Fix the input, not the weights — the audits insist on this.)*
- **Make regression abstain, not default to 100.** When no baseline is supplied, exclude the term from the denominator (the abstention rule already exists in ADR 0002 — apply it to the degenerate input).
- **Promote adversarial to an explicit gate.** Add a hard `adversarial_success_rate` threshold alongside the composite, and **zero-tolerance** for safety-critical violations (collision/force) — mirroring the eval-gating rule that "policy/permission/side-effect violations are zero-tolerance while general correctness is judged on the pass-rate lower bound."
- **Gate on a Wilson lower bound, not a point estimate.** Replace `composite >= 85` with `wilson_lower_bound(successes, n) >= floor` (or a bootstrap lower bound), and **enforce a minimum episode floor** so a 1-episode run cannot APPROVE. Report the sample size with every verdict.
- **Persist `checkpoint_sha256`.** Bind the artifact bytes into the `Scorecard` so any approval can answer "which checkpoint bytes produced this?" This is the chain-of-custody every compliance feature downstream depends on.

### 1.2 Security

**External:** SSRF defense is **layered** — (1) scheme allowlist (http/https only), (2) explicit domain allowlist, (3) resolve DNS **and** block RFC1918 / loopback / link-local / `169.254.169.254` metadata / `::1` / `fc00::/7` / `100.64.0.0/10`, (4) **disable redirects** or re-validate every hop, (5) re-resolve at connection time to defeat **DNS rebinding**, (6) aggressive timeouts (connect ≤ 3 s, total ≤ 10 s). Webhook signing standard: **`t=<timestamp>,v1=<HMAC(secret, timestamp + "." + body)>`**, verified within a tolerance window, with an **idempotency key** and **exponential-backoff retries routed to a dead-letter queue**.

**ValidSim's gaps:** notify `_valid_url` checks scheme+netloc only (loopback/RFC1918/metadata reachable); HMAC signs the body alone (replayable forever, not URL-bound, no verify helper); `_post` retries on 4xx with no idempotency key; `/metrics` is unauthenticated; CORS defaults to `*`; the page loads Chart.js from a CDN with no SRI and no CSP while holding the API key in `sessionStorage`; rate-limiting keys on IP only (ignores `X-Forwarded-For`).

**Actions (P0/P1):**
- Add `is_safe_url()` with the full blocklist above; **disable redirects** (`follow_redirects=False`) and re-validate on any hop; set connect/total timeouts.
- Move to a **`t=,v1=` versioned signature** over `timestamp + "." + body`, publish a `verify_signature()` helper with `hmac.compare_digest`, and reject outside a 5-minute window.
- Add an **`event_id` + idempotency key**; retry only on 5xx/timeouts (not 4xx); route exhausted retries to a **dead-letter** path.
- Require auth on `/metrics` (or bind it to a separate internal port); default CORS to a **deny-all/allowlist**; add `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, and SRI/`crossorigin` on the CDN asset.
- Key the rate limiter on **API key** (falling back to a *trusted-proxy-aware* client IP), not the raw socket IP.

### 1.3 Data-integrity & hygiene

- **Memory store read-path aliasing (P0):** `get`/`history`/`list_for_checkpoint` return the stored object itself — a caller can silently rewrite a stored BLOCK verdict. Deep-copy on read (the save-side fix already exists; the read-side is the open half). This also removes the memory↔SQLite parity divergence.
- **CLI drops `checkpoint_id` (P1):** bind it onto the task (as `pipeline` does) so `--checkpoint` actually changes the simulation and the worker payload.
- **`make smoke` / `delete --latest` (P1):** the smoke target must pass on a clean machine; `delete --latest` must read the durable store, not the JSON cache (currently shipped as red tests).
- **CI benchmark gate is vacuous (P1):** commit `tests/bench/baseline.json`, or make a missing baseline a hard failure under `--compare`.
- **Prometheus counter monotonicity (P1):** `validsim_runs_total` *decreases* on delete, breaking `rate()`. Keep `_total` monotonic and expose "current runs" as a separate **gauge**; count deletes as their own counter.
- **Housekeeping (P2):** `git rm` the tracked `$null`; delete the ~199 MB `probe_*.db` and stale `.coverage`; move `auth-panel.png` out of the root; drop the byte-identical `_bak_viewcore.js`; move the 73 tracked `_probe_*.py` scratch files under `tools/audit/` or delete.

---

## 2. ADVANCE — make the core sim-to-real validation actually valid (P0)

This is where the product can become defensible rather than a wrapper.

### 2.1 Rank checkpoints with a *validated offline metric*
**External:** **CI-MSE** (arXiv 2606.29898) localizes task-critical trajectory segments and restricts error to them, reaching **Spearman −0.87** between offline error and rollout performance vs **−0.61** for raw MSE across 27 checkpoints. It also flags the core insight ValidSim is missing: **aggregate metrics are dominated by non-critical motion** and thus correlate weakly with real success.

**Action:** replace/augment the mock's raw success aggregate with (a) a **critical-interval** error term and (b) a **rank-correlation self-check** — ValidSim should continuously measure *its own* Spearman correlation between its offline score and a held-out rollout set, and publish it. If the score can't rank checkpoints, the product's premise fails.

### 2.2 Close the transfer-validity gap with conformal prediction
**External:** conformal prediction gives **distribution-free coverage guarantees**; **Adaptive Conformal Inference (ACI)** recalibrates online as the distribution shifts (CoSMO, arXiv 2610.18321; Berkeley statlearn; UIC safety-critical control). The vault's own §14 already names this as the only path to transfer validity.

**Action:** build a **conformal calibration layer**: pair sim episodes with a small real-telemetry set, calibrate a prediction interval on the sim→real performance gap, and emit a **transfer-validity band** on the scorecard ("real success ∈ [x, y] at 90% coverage"). This is the wedge that turns "we ran simulations" into "we quantify the reality gap."

### 2.3 Model what the sim does *not* model — and gate claims on it
**External (robo2u):** keep a **written list of "things our sim does not model"** and *gate every sim-only claim against it*; the **#1 reality gap is contact & friction**, ahead of rendering; **actuator modeling + wide domain randomization** are the two highest-ROI fixes; *"treat friction coefficients, contact stiffness, and restitution as uncertain parameters to randomize, not constants."*

**Action:**
- Ship a machine-readable **`sim_capabilities.yaml`** (contact fidelity, deformables, sensor artifacts, actuator model, latency) and have the scorecard **annotate** any claim that depends on an unmodeled regime.
- Add **actuator modeling** (torque/current limits, lag, backlash) and **latency injection** to the mock/Isaac adapter — currently the mock commands ideal torque instantly and has zero sensing→actuation delay.
- **Domain-randomize friction/contact** instead of using fixed constants (the mock currently ignores `scenario.params` entirely — D12).

### 2.4 Adopt the two-simulator rule
**External:** *"Most real programs run two of these, not one"* — a **GPU sim to train** (Isaac Lab / MuJoCo MJX) and a **ROS-native sim to integrate/regression-test** (Gazebo / Isaac Sim). *"Trying to do both jobs in one simulator is where teams waste months."*

**Action:** position ValidSim as the **regression/validation layer across both**, not as a replacement for either. Add a **Gazebo adapter** alongside the Isaac HTTP adapter so the same scorecard can gate a ROS 2 stack — this widens the addressable market and matches how teams actually work.

### 2.5 Integrate the real benchmark ecosystem
**External:** the 2026 eval landscape is **LIBERO / LIBERO-Plus / LIBERO-Pro / RoboCasa / ManiSkill / VLA-Arena / RoboDojo / RoboMemArena**, plus **FlagEval-Robo**'s **sim + real dual-track** ranking; NVIDIA's **Isaac GR00T N** reference platform (June 2026) and **Isaac Lab**.

**Action:** ship first-class task definitions for **LIBERO** (simple tabletop) and **RoboCasa** (long-horizon) at minimum, so a customer's scorecard is **externally comparable** and reproducible. This converts "we have a benchmark" into "we speak the ecosystem's language." (Also: the mock's "success = base − randomization + checkpoint tier − difficulty" is a fabricated generator; any accuracy number from it is meaningless — never present mock numbers as model quality.)

---

## 3. ENHANCE — infrastructure, delivery, and quality (P1/P2)

### 3.1 Queue/lease robustness
**External:** the canonical pattern is an **atomic Lua "single-flight" lease**: check key → **increment a fencing counter** → write `owner:token` with TTL; renew/release via Lua; the **shared store rejects writes from an expired lease holder** (Redisson `FencedLock`; etcd `contrib/lock`). ValidSim's design already matches this — the gaps are the **sub-second lease floor** (`_utc_now` truncates to whole seconds, so a 1 s lease can be reclaimed while alive — currently a strict xfail), the **inconsistent clock seam** (`renew_lease`/`_apply_status` use raw `_utc_now`), and the **reclaim-budget off-by-one** between the claim and reap paths.

**Actions:** make the clock seam uniform; use millisecond precision (or a monotonic clock) for lease math; unify the reclaim-budget threshold; add a **dead-letter pubsub stream** the worker actually consumes; and make the Redis reaper **incremental** (the current one is O(all-time jobs) every poll).

### 3.2 Observability & error contracts
- Define **response models** for every route (currently none) → automatic OpenAPI correctness + client codegen.
- Unify the **three incompatible error contracts** into one envelope.
- Paginate `/api/v1/dashboard/history` (currently unbounded).
- Wire the `store_stats` fast paths (already written, currently dead) so list/dashboard don't hydrate full history.

### 3.3 Toolchain (2026 standard)
**External:** the Python toolchain has converged on **uv** (packaging/lock/venv, 10–100× faster, replaces pip/venv/poetry/pyenv) + **Ruff** (lint+format, replaces flake8/black/isort/pyupgrade) + **ty/mypy --strict** (type-check), all in one `pyproject.toml`, with **pre-commit** hooks.

**Actions:** add `uv.lock`; commit `pyproject.toml` + lock; adopt `ruff check --fix` + `ruff format` in pre-commit and CI; move to **mypy/ty strict** (the codebase already uses full type hints and frozen dataclasses — it's ready); delete the parallel `requirements*.txt` drift; expand the ruff rule set (`I, B, UP, SIM, C4, ASYNC, RUF`) beyond the current `E4/E5/E7/E9/F`.

### 3.4 Test-suite trust
- **Resolve the two contradictory SQLite `close()` contracts** and the two contradictory zero-baseline severity tests — at most one of each can pass.
- Stop shipping **red tests as documentation** (or mark them `xfail(strict=True)` with an owner + issue link so CI is meaningful).
- **Actually run** the ~68–75 fleet-written tests that have never executed; a green suite that certifies an inactive control is worse than no suite.
- Add the missing `baseline.json` so the benchmark gate can fail.

---

## 4. UPGRADE — architecture, compliance, and scale (P1/P2)

### 4.1 Immutable, tamper-evident evidence store
**External:** tamper-evidence = **append-only + SHA-256 hash chain** (each entry stores `prev_hash`), cryptographically verifiable, like git commits/blockchains (AuditWeave 2026; AgentLens; SOP-Snap). ValidSim's `SECURITY.md` claims tamper-detectability that is **false for memory/SQLite**, while `DELETE` physically removes evidence.

**Actions:**
- Add an **append-only `audit_chain` table** (or per-row `prev_hash`) with a `verify_chain()` command; make `DELETE` a **soft delete that writes a tombstone** into the chain rather than removing the row.
- Reconcile `SECURITY.md` with the code, or make the claim true.

### 4.2 Compliance posture (the terminal moat)
**External:** **EU AI Act** high-risk obligations are being **re-ordered/phased** (Omnibus VII, May 2026) but not dropped; **ISO 10218** was revised (Feb 2025) and **ISO 13482** covers personal-care robots; products like **Partenit** already ship **machine-checkable** compliance ("rules tied to ISO 13482, ISO 10218, EU AI Act, every action checked in <10ms with the citation ready for audit").

**Action:** this is ValidSim's stated "terminal moat" — build a **machine-checkable compliance mode**: map each gate rule to a **cited clause** (ISO 10218-1/-2, ISO 13482, EU AI Act Art. 9/15, EU Machinery Regulation) and emit an **audit-ready evidence package** (hash-chained, cited, exportable). Note the vault's own warning: the immutability gap and the working `DELETE` directly contradict this pitch — fix 4.1 first.

### 4.3 Scale & platform
**External:** the platform proposal's own thesis ("a high-quality single-host app, not yet a deployable platform") and the five hard blockers (K8s packaging, GPU orchestration, schema migrations, backups, release engineering).

**Actions:** add **schema migrations** (Alembic or a versioned DDL runner — the stores currently ALTER-on-open), a **backup/restore** path (runbook §5.1 is manual-only), and a **release process** (CHANGELOG link targets are placeholder `example.com`; no git tags despite SECURITY.md's "0.2.x supported").

---

## 5. BUILD — new capability that creates the moat (P1/P2)

| Build | What it is | Why it wins |
|---|---|---|
| **Transfer-validity band** (§2.2) | Conformal-calibrated sim→real gap interval on the scorecard | The only defensible claim in this market; no competitor productizes it |
| **Cross-sim regression** (§2.4) | One scorecard gating both Isaac and Gazebo pipelines | Matches how teams actually work (two sims) |
| **Benchmark hub** (§2.5) | LIBERO / RoboCasa / ManiSkill tasks out of the box | External comparability + instant credibility |
| **Compliance evidence package** (§4.2) | Hash-chained, clause-cited, exportable PDF/JSON | The "terminal moat" the vault names |
| **Scorecard self-audit** (§2.1) | Publish ValidSim's own Spearman rank-correlation vs held-out rollouts | Turns "trust us" into "here's our validity metric" |
| **Fleet gating** (from MVP non-goals) | Gate a whole fleet's checkpoints, not one model | The Tier-3 fleet-operator buyer tier |
| **Notify-as-product** (§1.2) | Slack/Teams/webhook scorecards, hardened | Makes the CI loop real end-to-end |

---

## 6. Evidence upgrades for fundraising / YC W27

The vault's biggest weakness is that **every traction number is a `[VERIFY]` placeholder**. External guidance (NVIDIA Inception: free, 10,000+ startups, credits; YC standard terms) is only useful with **dated artifacts**.

- **Convert the demo into a reproducible artifact:** a public repo + a committed `baseline.json` + a green CI run + a published scorecard for a *named* checkpoint (not `vrun-ed0c150a` on a mock).
- **Publish the sim-capability list and the transfer-validity metric** — these are the two artifacts that make the "CI for robots" claim non-hand-wavy.
- **Replace the six contradictory test counts** with one number produced by CI, quoted nowhere else.
- **Fix the story inconsistency:** the marketing site says "thousands of episodes" / "ten thousand scenarios" while the demo run is 1,050 episodes and the spec's 8-week plan (Isaac, real VLA, 100K runs) has no binary evidence — only a 15.56 s webm and synthetic 100.0-scored DB rows.

---

## 7. Prioritized roadmap

**Phase 0 — Stop the bleeding (days).** Memory-store read-copy; `checkpoint_id` binding; `make smoke`; `delete --latest`; commit `baseline.json`; auth on `/metrics`; CORS default; `git rm $null`; delete probe DBs. *(All small, all verifiable.)*

**Phase 1 — Make the gate honest (1–2 weeks).** Randomization ladder + regression abstention + adversarial hard gate + Wilson lower bound + minimum episode floor + persist `checkpoint_sha256`. *(This is the P0 that unblocks every downstream claim.)*

**Phase 2 — Harden (2–3 weeks).** SSRF allowlist + redirect-off; `t=,v1=` HMAC + idempotency + DLQ; notify wiring; CSP/SRI; per-key rate limiting; Prometheus monotonicity; lease clock/precision fixes; run the never-executed tests.

**Phase 3 — Differentiate (3–6 weeks).** Conformal transfer-validity band; `sim_capabilities.yaml` gating; actuator + latency modeling; LIBERO/RoboCasa tasks; Gazebo adapter; hash-chained audit store.

**Phase 4 — Platform & moat (6–12 weeks).** Schema migrations + backups + releases; machine-checkable compliance mode with cited clauses; scorecard self-audit published; uv/Ruff/ty toolchain; fleet gating.

---

## 8. Sources

**Sim-to-real / robotics validation**
- Robot Simulation & Digital Twins guide (sim CI ladder, determinism trap, two-simulator rule, contact/friction #1 gap, sim-capability list) — https://blog.robo2u.com/posts/robot-simulation-digital-twin-ultimate-guide
- Critical Interval MSE (offline validation metric, Spearman −0.87) — https://arxiv.org/html/2606.29898v2
- CoSMO: Adaptive Conformal Inference for robot navigation — https://arxiv.org/html/2610.18321
- Conformal Prediction (Berkeley statlearn) — https://www.stat.berkeley.edu/~ryantibs/statlearn-s23/lectures/conformal.pdf
- Isaac GR00T reference platform / Isaac Lab — https://developer.nvidia.com/isaac/lab/
- LIBERO benchmark — https://baike.baidu.com/item/LIBERO/67907566 · FlagEval-Robo dual-track — https://hub.baai.ac.cn/view/57324
- Sim-to-real gap (contact/friction as primary failure source) — https://baike.baidu.com/item/Sim-to-Real%20Gap/67505000

**Evaluation gating**
- Agent regression testing with Wilson lower bound, per-case reporting, zero-tolerance violations — https://www.alphalab.site/agent-nondeterministic-regression-testing
- Rogue: auditable policy checks + deterministic transcripts — https://www.thenextgentechinsider.com/rogue-open-source-ai-agent-testing-for-safety-reliability/

**Security**
- SSRF defense-in-depth (allowlist, private-range blocklist, redirect-off, DNS rebinding) — https://www.offensive360.com/blog/ssrf-vulnerability-server-side-request-forgery · OWASP cheat sheet — https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Webhook HMAC timestamp+nonce anti-replay — https://blog.csdn.net/weixin_32234493/article/details/165451854
- Webhook delivery best practices (retry, exponential backoff, DLQ, idempotency) — https://razorpay.com/docs/webhooks/best-practices/

**Distributed systems**
- Fencing tokens / atomic lease Lua — https://redisson.org/glossary/redis-lock.html · etcd `contrib/lock` — https://blog.csdn.net/gitblog_00318/article/details/143765809
- Prometheus counter monotonicity — https://prometheus.io/docs/prometheus/latest/feature_flags/
- Tamper-evident hash-chained audit logs — http://xueshu.baidu.com/s?wd=AuditWeave · https://www.mcpworld.com/en/detail/4b4b24a21bd6dfc76728c935a1928b78

**Compliance**
- EU AI Act timeline adjustment (Omnibus VII, May 2026) — https://chinawto.mofcom.gov.cn/article/jsbl/zszc/202606/20260603632825.shtml
- ISO 10218 (rev. Feb 2025) — https://baike.baidu.com/item/ISO%2010218/68868736
- Machine-checkable compliance (ISO 10218/13482, EU AI Act) — https://partenit.io/

**Toolchain / program**
- Modern Python with uv + Ruff (2026) — https://pyinns.com/python/modern-python-tools-and-libraries/modern-python-project-setup-with-uv-ruff-in-2026 · https://blog.rajpoot.dev/posts/python/modern-python-tooling-uv-ruff-2026
- NVIDIA Inception — https://www.nvidia.com/en-gb/startups/showcase/
