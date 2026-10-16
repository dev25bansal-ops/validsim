# Issues and Remediation Catalog

**Assessment date:** 2026-10-15  
**Repository baseline:** `98cc723ce0ff4e99f788d3c1a62f36d7fefb6188`  
**Working tree at assessment:** clean  
**Status:** evidence-backed remediation backlog; no code changes are included in this document

## Purpose and scope

This catalog records concrete problems requiring remediation in the ValidSim repository. It covers:

- correctness bugs and configuration hazards;
- measured performance bottlenecks;
- security vulnerabilities and integrity threats;
- architecture and reliability problems;
- code-quality and maintainability gaps; and
- technical debt, documentation drift, and reproducibility gaps.

The catalog is intentionally evidence-first. Each entry identifies a repository location, a reproduction or measurement method when available, expected versus actual behavior, business impact, severity, priority, effort, dependencies, and a recommended timeline. Findings that need a live PostgreSQL, Redis, Isaac worker, or externally reachable deployment are explicitly marked as needing follow-up validation.

## Rating rubric

### Severity

| Rating | Meaning |
|---|---|
| **Critical** | Can invalidate safety or validation evidence, enable a severe compromise, or cause systemic loss of integrity. Immediate containment is required. |
| **High** | Can expose or destroy user/system data, break distributed operation, publish an invalid artifact, or materially compromise reproducibility. |
| **Medium** | Causes meaningful degradation, operational risk, or a latent security exposure, but normally needs additional deployment conditions or scale to become severe. |
| **Low** | Localized maintainability, documentation, or process problem with limited immediate user impact. |

### Priority

- **P0:** contain or fix before the next externally reachable deployment, validation release, or safety-significant use.
- **P1:** fix before the next production/release tag or within the next engineering sprint.
- **P2:** schedule in the next maintenance cycle; do not allow it to remain undocumented.
- **P3:** backlog item with an owner and a target milestone.

Priority is not severity alone: exposure, likelihood, user impact, and dependency ordering are included.

### Effort and timeline

Effort is engineering time for implementation, tests, and review; it excludes waiting for external infrastructure or policy decisions. `h` means engineer-hours and `d` means engineer-days.

## Ranked remediation summary

| Rank | ID | Area | Severity | Priority | Effort | Recommended target |
|---:|---|---|---|---|---:|---|
| 1 | SEC-001 | Fail-open API authentication | High* | P0 | 2–4 h | Before any network-exposed deployment |
| 2 | SEC-002 | Remote-worker result integrity | Critical | P0 | 3–6 d | Before safety-significant worker approvals |
| 3 | SEC-004 | Silent mock-backend fallback | High | P0 | 2–4 h | Before production validation |
| 4 | SEC-003 | Plaintext worker bearer transport | High | P0 | 1–2 d | Before remote worker production use |
| 5 | REL-001 | Redis queue-capacity accounting | High | P0 | 4–8 h | Before multi-worker queue deployment |
| 6 | SEC-006 | Process-local store and queue | High | P0 | 1–2 d | Before multiple API/worker replicas |
| 7 | PERF-001 | Full-history validation pagination | High | P1 | 1–2 d | Before history grows materially |
| 8 | BUG-001 | Missing benchmark baseline | High | P1 | 2–4 h | Before the next nightly gate |
| 9 | SEC-005 | Release gates run after publication | High | P1 | 2–4 h | Before the next tagged release |
| 10 | BUILD-001 | Unlocked Python dependencies/runtime | High | P1 | 1–2 d | Before the next release tag |
| 11 | BUILD-004 | No clean-Linux/offline bootstrap | High | P1 | 1–2 d | Before claiming reproducible rebuilds |
| 12 | BUILD-005 | Tracked site artifacts/no site CI | High | P1 | 1–2 d | Before site release ownership |
| 13 | SEC-008 | Webhook SSRF | Medium | P1 | 1–2 d | Before notification wiring |
| 14 | SEC-009 | Process-local limits and metrics | Medium | P1 | 1–2 d | Before horizontal scaling |
| 15 | DB-001 | Runtime DDL instead of migrations | High | P1 | 2–4 d | Before multi-replica production upgrades |
| 16 | PERF-002 | Unbounded dashboard/job/model history | Medium | P1 | 1–2 d | Before sustained dashboard polling |
| 17 | BUG-002 | New benchmark names ignored | High | P1 | 2–4 h | With benchmark baseline repair |
| 18 | BUILD-002 | Python 3.10 support mismatch | High | P1 | 2–4 h | Before advertising Python 3.10 |
| 19 | BUG-003 | Zero benchmark baseline crashes | Medium | P1 | 1 h | With benchmark gate repair |
| 20 | SEC-007 | Wildcard CORS default | Medium | P1 | 2–4 h | Before browser-facing deployment |
| 21 | SEC-010 | Unauthenticated/plaintext data services | Medium | P1 | 1–2 d | Before non-isolated Compose use |
| 22 | DB-002 | Missing PostgreSQL composite index | Medium | P2 | 4–8 h | Next database maintenance sprint |
| 23 | BUILD-003 | Multiple dependency authorities | High | P1 | 1 d | With lockfile work |
| 24 | DOC-002 | LLM configuration contradiction | Medium | P2 | 2–4 h docs; 1–3 d code | Next product decision review |
| 25 | DOC-005 | Audit-trail immutability overclaim | Medium | P1 | 1–2 d | Before compliance/enterprise claims |
| 26 | DOC-006 | Existing issue catalog is stale | Medium | P2 | 2–4 h | Next audit cycle |
| 27 | QUAL-002 | Build/CI/status workflow drift | Medium | P2 | 4–8 h | Next build-maintenance cycle |
| 28 | QUAL-003 | Quality policy not enforced | Low | P3 | 4–8 h | Next quality-tooling cycle |
| 29 | DOC-001 | Rate-limit documentation drift | Medium | P2 | 1–2 h | Immediate documentation pass |
| 30 | DOC-003 | Obsolete database variable in runbook | Low | P2 | 30 min | Immediate documentation pass |
| 31 | DOC-004 | ADR contains obsolete gate semantics | Low | P2 | 1–2 h | Next ADR maintenance pass |

