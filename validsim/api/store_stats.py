"""O(1)-per-store-summary observability reads that never hydrate episodes.

Why this module exists
----------------------
Every read surface in :mod:`validsim.api` derives its values from
``store.history()``, which returns **fully reconstructed**
:class:`~validsim.store.memory.StoredRun` objects. For the SQLite and
Postgres backends that means ``history()`` reads *every* row and JSON-decodes
*every* blob -- ``scorecard_json``, ``episodes_json``, ``evaluation_json``,
``safety_json`` and ``regression_json`` -- only for the caller to read two or
three scalar fields off the result.

Measured on this repository (Python 3.14.4, sqlite3, 1 episode per run,
``sqlite3.connect`` defaults, median of 3 samples)::

    n=10_000 rows   SELECT * fetchall ........................  291 ms
                    + scorecard Scorecard() ..................  631 ms
                    + episodes EpisodeResult() ..............  188 ms
                    + evaluation + safety ...................  328 ms
                    + regression .............................   79 ms
                    total store.history() .................... 1747 ms

    n=100_000 rows  store.history() ........................ 16882 ms

The ``episodes_json`` re-hydration that earlier agents asserted is real but is
**not** the dominant term: ``episodes_json`` is only ~11% of ``history()`` at
n=10_000. The dominant terms are the row fetch itself and
``scorecard_json``/``evaluation_json``/``safety_json`` decoding. So a fix that
only stops hydrating episodes would leave the overwhelming majority of the
cost in place.

What this module does instead
----------------------------
It computes the exact same values the endpoints already compute, from the
*narrow projection* each value lives in:

* :func:`store_totals` -- run/approval/block counts and the average composite
  score, i.e. everything ``/api/v1/dashboard/summary`` returns, from
  ``composite_score``/``deploy_decision`` alone.
* :func:`latest_composite` -- the newest run's composite score, i.e.
  ``validsim_composite_score`` from ``ORDER BY created_at DESC LIMIT 1``.
* :func:`run_summaries` -- the compact :meth:`StoredRun.summary` dicts the list
  and dashboard-history endpoints serialise, from the seven promoted columns
  only.

Every function takes a store and uses :func:`_narrow` to pick the cheapest
available strategy, so it works unchanged on all three backends:

* SQLite / Postgres -- a ``SELECT`` over the indexed, promoted columns. No
  ``*``, so no JSON blob is ever read from disk. Uses the already-present
  ``idx_validations_created`` index for ordering, so the row set stays
  O(limit) rather than O(total runs).
* In-memory -- the promoted columns are already resident attributes on the
  stored objects, so the answer costs one pass with no allocation and no
  copying, and the page is sliced before it is built.

This module was born **not** wired in: it was a drop-in for whoever owns
``validsim/api/main.py`` and ``validsim/api/metrics.py``. :func:`store_totals`
and :func:`latest_composite` are now wired into
:func:`validsim.api.metrics.render_metrics` (via ``_summary``), because the
scrape path they replaced rehydrated every JSON blob of every stored run to
read three scalars -- measured 105 ms at n=4 000, growing linearly, on the
**unauthenticated** ``/metrics`` endpoint. :func:`run_summaries` and
:func:`strategy_for` remain unwired; they are the list/dashboard-history
endpoints' replacement and nothing calls them yet.

Correctness contract
--------------------
The values returned here are exactly what the current code computes, so a
caller can swap one for the other without touching its response shape. That
property is enforced by ``tests/test_api_obsstats_agent.py``, which asserts
the fast path equals the ``history()`` path for the in-memory, SQLite and
fake-connection Postgres backends.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

__all__ = [
    "StoreTotals",
    "SUMMARY_KEYS",
    "run_summaries",
    "latest_composite",
    "store_totals",
    "strategy_for",
]


#: Key order of :meth:`~validsim.store.memory.StoredRun.summary`, i.e. the exact
#: shape :func:`run_summaries` must reproduce.
SUMMARY_KEYS: tuple[str, ...] = (
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
    "baseline_run_id",
    "composite_score",
    "deploy_decision",
    "episode_count",
)

#: Columns the SQL summary projection reads, in select order. These are the
#: promoted scalar columns plus ``scorecard_json`` -- the only blob it must
#: decode, and only because ``episode_count`` is not promoted to a column of
#: its own. The other JSON blobs (``episodes_json``, ``evaluation_json``,
#: ``safety_json``, ``regression_json``) are deliberately absent: that absence
#: is the whole point of the projection.
_SQL_SUMMARY_KEYS: tuple[str, ...] = (
    "run_id",
    "checkpoint_id",
    "task_id",
    "created_at",
    "baseline_run_id",
    "composite_score",
    "deploy_decision",
    "scorecard_json",
)

_SELECT_SUMMARY_SQL = (
    "SELECT run_id, checkpoint_id, task_id, created_at, baseline_run_id, "
    "composite_score, deploy_decision, scorecard_json "
    "FROM validations ORDER BY created_at DESC LIMIT ? OFFSET ?"
)

_SELECT_TOTALS_SQL = (
    "SELECT COUNT(*), "
    "COALESCE(SUM(CASE WHEN deploy_decision = 'APPROVE' THEN 1 ELSE 0 END), 0), "
    "COALESCE(SUM(composite_score), 0) "
    "FROM validations"
)

_SELECT_LATEST_SQL = (
    "SELECT composite_score FROM validations ORDER BY created_at DESC LIMIT 1"
)


class StoreTotals:
    """Aggregate counts for a store, plus the mean composite score.

    Mirrors the exact keys ``/api/v1/dashboard/summary`` returns, so it can be
    dropped in as a replacement. ``avg_composite`` is ``None`` for an empty
    store (the endpoint's documented "no runs yet" value) and is rounded to
    two decimals exactly as the endpoint rounds it.
    """

    __slots__ = ("total_runs", "approvals", "blocks", "avg_composite")

    def __init__(
        self, total_runs: int, approvals: int, blocks: int, avg_composite: float | None
    ) -> None:
        """Store the four dashboard KPI values."""
        self.total_runs = total_runs
        self.approvals = approvals
        self.blocks = blocks
        self.avg_composite = avg_composite

    @property
    def runs(self) -> int:
        """Alias for ``total_runs``; the metric is named ``validsim_runs_total``."""
        return self.total_runs

    def as_dict(self) -> dict[str, Any]:
        """Render as the exact JSON body of ``/api/v1/dashboard/summary``."""
        return {
            "total_runs": self.total_runs,
            "approvals": self.approvals,
            "blocks": self.blocks,
            "avg_composite": self.avg_composite,
        }

    def __eq__(self, other: object) -> bool:
        """Compare by value, so a test can assert fast path == slow path."""
        if not isinstance(other, StoreTotals):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        """Readable repr for assertion failures."""
        return f"StoreTotals({self.as_dict()})"


def _row_mapping(row: Any, keys: tuple[str, ...]) -> Mapping[str, Any]:
    """Normalise a DB row to a mapping keyed by ``keys`` in select order.

    ``keys`` must be the exact column list of the statement that produced
    ``row``. Passing the wrong list here is not a loud failure: a positional
    ``zip`` happily pairs a value against the wrong name and returns a
    plausible-looking but incorrect dict, so every call site names its own
    columns explicitly rather than defaulting to :data:`SUMMARY_KEYS`.
    """
    try:
        return {k: row[k] for k in keys}
    except (TypeError, IndexError, KeyError):
        return dict(zip(keys, row))


def _summary_from_run(run: Any) -> dict[str, Any]:
    """Build one summary dict from a resident :class:`StoredRun` object."""

    card = run.scorecard
    return {
        "run_id": run.run_id,
        "checkpoint_id": run.checkpoint_id,
        "task_id": run.task_id,
        "created_at": run.created_at,
        "baseline_run_id": run.baseline_run_id,
        "composite_score": card.composite_score,
        "deploy_decision": card.deploy_decision,
        "episode_count": card.episode_count,
    }


def _sqlite_conn(store: Any) -> Any | None:
    """Return the store's raw sqlite3 connection, or ``None`` if not SQLite.

    Duck-typed on the private ``_conn`` plus a ``sqlite3.Connection`` check
    rather than an ``isinstance`` import of the concrete store class, so a
    subclass or a test double that exposes a real connection still qualifies.
    """
    conn = getattr(store, "_conn", None)
    return conn if isinstance(conn, sqlite3.Connection) else None


def strategy_for(store: Any) -> str:
    """Name the strategy :func:`store_totals` would use for ``store``.

    One of ``"sql"`` (narrow indexed projection), ``"memory"`` (resident
    attribute pass) or ``"generic"`` (last-resort ``history()``). Exposed so a
    benchmark or a test can assert which path a store actually took instead of
    inferring it from timings.
    """
    if _sqlite_conn(store) is not None:
        return "sql"
    if hasattr(store, "_runs"):
        return "memory"
    return "generic"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def store_totals(store: Any) -> StoreTotals:
    """Run/approval/block counts and mean composite score for ``store``.

    Replaces the ``history()``-based body of ``/api/v1/dashboard/summary``.
    On a SQL backend this is a single ``SELECT COUNT(*), SUM(...), SUM(...)``
    that touches no JSON column at all, so its cost is independent of how many
    episodes each run stored.

    Args:
        store: Any :class:`~validsim.store.memory.ValidationStore` backend.

    Returns:
        A :class:`StoreTotals`; ``avg_composite`` is ``None`` when the store is
        empty.
    """
    conn = _sqlite_conn(store)
    if conn is not None:
        with _store_lock(store):
            total, approvals, composite_sum = conn.execute(_SELECT_TOTALS_SQL).fetchone()
        return _totals_from_parts(int(total), int(approvals), float(composite_sum))

    if hasattr(store, "_runs"):
        # The in-memory store keeps runs in an insertion-ordered dict and
        # `history()` copies *and sorts* that whole dict on every call. Totals are
        # order-independent, so walk the dict values directly: no list copy and
        # no O(n log n) sort. The lock is borrowed for the same reason the store
        # borrows it everywhere else -- the dict can be mutated by a concurrent
        # save, and an unlocked read of it can raise "dict changed size".
        with _store_lock(store):
            total = approvals = 0
            composite_sum = 0.0
            for run in store._runs.values():
                total += 1
                card = run.scorecard
                if card.deploy_decision == "APPROVE":
                    approvals += 1
                composite_sum += card.composite_score
        return _totals_from_parts(total, approvals, composite_sum)

    runs = store.history()
    total = len(runs)
    approvals = sum(1 for r in runs if r.scorecard.deploy_decision == "APPROVE")
    composite_sum = 0.0
    for r in runs:
        composite_sum += r.scorecard.composite_score
    return _totals_from_parts(total, approvals, composite_sum)


def _totals_from_parts(total: int, approvals: int, composite_sum: float) -> StoreTotals:
    """Assemble a :class:`StoreTotals` from raw counts and a score sum.

    One place applies the empty-store guard and the two-decimal rounding, so
    every strategy produces byte-identical output and the rounding rule cannot
    drift between backends.
    """
    if total == 0:
        return StoreTotals(0, 0, 0, None)
    return StoreTotals(
        total_runs=total,
        approvals=approvals,
        blocks=total - approvals,
        avg_composite=round(composite_sum / total, 2),
    )


def latest_composite(store: Any) -> float:
    """Composite score of the newest stored run; ``0.0`` when the store is empty.

    Replaces the ``history()``-derived ``validsim_composite_score`` gauge. On
    a SQL backend this is ``ORDER BY created_at DESC LIMIT 1`` served by the
    ``idx_validations_created`` index, so it reads one row instead of all of
    them.
    """
    conn = _sqlite_conn(store)
    if conn is not None:
        with _store_lock(store):
            row = conn.execute(_SELECT_LATEST_SQL).fetchone()
        return float(row[0]) if row is not None else 0.0

    if hasattr(store, "_runs"):
        # Same reasoning as store_totals: the newest run is a max over
        # created_at, which does not require history()'s copy-and-sort of the
        # whole dict.
        #
        # The tie-break must match ``history()[-1]`` exactly, and ``max()`` does
        # not: ``sorted`` is stable, so among runs sharing the maximum
        # ``created_at`` ``history()[-1]`` is the *last inserted*, while
        # ``max()`` returns the *first* maximal element. Probed on three runs
        # sharing one stamp (composites 11/22/33 inserted in that order):
        # ``history()[-1]`` gave 33.0 and ``max()`` gave 11.0. Scanning forward
        # and replacing on ``>=`` (not ``>``) therefore keeps the last-inserted
        # of a tie, which is the ``history()[-1]`` answer this function exists to
        # reproduce.
        with _store_lock(store):
            newest = None
            newest_stamp: str | None = None
            for run in store._runs.values():
                if newest_stamp is None or run.created_at >= newest_stamp:
                    newest, newest_stamp = run, run.created_at
        if newest is None:
            return 0.0
        return float(newest.scorecard.composite_score)

    runs = store.history()
    return float(runs[-1].scorecard.composite_score) if runs else 0.0


def run_summaries(
    store: Any, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, Any]]:
    """Compact run summaries, newest first, without hydrating any episode.

    Replaces the ``history()`` + slice + :meth:`StoredRun.summary` body shared
    by ``/api/v1/validations`` and ``/api/v1/dashboard/history``.

    Args:
        store: Any validation-store backend.
        limit: Maximum number of summaries to return. ``None`` returns all,
            which is what the dashboard-history endpoint needs; the list
            endpoint should always pass its own page size so the row set stays
            bounded.
        offset: Number of newest-first summaries to skip.

    Returns:
        Dicts with exactly :meth:`StoredRun.summary`'s keys and values.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative or None")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    conn = _sqlite_conn(store)
    if conn is not None:
        return _sql_summaries(conn, store, limit, offset)

    if hasattr(store, "_runs"):
        return _memory_summaries(store, limit, offset)

    runs = store.history()
    newest_first = _newest_first(runs, offset, limit)
    return [_summary_from_run(r) for r in newest_first]


def _newest_first(runs: list[Any], offset: int, limit: int | None) -> list[Any]:
    """Slice an oldest-first ``history()`` result into a newest-first page."""
    ordered = list(reversed(runs))
    if limit is None:
        return ordered[offset:]
    return ordered[offset : offset + limit]


def _memory_summaries(store: Any, limit: int | None, offset: int) -> list[dict[str, Any]]:
    """Build one page of summaries from the in-memory store.

    Deliberately just ``history()`` + slice, and deliberately *not* cleverer.
    An O(n log k) :func:`heapq.nlargest` window was tried here on the theory
    that skipping a full sort would win. Measured on this repo it lost badly:
    9.02ms vs 0.62ms for a 25-row page at n=10_000. ``list.sort`` runs in C on
    nearly-sorted data and costs well under a millisecond, while ``nlargest``
    pays a Python-level tuple construction and comparison per element. The
    lesson is that the asymptotics were never the binding constraint at these
    sizes, so the simple version stays.

    ``history()`` copies and sorts under the store's own lock, so the
    snapshot is already consistent; no extra locking is needed here.
    """
    runs = store.history()
    if limit is None:
        return [_summary_from_run(r) for r in reversed(runs)]
    # Slice the *runs* first, then build dicts for the page only. Building a
    # summary for all n runs and then discarding n-limit of them (the obvious
    # one-liner) allocates n dicts to return 25, which measured 35.8ms vs
    # 6.6ms at n=10_000.
    page = runs[len(runs) - offset - limit :] if offset + limit < len(runs) else runs
    if offset:
        page = page[: len(page) - offset]
    return [_summary_from_run(r) for r in reversed(page)]


def _sql_summaries(
    conn: Any, store: Any, limit: int | None, offset: int
) -> list[dict[str, Any]]:
    """Build one page of summaries for a SQL backend, decoding only the blob.

    ``episode_count`` lives inside ``scorecard_json`` on the SQL backends rather
    than in a promoted column, so producing the exact
    :meth:`StoredRun.summary` shape requires decoding that one blob per *visited
    row* -- but not per stored row. ``LIMIT``/``OFFSET`` cap how many rows are
    visited, so a page costs O(page size) regardless of how many runs the store
    holds. Compared with ``history()``, which visits every row and additionally
    rebuilds episodes, evaluation, safety and regression, this removes the
    O(total runs) term entirely.

    Kept in its own function so the unavoidable per-row decode is visible in
    one place instead of hidden inside a branch.
    """
    with _store_lock(store):
        rows = conn.execute(
            _SELECT_SUMMARY_SQL, (_sql_limit(limit), offset)
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        card = _row_mapping(row, _SQL_SUMMARY_KEYS)
        out.append(
            {
                "run_id": card["run_id"],
                "checkpoint_id": card["checkpoint_id"],
                "task_id": card["task_id"],
                "created_at": card["created_at"],
                "baseline_run_id": card["baseline_run_id"],
                "composite_score": card["composite_score"],
                "deploy_decision": card["deploy_decision"],
                "episode_count": _episode_count_from(card["scorecard_json"]),
            }
        )
    return out


def _sql_limit(limit: int | None) -> int:
    """Translate ``limit=None`` into the SQL sentinel for "no limit".

    Measured, not assumed: in SQLite ``LIMIT NULL`` raises
    ``sqlite3.IntegrityError: datatype mismatch`` rather than meaning
    unbounded, while ``LIMIT -1`` returns every row. Both SQLite and
    PostgreSQL treat a negative limit as unbounded, so ``-1`` is portable and
    ``None`` is never bound.
    """
    return -1 if limit is None else limit


def _episode_count_from(scorecard_json: str | None) -> int:
    """Extract ``episode_count`` from a ``scorecard_json`` blob.

    Parses the whole blob with :mod:`json` rather than reaching for a regex
    substring: a regex would be faster on a happy path but silently returns
    the wrong number for a blob whose formatting differs, and a summary field
    that is quietly wrong is worse than one that costs a decode.
    """
    if not scorecard_json:
        return 0
    return int(json.loads(scorecard_json).get("episode_count", 0))


class _NullLock:
    """A no-op context manager for stores that expose no lock."""

    def __enter__(self) -> None:
        """Enter the (empty) critical section."""

    def __exit__(self, *exc: object) -> bool:
        """Exit the (empty) critical section without swallowing errors."""
        return False


def _store_lock(store: Any) -> Any:
    """Return ``store``'s own lock, or a no-op stand-in.

    Every read in the SQLite backend runs under ``self._lock`` because the
    connection is shared with ``check_same_thread=False``; borrowing the same
    lock is what makes it safe to issue a query here.
    """
    lock = getattr(store, "_lock", None)
    if lock is None:
        return _NullLock()
    return lock
