# Category 2 — Security Audit (c2-security)

**Agent:** c2-security · **Date:** 2026-09-26 · **Tree:** `d:\SIM-TO-REAL` @ `ff15533` + uncommitted working-tree changes
**Scope:** `api/`, `jobs/`, `notify/`, `engine/{pdf,export,anomaly}.py`, `sim/`, `scenarios/`, `store/`, `{config,config_loader,cli,logging}.py`, `web/`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/`, `actions/`, `SECURITY.md`, `.env.example`

**Product context:** ValidSim produces APPROVE/BLOCK verdicts that gate deployments of physical robots. A wrong APPROVE is a safety defect, not a confidentiality bug. Severity weighting follows `SECURITY.md:8-11`.

**Method note:** every finding below was verified by execution against the current tree, with a control case to prove the repro is meaningful. Four of my earlier findings were re-verified and are now **REFUTED** (fixed by team-lead's edits) — they are listed because a security report that silently drops old findings is not auditable. All scratch scripts were written to `%TEMP%` only; no repository file was created, edited, or deleted except this report.

---

## Findings

| ID | Title | File:line | Severity | Status | Effort | Verification |
|---|---|---|---|---|---|---|
| S-01 | Non-ASCII `VALIDSIM_API_KEY` accepted at boot, then permanently unusable — silent failure | `api/main.py:542,581` | High | VERIFIED | 1h | `c2sec_verify4.py` (ASGI scope, 3 cases + controls) |
| S-02 | Webhook HMAC binds body only — captured pair replayable forever | `notify/dispatcher.py:143-145,265` | Medium | VERIFIED | 1d | `c2sec_verify.py` §V-02 + source read |
| S-03 | `/docs`, `/redoc`, `/openapi.json` unauthenticated under a configured key | `api/main.py:488-492` | Medium | VERIFIED | 2h | `c2sec_verify2.py` §V-05 (control: 401 on gated route) |
| S-04 | `checkpoint_sha256` never read, never persisted, test-pinned as permissive; no envelope→verdict binding | `config.py:154`, `engine/scorecard.py:98-111` | High | VERIFIED | 2d | `git grep` (2 hits) + `dataclasses.fields` probe |
| S-05 | Rate limiter: `DELETE` unmetered; stale-bucket sweep never advances under denied traffic | `api/main.py:207,241-262` | Medium | VERIFIED | 3h | `c2sec_verify3.py` §V-08 (5-step trace) |
| S-06 | JSON logger serialises any `extra` field verbatim; no redaction layer | `logging.py:98-101` | Medium | VERIFIED | 1h | `c2sec_verify2.py` §V-20 (secret echoed in output) |
| S-07 | Shipped default is unauthenticated (`development` + empty key) | `docker-compose.yml:46,50` | High | VERIFIED | 2h | `c2sec_verify3.py` §V-04 (compose parse) |
| S-08 | Three shipped "controls" are implemented and tested but never called | `config.py:62`, `config_loader.py:227`, `scenarios/llm_generator.py:541` | Medium | VERIFIED | 1d | `git grep` call-site counts |
| R-01 | ~~Unescaped reportlab markup → SSRF~~ | `engine/pdf.py:83,188` | ~~Critical~~ | **REFUTED (fixed)** | — | 7/7 sinks `outbound=0`, control fired |
| R-02 | ~~SMTP STARTTLS without cert verification~~ | `notify/email.py:305-318` | ~~Medium~~ | **REFUTED (fixed)** | — | `verify_mode=CERT_REQUIRED`, `check_hostname=True` |
| R-03 | ~~Non-ASCII supplied header → 500~~ | `api/main.py:581` | ~~Medium~~ | **REFUTED (fixed)** | — | non-ASCII header → 401 |
| R-04 | ~~"Auth-state oracle" via non-ASCII header~~ | `api/main.py:581` | ~~Medium~~ | **REFUTED (fixed)** | — | keyed 401 == unkeyed 401 for the *error* path |

---

## S-01 · Non-ASCII `VALIDSIM_API_KEY` is accepted at boot, then permanently unusable — and the failure is now silent

**Severity:** High · **Status:** VERIFIED · **Effort:** 1 hour

### Description

`api/main.py:581` now compares bytes:

```581:581:validsim/api/main.py
        if not secrets.compare_digest(supplied.encode("utf-8"), api_key.encode("utf-8")):