\* `SEC-001` is High for a network-exposed deployment. Its CVSS score is lower in a loopback-only development deployment because the attack surface is constrained.

---

## D. Architecture, build, and scalability problems

### BUILD-001 — Python dependencies and runtime are not reproducibly locked

**Severity:** High  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Package-management policy, supported Python versions, lockfile tooling  
**Timeline:** Before the next release tag

**Problem and evidence:** `pyproject.toml:2-3,13,18-40` and `requirements.txt:1-8` use lower bounds rather than exact versions. No Python lockfile with hashes was present. `Dockerfile:12,29` and `docker-compose.yml:82,98` use mutable Python, Redis, and PostgreSQL tags. Build tools in `.github/workflows/release.yml:150-151` are unpinned.

**Expected:** The same commit resolves the same dependency graph, runtime, OS image, and build tools on every supported machine.  
**Actual:** A later package/image release can silently change behavior or vulnerability exposure.

**Business impact:** Clean rebuilds, incident reproduction, rollbacks, SBOM accuracy, and offline/air-gapped installation are unreliable.

**Remediation:** Select one canonical lock workflow, commit hashes, pin the Python/Node runtimes and container digests, and pin release tooling. Keep generated requirements exports synchronized from the canonical source.

### BUILD-002 — Declared Python 3.10 support is internally inconsistent

**Severity:** High  
**Priority:** P1  
**Effort:** 2–4 h  
**Dependencies:** Decision to raise the minimum or retain Python 3.10  
**Timeline:** Before advertising Python 3.10 compatibility

**Problem and evidence:** `pyproject.toml:13` declares `>=3.10`. `validsim/project_config.py:80-98` documents/imports `tomli` on Python 3.10, but `tomli` is absent from all dependency manifests. `tests/test_delivery_pipeline.py:11-18` imports Python-3.11-only `tomllib`. CI runs only Python 3.12 in `.github/workflows/ci.yml:30-42` and the nightly/release workflows.

**Expected:** Every declared supported interpreter can install and collect the test suite.  
**Actual:** Python 3.10 can fail on project configuration or test collection, and CI does not exercise it.

**Business impact:** Users following package metadata can receive an unusable installation; support claims are not evidence-backed.

**Remediation:** Either raise the minimum to the tested runtime or add conditional `tomli` support, replace the unguarded `tomllib` import, and add a Python 3.10 CI job.

### BUILD-003 — Multiple dependency authorities can drift

**Severity:** High  
**Priority:** P1  
**Effort:** 1 d  
**Dependencies:** BUILD-001 and canonical manifest decision  
**Timeline:** During lockfile work

**Problem and evidence:** `Makefile:16-19` installs both requirements files; `Dockerfile:23-26` installs only runtime requirements; `actions/validate/action.yml:103-119` may install the package or fall back to requirements; `.github/workflows/ci.yml:43-53` installs both; `pyproject.toml:16-17,29-30` says they must remain synchronized. The test at `tests/test_delivery_pipeline.py:141-145` checks only a small subset of declaration parity.

**Expected:** Local, CI, Docker, and package installs resolve the same supported dependency graph.  
**Actual:** Different paths can install different package sets and versions.

**Business impact:** “Works in CI” and “works in the image” can diverge, making failures hard to reproduce and fixes easy to regress.

**Remediation:** Make `pyproject.toml` plus a generated lockfile authoritative, or make one lockfile authoritative and test every supported install path against it.

### BUILD-004 — No clean-Linux/offline bootstrap or single verified test command

**Severity:** High  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** BUILD-001, Linux bootstrap design, wheel/cache policy  
**Timeline:** Before claiming clean-room or air-gapped rebuild support

**Problem and evidence:** `Makefile:3-25` assumes GNU Make and installs into the caller’s environment. `scripts/build.ps1:39-72` is Windows PowerShell-specific. `README.md:22-47` is PowerShell-first. `Dockerfile:23-26` requires network access. There is no Python wheelhouse/cache procedure or documented `pip --no-index` path.

**Expected:** A clean Linux clone can create its environment, install exact dependencies, and run the project’s canonical checks with one documented command.  
**Actual:** Existing commands assume preinstalled tooling, network access, and/or Windows PowerShell.

**Business impact:** The required clean-Linux rebuild is not repeatable, and offline recovery or isolated validation cannot be performed reliably.

**Remediation:** Add a Linux-compatible bootstrap and one canonical test command, document Docker and non-Docker paths separately, and provide an explicit offline cache/wheelhouse workflow.

