# ADR 0006: The CLI gate is a verdict reader over the durable store

- **Status:** Accepted
- **Date:** 2026-09-21
- **Supersedes:** parts of [[ADR 0002]] — specifically how `validsim gate`
  derives its answer. The composite formula, the weights, and what the engine
  persists are unchanged.

## Context

[[ADR 0002]] ended with: "The CLI `validsim gate` re-reads the stored
composite/threshold and **exits non-zero on BLOCK**." Implementing that
literally produced three defects, all found in the 2026-09-21 audit
(`docs/ISSUE_CATALOG.md` items 01, 06, 22):

1. **The composite is not the whole verdict.** The engine also blocks for
   reasons a score cannot express — a run that delivered fewer episodes than its
   task asked for proves nothing at any score. A gate that recomputes
   `composite >= threshold` therefore *approves runs the engine blocked*. This
   was reachable from the shipped CI action, and a passing test pinned it as
   intended behaviour because the test derived its expectation from the same
   field the gate ignored.
2. **The run was read from a plain JSON file.** `gate` resolved the run id and
   the scorecard from `VALIDSIM_CACHE_FILE` (`.validsim/scorecards.json`) — a
   world-writable file any step in the same job could rewrite. Approval of a
   physical fleet rested on "did a file contain the string APPROVE".
3. **`--threshold` could lower the bar.** Documented as an override, a smaller
   value replaced the stored threshold, so `gate --threshold 0` turned any
   `BLOCK` into an `APPROVE` from the command line.

Separately, `_RUN_ID_RE` existed in `validsim/cli.py` and was never applied, so
the malformed ids in the docs (`--run-id abc123`) were accepted as cache keys.

## Decision

`gate` stops being a calculator and becomes a **reader of the recorded verdict,
over the durable store only**. Four rules, in `validsim/cli.py`:

- **Store, never cache.** `gate` fetches the run via
  `create_store().get(run_id)` (`validsim/store/__init__.py`). The JSON cache
  remains what `status` / `scorecard` / `report` show a human; it is not in the
  trust path.
- **A durable backend is a precondition, not a preference.** The default
  `VALIDSIM_STORE=memory` cannot outlive the `validsim run` process that filled
  it, so `gate` calls `_require_durable_store()` and exits `2` with a message
  naming the accepted backends rather than guessing from the cache. CI users get
  this from `actions/validate/action.yml`, which exports
  `VALIDSIM_STORE=sqlite` plus a `runner.temp` database path.
- **Only the exact string `APPROVE` approves.** `approved = (card.deploy_decision
  == "APPROVE" and composite >= effective)` — comparison is equality against the
  constant, so `approve`, `APPROVE-ish`, or an empty field block. SQLite also
  refuses a null verdict (`deploy_decision TEXT NOT NULL`,
  `validsim/store/sqlite.py:42`); Postgres declares the column nullable
  (`validsim/store/postgres.py:86`), so parity there is unenforced and a null can
  only ever block, never approve.
- **`--threshold` may only tighten.** `effective = max(stored, given)`, and a
  negative value means "use the stored threshold".

Run-targeting keeps the `0/1/2` exit-code contract, with `2` reserved for
misuse: no durable store, a run id that fails `_RUN_ID_RE.fullmatch`
(`vrun-<8 hex>`), no stored run, or both `--run-id` and `--latest`.
`--latest` resolves against `store.history()[-1]`, not the cache.

## Consequences

**Positive**

- The decision and the audit record are the same row. API, dashboard,
  `validsim models`, and the gate cannot disagree about a run's verdict.
- Forging a verdict now requires writing to the database the API serves, not
  dropping a JSON file in a working directory.
- The tighten-only threshold makes the CI flag an accelerator, never an
  override; no pipeline step can buy back an approval.
- A misconfigured store fails *loudly* with an actionable message instead of
  silently approving or blocking.

**Negative / trade-offs**

- Behaviour change for hand-run CLI use: `validsim gate` on a fresh clone with
  no `VALIDSIM_STORE` now exits `2` where it once read the cache. Intentional —
  but scripts that relied on it must set a backend.
- sqlite under `runner.temp` is job-scoped, so `run → gate` must stay in one
  job. Cross-job and cross-run gating still needs the hosted store
  ([[ADR 0004]]), and the store default question stays open
  (`docs/ISSUE_CATALOG.md` item 21).
- The engine's verdict is now load-bearing for safety, so the code that writes
  `deploy_decision` is in the critical path; changing its semantics is an ADR
  event, not a refactor.
- `compare --candidate-latest` still resolves its ids from the cache. Left as
  is: it reports a diff between two stored runs and gates nothing, so a forged
  cache can only make it exit `2` or compare real runs. Revisit if it ever
  becomes a decision input.

## References

- `validsim/cli.py` — `gate`, `_resolve_stored_run_id`, `_require_durable_store`,
  `_newest_stored_run_id`, `_DURABLE_STORE_BACKENDS`.
- `validsim/store/__init__.py` — `store_backend()`, `create_store()`.
- `actions/validate/action.yml` (exports the store) ·
  `actions/scorecard/action.yml` (fails closed on a missing scorecard).
- `tests/test_cli.py` (`TestGate` — forged cache, non-`APPROVE` verdicts,
  malformed ids) · `tests/test_delivery_pipeline.py` (CI configuration).
- [[ADR 0002]] (composite weights, unchanged) · [[ADR 0004]] (store backends).
- `docs/ISSUE_CATALOG.md` items 01, 06, 11, 21, 22 · [[Product Principles]] #4.
