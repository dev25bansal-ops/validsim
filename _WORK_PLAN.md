# ValidSim — Work Division Plan

**Generated:** 2026-10-20 · **Baseline:** HEAD `2ffeb6c` (branch `main`) · **Source:** `_REPO_MAP.md`

This is the division of the 117 curated open items (26 P0, 50 P1, 41 P2) into
**workstreams with disjoint file ownership**, so two people can work in parallel
without ever touching the same file. The repo already defines a 10-member fleet
in `.codebuddy/simtoreal-fleet.md`; the streams below refine that topology against
the evidence in `_REPO_MAP.md`.

---

## Ground rules (binding on every stream)

These are the rules the repo's own fleet file already states. They are reproduced
here because they are what make parallel work safe.

1. **Prove before fixing.** One defect per iteration, with a minimal reproduction.
   "No defect found" is a valid result. No speculative refactors.
2. **Read before edit.** Never rewrite a large file wholesale.
3. **Never weaken a test.** No editing, deleting, skipping, xfail-ing or loosening
   an existing test to get green. Adding tests is fine.
4. **Never touch git.** Streams do not commit, push, or branch. The lead does that.
5. **Report, don't trespass.** A defect outside your stream goes in your report,
   never in your editor.
6. **Honest numbers only.** If you did not measure it, do not state it.

---

## The streams

Eight streams, partitioned by file. Ownership is exclusive: **no file appears in two
streams.** The lead owns the shared files and resolves cross-stream conflicts.

| # | Stream | Owns (exclusively) | P0 items |
|---|---|---|---|
| **A** | Packaging & release | `pyproject.toml`, `Dockerfile`, `.dockerignore`, `MANIFEST.in`*, `release.yml`, `docker-compose*.yml` | 1, 5, 19 |
| **B** | Store & persistence | `validsim/store/*` | 9, 10 |
| **C** | Security & secrets | `validsim/project_config.py`, `validsim/notify/egress.py`, `SECURITY.md` | 13, 18, 22 |
| **D** | Notification | `validsim/notify/{dispatcher,config}.py` | 11, 12, 17, 20 |
| **E** | Engine & renderers | `validsim/engine/{export,pdf,scorecard}.py` | 7, 8 |
| **F** | API & dashboard | `validsim/api/*`, `validsim/web/*` | 6, 21, 26 |
| **G** | CLI & jobs | `validsim/cli.py`, `validsim/jobs/*` | 14, 15, 16, 24, 25 |
| **H** | Docs & config | `docs/*`, `.env.example`, `README.md`, `CHANGELOG.md` | 2, 3, 23 |

\* `MANIFEST.in` does not exist yet; stream A creates it.

### Shared files — lead only

`conftest.py`, `pytest.ini`, `pyproject.toml` (ruff/mypy sections), `requirements*.txt`,
`README.md`, `docs/**`, `tests/conftest.py`.

Streams A and H both need `pyproject.toml` and `README.md`. **Sequence them:**
A goes first (it adds `package-data` and `MANIFEST.in`), H picks up the rest after.
Do not run A and H concurrently.

### Test ownership

Tests follow the code they cover: a stream that fixes `validsim/store/sqlite.py`
owns `tests/test_store_*.py`. The one exception is `tests/conftest.py`, which is
lead-only — it is imported by every test file, so a concurrent edit there breaks
every other stream.

---

## P0 sequence — the critical path

The 26 P0 items are **not** independent. This is the order that unblocks the most
work per unit of effort.

### Phase 1 — one-hour fixes, no dependencies (do these first, in parallel)