### BUILD-005 — Site install artifacts and generated state are tracked; site is absent from CI

**Severity:** High  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Hosting/runtime decision, Node version policy  
**Timeline:** Before treating the marketing site as a release artifact

**Problem and evidence:** `site/package-lock.json` exists, but **6,372 files under `site/node_modules` are tracked**, including Windows binaries such as `site/node_modules/.bin/astro.cmd` and `site/node_modules/@esbuild/win32-x64/esbuild.exe`. Generated Astro state under `site/.astro` is also tracked. `site/package.json:7-18` has build/check scripts, but no workflow runs them and no Node runtime pin or `engines` field was found.

**Expected:** Source and lockfile are versioned; generated dependencies/state are ignored; CI verifies the site on a supported Node runtime.  
**Actual:** The repository contains platform-specific generated artifacts and no tested site build contract.

**Business impact:** Clone size, review noise, and platform drift increase; Linux site failures can remain undetected until deployment.

**Remediation:** Remove tracked `node_modules`/`.astro`, add ignore rules, pin Node/npm, and add CI steps for `npm ci`, `npm run check`, and `npm run build`.

### DB-001 — Runtime DDL is used instead of versioned migrations

**Severity:** High  
**Priority:** P1  
**Effort:** 2–4 d for an initial migration baseline  
**Dependencies:** Migration owner, rollout policy, database privileges  
**Timeline:** Before multi-replica production upgrades

**Problem and evidence:** `validsim/store/postgres.py:91-120,476-495` and `validsim/store/sqlite.py:50-77` create indexes and issue `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` during application startup. No migrations directory, migration tool configuration, or schema-version directory was found. `docs/PLATFORM_PROPOSAL.md:57-67` acknowledges the lack of schema versioning.

**Expected:** Schema changes are versioned, reviewable, independently runnable, and compatible with expand/contract deployment.  
**Actual:** Every application process needs schema mutation privileges and performs ad hoc startup DDL.

**Business impact:** Concurrent upgrades, rollback compatibility, schema provenance, and least-privilege database roles are harder to guarantee.

**Remediation:** Introduce versioned migrations, separate migration execution from application startup, add a schema version, and remove DDL privileges from the runtime role.

### DB-002 — PostgreSQL lacks the composite checkpoint/history index used by SQLite

**Severity:** Medium  
**Priority:** P2  
**Effort:** 4–8 h plus migration coordination  
**Dependencies:** DB-001 and representative query-plan data  
**Timeline:** Next database-maintenance sprint

**Problem and evidence:** SQLite creates `(checkpoint_id, created_at)` at `validsim/store/sqlite.py:65-68`, while PostgreSQL creates separate single-column indexes at `validsim/store/postgres.py:94-108`. PostgreSQL queries filter by checkpoint and sort by creation time at `validsim/store/postgres.py:560-568`.

**Expected:** PostgreSQL has an index matching the filter/order access path.  
**Actual:** Growing checkpoint history may require filtering and sorting more rows than necessary.

**Business impact:** Checkpoint-history latency and database CPU can degrade at scale. Exact production impact still needs `EXPLAIN` and representative-load measurement.

**Remediation:** Add the composite index through a migration and compare query plans before/after.

---

## E. Code quality and maintainability

### QUAL-002 — Build scripts, CI, Makefile, and Build Status do not describe one process

**Severity:** Medium  
**Priority:** P2  
**Effort:** 4–8 h  
**Dependencies:** Canonical build-command decision  
**Timeline:** Next build-maintenance cycle

**Problem and evidence:** `CONTRIBUTING.md:7,61-87` presents local commands as CI-equivalent, but `Makefile:21-28` separates normal tests from the coverage gate. `.github/workflows/ci.yml:55-64` runs Ruff and coverage while `scripts/build.ps1:74-88` runs a different pytest command. `scripts/build.ps1:130-132` and `vault/00 - Dashboard/Build Status.md:11-15` claim the vault status is regenerated in CI, but current workflows do not invoke that script and `builds/` is ignored.

**Expected:** One canonical command and status-generation path are executable from a clean clone and match CI.  
**Actual:** Developers can receive different verification levels depending on entry point, and the “latest” vault status cannot be reliably regenerated.

**Business impact:** False confidence in local verification and stale engineering status reporting.

**Remediation:** Choose one canonical build command, invoke it from CI or remove stale claims, and publish generated status as CI artifacts rather than relying on ignored local files.

### QUAL-003 — Written quality policy is broader than automated enforcement

**Severity:** Low  
**Priority:** P3  
**Effort:** 4–8 h for a scoped type-checking baseline  
**Dependencies:** Type-checking tool and acceptable baseline  
**Timeline:** Next quality-tooling cycle

**Problem and evidence:** `CONTRIBUTING.md:112-119,142-155` requires type hints, docstrings, frozen models, `__all__`, and branch-coverage behavior. CI at `.github/workflows/ci.yml:55-64` runs Ruff and pytest/coverage only. `pyproject.toml:53-68` contains Ruff configuration but no mypy/pyright configuration, and `Makefile:11-28` has no type-check command.

**Expected:** Release-critical quality policy is mechanically checked, or the written policy accurately describes reviewer expectations.  
**Actual:** Several requirements depend on manual review and can drift.

**Business impact:** Public API/type drift and inconsistent maintainability standards can accumulate unnoticed.

