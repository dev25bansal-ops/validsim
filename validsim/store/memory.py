"""In-memory, thread-safe validation result store.

Completed runs are kept in a process-local dictionary guarded by a
:class:`threading.Lock`. The interface (save/get/list/history) is deliberately
narrow so alternative backends (e.g. the SQLite store in
:mod:`validsim.store.sqlite`) satisfy the same contract without touching
callers.

This module is also where the *shared* store contract is declared, because
every other backend subclasses :class:`ValidationStore` purely to advertise
interface compatibility -- so a rule written here is inherited by all of them
and cannot drift per backend. Two rules are declared here:

**Append-only (``save`` is first-write-wins).** Re-saving an existing
``run_id`` never mutates the stored record, on any backend. See
:class:`ValidationStore` for why that is the correct trade for a deploy gate.

**Reconstruct, never filter (``_reconstruct_kwargs``).** Rows written before a
:class:`~validsim.engine.scorecard.Scorecard` field existed must keep loading,
so a field absent from a stored blob falls back to its dataclass default. A
read-path filter would protect the future by breaking the past.
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from validsim.engine.evaluation import EvaluationResult
from validsim.engine.regression import RegressionReport
from validsim.engine.safety import SafetyResult
from validsim.engine.scorecard import Scorecard
from validsim.sim.runner import EpisodeResult

__all__ = [
    "DuplicateRunError",
    "StoredRun",
    "ValidationStore",
    "rebind_row_identity",
    "reconstruct_kwargs",
]


def reconstruct_kwargs(cls: type[_C], data: dict[str, Any]) -> dict[str, Any]:
    """Map a stored JSON object onto ``cls``'s constructor keywords.

    Shared by every backend's read path (SQLite and PostgreSQL) so the two
    cannot drift, and built from :func:`dataclasses.fields` at call time so it
    cannot fall behind :class:`~validsim.engine.scorecard.Scorecard` either.
    The failure this replaces was real: ``Scorecard`` gained the
    adversarial-segment fields, the scorecard *was* written with them (it is a
    JSON blob), and both backends' hand-written ``_scorecard_from_dict``
    silently reset them to their defaults on reload -- a gate whose stated
    reason vanished on read. Enumerating fields by hand is what caused that;
    deriving them is what prevents the next one.

    Backward compatibility is the reason nothing is strict here. A blob written
    by an older release simply has no key for a newer field, so a missing key
    falls back to the field's default (a present-but-``None`` key keeps its
    ``None``). The read path never *filters* a row on the presence of a field,
    because a database that gains a column must keep serving its existing rows.

    The tuple-annotated fields are the lossy part of any JSON round trip: JSON
    has no tuple, so ``confidence_interval`` and ``block_reasons`` come back as
    lists and silently break equality (and hashing) against the object that was
    stored. Anything annotated with :data:`tuple` is restored as a tuple here.

    Args:
        cls: A dataclass whose fields define the accepted keywords.
        data: The decoded JSON object from storage.

    Returns:
        Keyword arguments suitable for ``cls(**kwargs)``.

    Raises:
        TypeError: Propagated from ``cls(**kwargs)`` if ``data`` carries a key
            that is not a field of ``cls`` -- a genuine shape mismatch, and one
            worth surfacing rather than swallowing.
    """
    kwargs: dict[str, Any] = {}
    for spec in dataclasses.fields(cls):
        if spec.name not in data:
            continue  # legacy row: this field did not exist when it was written
        value = data[spec.name]
        if "tuple" in str(spec.type) and isinstance(value, list):
            value = tuple(value)
        kwargs[spec.name] = value
    return kwargs


_C = TypeVar("_C")


def rebind_row_identity(
    scorecard: Scorecard,
    *,
    run_id: str,
    checkpoint_id: str,
    task_id: str,
    created_at: str,
) -> Scorecard:
    """Return ``scorecard`` carrying the *row's* identity rather than the blob's.

    A persistent row states its identity twice: once in indexed columns
    (``run_id``/``checkpoint_id``/``task_id``/``created_at``) and once inside
    the ``scorecard_json`` blob. Every query filters on the columns, so they
    are the only copy a lookup can actually find. A read that reported the
    blob's copy instead answered a different question than the one it was
    asked: a run saved under ``COLUMN-ID`` came back reporting ``BLOB-ID``, and
    the id it reported was un-fetchable -- ``get("BLOB-ID")`` returned ``None``
    while ``get("COLUMN-ID")`` returned that same object. ``list_for_checkpoint``
    was worse, filtering on one checkpoint and returning a run that claimed
    another. ``StoredRun`` carries both copies and nothing validated them, so
    the divergence is reachable the moment a caller builds a run whose top-level
    fields disagree with its scorecard.

    Rebinding rather than raising is deliberate. The indexed columns are the
    authoritative copy precisely because they are what SQL keys and filters on,
    and a row already on disk cannot be asked to agree -- so a legacy or
    hand-edited row still reads back self-consistently instead of becoming
    unreadable. When the two copies already match, which is every well-formed
    row, this returns ``scorecard`` itself, unchanged.

    Args:
        scorecard: The card rebuilt from the row's stored blob.
        run_id: The row's indexed ``run_id`` column.
        checkpoint_id: The row's indexed ``checkpoint_id`` column.
        task_id: The row's indexed ``task_id`` column.
        created_at: The row's indexed ``created_at`` column.

    Returns:
        ``scorecard`` when it already agrees with the row, otherwise a copy
        with its four identity fields replaced by the row's.
    """
    if (
        scorecard.run_id == run_id
        and scorecard.checkpoint_id == checkpoint_id
        and scorecard.task_id == task_id
        and scorecard.created_at == created_at
    ):
        return scorecard
    return dataclasses.replace(
        scorecard,
        run_id=run_id,
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        created_at=created_at,
    )


class DuplicateRunError(Exception):
    """Raised by :meth:`ValidationStore.save_exists` for an already-stored id.

    ``save`` itself never raises this: it is the tolerant, append-only path that
    a retry goes through, and turning a duplicate into an exception would make
    an idempotent write look like a failure. This exception exists for the
    callers that need to *know* a write was a duplicate -- chiefly the
    pipeline, so it can re-read the record that is actually on the record
    instead of returning a verdict the store never accepted.

    The message names the offending ``run_id`` because a duplicate id is
    nearly always a bug or a retry in the caller's own control, and the id is
    the only thing needed to identify it.
    """

    def __init__(self, run_id: str) -> None:
        """Record the offending ``run_id`` and format the message."""
        self.run_id = run_id
        super().__init__(f"run {run_id} already exists in the store")


#: Recursion cap for :func:`_copy_value`. Real records are a handful of levels
#: deep (``StoredRun`` -> episode -> summary dict), so this is a safety valve
#: against a caller-supplied structure that cycles, not a working limit.
_MAX_SNAPSHOT_DEPTH = 12


def _copy_value(value: Any, *, _depth: int = 0) -> Any:
    """Return *value* with every mutable container reachable from it rebuilt.

    Only the containers this record actually contains are rebuilt -- ``dict``,
    ``list``, ``tuple``, ``set`` -- plus any nested dataclass, so a new mutable
    field is covered by construction rather than by someone remembering to add
    it here. Immutable leaves (including the scorecard's ``tuple``-annotated
    fields) are shared as-is because they cannot change.

    The mutable fields actually present in a :class:`StoredRun` today are
    ``episodes`` (list), ``evaluation.per_task_success`` and
    ``evaluation.failure_taxonomy`` (dicts), ``scorecard.failure_taxonomy``
    (dict), ``episode.joint_states_summary`` (dict) and ``regression.items``
    (list). Every one of them is reachable from here.
    """
    if _depth >= _MAX_SNAPSHOT_DEPTH:
        return value
    if isinstance(value, dict):
        return {key: _copy_value(item, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_value(item, _depth=_depth + 1) for item in value)
    if isinstance(value, (set, frozenset)):
        return type(value)(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _copy_dataclass(value, _depth=_depth + 1)
    return value


def _copy_dataclass(obj: Any, *, _depth: int = 0) -> Any:
    """Structurally copy *obj*, rebuilding each of its init-able fields.

    ``dataclasses.replace`` is a **shallow** copy: it rebuilds the wrapper but
    shares every mutable field, which is exactly how the caller-reachable
    mutables escaped the previous copy. Rebuilding each field through
    :func:`_copy_value` closes that gap for this record, and skipping
    ``init=False`` fields leaves the ones a caller cannot set alone.
    """
    changes = {
        field.name: _copy_value(getattr(obj, field.name), _depth=_depth + 1)
        for field in dataclasses.fields(obj)
        if field.init
    }
    return dataclasses.replace(obj, **changes)


def _snapshot(run: StoredRun) -> StoredRun:
    """Detach a run from every object its caller still holds a reference to.

    ``StoredRun`` is frozen, which reads as "immutable" but is not:
    ``episodes`` is a list of *non-frozen* dataclasses and
    ``scorecard.failure_taxonomy`` is a dict. Storing the caller's object by
    reference meant anything it went on to do to its own episode list or
    taxonomy rewrote the record -- clearing the list took a stored verdict to
    zero episodes, and flipping ``episodes[0].success`` changed the evidence
    behind an APPROVE with no write, no lock and no log line. That is exactly the
    "silently rewritten verdict" the append-only contract on this class exists
    to make impossible, and the persistent backends never had the hole because
    they rebuild on read.

    The copy is structural rather than a hand-enumerated list of fields, so a
    mutable field added to :class:`StoredRun` or to any nested artifact is
    covered by construction instead of by someone remembering to extend this
    function -- the previous version enumerated two of the five reachable
    mutables and so left the other three shared with the caller.

    Scalars are shared (they are immutable); every ``dict``/``list``/``tuple``/
    ``set`` and every nested dataclass reachable from the record is rebuilt.
    That includes the containers *inside* the nested artifacts --
    ``episodes[i].joint_states_summary``, ``evaluation``'s two dicts and
    ``regression.items`` -- which the persistent backends were always immune to,
    because they serialise at save time.
    """
    return _copy_dataclass(run)


@dataclass(frozen=True)
class StoredRun:
    """A completed validation run with all engine artifacts.

    Attributes:
        run_id: Store-assigned id, format ``"vrun-<uuid8>"``.
        checkpoint_id: Checkpoint that was validated.
        task_id: Task that was executed.
        created_at: ISO-8601 UTC timestamp (mirrors the scorecard).
        scorecard: Final composite verdict.
        evaluation: Aggregate success/failure metrics.
        safety: Safety metrics for the run.
        episodes: Raw per-episode results (used by failure inspection).
        baseline_run_id: Baseline used at creation time, if any.
        regression: Baseline comparison report, if performed.
    """

    run_id: str
    checkpoint_id: str
    task_id: str
    created_at: str
    scorecard: Scorecard
    evaluation: EvaluationResult
    safety: SafetyResult
    episodes: list[EpisodeResult] = field(default_factory=list)
    baseline_run_id: str | None = None
    regression: RegressionReport | None = None

    def summary(self) -> dict[str, Any]:
        """Compact JSON-friendly run summary for list/detail endpoints."""
        return {
            "run_id": self.run_id,
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "baseline_run_id": self.baseline_run_id,
            "composite_score": self.scorecard.composite_score,
            "deploy_decision": self.scorecard.deploy_decision,
            "episode_count": self.scorecard.episode_count,
        }


class ValidationStore:
    """Thread-safe dict-based store of :class:`StoredRun` records.

    **The store contract (binding on every backend).** All backends subclass
    this class, so the two rules below are declared once here and inherited;
    a backend that implemented them differently would be a bug, not a variant.

    1. ``save(run)`` is **append-only**: the first write for a ``run_id`` is the
       record, and a later ``save`` of the same id is a no-op that returns the
       caller its own (unstored) ``run`` unchanged. It never mutates, deletes
       or re-dates what is stored, and it never raises for the duplicate.
    2. ``get``/``list_for_checkpoint``/``history`` return exactly the
       first-recorded object, so no read path can surface a rejected write.

    **Why append-only and not last-write-wins.** The alternative -- a later
    ``save`` silently replacing the row -- was the historical behaviour of this
    store and of the SQLite backend, and it was the wrong trade for this
    product. The store is the evidence record a deploy gate signs off against,
    so the question "which write survives?" has an asymmetric cost: a lost
    duplicate is invisible and harmless (the caller is retrying an operation
    whose first attempt already succeeded), while a lost original is a
    *silently rewritten verdict*. That is not hypothetical here --
    ``validsim/jobs/worker.py`` reuses ``spec.run_id`` on every retry, and
    ``actions/validate/action.yml`` selects the SQLite backend by default, so
    a crash-and-retry on the default path could turn a recorded 95.0/APPROVE
    into 20.0/BLOCK with no error and no log line. For a record whose whole
    purpose is to be trusted after the fact, the failure mode must be the
    harmless one.

    **Why the duplicate is silent rather than an exception.** A retry is
    *expected* traffic, not an error, and an exception on an idempotent write
    would make a crash-and-retry surface as a hard failure. The cost of
    silence is one invariant for callers to hold -- "the object ``save``
    returns is what you passed in, not necessarily what is stored" -- which is
    why it is stated in the docstring, enforced in
    :meth:`TestAppendOnlyWriteContract` (``tests/test_store_parity.py``), and
    given an explicit escape hatch in :meth:`save_exists` for the one caller
    that must not guess. The sanctioned way to change what is on the record for
    a given id is :meth:`delete` followed by a fresh ``save``: the correction is
    then explicit and visible.

    **Not frozen.** Append-only is about implicit mutation, not immutability.
    :meth:`delete` is a deliberate operator action, and after it the id is free
    for a corrected run.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._runs: dict[str, StoredRun] = {}
        self._lock = threading.Lock()

    @staticmethod
    def new_run_id() -> str:
        """Generate a fresh run id like ``"vrun-1a2b3c4d"``."""
        return f"vrun-{uuid.uuid4().hex[:8]}"

    def save(self, run: StoredRun) -> StoredRun:
        """Record ``run`` under ``run.run_id``; return ``run`` unchanged.

        Append-only (see the class docstring): if that id is already stored,
        this is a no-op -- the existing record is kept, byte for byte, and the
        duplicate ``run`` is *not* what subsequent reads return.

        The return value is always the ``run`` argument, never a re-read of
        storage. That keeps the signature total and non-raising, at the cost of
        one invariant a caller must know: after a duplicate, the returned
        object is *not* the record on disk. A caller that must not act on an
        unstored verdict should use :meth:`save_exists`, which tells it
        outright.

        Args:
            run: The completed run to record.

        Returns:
            The ``run`` argument, unchanged.
        """
        with self._lock:
            self._runs.setdefault(run.run_id, _snapshot(run))
        return run

    def save_exists(self, run: StoredRun) -> bool:
        """Record ``run`` unless its id is taken; return whether it was new.

        The strict counterpart of :meth:`save`, for callers that need to know
        whether their write reached the record -- notably the validation
        pipeline, which reuses a caller-supplied ``run_id`` and so cannot
        assume its verdict is the one on the record.

        Returns:
            ``True`` if ``run`` was recorded, ``False`` if the id was already
            taken (in which case nothing was written and the stored record is
            unchanged).
        """
        with self._lock:
            if run.run_id in self._runs:
                return False
            self._runs[run.run_id] = _snapshot(run)
            return True

    def get(self, run_id: str) -> StoredRun | None:
        """Return the run for ``run_id`` or ``None`` if unknown."""
        with self._lock:
            return self._runs.get(run_id)

    def delete(self, run_id: str) -> bool:
        """Remove the run for ``run_id``; return ``True`` if it existed.

        The pop happens under the store lock so a concurrent reader never
        observes a partially removed record. The operation is idempotent: a
        second delete of the same id (or an unknown id) removes nothing and
        returns ``False``.
        """
        with self._lock:
            return self._runs.pop(run_id, None) is not None

    def list_for_checkpoint(self, checkpoint_id: str) -> list[StoredRun]:
        """All runs for a checkpoint, oldest first."""
        with self._lock:
            matches = [r for r in self._runs.values() if r.checkpoint_id == checkpoint_id]
        return sorted(matches, key=lambda r: r.created_at)

    def history(
        self, since: str | None = None, until: str | None = None
    ) -> list[StoredRun]:
        """Every stored run, oldest first, optionally bounded by a date range.

        Args:
            since: Inclusive lower bound, an ISO-8601 string. Runs whose
                ``created_at`` precedes it are dropped.
            until: Inclusive upper bound, an ISO-8601 string. Runs whose
                ``created_at`` follows it are dropped.

        ``created_at`` is stored as ISO-8601 UTC text in a fixed format, so
        lexicographic string comparison equals chronological comparison; the
        bounds are therefore applied directly with ``>=``/``<=``. A ``None``
        bound leaves that side unbounded, and with both ``None`` the result is
        identical to the unfiltered history.
        """
        with self._lock:
            runs = list(self._runs.values())
        if since is not None:
            runs = [r for r in runs if r.created_at >= since]
        if until is not None:
            runs = [r for r in runs if r.created_at <= until]
        return sorted(runs, key=lambda r: r.created_at)

    def count(self) -> int:
        """Number of stored runs.

        Equivalent to :meth:`__len__` but exposed as a method so every
        :func:`create_store` backend offers the same ``count()`` surface for
        callers (e.g. the CLI ``models`` footer) that prefer an explicit call
        over ``len(store)``. The read happens under the store lock so the
        value is a consistent snapshot of the dictionary.
        """
        with self._lock:
            return len(self._runs)

    def __len__(self) -> int:
        """Number of stored runs."""
        with self._lock:
            return len(self._runs)

    def close(self) -> None:
        """Release backend resources (no-op for the in-memory store).

        Present so callers can treat every :func:`create_store` backend
        uniformly; the SQLite backend overrides this to close its connection.
        """
