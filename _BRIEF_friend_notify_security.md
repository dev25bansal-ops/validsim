# Brief: Notification & Webhook Security — Stream C+D

**Repo:** `D:/SIM-TO-REAL` · **Branch:** `main` · **Baseline commit:** `d976cab`
**Owner:** you · **Streams:** C (security & secrets) + D (notification)

This brief is self-contained. You do not need to have read the repository map.

---

## Why this work matters

A webhook is the only place a verdict leaves the system. The scorecard says
APPROVE or BLOCK; a CI job reads it, a Slack channel posts it, a deploy gets
gated on it. Every defect in this brief breaks the same promise in a different
way: **a consumer of the notification cannot trust what it received, or cannot
prove where it came from.**

All seven are P0. Five are small and mechanical. Two are genuinely hard and
need a decision.

---

## Files you own (exclusively)

Nobody else will touch these. If you need a change outside this list, put it in
your report — do not edit it.

```
validsim/notify/dispatcher.py
validsim/notify/config.py
validsim/notify/egress.py
validsim/project_config.py
SECURITY.md
```

**Tests you own** (one per area, so the streams never collide):

```
tests/test_notify_security_agent.py
tests/test_notify_config_index_alignment_agent.py
tests/test_notify_egress_agent.py
tests/test_notify_ssrf_timeout_agent.py
tests/test_notify_routing.py
tests/test_project_config.py
```

**Files you must NOT touch** — they belong to other streams:

- `validsim/engine/*` — stream E
- `validsim/api/*`, `validsim/web/*` — stream F
- `validsim/cli.py`, `validsim/jobs/*` — stream G
- `validsim/store/*` — stream B
- `pyproject.toml`, `README.md`, `conftest.py`, `pytest.ini`, `docs/**` — the lead
- `validsim/notify/email.py`, `validsim/notify/__init__.py` — shared; ask the lead first

If a fix genuinely needs one of these, say so in your report and let the lead
make the call. Do not widen your own scope.

---

## The seven defects

### 1. A blank HMAC secret produces a publicly forgeable signature — `P0-11`

`dispatcher.py:428-429` signs whenever `secret is not None`. `register()`
(`dispatcher.py:303-311`, with `secret` on line 308) takes `secret: str | None = None`
with no emptiness check,
so `""` or `" "` yields a well-formed `X-ValidSim-Signature` that anyone can
recompute.

The env path is already safe — `config.py:273` does `_at(secrets, index, "") or None`,
turning `""` into `None`. Only the direct programmatic API is exposed.

**Fix:** reject blank secrets in `register()`.
**Effort:** ~30 min.

> Note: `tests/test_notify_security_agent.py:264-296` currently *proves* the
> forgery and is explicitly marked create-only audit evidence so the suite stays
> green whether or not it is ever fixed. If you fix this, that test's meaning
> changes — read its docstring and decide whether to invert it into a real
> regression test. **Do not delete or weaken it to make things pass.**

### 2. A captured webhook pair replays forever — `P0-12`

`_sign_body` (`dispatcher.py:429`) signs the serialized body and nothing else —
no timestamp, no nonce, no delivery id, no signature version. A captured
`(body, signature)` pair is valid forever, so a stale APPROVE can be replayed
into a deploy channel after a genuine BLOCK.

This is now a **live** window, not a hypothetical one: the notify wiring landed
(this is P1-2 in the map), and `_MAX_RETRIES` widens it.

**Fix:** sign `timestamp.nonce.body`, and expose the timestamp so a receiver can
enforce a window.
**Effort:** ~2 h.

### 3. URL credential redaction leaks the password tail — `P0-13`

`project_config.py:246`:

```python
_URL_USERINFO_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<creds>[^/@]+)@")
```

`[^/@]+` stops at the **first** `@` or `/`, not the last. Reproduced:

```python
mask_secret('postgresql://validsim:p@ssw0rd@db.internal:5432/validsim')
# -> 'postgresql://***REDACTED***@ssw0rd@db.internal:5432/validsim'
```

The tail `ssw0rd` survives in cleartext. The module docstring
(`project_config.py:20-22`, `:286-291`) calls redaction "a security boundary"
and promises the password is removed.

`tests/test_project_config.py:105-118` only exercises `hunter2` / `s3cr3t` —
neither contains `@` or `/`, which is exactly the shape the regex handles
correctly. **Add the missing test.**

**Fix:** anchor the userinfo match on the **last** `@` before the host.
**Effort:** ~30 min.

### 4. A blank URL field misaligns every positional companion — `P0-17`

`config.py:249` does `urls = _split(raw_urls)`, and `_split` (`:171-177`)
**drops** blank fields. But the companions are parsed with `_split_positional`
(`:180-196`), which **preserves** blanks as placeholders. `config.py:257` then
indexes the companions by the *compacted* index — two lists indexed against
different coordinate systems.

Verified:

```
URLS="https://a.example/h,,https://c.example/h"  SECRETS="s1,s2,s3"  ->  a->s1, c->s2
URLS="https://a.example/h,,https://c.example/h"  SECRETS="s1,,s3"   ->  a->s1, c->None
```

The second case is the docstring's *own* recommended config at `config.py:48-50`,
and it leaves the hook **unsigned**. An *invalid* URL is dropped *after*
enumeration, so its index is preserved and the pairing comes out right — that
asymmetry is the defect.

**Fix:** keep blank URL placeholders, or re-index companions against the
original positions.
**Effort:** ~1 h.

### 5. `allow_private_hosts=True` disables the whole egress policy — `P0-18`

`dispatcher.py:337-339` — `if not self._allow_private_hosts:` — skips the **entire**
`url_rejection_reason()` check when `allow_private_hosts` is true — and `egress.py:135-155` is the *only* place the
scheme allowlist (`ALLOWED_SCHEMES = {http, https}`) is enforced.