**Remediation:** Add a scoped type-checking job and structural checks, or narrow the documented policy to enforced guarantees. Avoid enabling broad lint groups without a migration plan.

---

## F. Technical debt and documentation drift

### DOC-001 — Security-sensitive rate-limit documentation is stale

**Severity:** Medium  
**Priority:** P2  
**Effort:** 1–2 h  
**Dependencies:** Documentation owner and source-of-truth decision  
**Timeline:** Immediate documentation pass

**Problem and evidence:** Current configuration documents the limiter as enabled by default in `.env.example:105-113`, `README.md:76-78`, `docs/api-reference.md:149-156,819-821`, and `docs/runbook.md:75,124-134`. The vault still says it is disabled/opt-in at `vault/09 - Risks/Risk Register.md:29-31`, `vault/04 - Engineering/API Design.md:120-121`, and `vault/04 - Engineering/Security Hardening.md:15-16`.

**Expected:** Operators and risk reviewers see one current security-default statement.  
**Actual:** Internal risk documentation can lead operators to believe rate limiting is absent.

**Business impact:** Incorrect threat assessments, deployment decisions, and incident response.

**Remediation:** Update or date the stale vault notes and add a consistency check for security-sensitive defaults.

### DOC-002 — LLM scenario configuration is contradictory

**Severity:** Medium  
**Priority:** P2  
**Effort:** 2–4 h for documentation-only correction; 1–3 d to wire and test the feature  
**Dependencies:** Product decision about whether LLM generation is shipped  
**Timeline:** Next product decision review

**Problem and evidence:** `docs/runbook.md:83-85` says an API key selects the LLM generator, while `docs/runbook.md:292-301` says no production caller exists. `docs/scenario-taxonomy.md:22-27,373-393` contains the same contradiction and requires an additional enable flag. `README.md:214-216` correctly calls the path unreachable; `.env.example:143-157` documents variables without the enable flag.

**Expected:** Operators can determine whether production runs use deterministic or LLM-generated scenarios.  
**Actual:** Setting an API key can create the false belief that production runs use LLM generation.

**Business impact:** Misleading validation provenance, unnecessary secret provisioning, and unsupported product claims.

**Remediation:** Either wire the factory into the shipped run path with tests, or label all LLM settings library-only and remove them from operational quick starts until reachable.

### DOC-003 — Runbook references obsolete database configuration

**Severity:** Low  
**Priority:** P2  
**Effort:** 30 min  
**Dependencies:** None beyond documentation ownership  
**Timeline:** Immediate documentation pass

**Problem and evidence:** `docs/runbook.md:116-122` describes `VALIDSIM_DATABASE_URL`, while current Compose uses `VALIDSIM_PG_URL` at `docker-compose.yml:51-64,129-138`. `vault/04 - Engineering/Security Hardening.md:55-56` records that the old wiring was removed.

**Expected:** The runbook names the variable consumed by the current container.  
**Actual:** Operators can set a variable that the container ignores.

**Remediation:** Replace it or mark it explicitly historical.

### DOC-004 — ADR 0002 contains obsolete gate semantics

**Severity:** Low  
**Priority:** P2  
**Effort:** 1–2 h  
**Dependencies:** ADR ownership  
**Timeline:** Next ADR maintenance pass

**Problem and evidence:** `docs/adr/0002-scorecard-composite-gate.md:100-106` first describes a composite-only reread, then says ADR 0006 supersedes it. `docs/adr/0006-gate-reads-the-durable-store.md:34-62` defines the current store-authoritative behavior.

**Expected:** Readers can identify one active gate contract.  
**Actual:** The obsolete text remains easy to implement or review against.

**Remediation:** Replace the old paragraph with an explicit historical note and link ADR 0006 as the active contract.

### DOC-005 — Audit-trail and immutability language exceeds current behavior

**Severity:** Medium  
**Priority:** P1  
**Effort:** 1–2 d plus product/legal review  
**Dependencies:** Deletion policy, compliance claims, and enterprise requirements  
**Timeline:** Before compliance or enterprise claims

**Problem and evidence:** `docs/adr/0004-store-abstraction-sqlite-postgres.md:8-13,65-107` describes a trustworthy audit trail and compliance asset while acknowledging deletion. `docs/api-reference.md:333-344` documents permanent deletion, and `docs/runbook.md:499-520` states that the store is not tamper-evident or immutable.

**Expected:** Product and compliance claims precisely match deletion and tamper-resistance behavior.  
**Actual:** “Audit trail,” “compliance asset,” and deletion semantics can be read as contradictory guarantees.

**Business impact:** Customer, regulatory, and procurement risk if evidence-retention guarantees are overstated.

**Remediation:** Supersede or revise the ADR; decide whether to restrict deletion, add a deletion ledger, or remove immutable-audit language.

### DOC-006 — Existing issue catalog is stale and contradicts repository state

**Severity:** Medium  
**Priority:** P2  
**Effort:** 2–4 h  
**Dependencies:** Decide whether it is historical or current  
**Timeline:** Next audit/documentation cycle

**Problem and evidence:** `docs/ISSUE_CATALOG.md:3-5` describes an old commit, modified working tree, 34 untracked artifacts, and 227 tracked files. The current baseline is clean at `98cc723`; `site/node_modules` alone contains 6,372 tracked files. The catalog also labels tracked test files as untracked and includes findings later marked fixed.

