# C5 — Notification & Reporting Reachability Audit (ValidSim)

**Agent:** c5-notify · **Scope:** `validsim/notify/`, `validsim/engine/export.py`, `validsim/engine/pdf.py`, notification/report surfaces across API, CLI, worker, dashboard.
**Date:** 2026-09-26 · **Method:** read-only inspection + executed probes. No production file was edited.

---

## Findings table

| ID | Title | file:line | Severity | Status | Effort | Verification command |
|---|---|---|---|---|---|---|
| C5-1 | Notification layer is 100% library-only — zero reachability from any entrypoint | `validsim/notify/dispatcher.py:167`, `validsim/notify/email.py:223` | **High** | VERIFIED | 1–2 days | `python %TEMP%/c5notify_reach2.py` |
| C5-2 | README + build dashboard assert a shipped, configurable notification feature that no user can reach | `README.md:13,154,164`; `00 - Dashboard/Build Status.md:68` | **High** | VERIFIED | 0.5 day doc + C5-1 | `git grep -n "Slack webhooks" README.md` |
| C5-3 | No configuration surface can express a webhook destination (env, CLI, or config file) | `.env.example:145-160`; `validsim/project_config.py:144-156` | **High** | VERIFIED | 1 day | `python %TEMP%/c5notify_cfg2.py` |
| C5-4 | Dispatcher env var is read at construction → boot-frozen, unlike the email notifier | `validsim/notify/dispatcher.py:175-181`; `validsim/notify/email.py:298` | **Medium** | VERIFIED | <1 hour | see finding body |
| C5-5 | 1,354 lines of tests are the only production caller of the notify layer | `tests/test_notify*.py` (7 files) | **Medium** | VERIFIED | n/a — measurement | `python -m pytest tests/ -q --collect-only` |
| C5-6 | Runbook documents a repair procedure for a feature with no reachable failure mode | `docs/runbook.md:220-228` | **Medium** | VERIFIED | 1 hour | see finding body |

---

## C5-1 — Notification layer is 100% library-only

**Subject: SHIPPED CODE (packaged, imported, tested) that is UNREACHABLE from any product entrypoint.** This is not dead code in the "unused prototype" sense — the code is complete, correct, well-tested and installed. It simply has no caller.

### Description
`validsim/notify/` ships two complete notifiers: `WebhookDispatcher` (HMAC-SHA256 body signing, per-hook severity routing, retry with exponential backoff, Slack Block Kit formatting) and `EmailNotifier` (SMTP multipart HTML+text, header-injection guards, `repr=False` on the password so it cannot leak into logs). Neither is constructed anywhere outside the package itself.

### Reproduction
Probe imports **every** entrypoint and inspects `sys.modules` for any `notify` module. `%TEMP%/c5notify_reach2.py`:
```python
import sys; sys.path.insert(0, r"d:\SIM-TO-REAL")
import validsim.api.main, validsim.cli, validsim.jobs.worker, validsim.api.dashboard, validsim.engine.pipeline
mods = sorted(m for m in sys.modules if m.startswith("validsim"))
print("A) loaded:", len(mods))
print("B) notify modules:", [m for m in mods if "notify" in m] or "NONE")
importlib.import_module("validsim.notify.dispatcher")          # CONTROL
print("C) after explicit import:", [m for m in sys.modules if "notify" in m])
```
Executed output:
```
A) validsim modules loaded by importing ALL entrypoints: 36
B) any loaded 'notify' module: NONE
C) CONTROL - after explicit import: ['validsim.notify.dispatcher', 'validsim.notify.email', 'validsim.notify']
D) total modules now: 39
```
**Control result (C) is what makes this meaningful:** the probe demonstrably *can* detect a notify load — it found three modules the instant one was imported. The absence in (B) is a real absence, not a probe artifact.

### Expected vs actual
- **Expected:** at least one entrypoint (API route, CLI command, or worker step) constructs a notifier and dispatches a completed scorecard.
- **Actual:** 36 modules load across API + CLI + worker + dashboard + pipeline; **zero** notify modules. Static search agrees — every hit for `WebhookDispatcher|EmailNotifier|dispatch(|send_scorecard` in `validsim/` is inside `validsim/notify/` itself. The CLI's 15 commands (`run, validate, status, scorecard, report, gate, delete, models, version, health, compare, jobs, job, worker`) contain **zero** occurrences of "notify" or "webhook".

### Business impact
A completed validation — the artifact the entire product exists to produce — cannot announce itself. The customer must poll the dashboard or CI. Every integration value proposition in `vault/03 - Product/Core User Flows.md:21-25` (submit → validate → **Slack notification** → review) is broken at step 4. This is also the cheapest high-value fix in the audit: the code already exists and is tested; only the wiring is missing.

### Dependencies
None. `httpx` is already a dependency; no new package is required for the wiring itself.

