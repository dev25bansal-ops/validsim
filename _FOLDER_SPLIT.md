# ValidSim — Two-Way Folder Split

**Repo:** `D:/SIM-TO-REAL` · **Branch:** `main` · **Baseline:** `4a28ffe`
**Companion to:** `_REPO_MAP.md` (the findings) and `_BRIEF_friend_notify_security.md`

A folder-first division: **you** own the compute core, **your friend** owns the
surfaces. Every tracked file is assigned to exactly one owner or to the lead.
**There is no overlap** — verified mechanically, not by eye.

```
$ git ls-files | wc -l
398
```

| Owner | Files | Lines | Folders |
|---|---:|---:|---|
| **You** — compute core | 114 | 38,772 | `validsim/engine/`, `validsim/store/`, `validsim/sim/`, `validsim/scenarios/`, `validsim/{config,config_loader,project_config,_log,__init__}.py` + their tests |
| **Your friend** — surfaces | 38 | 17,541 | `validsim/api/`, `validsim/jobs/`, `validsim/notify/`, `validsim/web/`, `validsim/cli.py` + their tests |
| **Lead** — shared & docs | 246 | 61,610 | everything else (see below) |

The 246 "lead" files are mostly documentation, the vault, the marketing site and
the CI/packaging config — plus the shared files that any change could break.
They are not a third workstream; they are the files **neither of you touches**.

---

## Why this split

**The seam is real.** The compute core decides *what the verdict is*; the
surfaces decide *how it is delivered*. `validsim/engine/scorecard.py` is the only
place `deploy_decision` is ever assigned. The surfaces read that verdict and
render it. That boundary is also the repo's own layering — the fleet file at
`.codebuddy/simtoreal-fleet.md` already divides along these same lines.

**The core is where the product's claim lives.** Every defect in the core
cluster changes *what is decided*:

- P0-7 — the renderers omit `block_reasons`, and the Markdown/PDF sentence can be
  **false** (a BLOCK for insufficient evidence prints "Composite score 95.0 is
  below the deploy threshold of 85.0")
- P0-9 — the SQL backends crash on a row whose detail blob has an unknown field
- P0-13 — URL credential redaction leaks the password tail in cleartext
- P0-2 — the SQLite store cannot open a path whose parent directory does not exist

**The surfaces are where the delivery risk lives.** The other cluster is about
whether a consumer of the verdict can trust it:

- P0-6 — the dashboard's Failure Taxonomy panel throws on every run
- P0-11 — a blank HMAC secret produces a publicly forgeable signature
- P0-17 — a blank URL field misaligns every positional companion, so the wrong
  secret signs the wrong destination
- P0-26 — `create_app` replaces the router lifespan context

**Effort is NOT balanced, and you should know that before you start.** Of the
26 P0s, the core owns 6 (~9 h) and the surfaces own 14 (~15 h); the remaining 6
(~7 h) are packaging, infra and docs that are the lead's. The surfaces side is
roughly 60% heavier.

The swing item is the **CLI cluster** — P0-3, P0-14, P0-15, P0-16, P0-24, P0-25,
all of which live in `validsim/cli.py` and `validsim/jobs/worker.py`. That
cluster is ~5 h and is self-contained (one command file and one worker file).
**Move it to the core side and the two sides land at 15 h and 9 h — the same
imbalance, reversed.** Leave it on the surfaces side and the surfaces carry it.

Whichever way you cut it, say so once, in writing, before either side starts.

---

## The division

### You — compute core

```
validsim/engine/          scorecard, export, pdf, pipeline, evaluation, safety,
                          regression, stats, anomaly, trends, benchmark, _coerce
validsim/store/           memory, sqlite, postgres, _frozen
validsim/sim/             runner, isaac_worker, shadow
validsim/scenarios/       generator, llm_generator
validsim/config.py
validsim/config_loader.py
validsim/project_config.py
validsim/_log.py
validsim/__init__.py
+ every test file that imports those (84 test files)
```

**Your P0s (6 of them, ~9 h):** P0-2, P0-7, P0-8, P0-9, P0-10, P0-13

P0-1 (the wheel omits `validsim/web/`) needs `pyproject.toml` — a lead file. It
is one `package-data` line, but `pyproject.toml` is shared, so the lead does that
edit, or one of you does it *alone* with the other informed.

P0-21 (`_memory_summaries`) lives in `validsim/api/store_stats.py` — your
friend's folder. **Hand it to them**, or take it and ask them not to touch
`store_stats.py`. It is a 30-minute fix and currently latent (the function has
no production caller, which the repo's own
`test_wiring_reachability_agent.py:129` records as "not yet routed").

### Your friend — surfaces

```
validsim/api/             main, dashboard, metrics, store_stats
validsim/jobs/            queue, worker, router, models
validsim/notify/          dispatcher, config, egress, email
validsim/web/             app.js, view-core.js, index.html, styles.css
validsim/cli.py
+ every test file that imports those (18 test files)
```

**Their P0s (14 of them, ~15 h):** P0-3, P0-6, P0-11, P0-12, P0-14, P0-15,
P0-16, P0-17, P0-18, P0-20, P0-21, P0-24, P0-25, P0-26

They already have a detailed brief for the notify/security subset:
`_BRIEF_friend_notify_security.md` (P0-11, 12, 17, 18, 20 — and P0-13, which is
in your folder; see the open decisions).

### Lead — shared, and nobody's

**Never touched by either of you.** These break silently if two people change
them, or if one person changes them without telling the other.

```
pyproject.toml            ruff/mypy config, package-data, entry points
conftest.py               imported by every test file
pytest.ini                marker registration
requirements*.txt
Dockerfile, docker-compose.yml, docker-compose.prod.yml, Makefile
.env.example, .gitignore, .dockerignore
README.md, CHANGELOG.md, CONTRIBUTING.md, SECURITY.md
docs/**, audit/**, vault/**, site/**, marketing/**
actions/**, examples/**, infra/**, scripts/**
.github/**, design-system/**
```

**Also lead-only, because they span both halves (43 test files):** the ones that
genuinely exercise both sides — `test_delivery_pipeline.py`,
`test_repo_hygiene.py`, `test_wiring_reachability_agent.py`,
`test_security_regressions.py`, `test_observability.py`, `test_e2e_async.py`,
`test_worker_shutdown.py`, `test_dashboard.py`, the four `test_api_*` files that
touch the engine, `test_logging_redaction.py`, `test_config_loader.py`,
`test_project_config.py`, `test_sim_factory.py`, the three `test_cli_*` files, and
all 12 of `tests/bench/` — the benchmark baseline is compared against numbers
produced by the engine, so a change on either side can move it. Plus the site
and marketing tests, which belong to the site tree the lead owns.

**The one rule that makes this work:** if you need a change in a shared file,
you do not make it. You write what you need in your report, and the lead does it
in one commit with both of you informed. That is not overhead — `pyproject.toml`
and `conftest.py` are exactly where a concurrent edit produces a conflict that
looks like a random test failure.

---

## What each side does

### You (core)

| Order | P0 | Folder | Effort | The defect |
|---|---|---|---|---|
| 1 | P0-13 | `project_config.py` | 30 min | Password tail leaks in cleartext when it contains `@` or `/` |
| 2 | P0-2 | `store/sqlite.py` | 15 min | `sqlite3.connect` with no parent `mkdir` |
| 3 | P0-7 | `engine/export.py`, `engine/pdf.py` | 2 h | Renderers omit `block_reasons`; the verdict sentence can be false |
| 4 | P0-8 | `engine/export.py`, `engine/pdf.py` | 1 h | Chain-of-custody SHA-256 dropped from the HTML card and the PDF |
| 5 | P0-9 | `store/{sqlite,postgres}.py` | 2 h | SQL backends crash on a row with an unknown blob field |
| 6 | P0-10 | `store/postgres.py` | 3 h | Postgres does not override `save_many`, so batching never happens |
| 7 | P0-1 | `pyproject.toml` (lead) | 1 h | The wheel ships zero files under `validsim/web/` |

P0-7 and P0-8 are the same files and should be one change.

### Your friend (surfaces)

They already have the notify/security brief. Beyond it:

| Order | P0 | Folder | Effort | The defect |
|---|---|---|---|---|
| 1 | P0-6 | `web/app.js` | 1 h | Failure Taxonomy panel throws on every run |
| 2 | P0-21 | `api/store_stats.py` | 30 min | Returns the oldest records when the page runs past the end |
| 3 | P0-26 | `api/main.py` | 1 h | `create_app` replaces the router lifespan context |
| 4 | P0-25 | `cli.py` | 30 min | `job enqueue` lets `QueueFullError` escape uncaught |
| 5 | P0-3 | `cli.py` | 10 min | Empty `VALIDSIM_CACHE_FILE` crashes `validsim run` |
| 6 | P0-14/15/16 | `cli.py`, `jobs/worker.py` | 2 h | The CLI's cache is a second, unsynchronised source of truth |

P0-14, P0-15 and P0-16 share one root cause — the local cache is treated as
authoritative where the durable store should be. **Fix them as one coherent
change**, not three patches.

---

## Rules for both sides

1. **Prove before fixing.** One defect per iteration, with a minimal reproduction.
   "No defect found" is a valid result.
2. **Read before edit.** Never rewrite a large file wholesale.
3. **Never weaken a test.** No editing, deleting, skipping, xfail-ing or loosening
   an existing test to get green. Adding tests is fine. If a test pins a defect
   as current behaviour, invert it into a real regression test.
4. **Never touch git.** No commit, push or branch. The lead does that.
5. **Report, don't trespass.** A bug outside your folders goes in your report.
6. **Honest numbers only.** If you did not measure it, do not state it.

**Baselines to verify against:**

```bash
python -m pytest tests/ -p no:cacheprovider --no-cov
# 3254 tests, 0 failures, 0 errors, 51 skipped

python -m pytest tests/ -p no:cacheprovider --cov=validsim --cov-report=term
# 95.96% total (CI gate is 90%)
```

Each side also runs its own cohort before handing back — yours is the engine,
store, sim and scenarios tests; theirs is the api, jobs, notify, web and cli
tests. Neither may show a failure. If the count *drops*, something was deleted.

---

## Two open decisions for you to make

1. **P0-13** (credential leak) — it is in your folder (`project_config.py`) but
   their brief covers it as part of the secret-handling cluster. **Give it to
   them** (their brief is already written for it) **or keep it** (it is 30
   minutes and self-contained). Either is fine; it just must not be done twice.

2. **P0-21** (pagination) — it is in their folder (`api/store_stats.py`) but you
   found and characterised it. **Give it to them** with the exact trigger
   (`offset < n < offset + limit`), or take it and ask them not to touch
   `store_stats.py`.

Both are currently *latent* rather than live — `run_summaries` has no production
caller, and the repo's own `test_wiring_reachability_agent.py:129` records it as
"not yet routed".