**Expected:** A current issue catalog is safe to use for prioritization and repository inventory.  
**Actual:** Historical and open findings are mixed, and current repository metadata is wrong.

**Business impact:** Already-fixed work can be reopened, required tests can be mistaken for disposable artifacts, and prioritization can use invalid counts.

**Remediation:** Label the old document as a historical snapshot and maintain open/closed findings in this catalog or a generated audit report with baseline metadata.

---

## Validation limitations and follow-up evidence

The following limits are important when interpreting this catalog:

1. **PostgreSQL scale:** The pagination timings were measured with SQLite. PostgreSQL source inspection confirms the same full-history query pattern, but production PostgreSQL timing and query plans still need a representative database.
2. **Redis capacity:** The queue-capacity defect was reproduced through a Lua-capable `fakeredis` path. A live Redis integration test should be added before and after remediation.
3. **Remote exploitability:** Anonymous deletion is locally confirmed. Internet or LAN exploitability depends on port publishing, reverse-proxy configuration, and network policy.
4. **Worker integrity:** The forged-worker response was tested with a mock transport. A real worker integration test is required to verify digest and identity enforcement end to end.
5. **Full test suite:** Targeted API, queue, benchmark, scorecard, and Ruff checks passed during the evidence pass. The complete `tests/` suite did not finish within five minutes and displayed failures before timing out; it is not treated as passing.
6. **No production secrets were copied:** This catalog contains paths, behavior, and redacted examples only. It does not include tokens, credentials, or private payloads.

## Recommended execution sequence

### Phase 0 — Containment before deployment

1. Make API authentication fail closed (`SEC-001`).
2. Reject unknown backends and require explicit real/mock selection (`SEC-004`).
3. Prevent remote worker use over plaintext and require credentials (`SEC-003`).
4. Disable production approval on unbound worker results until `SEC-002` is addressed.
5. Do not scale API/worker processes until durable shared store/queue configuration is enforced (`SEC-006`).

### Phase 1 — Release and rebuild integrity

1. Repair release dependency ordering and permissions (`SEC-005`).
2. Establish the Python lockfile/runtime pin (`BUILD-001`) and canonical dependency authority (`BUILD-003`).
3. Add the clean Linux/offline bootstrap (`BUILD-004`).
4. Repair the benchmark baseline and comparator (`BUG-001`, `BUG-002`, `BUG-003`).
5. Remove tracked site artifacts and add site CI (`BUILD-005`).

### Phase 2 — Scale and data integrity

1. Fix Redis queue accounting (`REL-001`).
2. Add bounded store/queue/dashboard queries (`PERF-001`, `PERF-002`).
3. Introduce versioned database migrations and the PostgreSQL composite index (`DB-001`, `DB-002`).
4. Move admission control/rate limits/metrics to shared infrastructure (`SEC-009`).
5. Harden webhook egress before enabling notifications (`SEC-008`).

### Phase 3 — Documentation and maintainability

1. Reconcile runbooks, vault notes, ADRs, and security defaults (`DOC-001` through `DOC-005`).
2. Archive or replace the stale historical issue catalog (`DOC-006`).
3. Align local/CI/build status commands (`QUAL-002`).
4. Add scoped type checking or narrow the quality policy (`QUAL-003`).

## A. Software bugs and correctness defects

### BUG-001 — Nightly benchmark gate has no baseline

**Severity:** High  
**Priority:** P1  
**Effort:** 2–4 h to generate and commit a baseline; 1–2 d if separate fast/slow baselines are introduced  
**Dependencies:** Stable runner, representative workload, baseline owner, and benchmark policy  
**Timeline:** Before the next nightly gate or release decision

**Problem and evidence:** `tests/bench/baseline.json` is absent. `tests/bench/conftest.py:105-122` requires it when comparison is requested, while `.github/workflows/nightly.yml:124-139` runs the comparison. The documented comparison command produces `BENCHMARK GATE CANNOT MEASURE: no baseline to compare against` and exits nonzero.

**Reproduction:**

```powershell
python -m pytest tests/bench/test_bench_engine.py::test_bootstrap_ci_default_resamples[1000] --validsim-benchmark-compare -q
```

**Expected:** The nightly job compares measurements with a committed, versioned baseline.  
**Actual:** The gate cannot measure and fails because no baseline exists.

**Business impact:** Nightly performance validation is unavailable. Teams either receive a permanently failing gate or disable/ignore it, reducing early regression detection.

**Remediation:** Generate a baseline on the supported runner, commit it with provenance, define fast/slow baseline ownership, and fail clearly when the baseline is missing.

### BUG-002 — Benchmark measurements missing from the baseline are silently ignored

**Severity:** High  
**Priority:** P1  
**Effort:** 2–4 h  
**Dependencies:** Baseline coverage policy and BUG-001  
**Timeline:** With benchmark-gate repair

**Problem and evidence:** `tests/bench/conftest.py:123-137` skips a current measurement when `prior is None`. A synthetic comparison with only `known` in the baseline and `new_measurement` in the current results returned `[]` rather than reporting incomplete coverage.

**Expected:** A new benchmark name must be explicitly baselined or fail as unmeasured.  
**Actual:** Newly added measurements can be absent from the gate without warning.

**Business impact:** New benchmark surfaces can regress without any monitored threshold, creating false confidence in performance coverage.