| Item | Stream | Fix | Verify |
|---|---|---|---|
| P0-3 | H | `cli.py:154` treats a set-but-empty `VALIDSIM_CACHE_FILE` as unset (the store's sibling case at `store/__init__.py:69` already does this) — or drop the empty default from `.env.example` | `validsim run` with the shipped `.env.example` |
| P0-13 | C | `project_config.py:246` `[^/@]+` stops at the **first** `@`/`/`; it must stop at the **last** | `mask_secret('postgresql://u:p@ssw0rd@h/d')` no longer leaks `ssw0rd` |
| P0-2 | H | `sqlite.py:261` `sqlite3.connect` with no parent `mkdir` (the CLI cache at `cli.py:168` already does this) | `SqliteValidationStore("<tmp>/a/b/x.db")` opens |
| P0-21 | F | `_memory_summaries` returns the **oldest** records when `offset < n < offset + limit` (the `else runs` branch at `store_stats.py:420`); currently **latent** — `run_summaries` has no production caller | a pagination test that samples `offset < n < offset + limit` |
| P0-25 | G | `job enqueue` lets `QueueFullError` escape uncaught | `validsim job enqueue` on a full queue exits cleanly |

All five are one-liners. They are also the five most likely to be found by a user
in the first five minutes of using the product.

### Phase 2 — packaging and deployment (streams A, H)

P0-1, P0-5, P0-19, P0-23, P0-4.

P0-1 is the highest-value item in the whole list: the wheel ships **51 entries and
zero under `validsim/web/`**, and `validsim/api/main.py:1430` does
`app = create_app()` at module scope — so `import validsim.api.main` fails on any
non-editable install. `pip install .` and `uvicorn validsim.api.main:app` are both
broken, and no check catches it because `twine check` validates METADATA only and
the Dockerfile never installs the wheel.

P0-4, P0-5 and P0-19 together mean **the documented production stack cannot start**:
the healthcheck resolves the Redis host as the literal string `"0"`, the `validsim`
console script does not exist in the image, and the compose file pulls
`ghcr.io/abhijeet1267/validsim` while the release workflow publishes to
`ghcr.io/dev25bansal-ops/validsim`.

Fix order within A: **P0-5 → P0-1 → P0-4 → P0-19**. P0-1 is what makes the other
two testable.

### Phase 3 — the core claim (streams E, F)

P0-7 is the one that matters most for the product. It is not a cosmetic omission:
the Markdown and PDF renderers compute `relation = "is below" if BLOCK`, so a run
blocked for *insufficient evidence* or for the *adversarial floor* — with a
composite **above** the threshold — produces the sentence:

> Composite score **95.0** is below the deploy threshold of **85.0**.

which is flatly false, and the actual reason is absent from the artifact. The CLI
and the dashboard both render `block_reasons` correctly; only the two surfaces a
recipient reads (the email card and the PDF) drop them. `actions/scorecard` drops
them too, so a CI-visible BLOCK carries no reason.

P0-6 is the same class of defect on the web side: the API serialises
`failure_taxonomy` as a **dict** (`{"collision": 7}`) and the dashboard indexes it
as `c.failure_taxonomy[i][1]`, which throws `TypeError: Cannot read properties of
undefined` on every run.

### Phase 4 — security (streams C, D)

P0-11, P0-12, P0-17, P0-18, P0-22.

P0-17 is the subtle one: `_split` **drops** blank URL fields but `_split_positional`
**preserves** them as placeholders, and the two lists are then indexed against
different coordinate systems. The result is that the wrong secret signs the wrong
destination — and the docstring's own recommended config produces an **unsigned**
hook.

Note P0-11 is narrower than the map states: the env path normalises a blank secret
to `None`, so the empty-key signing needs the direct `register()` API. Fix the
guard (`if secret:` not `if secret is not None:`) regardless — it is a one-word
change.

### Phase 5 — durability (streams B, G)

P0-9, P0-10, P0-14, P0-15, P0-16, P0-24, P0-26.

P0-9 and P0-26 are the ones that break the *record* rather than the *reading of
it*: the SQL backends crash on a row whose detail blob carries a field this build
does not know, and `create_app` **overwrites** `application.router.lifespan_context`
at `main.py:996`, silently dropping any app-level startup/shutdown handler.

P0-14, P0-15 and P0-16 share one root cause: **the CLI's local cache is a second,
unsynchronised source of truth.** `delete` does not invalidate it, `compare
--latest` resolves from it rather than the durable store (against ADR 0006's own
"the gate reads the durable store" rule), and the CLI worker never reaps expired
leases outside `run_forever`. Fix these as one coherent change to the CLI's
cache/authority boundary, not three unrelated patches.

---

## Parallel work plan

```
LEAD ── owns: conftest.py, pytest.ini, pyproject.toml, README.md, docs/**, tests/conftest.py
  │
  ├── Phase 1 (all streams, parallel, no conflicts)
  │     H: P0-3, P0-2      C: P0-13      F: P0-21      G: P0-25
  │
  ├── Phase 2 (A alone, then H — they share pyproject.toml)
  │     A: P0-5 → P0-1 → P0-4 → P0-19
  │     H: P0-23 (after A lands, so the doc describes the fixed artifact)
  │
  ├── Phase 3 (E and F, parallel — disjoint files)
  │     E: P0-7, P0-8      F: P0-6
  │
  ├── Phase 4 (C and D, parallel — disjoint files)
  │     C: P0-13✓, P0-18, P0-22    D: P0-11, P0-12, P0-17, P0-20
  │
  └── Phase 5 (B and G, parallel — disjoint files)
        B: P0-9, P0-10              G: P0-14, P0-15, P0-16, P0-24, P0-25✓
```

A and H are the only pair that must not run concurrently. Everything else is
parallel-safe by file ownership.

---

## P1 and P2 — after the P0s

**P1 (50 items)** cluster into six themes worth handling as groups rather than as
individual tickets:

1. **Scale** — P1-49 and neighbours: `PERF-001` (the history route loads the whole
   history then paginates in memory) and `PERF-002` (unbounded dashboard/job/model
   history). Both are `validsim/api/*` + `validsim/store/*`, so they straddle
   streams B and F — assign one owner.
2. **Worker result binding** — the Isaac worker reply is validated for episode
   count, seed, and randomization level, but **not** for `task_id`, `episode_id`,
   or `checkpoint_id`. A forged reply is accepted verbatim. `isaac_worker.py:653`
   even documents that the worker "should echo it back on each episode" — but
   `EpisodeResult` has no `checkpoint_id` field, and `_episode_from_dict` *rejects*
   unknown fields, so a compliant worker would be rejected. This is the residual
   half of SEC-002 and it is the single largest integrity gap in the product.
3. **Robustness is unmeasurable** — `runner.py:625` and `:631` pass the same
   `task.randomization` for every episode, so `_MIN_ROBUSTNESS_GROUPS = 2` is never
   met and the 0.2-weight component abstains on every production run. The engine
   says so itself at `scorecard.py:161-164`: *"The runner, not this module, is the
   root cause and is where a fix belongs."* Fixing it changes every composite score
   ever computed, so it needs a decision, not just a patch.
4. **Redis queue capacity** — `REL-001` (P0 severity, still open): the in-memory
   backend counts running jobs toward `max_depth`; the Redis backend counts only
   the ready list, so a running job does not. Reproduced: with `max_depth=1`, the
   in-memory backend raises `QueueFullError` and Redis accepts the second enqueue.
5. **Database** — `DB-001` (runtime DDL instead of versioned migrations) and
   `DB-002` (Postgres lacks the composite `(checkpoint_id, created_at)` index
   SQLite has at `sqlite.py:65-68`).
6. **Live-backend gaps** — Postgres parity, live Redis, and the Isaac wire contract
   all need environments this audit did not have. They are unverified, not proven
   clean.

**P2 (41 items)** are documentation drift and hygiene. The highest-value cluster
is §5 of the map — the docs-vs-code contradictions. The most consequential single
one: `docs/api-reference.md:262` documents the composite as a fixed
`0.4·success + 0.3·safety + 0.2·robustness + 0.1·regression`, but the engine
**renormalises over measured components only**. On the configuration of every
production run (robustness unmeasured), the documented formula yields **70.00**
where the engine produces **87.50** — a 17.5-point difference for an integrator
computing the score from the docs. The marketing site documents the *correct*
behaviour (`site/src/data/content.ts:130`), so `api-reference.md` is the outlier.

---

## Definition of done

Per stream:

- [ ] Every P0 in scope has a **minimal reproduction** recorded before the fix.
- [ ] The fix has a **regression test** that fails before it and passes after.
      No existing test is weakened, skipped, or xfail-ed to get green.
- [ ] The full suite passes: `python -m pytest tests/ -p no:cacheprovider --no-cov`
      (currently **3254 tests, 0 failures, 0 errors, 51 skipped**).
- [ ] Coverage stays at or above the CI gate of 90% (currently **95.96%**).
- [ ] Any doc the change makes stale is updated **in the same commit**, with the
      code line and the doc line both cited.
- [ ] The report states what was **not** done, and why.

Repo-wide, before the branch lands:

- [ ] `make smoke` works on a clean clone.
- [ ] `pip install .` then `import validsim.api.main` succeeds.
- [ ] `docker compose -f docker-compose.prod.yml up --wait` reaches healthy.