### Recommended fix
Wire a single post-persist hook in `validsim/engine/pipeline.py` (after the `store.save(...)` call at `pipeline.py:155-168`) — the one choke point all three entrypoints already funnel through. Requirements:
1. **Fire-and-forget.** Dispatch on a background executor, never inline. Worst case today is 3 attempts × 5s timeout + 0.3s backoff ≈ **15.3s per hook, serialized** (`dispatcher.py:48-52`), so N hooks cost N × 15.3s in the request thread. Nine hooks ≈ 137s.
2. **Never propagate.** Wrap the whole fan-out in `try/except`; a webhook 500 must not fail a validation.
3. **Build the dispatcher per-dispatch**, not once at startup — see C5-4.
4. **Never fire on an unpersisted verdict.** Dispatch only after `store.save()` returns, so an alert can never announce an APPROVE that was never gate-visible.

### Test strategy
Describe only — not written, per directive:
- A test asserting a scorecard reaching `store.save()` results in exactly one dispatch per registered hook.
- A test asserting a raising notifier does **not** fail `run_and_score` (the non-blocking guarantee).
- A test asserting dispatch ordering: persist precedes dispatch.
- A reachability guard test — import every entrypoint and assert at least one `validsim.notify.*` module is loaded. **This is the test that would have caught C5-1 and would prevent its recurrence.**

---

## C5-2 — Documentation asserts a shipped, configurable feature that does not exist

**Subject: SHIPPED DOCS making a false capability claim.**

### Description
The project's own README and build dashboard mark the notification feature complete. It is not reachable (C5-1) and not configurable (C5-3).

### Reproduction
`README.md:13` — the `validsim/` row advertises the platform as including "Slack webhook + SMTP email dispatchers".
`README.md:154` — `- [x] **Slack webhooks** — Block Kit payloads, optional HMAC-SHA256 signing, retry with exponential backoff`
`README.md:164` — `- [x] **Email notification channel**`
`00 - Dashboard/Build Status.md:68` — "Slack webhooks | Notifications with HMAC signature verification + retry on failure" listed under "## New Capabilities This Iteration".

The only candid statement in the corpus is `vault/05 - Execution/8-Week Sprint Plan.md:86` — *"Dispatcher code ships; public registration API and live Slack configuration are not verified"* — and it is contradicted three lines later by the same file's Week 4-5 **Exit Criteria** at `:89`: *"Developer can submit checkpoint via CLI or GitHub Actions, receive Slack notification."* The sprint was scored against a criterion that, by its own line 86, was never met.

### Expected vs actual
- **Expected:** a `[x]` means a user can reach the capability.
- **Actual:** a technical buyer reading the README would reasonably conclude they can configure a Slack alert. They cannot, by any documented mechanism.

### Business impact
The README is the first artifact a technical evaluator reads and the one often screenshotted into a procurement document. A false `[x]` on the front door is materially worse than an absent feature: it reads as evidence about the *other* claims. Note the contrast that makes this a genuine outlier — the other `[x]` lines reconcile against code (`CLI report command:153` has a working command; `Coverage gate:165` is enforced in CI). **The notification lines are `[x]` with no surface, so the checkbox lies relative to the convention the others follow.**

### Dependencies
None for the doc change. The durable fix couples to C5-1.

### Recommended fix
Two options, and the ordering matters:
- **Preferred:** correct the docs **and** land C5-1's wiring **in the same change**. Doing the wiring silently afterwards leaves the original claim standing and untraceable — and a published README line cannot be unpublished.
- Minimum: annotate each `[x]` as "library-only, not yet wired" and add the sprint-plan `:86` caveat to the README.

### Test strategy
Describe only: a docs-lint check asserting every `[x]` README capability maps to an importable, entrypoint-reachable symbol. That is the general form of the C5-1 reachability guard and would catch this class automatically.

---

## C5-3 — No configuration surface can express a webhook destination

**Subject: SHIPPED CONFIG (`.env.example`) plus a TEST-ONLY config module.**

### Description
Two independent configuration mechanisms were checked. Neither can enable a delivery.

**Environment:** `.env.example:145-160` has a section headed `# Notifications: webhooks + email`. Webhooks receive exactly **one** variable — `VALIDSIM_WEBHOOKS_LIVE=0`, a *transport* switch — and **no way to register a URL**. Email receives a full six-variable block (`SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`). The comment even promises "POST completed scorecards live to **registered** webhook endpoints" with no way to register one. The asymmetry is the tell: email is fully env-expressible so it was fully documented; webhooks are not expressible so they were not.