**Remediation:** Require every measured name to exist in the applicable baseline, or emit a failing “baseline incomplete” result. Use separate fast/slow baselines if their measured sets differ.

### BUG-003 — Zero-valued benchmark baseline causes an unhandled division by zero

**Severity:** Medium  
**Priority:** P1  
**Effort:** 1 h  
**Dependencies:** Define absolute-delta semantics for zero/near-zero timings  
**Timeline:** With benchmark-gate repair

**Problem and evidence:** `tests/bench/conftest.py:138-145` computes `delta / prior` after the absolute-delta check. A baseline value of `0.0` and current value of `50.0` raise `ZeroDivisionError`.

**Expected:** Invalid or zero baselines produce a clear baseline-integrity error or use an absolute-delta comparison.  
**Actual:** The gate crashes with an unhandled exception.

**Business impact:** A malformed or newly generated baseline can fail the benchmark job without an actionable diagnosis.

**Remediation:** Treat `prior <= 0` as invalid for percentage comparison and use a bounded absolute-delta rule or explicit validation failure.

### REL-001 — Redis `max_depth` excludes running jobs

**Severity:** High  
**Priority:** P0  
**Effort:** 4–8 h  
**Dependencies:** Redis queue accounting design and cross-backend contract tests  
**Timeline:** Before multi-worker queue deployment

**Problem and evidence:** `validsim/jobs/queue.py:606-625` counts the Redis ready list, while `validsim/jobs/queue.py:1649-1690` removes a claimed job from that list. The in-memory reference at `validsim/jobs/queue.py:1140-1164` counts queued plus running jobs.

**Reproduction:** With `max_depth=1`, enqueue one job, claim it, then enqueue a second. The Redis path returned:

```text
queued running ['running', 'queued'] 1 2
```

**Expected:** The second enqueue is rejected because one job is already active.  
**Actual:** The second job is accepted because only the ready list is counted.

**Business impact:** Configured admission limits can be exceeded, causing memory/worker overload and inconsistent behavior between development and production backends.

**Remediation:** Maintain an atomic pending-job counter or update queued/running/terminal counters consistently in Redis Lua scripts. Add a live-Redis parity test; the existing test double does not execute the same capacity logic.

### SEC-004 — Invalid simulation backend silently selects the mock backend

**Severity:** High  
**Priority:** P0  
**Effort:** 2–4 h  
**Dependencies:** Deployment manifest changes and explicit development-mode policy  
**Timeline:** Before production validation

**Problem and evidence:** `validsim/sim/__init__.py:38-58` selects Isaac only for the exact value `isaac`; every other value, including a typo or missing variable, selects `MockIsaacBackend`. `.env.example:124-128` explicitly defaults to `mock`, while Compose does not set the backend.

**Reproduction:**

```text
VALIDSIM_BACKEND=typo
=> MockIsaacBackend mock-isaac
```

**Expected:** Unknown values fail startup with an actionable configuration error.  
**Actual:** A real-worker deployment can silently run synthetic validation.

**Business impact:** A validation can appear successful without exercising the intended physics/GPU worker, invalidating safety or release decisions.

**Remediation:** Accept only `mock` and `isaac`; require `mock` explicitly for development and require Isaac URL/authentication/checkpoint verification in production.

---

## B. Performance bottlenecks

### PERF-001 — Validation pagination hydrates the entire durable history

**Severity:** High  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Store-level summary-query interface and ordering contract  
**Timeline:** Before validation history grows materially

**Problem and evidence:** `validsim/api/main.py:1137-1162` calls `st.history()` and only then reverses and slices. `validsim/store/sqlite.py:406-431` and `validsim/store/postgres.py:570-597` reconstruct full records, including serialized episode data, before the API retains a page.

**Reproduction and measured impact:** For one-episode records in an in-memory SQLite database:

| Rows | `history()` | `GET /api/v1/validations?limit=1` |
|---:|---:|---:|
| 1,000 | 30.54 ms | not recorded |
| 5,000 | 148.23 ms | approximately 245 ms |

The memory store was approximately 0.25 ms at 5,000 rows, showing the durable-store scaling problem.

**Expected:** A one-row page performs a bounded, summary-only query.  
**Actual:** Latency and deserialization work grow with total history, not page size.

**Business impact:** API response time, CPU, database I/O, and memory use grow as evidence accumulates. Larger production histories can cause slow dashboards and request timeouts.

**Remediation:** Add indexed, newest-first summary queries with `LIMIT`/cursor semantics. Keep full-detail reads for scorecards and failure views.

### PERF-002 — Dashboard, model, regression, and job endpoints are unbounded

**Severity:** Medium  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Store/queue pagination APIs and dashboard client changes  
**Timeline:** Before sustained dashboard polling

**Problem and evidence:** `validsim/api/dashboard.py:35-56` returns all dashboard history. `validsim/api/main.py:1239-1246,1294-1336` aggregates full model/regression history. `validsim/jobs/router.py:183-209` and `validsim/jobs/queue.py:1699-1709` scan all jobs before slicing. `validsim/web/app.js:784-790,994-1001` polls those endpoints.

**Measured impact at 5,000 SQLite-backed runs:**

| Endpoint | Approx. latency | Response size/behavior |
|---|---:|---|
| `/api/v1/dashboard/history` | 172 ms | approximately 975 KB |
| `/api/v1/models?limit=1` | 143 ms | full-history aggregation before one-row output |
| `/api/v1/validations?limit=1` | 245 ms | full-history hydration |

