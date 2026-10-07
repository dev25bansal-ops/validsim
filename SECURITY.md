# Security Policy — ValidSim

ValidSim is a sim-to-real validation platform for robot foundation models: it runs
simulated and adversarial episodes against a checkpoint, scores success / safety /
robustness / regression, and returns an **APPROVE / BLOCK** decision that customers
wire into their deployment pipelines.

Because a ValidSim output can gate physical behaviour, we treat security defects as
safety defects. A bug that lets an attacker forge an `APPROVE`, tamper with a stored
scorecard, or exfiltrate a customer's model checkpoints is a **critical** finding to
us even if it has no CVSS-style "remote code execution" label.

This policy applies to this repository. There is no hosted ValidSim platform yet; if
one ships, this policy extends to it and this line will say so.

> **Intake status — read this first.** The only report channel that exists today is
> GitHub's private vulnerability reporting on this repository (Channel A). There is no
> security mailbox, no PGP key, and no hosted-platform inbox. Earlier drafts of this
> document printed an address and a key fingerprint that were never provisioned; a
> report sent there reached nobody, which is a worse outcome than having no channel at
> all. If Channel A is unavailable to you, use the fallback in §1.

---

## 1. Reporting a vulnerability

Use **one** of the routes below. Do **not** open a public issue, pull request,
discussion, or social-media post for an unpatched vulnerability.

### Channel A — GitHub private security advisory

This gives you a private thread, keeps the report out of public view by default, and
lets a CVE advisory be published afterwards:

1. Open this repository on GitHub and go to the **Security** tab.
2. Click **"Report a vulnerability"** (the same form lives at
   `<repository URL>/security/advisories/new`).
3. Describe the issue and your proposed severity; submit.

Anonymous reports are accepted, but without a way back to us we cannot offer credit or
answer follow-up questions — the advisory thread is how we stay in touch.

### Fallback — if the form is not visible

*Private reporting* must be enabled on the repository for that button to appear. If it
is not, open a **public issue containing no vulnerability details** — just "I would
like to report a security issue; please enable private vulnerability reporting" — and
we will turn it on and open the advisory for you. Include the string `VALIDSIM-SEC` in
the title so it is recognisable at a glance.

### What to include (helps us triage fast)

- Affected component: API (`validsim/api/`), CLI (`validsim/cli.py`), engine
  (`validsim/engine/`), scenario generator (`validsim/scenarios/`), store
  (`validsim/store/`), webhook dispatcher (`validsim/notify/`), Docker/Compose,
  GitHub Actions (`actions/`), or the hosted platform.
- Version and how it was deployed (container, `pip install .`, managed SaaS).
- Reproduction steps, PoC script, or a failing test. **Please keep PoCs non-destructive**
  and target only systems you own or are explicitly authorised to test.
- Impact: what an attacker gains, and what they must already have to get there.
- Your GitHub handle / name if you would like attribution.

### Safe harbour

We consider security research conducted in good faith under this policy to be
**authorised access**. If you:

- only test against your own ValidSim tenant, your own API keys, or your own
  installations;
- avoid privacy violations, data exfiltration/destruction, and service degradation
  (no denial-of-service, spam, or rate-limiter abuse testing at scale);
- do not use automated scanners against shared or multi-tenant infrastructure without
  prior written coordination;
- give us a reasonable period to remediate before any disclosure;

then we will **not** initiate or support legal action, and we will not report your
activity as an incident. We have no bug-bounty cash programme at this stage;
we offer attribution in the advisory and, for substantive findings, a letter of
recommendation and free platform access for your team.

---

## 2. Response SLA targets

These are internal targets, not contractual commitments.

| Stage | Target | What you can expect |
|---|---|---|
| Acknowledgement | **48 hours** (business days: 24h) | Human reply confirming receipt, reporter ID assigned, best-effort initial severity read. |
| Triage | **5 business days** | Reproduction confirmed or a question back to you; severity agreed; ownership and fix track assigned. |
| Remediation — Critical | **7 calendar days** for mitigation, 30 days for a full fix | Workaround or emergency patch; if a customer-facing exposure exists, we notify affected tenants. |
| Remediation — High | **30 calendar days** | Fix in the next patch or minor release. |
| Remediation — Medium | **90 calendar days** | Scheduled into the normal release train. |
| Remediation — Low / hardening | **Best effort**, next minor or major | May be combined with other work. |
| Advisory / patch release | **Within 5 business days** of the fix shipping | `SECURITY.md`-linked advisory, changelog entry, and a note to you. |

