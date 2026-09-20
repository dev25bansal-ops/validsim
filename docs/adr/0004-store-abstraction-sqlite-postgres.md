# ADR 0004: Pluggable validation store — SQLite (overwrite) and PostgreSQL (append-only)

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Every validation run produces a `StoredRun`: the composite `Scorecard` plus the
full detail (evaluation, safety, raw episodes, baseline id, regression report).
This is the system of record behind the API (`/validations`, `/models`,
`/failures`, `/regressions`), the dashboard, the CLI, and the deploy gate — and,
for Tier-4 compliance buyers, an **audit trail** that must be trustworthy
([[Product Principles]] #5, [[IP Strategy]] "immutable audit trail").

But the deployment contexts differ radically:

- **Tests, CLI, and local dev** must run with zero setup and no external service
  ([[Product Principles]] #1).
- **A single engineer's laptop / a small team** wants persistence without
  standing up a database.
- **Production** is multi-process (API + workers, [[ADR 0003]]), needs durability,
  concurrency, indexed queries, and is already deploying PostgreSQL 16
  ([[Tech Stack]]).

Callers should not care which of these is active. The question is how to give one
code path three storage tiers without leaking backend differences into the API,
and whether "persistence" has the same meaning in each tier.

## Decision

Define a narrow store interface once and make every backend satisfy it. The
in-memory `ValidationStore` (`validsim/store/memory.py`) *is* the interface —
`save / get / delete / list_for_checkpoint / history / count / __len__ / close /
new_run_id`. `SqliteValidationStore` and `PostgresValidationStore` each subclass
it purely to advertise interface compatibility (and reuse `new_run_id`), then
override every state-touching method. A `create_store()` factory selects the
backend from `VALIDSIM_STORE` (`memory` | `sqlite` | `postgres`), so callers are
backend-agnostic.

Each run is persisted as **one row**: the query-hot fields (checkpoint, composite
score, decision, timestamp) are promoted to indexed columns, and the composite
scorecard plus the full detail are stored as JSON blobs (SQLite `TEXT`, Postgres
`JSONB`). This keeps every endpoint behaviourally identical across backends, and
legacy rows written before the detail columns existed remain readable through a
documented fallback (reconstruct an approximate evaluation/safety from the
scorecard). Schema upgrades are applied in place on open (`ALTER ... ADD COLUMN`).

Postgres mirrors the queue's optional-driver discipline (see [[ADR 0003]]): the
`psycopg` driver is imported lazily, the connection and `CREATE TABLE/INDEX IF
NOT EXISTS` are deferred to first use, so `create_store()` returns a configured
store without touching the network; a missing driver surfaces as an actionable
`RuntimeError` at first use. Identifiers (table name) are whitelisted against
`^[a-z_][a-z0-9_]*$` before interpolation; all *values* travel exclusively
through `%s`/`?` placeholders.

### Append-only Postgres vs. overwrite SQLite

The two persistent backends deliberately implement `save` with **different write
semantics**:

- **SQLite — overwrite.** `save` issues `INSERT OR REPLACE`, so re-saving a known
  `run_id` replaces the row. This matches the in-memory dict's contract exactly
  and is the right ergonomics for a local scratchpad / dev database: you fix a
  bad run, rerun, and the record updates in place.
- **PostgreSQL — append-only.** `save` issues
  `INSERT … ON CONFLICT (run_id) DO NOTHING`, so **the first write for a run id
  wins and later writes to the same id are ignored.** The deployed verdict log is
  therefore immutable: a persisted `APPROVE`/`BLOCK` decision can never be
  silently rewritten after the fact.

The split is intentional. The mutable tier is the one a developer owns; the
append-only tier is the one that backs the compliance audit trail, where
"the score we reported yesterday" must not be editable today. `delete()` is still
available on all backends (operator-driven run removal, `DELETE /validations/{id}`
→ 204/404), so append-only governs *writes to an existing id*, not the ability to
remove a run under explicit operator action.

## Consequences

**Positive**

- One code path serves laptop, single-node, and multi-process production; swapping
  tiers is one environment variable.
- Local/test flows stay dependency-free and side-effect free; Postgres never
  imports its driver or opens a socket until actually used.
- JSON-blob-plus-indexed-columns keeps every endpoint identical across backends
  while still allowing fast checkpoint/time queries.
- Append-only Postgres gives the deploy gate a tamper-evident history — a
  compliance asset ([[Compliance]], [[Moat]]).
- In-place `ALTER` migrations plus the legacy-row fallback let old databases stay
  readable across releases.

**Negative / trade-offs**

- **`save` is not idempotent across tiers.** Re-saving the same `run_id` updates
  SQLite/memory but is a no-op on Postgres. Callers that rely on "save = upsert"
  would see different behavior in production; the distinction is documented and
  the append-only path is the safer default, but it is a real semantic seam.
- Three backends mean three implementations to keep in lock-step against the
  interface; behavioural parity is enforced by tests, not the type system.
- Storing full detail as opaque JSON blobs trades query power for portability —
  rich analytics over episode/regression internals would need either JSON-path
  queries (Postgres) or extraction (SQLite), not plain SQL columns.
- `delete()` exists on the append-only store too, so "append-only" is a write-path
  guarantee, not an absolute immutability guarantee; true regulatory immutability
  would need an additional append-only table / trigger or Timescale retention
  policy (a candidate for a future ADR).

## References

- `validsim/store/memory.py` · `validsim/store/sqlite.py` ·
  `validsim/store/postgres.py` · `validsim/store/__init__.py` (`create_store`).
- [[Solution Architecture]] L3/L5 · [[Tech Stack]] · [[Data Flow]].
- [[ADR 0003]] (shared lazy/optional-driver pattern).