**Config file:** `validsim/config_loader.py` can *parse* a `[notify]` table — my probe wrote `notify = { urls = ["https://hooks.slack.com/x"] }` to a TOML file and `load_config_file` returned it intact. **But nothing consumes it.** `load_config_file` / `discover_config_file` / `load_default_config` are referenced only inside `config_loader.py` itself; no entrypoint calls them. The only module that whitelists a `notify` key is `validsim/project_config.py:153`, and that module **self-documents as unwired** at `project_config.py:3-6`: *"Status: not yet wired into production. Nothing in the shipped execution path (API, CLI, job worker) imports this module; it is currently reachable only from `tests/test_project_config.py`."*

### Expected vs actual
- **Expected:** a user configures a destination URL and enables delivery.
- **Actual:** the URL can be *parsed* into a dict that is then **discarded**. Setting `VALIDSIM_WEBHOOKS_LIVE=1` alone accomplishes nothing.

### Business impact
This is what makes C5-1 unfixable by configuration alone and turns the runbook's advice into a dead end (C5-6). It also means the "double opt-in trap": live delivery needs BOTH an env flag the user can set AND a `register()` call no user can make.

### Dependencies
None. A `VALIDSIM_WEBHOOK_URLS` (comma-separated) + `VALIDSIM_WEBHOOK_SECRET` + `VALIDSIM_WEBHOOK_FORMAT` triple matches the existing SMTP precedent and needs no config-file schema.

### Recommended fix
Add the three env vars to `.env.example` and read them in the pipeline hook. Caveat worth stating: a flat list cannot express **per-hook `min_severity`** — a feature the dispatcher already implements and has a dedicated test file for (`tests/test_notify_routing.py`, 11,858 bytes). Ship a single global `VALIDSIM_WEBHOOK_MIN_SEVERITY` in v1; per-hook routing arrives with a `NotifyPolicy` object in v2. Do not let the flat-list limitation silently drop an already-tested feature.

### Test strategy
Describe only: a test asserting a configured URL produces one live delivery attempt; and a test asserting an unconfigured deployment produces zero network calls (the current default, which must be preserved).

---

## C5-4 — Dispatcher env var is read at construction (boot-frozen), unlike the email notifier

**Subject: SHIPPED CODE — latent inconsistency, becomes live once C5-1/C5-3 are fixed.**

### Description
The two notifiers resolve configuration at **different times**:
- `WebhookDispatcher.__init__` (`dispatcher.py:175-181`) reads `VALIDSIM_WEBHOOKS_LIVE` **at construction** and stores it. Flipping the env var afterwards has no effect for the object's lifetime.
- `EmailNotifier._deliver` (`email.py:298`) calls `SmtpSettings.from_env()` **at send time** — hot-reloadable.

The module docstrings describe the two as "deliberately symmetric" (`email.py:6-7`). They are symmetric in behaviour, not in config resolution.

### Reproduction
Static, exact line citations above; the asymmetry is visible by reading the two constructors. Severity is currently latent because nothing constructs a dispatcher (C5-1) — **it becomes a live operational trap the moment C5-1 lands.**

### Expected vs actual
- **Expected:** consistent config-resolution semantics across notifiers, or a documented difference.
- **Actual:** an operator who builds the dispatcher once at app startup flips `VALIDSIM_WEBHOOKS_LIVE`, sees nothing happen, and gets no error — the worst failure mode for a feature whose purpose is "tell me it blocked."

### Business impact
Silent misconfiguration. The operator concludes the tool is broken rather than that a restart is needed.

### Dependencies
None.

### Recommended fix
Build the dispatcher per-dispatch. It is a list append and a bool (`dispatcher.py:180`), so the cost is nil and it buys hot-reload parity with email. Additionally, surface the effective state in `GET /api/v1/health` as `{"hooks": N, "live": bool}` — that endpoint already reports effective runtime config (store and job-queue backends) and is where an operator looks when nothing is sending. A startup-only warning is insufficient for a long-lived API process.

### Test strategy
Describe only: a test that constructs a dispatcher, flips the env var, and asserts the new value is honoured on the next dispatch.

---

## C5-5 — 1,354 lines of tests are the only production caller

**Subject: SHIPPED TESTS — measurement, not a defect.**

### Description
The notify layer's only consumers are its own test files:

| File | Bytes |
|---|---|
| `tests/test_notify.py` | 4,265 |
| `tests/test_notify_email.py` | 9,533 |
| `tests/test_notify_routing.py` | 11,858 |
| `tests/test_notify_integration.py` | 13,878 |
| `tests/test_notify_enhancements.py` | 9,081 |
| `tests/test_notify_email_security.py` | 6,857 |
| `tests/test_shadow_email_paths.py` | 15,039 |
| **Total** | **~1,354 lines** |

Test code outweighs the ~570 lines of implementation. That is not waste in itself — the tests are good, and they encode real security properties (HMAC verification, SMTP header-injection rejection, secret redaction). **But it is the measure of C5-1:** a feature can be comprehensively tested and still be unreachable, because tests construct the object directly and bypass the product surface entirely.