If we cannot meet a target we will say so explicitly, with a reason and a revised
date. If you believe a report has stalled, reply in the advisory thread and ask for it
to be escalated to a founder (the accountable owner of this document).

**Embargo:** we ask for a **90-day** private window from triage, or until the patch is
released, whichever comes first. We will extend it on request. We release security
fixes through normal versioned releases so that self-hosted operators get them without
needing to read the advisory first.

---

## 3. Scope

### In scope

- The FastAPI service and dashboard (`validsim/api/`, `validsim/web/`), including
  authentication, CORS, pagination, and rate-limiting behaviour.
- The validation engines that produce safety-critical output: evaluation, safety,
  regression, scorecard, and the bootstrap confidence-interval logic
  (`validsim/engine/`). **Anything that can flip a `BLOCK` into an `APPROVE` is
  automatically High or Critical.**
- Adversarial scenario generation, including the LLM provider path and its schema
  validation and deterministic fallback (`validsim/scenarios/`).
- Persistence layers: the SQLite, in-memory, and PostgreSQL stores
  (`validsim/store/`), including query construction and run/scorecard integrity.
- The job queue and worker path (Redis + `validsim.cli worker`), and the Isaac GPU
  worker HTTP contract (`validsim/sim/isaac_worker.py`, `docs/isaac-worker.md`).
- Outbound webhook signing and delivery (`validsim/notify/`).
- The CLI, including when it moves to its own MIT-licensed repository in Phase 2.
- Container and orchestration definitions (`Dockerfile`, `docker-compose.yml`),
  CI/CD workflows (`.github/workflows/`), and the reusable GitHub Actions in
  `actions/` — including any way a workflow run can be made to leak
  `VALIDSIM_API_KEY`, `POSTGRES_PASSWORD`, webhook secrets, or LLM provider keys.
- Asset/config parsing (`validsim/config.py`), including path handling for URDF and
  USD references.
- The managed/hosted ValidSim platform and its tenant isolation — once one exists.
  Nothing is hosted today, so there is no shared tenancy to attack yet.

### Out of scope

- **Versions no longer supported** (see §4) and forks that have modified the code.
- Third-party components as such: Python/PyPI packages, FastAPI, uvicorn, Pydantic,
  httpx, Redis, PostgreSQL, Slack, GitHub, NVIDIA Isaac Sim/Lab, OpenAI-compatible
  model providers, and any CDN or SaaS we depend on. Report these to the upstream
  project; **do** report them to us if a ValidSim-specific integration makes the
  issue exploitable or blocks an upstream fix.
- **Unsafe defaults are ours, not yours.** A control that is off until the operator
  turns it on is a defect in the product, so reports that a *shipped default* leaves a
  deployment unprotected are **in scope** and have historically been correct: an empty
  `VALIDSIM_API_KEY` used to report authentication as enabled while leaving every route
  open, and the API container used to publish on all interfaces. Both are fixed. What
  stays out of scope is an operator deliberately opting out of a control that is on by
  default — `VALIDSIM_RATE_LIMIT=0`, or a wildcard `VALIDSIM_CORS_ORIGINS` set
  explicitly after reading the runbook.
- Findings requiring physical access to a data centre, or social engineering of
  employees, contractors, or customers.
- Denial-of-service / resource-exhaustion reports without a demonstrated bypass of
  the configured rate limit, and volumetric attacks generally.
- Theoretical attacks without a working reproduction, best-practice nudges with no
  impact path, and missing hardening headers alone.
- Vulnerabilities in the mock simulation backend's *accuracy* — i.e. "the mock Isaac
  backend is not a faithful physics simulation". That is a product limitation
  (documented), not a security defect. It **is** in scope if a crafted input makes
  the mock backend produce a decision it should not have produced.
- Spam, phishing, or credential-stuffing against ValidSim marketing sites, and
  content on third-party sites linking to us.
- Issues in `project.docx`, `vault/`, or other internal business documents. These are
  marked **CONFIDENTIAL — Founding Document**. If you have received them without
  authorisation, tell us through the Channel A advisory form (or the §1 fallback)
  rather than filing a security report; we will treat it as an information-security
  incident, not a vulnerability.

### Not a security vulnerability, please use normal channels

Bugs with no security impact, feature requests, dependency version bumps with no
exploitable path, lint/type warnings, and test failures → open a regular issue in this
repository's **Issues** tab.

---

## 4. Supported versions