After 1,000 terminal Redis jobs, `RedisJobQueue.list()` took approximately 47 ms and its insertion index still held all 1,000 IDs.

**Expected:** Dashboard and list endpoints request bounded recent data, and terminal job history has a retention policy.  
**Actual:** Payload, latency, and retained IDs grow with lifetime history.

**Business impact:** Repeated browser polling consumes increasing server CPU, database bandwidth, Redis memory, and client memory.

**Remediation:** Add bounded queries/cursors at the storage boundary, limit dashboard history, and expire or archive terminal job records.

---

## C. Security and validation-integrity vulnerabilities

### SEC-001 — API authentication fails open by default

**Severity:** High when network-exposed; Medium for loopback-only development  
**CVSS v3.1:** 9.8, `AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H`, assuming network reachability  
**Priority:** P0  
**Effort:** 2–4 h  
**Dependencies:** Deployment/authentication policy and development override  
**Timeline:** Before any network-exposed deployment

**Problem and evidence:** `validsim/api/main.py:914-920,1007-1049,1051-1065` makes authentication a no-op when no API key is configured. `validsim/api/main.py:1169-1195` exposes destructive deletion. `docker-compose.yml:45-50` defaults to development mode with an empty key; `Dockerfile:39-46,54-68` does not force production mode.

**Reproduction:** With `VALIDSIM_API_KEY` and `VALIDSIM_ENV` unset, a test client observed `auth_enabled: false`, created a validation with status `201`, and deleted it with status `204` without credentials.

**Expected:** Destructive and data-bearing APIs require authentication, or the service refuses to start without it.  
**Actual:** Anonymous clients can submit work, enumerate evidence, retrieve scorecards/failures, and delete runs.

**Business impact:** Unauthorized data access/deletion, compute abuse, and attacker-created approval records.

**Remediation:** Fail closed by default for network-serving profiles; require an explicit development-only unauthenticated override; keep production startup validation and document the deployment profiles.

### SEC-002 — Remote-worker results are not bound to the requested artifact or task

**Severity:** Critical qualitative integrity issue  
**CVSS:** Not responsibly calculable because exploitation requires a compromised/misbehaving configured worker or altered worker traffic.  
**Priority:** P0  
**Effort:** 3–6 d  
**Dependencies:** Worker protocol, artifact registry, digest propagation, contract versioning, and worker migration  
**Timeline:** Before safety-significant worker approvals

**Problem and evidence:** `validsim/sim/isaac_worker.py:216-275,527-560` validates field shape but not returned `task_id`, `episode_id`, checkpoint identity/digest, finite numeric values, or physical ranges. `validsim/config.py:172-190` accepts `checkpoint_sha256`, but `validsim/api/main.py:769-783` never uses it. `validsim/engine/pipeline.py:137-145` immediately scores the returned results.

**Reproduction:** A mocked worker response with `task_id: forged-task`, `episode_id: forged-id`, negative duration/distance/force, and success `true` was accepted and passed into scoring.

**Expected:** The result proves it corresponds to the requested checkpoint, task, seed, and episode, and all safety fields are finite and within domain bounds.  
**Actual:** A worker can return structurally valid but semantically forged data.

**Business impact:** ValidSim can approve a checkpoint that was not tested, was substituted, or produced impossible safety metrics.

**Remediation:** Add immutable artifact digest and worker-loaded checkpoint echo; match checkpoint/task/episode/seed; reject non-finite/out-of-range values; version the contract; add adversarial contract tests.

### SEC-003 — Isaac worker permits plaintext bearer-token transport

**Severity:** High  
**CVSS v3.1:** 8.1, `AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H`, assuming an on-path attacker  
**Priority:** P0  
**Effort:** 1–2 d  
**Dependencies:** TLS certificates/service identity and worker deployment support  
**Timeline:** Before remote worker production use

**Problem and evidence:** `validsim/sim/isaac_worker.py:321-325,342-344,538-584` accepts arbitrary worker URLs and sends a bearer token when configured. `.env.example:134-141` documents an `http://localhost:8080` URL and makes the key optional.

**Reproduction:** A mocked transport captured `url=http://worker.internal/episodes/run` and `Authorization: Bearer secret`.

**Expected:** Production worker credentials and validation payloads use authenticated encrypted transport.  
**Actual:** An `http://` configuration transmits the token and results in cleartext; missing credentials are also allowed.

**Business impact:** An on-path attacker can steal the worker token, impersonate the worker, forge results, or disrupt validation.

**Remediation:** Reject HTTP outside explicit local development, require credentials in production, use HTTPS certificate validation/mTLS or workload identity, and combine with SEC-002 result binding.

### SEC-005 — Release publishes an image before package validation completes

**Severity:** High release-integrity risk  
**CVSS:** Not directly applicable; exploitation requires repository/tag/workflow execution privileges.  
**Priority:** P1  
**Effort:** 2–4 h  
**Dependencies:** Release DAG and registry publication policy  
**Timeline:** Before the next tagged release

**Problem and evidence:** `.github/workflows/release.yml:102-129` pushes the Docker image after `test`; `.github/workflows/release.yml:131-157` runs package validation with `needs: docker`. Workflow-wide permissions at `.github/workflows/release.yml:21-23` grant `packages: write` to jobs that do not need it.