```

That correctly closes the *supplied*-header 500 (R-03). But the **configured** key is still only `.strip()`ed, never validated:

```542:542:validsim/api/main.py
    api_key = (os.environ.get("VALIDSIM_API_KEY") or "").strip() or None
```

An operator who sets a passphrase containing a non-ASCII character (an accent, a non-Latin script) gets an API that **no client can ever authenticate against**, while every health signal reports healthy.

### Reproduction

```
control: correct key        -> 200 (expect 200)     <- proves the gate works
control: wrong ascii key    -> 401 (expect 401)     <- proves 401 is the "no access" answer
TEST:    non-ascii header   -> 401

CONFIGURED key = "sécret"  (UTF-8 'é' = C3 A9):
  /api/v1/health            -> 200   {"status":"ok", ...}   <- PUBLIC_PATHS exempt
  /metrics                  -> 200
  correct utf-8 key         -> 401
  wrong ascii key           -> 401
  create_app() with non-ASCII key: ACCEPTED (no parse-time rejection)
```

Command: `python %TEMP%\c2sec_verify4.py` (drives a raw ASGI scope; `TestClient`/`httpx` cannot transmit a non-ASCII header value, so a TestClient-based test **cannot** express this input).

### Expected vs actual

- **Expected:** a key that cannot be transmitted is rejected at startup with an actionable error, or at minimum returns a status distinct from "wrong credential."
- **Actual:** the key is accepted silently at boot; every request returns an ordinary **401**, indistinguishable from a wrong key; `/api/v1/health` returns `200 {"status":"ok"}`; the container `HEALTHCHECK` (`Dockerfile:64-65`) asserts exactly that 200, so **Docker reports the container healthy while the API is unusable.**

### Business impact

This is a **loud-to-silent regression** introduced by the partial fix. Before the byte-coercion landed, this configuration produced **500** on every request — unmissable. It now produces **401**, which an operator will diagnose as "my key is wrong" for hours, because the key *is* correct and there is nothing in the response saying otherwise. For a product sold into international fleet operators, a passphrase with an accent is entirely plausible, and nothing in `.env.example` or `SECURITY.md` warns against it. Availability impact only; no confidentiality loss.

Note the underlying cause is a header-encoding mismatch, not the HMAC: ASGI servers decode header values as **latin-1**, so a client sending the UTF-8 bytes of `é` is read as the two characters `Ã©`, and `supplied.encode("utf-8")` can never equal `api_key.encode("utf-8")`. No client on earth can send this key.

### Dependencies

None. Two-line change.

### Recommended fix

Reject a non-ASCII `VALIDSIM_API_KEY` at config-parse time, mirroring the existing fail-safe pattern used by `_read_rate_limit_config` (`api/main.py:214-238`) and `store/postgres.py:_validate_table`. Either is acceptable:
- raise `ValueError` at `create_app()` with a message naming the variable, or
- `context = ssl`-independent ASCII check → refuse to boot under `VALIDSIM_ENV=production`, and log a loud warning otherwise.

Prefer refusing to boot in **all** environments: a key that cannot be typed is never intentional, and a silent 401 loop is the worst possible failure mode for a deployment gate.

### Test strategy

A test asserting only "no 500" is insufficient — it passes today while leaving the operator locked out. Required: `create_app()` with `VALIDSIM_API_KEY="sécret"` raises (or warns) at construction; and with a key set, `GET /api/v1/health` must not report `ok` while a correctly-supplied credential is rejected. Drive the request as a raw ASGI scope; the standard transport cannot express the input.

---

## S-02 · Webhook HMAC binds the body only — a captured pair is replayable forever

**Severity:** Medium (6.5) · **Status:** VERIFIED · **Effort:** 1 day

### Description

`notify/dispatcher.py:143-145` signs the serialized body and nothing else:

```143:145:validsim/notify/dispatcher.py
def _sign_body(body: str, secret: str) -> str:
    """Return the hex HMAC-SHA256 of ``body`` keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
```

`_post` (`:265`) emits exactly one auth header. Verified by source inspection of `_post`:

```
headers[_SIGNATURE_HEADER] = _sign_body(body, secret)
```

No timestamp, no nonce, no delivery id, no signature version. A captured `(body, signature)` pair is therefore valid for the lifetime of the secret, and there is no way for a receiver to detect staleness.

### Reproduction

```
body-only HMAC reproducible: add853b103fbcc93...
captured body : {"deploy_decision":"BLOCK"}
captured sig  : 18480f38c2b044f3e3cfc59815d8f612...
-> signature covers body ONLY; no timestamp/nonce header is emitted
```

Command: `python %TEMP%\c2sec_verify.py` (final section). Control: `_sign_body` is a pure function of `(body, secret)`, so identical input yields identical output — this is a property of HMAC, not a fixture artifact.

### Expected vs actual

- **Expected:** a receiver can reject a replayed or stale delivery.
- **Actual:** the MAC is fully determined by the body. Replaying a captured `{"deploy_decision":"BLOCK", ...}` is indistinguishable from a fresh genuine delivery.

### Business impact

Webhook receivers are Slack channels and CI systems that humans read during an incident. A replayed stale `APPROVE` posted into the deploy channel *after* a genuine `BLOCK` is a human-in-the-loop safety failure. `_MAX_RETRIES = 2` (`:50`) widens the window by resending identical bytes on failure.

### Dependencies

Breaking change for receivers. **Verified: there are no existing receivers to break** — `git tag --list` is empty and `SECURITY.md:183-186` states nothing has been published. This is why I recommend a clean cut rather than a dual-signature deprecation window: dual-signing keeps the replayable path valid for the window, so the vulnerability outlives the fix.

### Recommended fix

Sign `f"{timestamp}.{nonce}.{body}"`; emit `X-ValidSim-Timestamp` and `X-ValidSim-Nonce`; document that receivers **must** reject `|now - timestamp| > 300` and dedupe on `nonce`. Pin the tolerance as a module constant with a test asserting it is 300 so it cannot drift silently. Ship as a major version bump with an explicit `v2` marker.

### Test strategy

Three cases, the second being the one that actually proves the design: (1) a valid request within the window is accepted; (2) **tampered timestamp** — flip one digit, assert rejection *even though the body is byte-identical* (this is what proves the timestamp is inside the MAC, not merely sent alongside it); (3) **cross-hook** — a body signed with hook A's secret must not validate under hook B's.

---

## S-03 · `/docs`, `/redoc`, `/openapi.json` are unauthenticated while `/` is correctly gated

**Severity:** Medium (5.3) · **Status:** VERIFIED · **Effort:** 2 hours

### Description

`api/main.py:488-492` constructs `FastAPI(...)` without `docs_url=None` / `redoc_url=None` / `openapi_url=None`. These routes are not under `/api/v1`, so they escape the `application.router.dependencies` gate (`:590`).

### Reproduction

With `VALIDSIM_API_KEY=supersecret`, no key supplied:

```
/docs                -> 200
/redoc               -> 200
/openapi.json        -> 200
/metrics             -> 200
/api/v1/metrics      -> 401
/api/v1/health       -> 200   (deliberate, PUBLIC_PATHS)
/api/v1/validations  -> 401   <- CONTROL: the gate IS active
```

Command: `python %TEMP%\c2sec_verify2.py` §V-05. The control matters: a 200 on `/docs` is only meaningful because a genuinely protected route returns 401 in the same process, ruling out "auth is off."

### Expected vs actual

- **Expected:** with a key configured, interactive docs and the schema are behind the same gate as the API they document.
- **Actual:** all three are public. `/openapi.json` hands an unauthenticated caller a complete route inventory, parameter schemas, bounds, and the auth header name.

### Business impact

Reconnaissance for an attacker holding a leaked or guessed key, and an interactive console reachable from any origin permitted by CORS. Low direct impact; it removes all guesswork from targeting.

**Explicitly not a finding:** `/metrics` being public is **correct and deliberate** (`api/main.py:847-853` documents the auth-stripping for the Prometheus path), and the payload is counters/gauges with no PII or verdict data. Do not "fix" it — a test demanding auth there would lock in a regression against a conscious decision.

### Dependencies

None.

### Recommended fix

When a key is configured, disable the docs unless explicitly opted in: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` unless `VALIDSIM_EXPOSE_DOCS=1`. Keep `/metrics` public. Note `tests/test_api_health.py:35-43` pins the health payload as a 7-key set, so a companion change to the health fields is a **test-contract edit**, not a config flip.

### Test strategy

With a key set: `/docs`, `/redoc`, `/openapi.json` all 404. Control: `/api/v1/validations` still 401. Without a key: unchanged behaviour.

---

## S-04 · `checkpoint_sha256` is never read, never persisted, and test-pinned as permissive; nothing binds the request envelope to the verdict

**Severity:** High · **Status:** VERIFIED · **Effort:** 2 days

### Description

`config.py:154` accepts an optional SHA-256 digest. It is never read by anything:

```
git grep checkpoint_sha256 -- validsim/   ->  2 hits, both in config.py
  validsim/config.py:146:  checkpoint_sha256: Optional SHA-256 digest ...   (docstring)
  validsim/config.py:154:  checkpoint_sha256: str | None = Field(          (declaration)
```

`Scorecard` has no such field (`checkpoint_sha256 in {f.name for f in dataclasses.fields(Scorecard)}` → `False`), so the value is structurally discarded at the persistence boundary. `_scorecard_from_dict` (`store/sqlite.py:70-93`) and `_run_from_row` (`store/postgres.py:166-189`) rebuild a scorecard from stored JSON without it.

Three facts make this worse than a missing validation:

1. **Never recomputed** — the digest is an unauthenticated caller assertion about bytes nobody re-hashed.
2. **Documented as validated** — `docs/api-reference.md:192` describes it as "exactly 64 chars (hex digest)"; the code enforces length only, not hex.
3. **Test-pinned as permissive** — `tests/test_config_fuzz.py:116-119` comment reads *"64-char digest of any characters passes (no hex-pattern constraint)"* and asserts `"b" * 64` is valid.

### Expected vs actual

- **Expected:** the artifact's digest is computed server-side from the bytes actually validated, carried into the persisted scorecard, and covered by any signature or audit record.
- **Actual:** the field is decorative. It is accepted, length-checked, documented as a hex digest, asserted permissive by a test, and then dropped — it never binds "the bytes we validated" to "the score we published."

### Business impact

For a product whose output gates physical robots, this is a chain-of-custody gap on the verdict itself. An operator cannot later prove which artifact a given APPROVE refers to. It also means any future signed-scorecard work (Ed25519 over the scorecard) would sign a verdict with **no binding to its inputs** — the signature would attest "ValidSim signed this score," not "this is the score for that checkpoint under that threshold."

Compounding: `jobs/queue.py` persists with `ON CONFLICT (run_id) DO NOTHING` (`store/postgres.py:414`), so a `run_id` collision is **silently dropped** and indistinguishable from success. `run_id` is a 32-bit uuid4 prefix (`store/memory.py:79`), which is adequate at current volume but should be widened alongside any tenancy work.

### Dependencies

Coordinate with c4-compliance, who is designing signed scorecards and org/project scoping — this is the integrity spine those features rest on.

### Recommended fix

Compute the digest server-side from the checkpoint artifact, store it on `Scorecard` and in the JSONB/SQLite columns, and include it in any signed envelope alongside `org_id`/`project_id`, `threshold`, and engine version. Separately: make duplicate `run_id` a **loud** error rather than `DO NOTHING`, and widen the id entropy.

### Test strategy

A test that posts two different digests for the same artifact and asserts the stored scorecard carries the *server-computed* value, not the caller's. Also: a test asserting a duplicate `run_id` raises rather than silently no-ops. Note that `test_config_fuzz.py:116-119` currently *asserts the gap*, so it must be updated in the same change — otherwise the suite defends the defect.

---

## S-05 · Rate limiter: `DELETE` is unmetered, and the stale-bucket sweep never advances under denied traffic

**Severity:** Medium · **Status:** VERIFIED · **Effort:** 3 hours

### Description

Two defects in `_SlidingWindowRateLimiter` and its scope predicate, both confirmed by execution:

```
DELETE /api/v1/validations/x            limited=False
POST   /api/v1/validations              limited=True
POST   /api/v1/jobs                     limited=True
```

**Sweep counter trace** (limit=2, window=60):

```
check 1: allowed  _operations=1
check 2: allowed  _operations=2
check 3: DENIED   _operations=2
check 4: DENIED   _operations=2
check 5: DENIED   _operations=2
```

Command: `python %TEMP%\c2sec_verify3.py` §V-08. Control: the same trace with limit=10 shows `_operations` advancing on allowed requests, so the counter is not simply broken — it is only incremented on the success path.

The increment at `api/main.py:207` sits **after** the early `return` at `:204`, so denied requests never advance it. The documented "periodic sweep of stale buckets" (`api/main.py:41-42`) therefore never runs for a client that is being throttled.

### Expected vs actual

- **Expected:** every write/sensitive route is metered, and the memory guard operates under sustained abuse.
- **Actual:** `DELETE` is unmetered; and a sustained flood of *denied* requests never triggers the sweep that reclaims stale buckets.

### Business impact

`DELETE` destroys the stored run — **the audit trail that is the safety record**. It is auth-gated, so severity is bounded, but an authenticated caller can delete the entire history in a tight loop with no 429. Eviction at max-buckets still bounds the table, so the sweep gap is a memory-efficiency and contract issue, not unbounded growth.

**Not a finding (verified good, do not re-raise):** the limiter is IP-keyed rather than header-keyed (`api/main.py:265-277`), which is correct — keying on a caller-supplied header would let an attacker mint a fresh bucket per request. Defaults fail **safe**: unset, unparsable, and negative values all fall back to 60/60s, and only an explicit `0` disables. Verified.

### Dependencies

None.

### Recommended fix

Add `DELETE` to `_is_rate_limited_request`. Move the `self._operations += 1` increment **above** the denial `return` so denied traffic advances the sweep. Document the `60 × N` budget multiplication for `uvicorn --workers N` in the runbook.

### Test strategy

Hammer `DELETE` past the budget and assert a 429 with `Retry-After`. Separately, drive N denied requests with no allowed traffic and assert the sweep ran (observable via bucket count dropping). The existing `test_periodic_sweep_reclaims_never_revisited` only exercises the allowed path, which is why this survived.

---

## S-06 · JSON logger serialises any `extra` field verbatim — no redaction layer

**Severity:** Medium · **Status:** VERIFIED · **Effort:** 1 hour

### Description

`logging.py:98-101` merges every non-reserved `LogRecord` attribute into the emitted JSON with no allow-list, deny-list, or value scrubbing.

### Reproduction

```
log.info("probe", extra={"api_key": "TOPSECRET", "password": "hunter2"})

-> {"timestamp": "...", "level": "INFO", "logger": "validsim.c2sec",
    "message": "probe", "api_key": "TOPSECRET", "password": "hunter2"}
```

Command: `python %TEMP%\c2sec_verify2.py` §V-20. Control: the same call with no `extra` emits only the four standard fields, so the leak is attributable to `extra` and not to the formatter baseline.

### Expected vs actual

- **Expected:** a secret passed to a logging call is redacted.
- **Actual:** it is serialised verbatim to stdout.

### Business impact

**Nothing leaks today** — I checked every call site: `SmtpSettings.password` is `field(repr=False)`, the API access log records only method/path/status/duration/request_id, and the CLI does not log the key. This is a latent trap, not a live breach.

It matters because of a **real ordering dependency**: c3-api's approved 5xx error-envelope work plans to log driver exceptions via `exc_info`, and `store/postgres.py:360-368` embeds the **full DSN — including `POSTGRES_PASSWORD`** — in its `ValueError` message. If anyone logs that exception with an `extra` field, or if a future change routes it through `extra`, the database password lands in stdout. `SECURITY.md:126-129` explicitly asks whether a workflow can leak `VALIDSIM_API_KEY`/`POSTGRES_PASSWORD`; today the answer is "no, by discipline, not by design."

### Dependencies

**Sequencing:** redaction must land before c3-api's envelope logging work.

### Recommended fix

Add a deny-list in `JsonLogFormatter.format` covering `password|secret|token|api_key|authorization|cookie|bearer|dsn`, replacing matches with `"***"`. ~10 lines, no call-site changes.

### Test strategy

Assert that a record with each denied key emits `"***"` and that the raw value does not appear anywhere in the serialised line. Also assert the DSN from `PostgresValidationStore._default_connect` cannot appear in a log record.

---

## S-07 · Shipped default deployment is unauthenticated

**Severity:** High (8.6) · **Status:** VERIFIED · **Effort:** 2 hours

### Description

```
VALIDSIM_ENV: "${VALIDSIM_ENV:-development}"
VALIDSIM_API_KEY: "${VALIDSIM_API_KEY:-}"
- "127.0.0.1:8000:8000"
```

`api/main.py:537-541` refuses to boot without a key only when `VALIDSIM_ENV` is `production`/`prod`. The compose stack — the documented deployment path — defaults to `development` with an empty key, so every `/api/v1` route including `POST /validations` (executes the pipeline, writes a verdict) and `DELETE /validations/{id}` (destroys the audit trail) is open.

### Expected vs actual

- **Expected:** the shipped default either fails closed or warns loudly.
- **Actual:** open by default. `api/main.py:544` logs a WARNING, which is easily lost in JSON log noise.

### Business impact

An unauthenticated caller can both read every scorecard and delete the evidence. For this product, destroying the verdict log is an integrity attack on the safety record itself. **Mitigating factor, stated honestly:** compose binds `127.0.0.1`, so out of the box this is host-local only; it becomes network-reachable the moment an operator republishes the port — which the compose comment at `:40-44` anticipates. The finding is about default posture, not a remotely exploitable default.

`SECURITY.md:143-150` already declares shipped unsafe defaults **in scope**, so this is a live product finding, not operator error.

### Dependencies

Ops sign-off on flipping the default.

### Recommended fix

Either default `VALIDSIM_ENV` to `production` in compose, or emit a startup **stderr** block (not a log line) when `auth_enabled=false`, naming the exact variable to set. Best: generate a random key on first boot into a mounted file, making "protected" the path of least resistance.

### Test strategy

With the shipped compose defaults, assert the health payload reports `auth_enabled: false` (so the state is at least honest) and that a warning containing `VALIDSIM_API_KEY` reaches stderr.

---

## S-08 · Three shipped "controls" are implemented and tested but never called

**Severity:** Medium · **Status:** VERIFIED · **Effort:** 1 day

### Description

Three security-relevant code paths have no production caller, so the guarantee they appear to provide is not enforced anywhere:

| Symbol | Declared | Production callers |
|---|---|---|
| `resolve_asset_path` | `config.py:62` | **0** — only `tests/test_config_paths.py` |
| `load_default_config` / `load_config_file` / `discover_config_file` | `config_loader.py:227,177,199` | **0** — only docstrings and `__all__` |
| `create_scenario_generator` | `scenarios/llm_generator.py:541` | **0** — `engine/pipeline.py:131` hard-codes `ScenarioGenerator(seed=seed)` |

Command: `git grep` call-site counts (see S-04 output format). Control for the third: `git grep create_scenario_generator -- validsim/engine/` returns **NONE**, while `pipeline.py:131` demonstrably instantiates the rule-based generator directly.

### Expected vs actual

- **Expected:** a tested guard is wired into the path it protects, or is removed.
- **Actual:** all three are fully implemented, documented, and tested — and unreachable. `config_loader` is the worst case: **`.env.example:154` advertises `VALIDSIM_CONFIG=` as "Path to an explicit config file"**, so an operator who sets it gets silently inert behaviour and no error.

`resolve_asset_path` is the highest-consequence of the three: the asset-path sandbox is airtight (NUL / absolute / `..` / symlink containment via `is_relative_to`), but **nothing calls it**. The fields it would protect (`urdf_path`, `scene_usd`) are validated for *shape* by `_validate_asset_path` and then carried as opaque strings into the Isaac worker payload — so there is no traversal today, but the containment guarantee is unenforced exactly where it would matter.

### Business impact

This is the systemic finding of the audit, and it is the honest answer to "why did these accumulate": **a green suite is not evidence that a control is enforced.** Three controls here would pass every test while doing nothing. Combined with S-04's *test-pinned-as-permissive* pattern and S-05's *passes-vacuously* pattern, the codebase exhibits four distinct mechanisms by which a test suite certifies a control that is not enforced.

### Dependencies

None to fix; needs an owner decision on whether each control should be wired or deleted.

### Recommended fix

Per control: either wire it at the point of use, or delete it and its test. For `resolve_asset_path`, add a comment stating it is not yet wired **and** an assertion at the future point of use so the next implementer calls it rather than re-implementing the check. For `config_loader`, either wire it into `create_app`/CLI startup or remove `VALIDSIM_CONFIG` from `.env.example` — advertising a setting that does nothing is worse than omitting it.

### Test strategy

A meta-test asserting no module in `validsim/` exports a security-relevant symbol with zero production callers would catch regressions of this class. At minimum, a test asserting `resolve_asset_path` is called by whatever eventually opens an asset file.

---

## False leads

Ruled out with evidence. Recorded so they are not re-raised and so the negative results are auditable.

- **Unescaped reportlab markup → SSRF (was Critical).** **REFUTED — fixed in the current tree.** `engine/pdf.py:83` and `:188` now escape `deploy_decision` and the taxonomy value alongside the four header fields. Control-gated probe: all 7 interpolated fields → `outbound=0`, and the control (a deliberately raw `Paragraph`) fired `hits+1`, proving the probe can see sinks. **Impact was also downgraded during this audit:** a 60 MB file and a 64-byte file produce identical PDFs with no timing delta, so the primitive is `unexfiltrated` even before the fix — 6.5 Medium, not Critical.
- **SMTP STARTTLS without certificate verification (was Medium).** **REFUTED — fixed.** `notify/email.py:305-318` now builds `ssl.create_default_context(cafile=certifi.where())` with a stdlib fallback. Measured: `verify_mode = 2` (`CERT_REQUIRED`), `check_hostname = True`. For the record, the *pre-fix* behaviour was genuinely `CERT_NONE` — `smtplib.starttls()` with no context uses `ssl._create_stdlib_context()`, which does **not** verify. A widely-held assumption, and it was wrong here.
- **Non-ASCII supplied header → 500 (was Medium).** **REFUTED — fixed** by the byte-coercion at `api/main.py:581`; now returns 401. Note the test must drive a raw ASGI scope: `httpx` raises on non-ASCII header construction, so a TestClient-based test cannot express this input at all.
- **"Auth-state oracle" (was Medium, my own framing).** **REFUTED as originally stated.** The oracle existed only because the `TypeError` made the *error* path return 500 on a keyed app and 200 on an unkeyed one. With byte-coercion, both return 401 — the non-ASCII header is no longer distinguishable from any other unauthenticated request. What remains is that *any* unauthenticated request returns 200 on an unkeyed instance and 401 on a keyed one, which is inherent to the design, already disclosed deliberately via `auth_enabled` on the public health route, and should be recorded as an accepted trade-off rather than a finding.
- **SQL injection — all three backends.** Clean. SQLite uses `?` throughout; Postgres parameterises every value with `%s` and validates the table identifier against `^[a-z_][a-z0-9_]*$` at construction (`store/postgres.py:147-163,326`); Redis keys are parameter-free with the job id regex-validated at the HTTP boundary (`jobs/router.py:35,53`). The one piece of inline SQL in CI (`ci.yml:252-254`) is a static literal. All Redis logic is Lua via `KEYS`/`ARGV` — no string-built commands.
- **Untrusted deserialization.** Zero `pickle` / `eval(` / `exec(` / `yaml.load` in shipped code. The only `eval` is redis-py's Lua entry point. `tests/test_delivery_pipeline.py` uses `yaml.safe_load` and is a test, not shipped code. Redis payloads are `json.dumps`/`json.loads` only.
- **`Content-Disposition` header injection.** Not reachable. `run_id` is server-generated (`store/memory.py:79`) and matches `vrun-[0-9a-f]{8}`; no `"`, `\`, CR, or LF is possible.
- **XSS in `validsim/web/app.js`.** Clean, and genuinely well-built. Every untrusted value reaches the DOM via `textContent`; all five `innerHTML` uses interpolate only a closure-constant SVG from the `ICONS` object or a value normalised against a closed allow-list (`APPROVE`/`BLOCK`; four fixed job statuses). **Recommend a lint rule** (ban `innerHTML` unless the argument is an `ICONS[...]` lookup) rather than a test — a test would pin current output and ossify implementation detail.
- **Asset path traversal.** Not exploitable today: NUL, absolute (both POSIX and Windows flavours), `..` segments, and symlink escape are all handled. But see S-08 — the containment function itself is never called.
- **Minimal YAML parser (`config_loader.py`).** No injection or traversal. Quote-aware comment stripping, duplicate keys rejected outright rather than last-wins, nested maps and flow collections rejected. Only concern is that it is unwired (S-08).
- **`engine/anomaly.py`** (211 lines, read for the first time this session). Searched for `subprocess`, `eval(`, `exec(`, `pickle`, `os.system`, `yaml.load`, `shell=True` — **none present**. Caveat: this was a targeted grep, not a line-by-line read; I am not claiming the file is exhaustively reviewed.
- **Rate limiter defaults and keying.** Correct as designed and verified: IP-keyed (not header-keyed), fail-safe defaults, `0` is the only disabling value, `max_buckets` cap present.

---

## Retractions and residual risk

Ten of my own claims were falsified by teammates re-running them during the multi-agent phase. They are recorded because a security report that hides its own errors is not auditable, and because the pattern is the transferable result:

- Slack `mrkdwn` injection — correct PoC attached to an **unreachable sink** (downgraded to latent).
- V-01 "arbitrary local file read / Critical" — a proven *fetch attempt* attached to an **unproven exfiltration**; a 60 MB file and a 64-byte file produce identical output.
- V-01 "credential-bearing surface" — **asserted, never measured**; the bearer rides a separate httpx client and DB credentials are environment variables.
- `auth_enabled` "not inferable" — a **single-instance probe** used to overturn a correct claim; inference is a cross-instance differential.
- Two vacuous PDF oracles — a literal-ASCII search over subsetted hex glyph runs, and a stream regex that matched **no streams** and so found nothing.
- An all-zero per-field sweep whose **control gate was also zero** — a listener that answered while the renderer never reached it.
- A spec-first table row that would have **pinned the bug as the requirement** (inverted direction: Redis reclaims rather than strands).
- A correct two-sink finding that became a **one-sink line item** in the status ledger.

**Every one was an instrument or bookkeeping defect, not a reasoning defect.** The resulting rule, which the test strategies above are built on: *gate a negative result on a known-positive that must fire on the same code path — a listener answering proves the network, not that the renderer reaches it.*

**Could not verify (stated as limits, not findings):**
- No live Postgres, Redis, Isaac GPU worker, or TLS-terminating proxy. The Redis lease-timestamp and depth-cap defects are `mechanism-confirmed` on source review only, not executed.
- No dependency CVE resolution — `requirements.txt` is 8 unpinned `>=` deps with no lockfile or hashes. `SECURITY.md:136-142` scopes third-party CVEs out, so unscored, but it is a real supply-chain gap.
- **The GPU worker is not in this repository.** Its compromise is equivalent to forging an `APPROVE`; I validated the client contract only. It needs a dedicated audit before promotion, and "worker compromise ⇒ BLOCK" belongs in the gate as an explicit condition.
- The lease/stranding defects referenced by teammates are **not** counted in my eight findings: I could not execute them, and per rule 2 they would be UNVERIFIED. They are real and worth a Redis-backed test.