We ship security fixes for the current minor line and the one immediately preceding
it. Patch releases are versioned normally (`0.2.1`, `0.2.2`, …); we do not backport
into a line once it reaches end-of-life.

Nothing has been published yet: `validsim.__version__` is `0.2.0` and the repository
carries no git tags. The table therefore describes the single line under development,
not a set of released artifacts; this section becomes a real support policy at the
first tag.

| Version | Status | Security fixes |
|---|---|---|
| **0.2.x** | Current line, in development (unreleased) | ✅ Yes — first priority |
| < 0.2.0 | Never released / pre-release | ❌ No |

Notes for reporters:

- The `0.2.x` line runs the **mock** simulation backend and is intended
  for development and demonstration. Do not use it to gate real hardware.
- Pre-release and `nightly` builds are supported on a best-effort basis; please note
  the exact commit SHA.
- Self-hosted stacks should track `CHANGELOG.md`; security-relevant changes are
  labelled there and mirrored in a GitHub security advisory.
- If you are on an unsupported line, we will still read your report — but the fix
  will be "upgrade", and our SLA targets in §2 apply to the supported lines.

---

## 5. Licensing posture and how it affects disclosure

ValidSim is currently **proprietary**, with one planned exception:

| Component | Location | Licence today | Phase 2 plan |
|---|---|---|---|
| Platform (API, engines, stores, worker adapters, dashboard) | `validsim/` (except `cli.py`) | Proprietary | Remains proprietary |
| **CLI** | `validsim/cli.py` and its supporting surface | Proprietary (in-tree) | **MIT open-source** |
| GitHub Actions plugin prototype | `actions/`, `examples/` | Proprietary | To be decided |
| Repo scaffolding (docs, vault, build scripts) | `scripts/`, `docs/`, `vault/` | Internal / confidential | Not published |

Practical consequences:

- **Report everything in this repo to us**, including defects you find in the
  not-yet-published CLI. Once the CLI is MIT-licensed and split into its own
  repository, it will get its own `SECURITY.md`; until then, this is the intake
  channel for it.
- Because the platform is closed-source, please do **not** publish details of
  platform findings, including PoC code, during the embargo window. MIT-licensed CLI
  findings follow the same courtesy, out of respect for our users rather than any
  claim of code ownership.
- Open-source release does not change our obligations to you: we will still respond
  under §2 and still credit reporters.
- We do not currently grant a bug-bounty payment; the CLI's future MIT licence is not
  a waiver of the embargo request in §2.

---

## 6. Hardening already in place

This is a summary of defences that exist in the codebase today, so you can calibrate
your testing and skip the low-hanging fruit. Several controls are configured through
environment variables and are read once at `create_app()` time. A control that is off
*because a default left it off* is a product finding under §3, not operator error.

**Authentication & transport surface**

- **API key authentication** — when `VALIDSIM_API_KEY` is set, every `/api/v1` route
  except `GET /api/v1/health` requires a matching `X-API-Key` header. Comparison is
  constant-time (`secrets.compare_digest`) and failures return a uniform `401`, so the
  response is not an oracle for key length or content. An empty value is treated as
  *not configured*: authentication is off, and `/api/v1/health` reports
  `auth_enabled: false` rather than claiming otherwise. With `VALIDSIM_ENV=production`
  (or `prod`) and no key, the app refuses to start instead of serving open routes.
- **CORS allow-list** — `VALIDSIM_CORS_ORIGINS` takes a comma-separated origin list
  instead of a hardcoded policy. The default is still `*`, which is an unsafe default
  by the §3 definition — narrowing it is tracked as open work, so a report saying "the
  CORS default is a wildcard" is already known and does not need a PoC.
- **Rate limiting** — a sliding-window limiter (`VALIDSIM_RATE_LIMIT` requests per
  `VALIDSIM_RATE_WINDOW_SECONDS`, default window 60s) applied to write and sensitive
  routes under `/api/v1/validations/…`, including the ad-hoc `/compare` endpoint,
  keyed per client and answered with `429` + `Retry-After`. The limit ships **on**
  at `DEFAULT_RATE_LIMIT = 60` requests per window; `0` disables it.

**Integrity of outbound and inbound data**

- **HMAC-signed webhooks** — hooks registered with a secret receive an
  `X-ValidSim-Signature` header carrying an HMAC-SHA256 over the JSON body, so
  receivers can authenticate Slack/CI notifications instead of trusting the sender
  address. Delivery uses bounded retries with exponential backoff rather than an
  unbounded queue.