Verified: `WebhookDispatcher(live=True, allow_private_hosts=True).register("h", "file:///etc/passwd")`
succeeds, as do `gopher://x/`, `http://metadata.google.internal/x` and
`http://foo.internal/x`. The default dispatcher correctly refuses all of them.

The flag's name and docstring (`dispatcher.py:271-279` (the `allow_private_hosts` docstring)) promise only a *host*
exemption — "permitting loopback, link-local and RFC1918 destinations". In
fact it also disables the scheme allowlist and the cloud-metadata hostname
block.

**Fix:** split the scheme and metadata-hostname checks out of the host check.
**Effort:** ~1 h.

### 6. The "warn" severity band is unreachable — `P0-20` (needs a decision)

`dispatcher.py:154` returns `"warn"` only when `composite >= threshold`. The only
three `block_reasons` `engine/scorecard.py:549,555,561` can emit are:

- (a) "adversarial success rate …"
- (b) "insufficient evidence …"
- (c) "composite {x} is below the configured threshold {y}"

Reasons (a) and (b) are exactly the two `_CRITICAL_BLOCK_REASON_PREFIXES` at
`dispatcher.py:101-104`. Reason (c) is appended only when
`composite < threshold` — so **a BLOCK with no critical-prefix reason is
logically impossible**. A `min_severity="warn"` hook therefore fires on
exactly the same events as `min_severity="critical"`.

This one is a **product decision**, not a mechanical fix: which block reasons
should count as "critical" versus "warn"? A fourth reason class is the likely
shape. **Propose the rule in your report** — do not unilaterally invent a
severity taxonomy.

**Effort:** ~1 h once the rule is agreed.

### 7. `SECURITY.md` claims an audit trail the stores do not provide — `P0-22`

`SECURITY.md:290-293` claims an audit trail that "makes tampering detectable".
The stores provide no such thing: they are append-only, first-write-wins, but
nothing detects a tamper — there is no signature over a run record, no hash
chain, and the local SQLite/Postgres rows are writable by anyone with file or
database access. A `DELETE` removes the run outright.

**Fix:** either soften the claim to what the code does, or scope it explicitly
("the remote worker protocol signs webhook deliveries; the run store does not
sign run records"). The second is the honest option.
**Effort:** ~30 min.

---

## Order of work

Do these in this order — each one is independently shippable, and the early
ones are the cheapest:

1. **P0-13** (credential leak) — highest severity, lowest effort, self-contained.
2. **P0-11** (blank secret) — one-line guard.
3. **P0-17** (index misalignment) — needs a careful test for the blank-field case.
4. **P0-18** (egress policy split) — straightforward, but check the `allow_private_hosts`
   callers do not regress.
5. **P0-12** (replay) — the largest of the mechanical ones; the signature format
   change is a wire-format change, so check `docs/notify_wiring_patches.md` and
   the receiver-side contract in `docs/isaac-worker.md` / `docs/api-reference.md`
   before you commit to a format.
6. **P0-22** (`SECURITY.md`) — after the code, so the doc describes what exists.
7. **P0-20** (severity band) — blocked on a product decision. **Raise the
   question early**; it may need the founder's input, so do it before you
   finish the rest.

---

## Rules (from the repo's own fleet file — they are not optional)

1. **Prove before fixing.** One defect per iteration, with a minimal reproduction.
   "No defect found" is a valid result. No speculative refactors.
2. **Read before edit.** Never rewrite a large file wholesale.
3. **Never weaken a test.** No editing, deleting, skipping, xfail-ing or loosening
   an existing test to get green. Adding tests is fine. If an existing test
   pins a defect as current behaviour, invert it into a real regression test
   rather than deleting it — and say so in your report.
4. **Never touch git.** Do not commit, push, or branch. The lead does that.
5. **Report, don't trespass.** A bug outside your files goes in your report,
   never in your editor.
6. **Honest numbers only.** If you did not measure it, do not state it.

---

## How to verify

The cohort you own, before and after:

```bash
python -m pytest tests/ -p no:cacheprovider --no-cov -k "notify or project_config"
```

**Baseline: 469 passed, 0 failed, 0 skipped.** It must stay at 469+ with zero
failures. If your change adds tests, the count goes up — that is fine. If it
goes *down*, you deleted something.

The whole suite, before you hand back:

```bash
python -m pytest tests/ -p no:cacheprovider --no-cov
```

**Baseline: 3254 tests, 0 failures, 0 errors, 51 skipped.**

And the coverage gate CI enforces:

```bash
python -m pytest tests/ -p no:cacheprovider --cov=validsim --cov-report=term
```

**Baseline: 95.96% total** — the gate is 90%, so you have headroom, but do not
drop below it.

---

## What to hand back

For each of the seven:

- **The defect** — one paragraph, in your own words.
- **The reproduction** — the exact command or snippet that fails before your
  fix and passes after. This must be a thing anyone can run.
- **The fix** — the file and the line.
- **The test** — the new or inverted regression test, and the before/after
  count.
- **What you did not do** — and why.

Plus, at the end:

- The two **product decisions** you need answered: (a) which block reasons
  count as "critical" for P0-20, and (b) whether `SECURITY.md` should be
  softened or scoped — and if softened, the exact wording you propose.
- Any defect you found **outside** your five files. Report it; do not fix it.
- Any place where the **code and a doc disagree** about the behaviour you
  changed. The map found several; if you change notify behaviour, check
  `docs/api-reference.md`, `docs/runbook.md`, `docs/notify_wiring_patches.md`
  and `README.md:149` (the three-level severity claim) for drift, and list
  every doc that now needs an update. Do not edit those docs — the lead owns
  them — just say which lines are stale.