### Business impact
False confidence. Coverage metrics read as though the feature is exercised, when the coverage is of the library and not of any user path.

### Test strategy
The fix is C5-1's reachability guard test. A coverage number cannot express "no entrypoint constructs this."

---

## C5-6 — Runbook documents a repair procedure with no reachable failure mode

**Subject: SHIPPED DOCS.**

### Description
`docs/runbook.md:220-228` (§4.4 "Webhook delivery failures") gives a three-step troubleshooting procedure. Step 1 is *"Is live mode on? Delivery defaults to dry-run… Set `VALIDSIM_WEBHOOKS_LIVE=1`."* Step 2 says to *"inspect `dispatcher.sent` or the hook's logs."*

Neither step can succeed: setting the env var accomplishes nothing because no hook can be registered (C5-3), and an operator following step 2 has no `dispatcher` object and no hook — those exist only inside a Python process they have not written. **The one real cause — there is no registration path — appears nowhere in the section.**

### Expected vs actual
- **Expected:** troubleshooting steps that resolve a reachable failure.
- **Actual:** three confident, specific instructions, all inert, for a condition a user cannot reach.

### Business impact
An on-call engineer burns time on a dead end. Worse than an overclaim: the section reads as authoritative, and the runbook is the document a responder trusts most under pressure and is least likely to re-read after the feature ships. The same file is accurate elsewhere (`runbook.md:427-431` correctly states the repo implements no audit log and that `checkpoint_sha256` is caller-supplied), which is what makes the incorrect section more misleading — it lends borrowed credibility.

### Dependencies
Fixed automatically by C5-1 + C5-3; the doc then describes a real procedure.

### Recommended fix
After the wiring lands, keep §4.4 and add the registration step. Until then, mark the section as describing the library API rather than a product capability.

### Test strategy
Describe only: a docs check asserting every runbook troubleshooting step references a surface reachable from an entrypoint.

---

## False leads — ruled out, with reasons

1. **"The notify package is dead code."** REFUTED. It is fully implemented, packaged, exported in `validsim/notify/__init__.py`, and covered by ~1,354 lines of tests. The defect is absence of a **caller**, not absence of implementation. Labelling it "dead code" would misdirect the fix toward deletion.

2. **"`.env.example` omits `VALIDSIM_WEBHOOKS_LIVE`."** REFUTED. `.env.example:151` defines it with a comment. The missing piece is a *URL*, not the transport flag.

3. **"The config file cannot express a webhook destination."** REFUTED as stated. `load_config_file` **does** parse and return a `[notify]` table (probe-confirmed). The accurate finding is narrower and stated in C5-3: parsing succeeds but **no code consumes the result** — the parser is itself unwired.

4. **"`project_config.py` provides a production config surface."** REFUTED. It self-documents as unwired test-only groundwork at `project_config.py:3-6`, and nothing in `validsim/` imports it outside its own test.

5. **"A dispatch failure could corrupt or fail a validation run."** REFUTED for the current library. `dispatcher.py:270` and `email.py:310` both catch broadly and never raise; failures are captured on `DeliveryResult`/`EmailDelivery`. The risk is latent and only materialises once wiring exists — which is why C5-1's fix specifies the non-blocking guarantee rather than assuming it.

6. **"The team lead's edits to `dispatcher.py`/`email.py` may have fixed reachability."** REFUTED. I re-ran the full reachability probe after those edits landed: 36 modules load from all entrypoints, **zero** notify modules. The finding survives the working-tree changes.

7. **"Webhook HMAC signing is absent."** REFUTED. `_sign_body` (`dispatcher.py:143-145`) implements hex HMAC-SHA256 and `dispatcher.py:261-262` attaches `X-ValidSim-Signature` on live sends when a secret is configured. The crypto is present and correct; only the delivery path is missing.

---

## Assumptions

1. **Scope is the notification and reporting surface.** I did not audit the scorecard composite defect (two of four weighted components are structurally constant, owned by another agent) except where it constrains notification design — see C5-1's note on escalation ordering.
2. **Working-tree state was current when probed** (2026-09-25 evening). C5-1 was re-verified after the team lead's edits to `dispatcher.py`, `email.py`, `pdf.py`, `anomaly.py`; all findings hold against that state.
3. **Severity** is judged against this product's stated buyers (robotics teams + enterprise/insurance, per `vault/02 - Problem & Market/Pain Quantified.md:24-28`). C5-1 is High rather than Critical because no data is corrupted and no security control fails — the loss is a missing capability plus a false claim.
4. **No production file was read-modified by me.** All probes were written to `%TEMP%` only. Probe scripts: `c5notify_reach2.py`, `c5notify_cfg2.py`.
5. **The 15 CLI commands were enumerated from executed `--help` output**, not from source reading, to avoid missing a dynamically registered command.