- **Strict schema validation at the boundary** — Pydantic v2 models are frozen and
  intentionally strict (`ConfigDict(frozen=True)`, bounded numeric ranges such as
  `dof` 1–40), so malformed configuration is rejected at the API/CLI edge rather than
  being interpreted deep inside the simulation or evaluation engines.
- **Path-traversal guards on asset fields** — `urdf_path`, `scene_usd`, and the other
  asset references are validated to reject absolute paths (checked under POSIX *and*
  Windows path flavours), `..` traversal segments, and NUL bytes. Resolution happens
  against `VALIDSIM_ASSET_ROOT`, and the symlink-resolved result is verified with
  `is_relative_to()` so a payload cannot steer a loader outside the asset root.
- **Deterministic, auditable scoring** — seeded simulation (`stable_seed`),
  bootstrap confidence intervals for regression comparisons, and a persisted
  run/scorecard record in PostgreSQL (JSONB + indexed columns) or SQLite, giving an
  audit trail that makes tampering detectable.

**Supply chain & runtime**

- **Multi-stage Docker build, non-root runtime** — dependencies are installed into an
  isolated venv in a builder stage and only the venv plus source are copied into a
  slim runtime image, so build tooling does not ship. A dedicated unprivileged
  `validsim` user (uid/gid 1001, `--shell nologin`) owns the app tree and is switched
  to via `USER` before the process starts, with a container `HEALTHCHECK` against
  `/api/v1/health`.
- **Required secrets, fail-fast** — `docker-compose.yml` uses
  `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}`, so the stack aborts rather than
  starting with an empty or default database password. Secrets come from the
  environment or a `.env` file that is never committed.
- **Loopback-restricted network exposure** — every published port in
  `docker-compose.yml` binds to `127.0.0.1`: the API on `:8000`, Redis on `:6379`,
  PostgreSQL on `:5432`. Nothing is reachable from another host unless an operator
  edits that binding deliberately, and services wait on `service_healthy` before
  starting.
- **Dependency hygiene** — runtime and dev/test dependencies are separated
  (`requirements.txt` vs `requirements-dev.txt`, mirrored by the `dev` extra), the
  shipped package is limited to the `validsim` tree, and CI runs lint (`ruff`), the
  test suite, and a Docker build on every push/PR, plus a nightly deep-validation run.

**Known gaps we would like help with** (accepted as future work, still report
concrete exploits against them): TLS termination and Postgres `sslmode=require`
(internal network only today), secrets management beyond env vars, per-tenant API
keys and RBAC (today: a single shared key), multi-tenant isolation in the hosted
platform, signed container images / SBOM generation, and the production Next.js
dashboard.

---

## 7. Internal handling

For our own reference, and so reporters know what happens after they hit "send":

1. **Intake** — we aim to read the GitHub advisory queue every day; a founder is the
   accountable owner for this document. There is no security team and no rota today,
   so read §2 as a commitment being made rather than a capability already staffed.
2. **Triage** — reproduce on a supported version, assign severity using the
   safety-critical weighting in the preamble, and decide whether affected tenants must
   be notified.
3. **Fix** — private branch, regression test added to `tests/` so the defect cannot
   return, reviewed by a second engineer.
4. **Ship** — patch release, `CHANGELOG.md` entry, GitHub security advisory (with CVE
   request via a CNA where warranted), and a direct note to the reporter.
5. **Learn** — post-fix review; recurring classes of defect become an engineering
   standard and, where relevant, a hardening item in §6.

---

## 8. Legal notice

This document is a disclosure policy, not a contract, and it does not create any
obligation on the part of the reporter or grant any right to payment. It is not legal
advice and does not modify any customer agreement, DPA, or terms of service. Safe
harbour statements of this kind are not enforceable in every jurisdiction, and
authorisation to test third-party infrastructure (including our cloud providers) must
be confirmed separately under their own terms.

> **This is a template for informational purposes. Consult with a qualified attorney
> for legal advice specific to your situation** — in particular before publishing, to
> confirm the safe-harbour language, embargo terms, and any export-control or
> incident-reporting obligations (e.g. EU CRA/NIS2, US CIRCIA, SOC 2 or ISO 27001
> commitments, and contractual breach-notification windows) that apply to ValidSim and
> to your customers' jurisdictions.

**Last updated:** 2026-09-21 · **Owner:** the founders, who are the escalation point
for every item above · **Review cadence:** quarterly, and on every minor release.