**Expected:** All package and test gates complete before publication, and only the publishing job receives package-write permission.  
**Actual:** The graph is `test → docker push → package-check`; an invalid package can be discovered after publication.

**Business impact:** Registry artifacts can represent a release that did not pass all advertised checks; excessive token scope increases supply-chain blast radius.

**Remediation:** Make Docker depend on both test and package-check; scope `packages: write` to Docker; consider immutable digests, signing, SBOM, and provenance.

### SEC-006 — Default memory store and queue split state across processes

**Severity:** High  
**Priority:** P0  
**Effort:** 1–2 d  
**Dependencies:** Durable PostgreSQL/Redis availability, migrations, deployment profiles  
**Timeline:** Before multiple API/worker replicas

**Problem and evidence:** `validsim/store/__init__.py:27-30,46-73` defaults to memory and silently falls back for unknown values. `validsim/jobs/queue.py:61-78,2019-2034` does the same for queues. `validsim/store/memory.py:346-349` and the in-memory queue keep state process-locally.

**Reproduction:** Two default stores each reported zero records. A job enqueued in one default queue was invisible to a second queue instance.

**Expected:** Production startup requires shared durable backends, and invalid backend names fail fast.  
**Actual:** Restarts lose runs; API and worker processes can disagree about jobs; replicas have divergent histories.

**Business impact:** Loss or inconsistency of validation evidence and jobs, with behavior dependent on request routing.

**Remediation:** Require PostgreSQL/Redis in production profiles, reject unknown backend names, and add startup checks that API and worker share the same durable backends.

### SEC-007 — CORS defaults to wildcard

**Severity:** Medium  
**Priority:** P1  
**Effort:** 2–4 h  
**Dependencies:** Dashboard-origin configuration  
**Timeline:** Before browser-facing deployment

**Problem and evidence:** `validsim/api/main.py:903-910` defaults CORS to `*` with all methods/headers; `.env.example:100-103` and `README.md:72-78` document the wildcard.

**Reproduction:** A request to `/metrics` with `Origin: https://evil.example` returned `Access-Control-Allow-Origin: *`.

**Expected:** Production allows only known dashboard origins.  
**Actual:** Any origin can read eligible responses and, when authentication is disabled, issue browser-based API requests.

**Business impact:** Amplifies SEC-001 and exposes operational/model data to arbitrary web origins.

**Remediation:** Default to an empty allow-list, reject wildcard CORS in production, and configure the dashboard origin explicitly.

### SEC-008 — Webhook destination validation permits SSRF targets

**Severity:** Medium, latent until notification wiring is enabled  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Notification product design and egress policy  
**Timeline:** Before notification wiring

**Problem and evidence:** `validsim/notify/config.py:213-271` accepts absolute HTTP(S) destinations without private-address checks. `validsim/notify/dispatcher.py:317-375,377-441` posts directly with `httpx.post`. The shipped completion path is currently dormant at `dispatcher.py:444-491`.

**Reproduction:** Registering `http://127.0.0.1:<port>/metadata` caused a local listener to receive a POST.

**Expected:** Notifications can only reach approved external destinations.  
**Actual:** Loopback, RFC1918, link-local, metadata-service, and internal DNS targets are accepted.

**Business impact:** Once enabled, an attacker who can influence configuration or registration can probe or send data to internal services.

**Remediation:** Use an explicit allow-list; resolve and reject loopback/private/link-local/reserved addresses; revalidate after DNS resolution; disable unsafe redirects; enforce egress policy.

### SEC-009 — Rate limits, admission control, and metrics are process-local

**Severity:** Medium  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Redis/shared coordinator and deployment topology decision  
**Timeline:** Before horizontal scaling

**Problem and evidence:** `validsim/api/main.py:270-297,490-512` keeps limiter/admission state in process memory. `validsim/api/metrics.py:15-16,88-117` stores HTTP counters in process memory.

**Reproduction:** Two independent limiters, each allowing one request per window, both accepted their first request for the same client.

**Expected:** Documented global limits and metrics remain consistent across workers/replicas.  
**Actual:** Effective limits multiply by process count and metrics reset/fragment across replicas.

**Business impact:** Validation overload, rate-limit bypass by replica multiplication, and incomplete operational visibility.

**Remediation:** Move admission/rate-limit state to a shared coordinator; use Prometheus multiprocess mode or a service-level metrics backend; document global versus per-replica semantics.

### SEC-010 — Compose PostgreSQL and Redis lack internal authentication/TLS

**Severity:** Medium, deployment-dependent  
**Priority:** P1  
**Effort:** 1–2 d  
**Dependencies:** Certificate issuance, secret management, database configuration  
**Timeline:** Before non-isolated Compose use

**Problem and evidence:** `docker-compose.yml:81-89` starts Redis without password/ACL/TLS. `docker-compose.yml:58-64` uses a plaintext PostgreSQL DSN with SSL disabled. API and worker share both services on the Compose network at `docker-compose.yml:117-161`.

**Expected:** Internal data services authenticate clients and protect credentials/data in transit.  
**Actual:** A compromised container or expanded network binding can read or tamper with queue and validation data.

**Business impact:** Queue manipulation, database disclosure, and credential capture.

**Remediation:** Configure Redis ACL/TLS and PostgreSQL `sslmode=require`, use Docker secrets/external secret management, and remove unnecessary host-published data-service ports.

---
